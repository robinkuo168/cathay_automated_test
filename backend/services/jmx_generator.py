import os
import json
import threading
import re, textwrap
import math
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import pandas as pd
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
from ibm_watsonx_ai.credentials import Credentials
from dotenv import load_dotenv
from lxml import etree
from lxml.builder import E
import xml.etree.ElementTree as ET
import xml.sax.saxutils as saxutils
from xml.sax.saxutils import escape as saxutils_escape
from dataclasses import asdict
from .logger import get_logger
from .llm_service import LLMService
import io
import csv
import asyncio
from dataclasses import dataclass, field

load_dotenv()

@dataclass
class CsvInfo:
    """儲存 CSV Data Set Config 的所有詳細參數"""
    name : str
    filename: str
    variable_names: List[str] = field(default_factory=list)
    delimiter: str = ","
    ignoreFirstLine: bool = False
    quotedData: bool = False
    recycle: bool = True
    stopThread: bool = False
    shareMode: str = "shareMode.all"
    encoding: str = "UTF-8"
    raw_content: Optional[str] = None
    total_rows: int = 0

@dataclass
class GlobalHttpDefaultsInfo:
    """儲存全域 HTTP Request Defaults 的設定。"""
    protocol: str = "https"
    domain: str = ""
    port: str = ""
    path: str = ""
    encoding: str = "UTF-8"
    connect_timeout: str = ""
    response_timeout: str = ""

@dataclass
class GlobalHeaderInfo:
    """儲存單一全域 HTTP 標頭的鍵值對。"""
    name: str
    value: str

@dataclass
class GlobalRandomVariableInfo:
    """儲存 Random Variable Config 元件的參數。"""
    name: str
    variable_name: str
    output_format: str
    min_value: str
    max_value: str
    per_thread: bool = False

@dataclass
class AssertionInfo:
    """儲存 Response Assertion 的所有參數。"""
    name: str
    test_field: str = "Assertion.response_data"
    test_type: int = 2
    patterns: List[str] = field(default_factory=list)
    is_or: bool = False
    is_not: bool = False
    main_sample_only: bool = True
    enabled: bool = True
    assume_success: bool = True

@dataclass
class ListenerInfo:
    """
    儲存 View Results Tree 監聽器的所有詳細參數。
    """
    name: str
    filename: str
    log_errors_only: bool = False
    log_successes_only: bool = False

@dataclass
class JsonExtractorInfo:
    """儲存 JSON Extractor (JSON 後置處理器) 的參數。"""
    name: str
    reference_name: str
    json_path_expression: str
    match_number: str = "1"
    default_value: str = "NOT_FOUND"
    enabled: bool = True

@dataclass
class HttpRequestInfo:
    """儲存單一 HTTP Request Sampler 的所有相關資訊。"""
    name: str
    method: str = "POST"
    protocol: str = ""
    domain: str = ""
    port: str = ""
    path: str = ""
    encoding: str = "UTF-8"
    connect_timeout: str = ""
    response_timeout: str = ""
    json_body: Optional[str] = None
    source_json_filename: Optional[str] = None
    is_parameterized: bool = False
    assertions: List[AssertionInfo] = field(default_factory=list)

@dataclass
class ThreadGroupContext:
    """儲存單一執行緒群組 (Thread Group) 的完整上下文，包含其所有子元件。"""
    name: str
    num_threads_str: str = "${__P(threads,1)}"
    ramp_time_str: str = "${__P(rampUp,1)}"
    loops_str: str = "${__P(loop,-1)}"
    duration_str: str = "${__P(duration,60)}"
    on_sample_error: str = "continue"
    scheduler: bool = True
    http_requests: List[HttpRequestInfo] = field(default_factory=list)
    headers: List[GlobalHeaderInfo] = field(default_factory=list)
    random_variables: List[GlobalRandomVariableInfo] = field(default_factory=list)
    listeners: List[ListenerInfo] = field(default_factory=list)
    csv_data_sets: List[CsvInfo] = field(default_factory=list)

@dataclass
class GlobalSettings:
    """儲存測試計畫層級的全域設定。"""
    http_defaults: Optional[GlobalHttpDefaultsInfo] = None
    headers: List[GlobalHeaderInfo] = field(default_factory=list)
    random_variables: List[GlobalRandomVariableInfo] = field(default_factory=list)

@dataclass
class GenerationContext:
    """儲存生成 JMX 所需的完整上下文，是傳遞給組裝函式的頂層物件。"""
    test_plan_name: str
    thread_groups: List[ThreadGroupContext]
    requirements: str
    test_plan_teardown: bool = True
    global_settings: Optional[GlobalSettings] = None
    listeners: List[ListenerInfo] = field(default_factory=list)

class JMXGeneratorService:
    def __init__(self, llm_service: Optional[LLMService] = None, model_name: str = "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"):
        """
        初始化 JMXGeneratorService
        :param llm_service: 可選的 LLMService 實例，如果為 None 則會自動創建
        :param model_name: 要使用的模型名稱，預設為 "default"
        """
        self._llm_service = llm_service
        self._model_name = model_name
        self.logger = get_logger(__name__)

    @property
    def llm_service(self) -> LLMService:
        """
        一個延遲載入 (lazy-loading) 的屬性，確保 LLMService 只在需要時才被初始化。
        :return: LLMService 的實例。
        """
        if self._llm_service is None:
            self.logger.info(f"初始化 LLMService (Model: {self._model_name})")
            try:
                from main import get_llm_service
                self._llm_service = get_llm_service(self._model_name)
            except ImportError:
                self.logger.warning("無法從 main 模組導入 get_llm_service，使用默認 LLMService 初始化")
                self._llm_service = LLMService()
        return self._llm_service

    async def generate_jmx_with_retry(self, requirements: str, files_data: List[Dict] = None, max_retries: int = 3) -> str:
        """
        JMX 生成流程的總指揮。

        此函式協調整個流程，從理解使用者需求到最終生成並驗證 JMX 檔案。
        它包含了轉換、準備、驗證、組裝和最終驗證等核心步驟。
        :param requirements: 使用者輸入的自然語言需求。
        :param files_data: 一個包含已上傳檔案資訊的字典列表。
        :param max_retries: (目前未使用) 最大重試次數。
        :return: 一個包含最終 JMX 內容的字串。
        :raises RuntimeError: 如果 LLM 轉換步驟失敗。
        :raises ValueError: 如果輸入資料解析失敗、資料驗證失敗或最終 JMX 結構無效。
        """
        self.logger.info("=== 開始執行 JMX 生成流程 ===")

        # 步驟 1: 強制執行 LLM 轉換
        self.logger.info("啟動 LLM 轉換，將使用者輸入統一為標準化模板...")
        final_requirements_template: str
        try:
            final_requirements_template = await self.convert_requirements_to_template(requirements, files_data)
            self.logger.info("LLM 成功將輸入轉換為結構化模板。")
        except Exception as e:
            self.logger.error(f"LLM 轉換步驟失敗: {e}", exc_info=True)
            raise RuntimeError(f"無法將您的需求轉換為可處理的格式: {e}")

        # 步驟 2: 準備生成上下文
        try:
            context = self._prepare_generation_context(final_requirements_template, files_data)
            self.logger.info(f"生成上下文準備完成，測試計畫: '{context.test_plan_name}'")
        except ValueError as e:
            self.logger.error(f"輸入資料準備或解析失敗: {e}", exc_info=True)
            raise e

        # 步驟 3: 在組裝前，驗證資料完整性
        self.logger.info("開始執行 JMX 組裝前的資料完整性驗證...")
        for tg_context in context.thread_groups:
            for req_info in tg_context.http_requests:
                # 檢查條件：如果請求本身沒有 domain，且全域也沒有設定 domain
                has_global_domain = (
                    context.global_settings and
                    context.global_settings.http_defaults and
                    context.global_settings.http_defaults.domain
                )
                if not req_info.domain and not has_global_domain:
                    error_msg = f"請求 '{req_info.name}' 缺少必要的伺服器位址(domain)，且未設定全域預設值。"
                    self.logger.error(f"資料驗證失敗: {error_msg}")
                    raise ValueError(error_msg)
        self.logger.info("資料完整性驗證通過。")

        # 步驟 4: 使用驗證通過的 context 進行組裝
        try:
            self.logger.info("開始組裝 JMX...")
            jmx_content = self._assemble_jmx_from_structured_data(context)

            # 步驟 5: 驗證組裝後的 JMX
            is_valid, message = self.validate_xml(jmx_content)
            if not is_valid:
                self.logger.error(f"JMX 組裝後驗證失敗: {message}")
                raise ValueError(f"組裝後的 JMX 結構無效: {message}")

            self.logger.info("JMX 組裝與驗證成功！")
            return jmx_content

        except Exception as e:
            self.logger.error(f"JMX 組裝過程中發生嚴重錯誤: {e}", exc_info=True)
            raise Exception(f"無法生成有效的 JMX 檔案: {e}")

    def _prepare_generation_context(self, requirements: str, files_data: List[Dict]) -> GenerationContext:
        """
        準備生成 JMX 所需的完整上下文 (Context) 物件。

        此函式是資料準備階段的核心，它負責將「字串」和「原始檔案」轉換為結構化的 Python 物件。
        1. 呼叫 `_analyze_requirements_dynamically` 將 LLM 生成的模板字串解析成一個包含層級關係的字典。
        2. 呼叫 `_safe_process_files` 處理所有上傳的檔案（如 CSV、JSON）。
        3. 將解析後的字典和檔案內容，填充到預先定義好的一系列 `dataclass` 物件中。
        4. 處理關鍵邏輯，例如決定 CSV 變數名稱的優先級（優先使用模板定義，若無才用檔案標頭）。
        :param requirements: 結構化的需求模板字串。
        :param files_data: 一個包含已上傳檔案資訊的字典列表。
        :return: 一個包含所有生成所需資訊的 GenerationContext 物件。
        """
        self.logger.info("=== 步驟 1: 開始準備生成上下文 ===")
        processed_files = self._safe_process_files(files_data)
        req_analysis = self._analyze_requirements_dynamically(requirements)

        global_settings = GlobalSettings(
            http_defaults=GlobalHttpDefaultsInfo(**req_analysis.get('global_http_defaults', {})),
            headers=[GlobalHeaderInfo(**h) for h in req_analysis.get('global_headers', [])]
        )

        thread_group_contexts = []
        for tg_data in req_analysis.get('thread_groups', []):
            tg_params = tg_data.get('params', {})
            tg_context = ThreadGroupContext(
                name=tg_data.get('name'),
                num_threads_str=tg_params.get('threads', '${__P(threads,1)}'),
                ramp_time_str=tg_params.get('rampup', '${__P(rampUp,1)}'),
                loops_str=tg_params.get('loops', '${__P(loop,-1)}'),
                duration_str=tg_params.get('duration', '${__P(duration,60)}'),
                on_sample_error=tg_params.get('on_sample_error', 'continue'),
                scheduler=tg_params.get('use_scheduler', 'false').lower() == 'true',
                headers=[GlobalHeaderInfo(**h) for h in tg_data.get('headers', [])],
                random_variables=[GlobalRandomVariableInfo(**rv) for rv in tg_data.get('random_variables', [])],
                listeners=[ListenerInfo(**l) for l in tg_data.get('listeners', [])]
            )

            # 處理 CsvDataSet
            for csv_data in tg_data.get('csv_data_sets', []):
                csv_params = csv_data.get('params', {})
                csv_filename = csv_params.get('filename')
                if not csv_filename:
                    continue

                csv_info_dict = next(
                    (csv for csv in processed_files.get('csv_configs', []) if csv.get('filename') == csv_filename),
                    None)
                if not csv_info_dict:
                    self.logger.warning(f"模板中定義的 CSV 檔案 '{csv_filename}' 未上傳或處理失敗，已跳過。")
                    continue

                final_variable_names = []
                # 1. 優先嘗試從模板中獲取 variable_names
                template_vars_str = csv_params.get('variable_names', '').strip()
                if template_vars_str:
                    # 如果使用者在模板中明確指定了，則使用它們
                    final_variable_names = [name.strip() for name in template_vars_str.split(',') if name.strip()]
                    self.logger.info(f"偵測到模板指令：為 '{csv_filename}' 使用指定的變數: {final_variable_names}")
                else:
                    # 2. 如果模板中沒有，則回退使用從 CSV 檔案讀取的標頭
                    final_variable_names = csv_info_dict.get('variable_names', [])
                    self.logger.info(f"模板中未指定變數，為 '{csv_filename}' 回退使用檔案標頭: {final_variable_names}")

                sharing_mode_from_template = csv_params.get('sharing_mode', 'All threads').lower()
                sharing_mode_jmeter = 'shareMode.all'
                if 'group' in sharing_mode_from_template:
                    sharing_mode_jmeter = 'shareMode.group'
                elif 'thread' in sharing_mode_from_template and 'all' not in sharing_mode_from_template:
                    sharing_mode_jmeter = 'shareMode.thread'

                csv_info = CsvInfo(
                    name=csv_data.get('name', 'CSV Data Set Config'),
                    filename=csv_filename,
                    variable_names=final_variable_names,
                    ignoreFirstLine=csv_params.get('ignore_first_line', 'false').lower() == 'true',
                    recycle=csv_params.get('recycle_on_eof', 'true').lower() == 'true',
                    stopThread=csv_params.get('stop_thread_on_eof', 'false').lower() == 'true',
                    quotedData=csv_params.get('quoted_data', 'false').lower() == 'true',
                    delimiter=csv_params.get('delimiter', ','),
                    shareMode=sharing_mode_jmeter,
                    raw_content=csv_info_dict.get('raw_content', '')
                )
                tg_context.csv_data_sets.append(csv_info)

            # 處理 HTTP Requests (此部分邏輯不變)
            for req_data in tg_data.get('http_requests', []):
                req_params = req_data.get('params', {})
                http_req_info = HttpRequestInfo(
                    name=req_data.get('name'),
                    method=req_params.get('method', 'POST'),
                    protocol=req_params.get('protocol', ''),
                    domain=req_params.get('domain', ''),
                    port=req_params.get('port', ''),
                    path=req_params.get('path', ''),
                    connect_timeout=req_params.get('connect_timeout', ''),
                    response_timeout=req_params.get('response_timeout', ''),
                    assertions=[AssertionInfo(**a) for a in req_data.get('assertions', [])]
                )

                body_filename = req_params.get('body_file')
                if body_filename:
                    json_content_info = processed_files.get('json_contents', {}).get(body_filename)
                    if json_content_info:
                        http_req_info.source_json_filename = body_filename
                        if tg_context.csv_data_sets:
                            http_req_info.is_parameterized = True

                tg_context.http_requests.append(http_req_info)

            thread_group_contexts.append(tg_context)

        return GenerationContext(
            test_plan_name=req_analysis.get('test_plan', {}).get('name', 'Generated Test Plan'),
            test_plan_teardown=req_analysis.get('test_plan', {}).get('teardown', True),
            thread_groups=thread_group_contexts,
            global_settings=global_settings,
            listeners=[ListenerInfo(**l) for l in req_analysis.get('listeners', [])],
            requirements=requirements
        )

    def _safe_process_files(self, files_data: List[Dict] = None) -> Dict:
        """
        安全地處理所有上傳的檔案資料。

        作為一個總調度函式，它會分類處理傳入的檔案列表，分別調用
        `_process_csv_files` 和 `_process_json_files`，並將結果匯總成一個字典。
        :param files_data: 一個包含已上傳檔案資訊的字典列表。
        :return: 一個包含 'csv_configs' 和 'json_contents' 的字典。
        """
        try:
            if not files_data:
                self.logger.warning("沒有傳入任何檔案資料")
                return {"csv_configs": [], "json_contents": {}}

            self.logger.info(f"開始處理 {len(files_data)} 個檔案")

            # 從 _process_csv_files 獲取的是字典，key 是檔名
            csv_configs_dict = self._process_csv_files(files_data)
            json_contents = self._process_json_files(files_data)

            self.logger.info(f"JSON 處理結果: {list(json_contents.keys())}")

            # 將 CSV configs 字典轉換為列表格式，並確保包含 raw_content
            csv_configs_list = []
            for filename, config in csv_configs_dict.items():
                if config and 'error' not in config:
                    # 確保我們從 _safe_process_single_csv 返回的所有重要資訊都被包含
                    csv_configs_list.append({
                        'filename': filename,
                        'variable_names': config.get('headers', []),
                        'total_rows': config.get('total_rows', 0),
                        'filepath': config.get('filepath', filename),
                        'raw_content': config.get('raw_content', '')  # 確保 raw_content 被傳遞
                    })
                    self.logger.info(
                        f"為列表添加 CSV 設定: '{filename}', 變數: {config.get('headers', [])}, raw_content 長度: {len(config.get('raw_content', ''))}")
                else:
                    self.logger.warning(f"跳過有問題的 CSV 設定: {filename}")

            result = {"csv_configs": csv_configs_list, "json_contents": json_contents}
            self.logger.info(f"檔案處理完成 - CSV: {len(csv_configs_list)}, JSON: {len(json_contents)}")
            return result

        except Exception as e:
            self.logger.error(f"檔案處理失敗: {e}", exc_info=True)
            return {"csv_configs": [], "json_contents": {}}

    def _analyze_requirements_dynamically(self, requirements: str) -> dict:
        """
        動態解析結構化的需求模板字串，並建立元件之間的層級關係。

        這是自訂模板格式的專用解析器。它使用正規表示式讀取 `[元件類型:元件名稱]` 格式的模板，
        並根據 `parent` 屬性將子元件正確地放入父元件的列表中，從而建立起整個測試計畫的樹狀結構。
        它還包含處理特殊情況的邏輯，例如將「作用於整個執行緒群組的斷言」暫存起來，並在最後分發給該群組下的所有請求。
        :param requirements: 結構化的需求模板字串。
        :return: 一個代表整個測試計畫結構的巢狀字典。
        """
        self.logger.info("================== 開始執行解析器 ==================")
        is_structured_format = re.search(r"^\s*\[[a-zA-Z]+:.+?\]", requirements, re.MULTILINE)

        if not is_structured_format:
            self.logger.warning("未偵測到結構化模板格式，退回。")
            return {'test_plan': {}, 'global_http_defaults': {}, 'global_headers': [], 'thread_groups': [],
                    'listeners': []}

        self.logger.info("偵測到結構化模板格式，啟用解析器。")
        analysis = {'test_plan': {}, 'global_http_defaults': {}, 'global_headers': [], 'thread_groups': [],
                    'listeners': []}

        # --- 第一階段：將模板字串解析為一個扁平的元件列表 ---
        all_components = []
        # 使用正規表示式尋找所有 [Component: Name] 區塊
        for match in re.finditer(r"\[([a-zA-Z]+):\s*(.+?)\]\n([\s\S]+?)(?=\n\[|\Z)", requirements, re.MULTILINE):
            comp_type, comp_name, comp_body = match.groups()
            component = {'type': comp_type.strip(), 'name': comp_name.strip(), 'params': {}}

            # 解析每個區塊內的 key = value 參數
            for param_match in re.finditer(r"^\s*([^#\s=]+?)\s*=\s*(.+?)\s*$", comp_body, re.MULTILINE):
                key, value = param_match.groups()
                component['params'][key.strip()] = value.strip().strip('\'"')

            # 為容器類型的元件預先初始化子列表，方便後續附加
            if component['type'] == 'ThreadGroup':
                component.setdefault('http_requests', [])
                component.setdefault('headers', [])
                component.setdefault('random_variables', [])
                component.setdefault('listeners', [])
                component.setdefault('csv_data_sets', [])
                component.setdefault('tg_level_assertions', [])  # 用於暫存執行緒群組層級的斷言
            elif component['type'] == 'HttpRequest':
                component.setdefault('assertions', [])
            all_components.append(component)

        # 建立一個以元件名稱為鍵的字典，方便快速查找父元件
        component_map = {}
        for comp in all_components:
            name = comp['name']
            if name not in component_map:
                component_map[name] = []
            component_map[name].append(comp)

        # --- 第二階段：遍歷扁平列表，建立元件之間的層級關係 ---
        test_plan_comp = next((c for c in all_components if c['type'] == 'TestPlan'), None)
        if not test_plan_comp:
            raise ValueError("模板中未找到 [TestPlan: ...] 元件。")

        analysis['test_plan'] = {
            'name': test_plan_comp['name'],
            'teardown': test_plan_comp['params'].get('tearDown_on_shutdown', 'true').lower() == 'true'
        }

        # 將所有元件分類並附加到其父層
        for comp in all_components:
            if comp['type'] == 'TestPlan':
                continue  # TestPlan 是根節點，跳過

            # 根據 'parent' 屬性尋找父元件
            parent_name = comp.get('params', {}).get('parent')
            if not parent_name:
                self.logger.warning(f"元件 '{comp['name']}' 缺少 'parent' 屬性，已跳過。")
                continue

            parent_candidates = component_map.get(parent_name)
            if not parent_candidates:
                self.logger.warning(f"元件 '{comp['name']}' 找不到父層 '{parent_name}'，已跳過。")
                continue

            # 確定唯一的父元件實體
            parent_comp = next((p for p in parent_candidates if p['type'] in ['TestPlan', 'ThreadGroup']), None)
            if not parent_comp:
                parent_comp = next((p for p in parent_candidates if p['type'] == 'HttpRequest'), None)

            if not parent_comp:
                self.logger.warning(
                    f"元件 '{comp['name']}' 雖然找到了名為 '{parent_name}' 的候選父元件，但它們的類型不適合做為父層，已跳過。")
                continue

            comp_type, parent_type = comp['type'], parent_comp['type']

            # 根據父元件的類型，將當前元件放入對應的子列表中
            if parent_type == 'TestPlan':
                if comp_type == 'ThreadGroup':
                    analysis['thread_groups'].append(comp)
                elif comp_type == 'GlobalHttpRequestDefaults':
                    http_defaults_params = comp['params'].copy()
                    http_defaults_params.pop('parent', None)  # 移除 parent 屬性，避免後續 dataclass 初始化錯誤
                    analysis['global_http_defaults'] = http_defaults_params
                elif comp_type in ['HttpHeaderManager', 'GlobalHttpHeaderManager']:
                    headers = [{'name': k.split('.', 1)[1], 'value': v} for k, v in comp['params'].items() if
                               k.startswith('header.')]
                    analysis['global_headers'].extend(headers)
                elif comp_type == 'Listener':
                    listener_params = {
                        'name': comp['name'],
                        'filename': comp['params'].get('filename', ''),
                        'log_errors_only': comp['params'].get('log_errors_only', 'false').lower() == 'true',
                        'log_successes_only': comp['params'].get('log_successes_only', 'false').lower() == 'true'
                    }
                    analysis['listeners'].append(listener_params)

            elif parent_type == 'ThreadGroup':
                if comp_type == 'HttpRequest':
                    parent_comp['http_requests'].append(comp)
                elif comp_type == 'CsvDataSet':
                    parent_comp['csv_data_sets'].append(comp)
                elif comp_type == 'HttpHeaderManager':
                    headers = [{'name': k.split('.', 1)[1], 'value': v} for k, v in comp['params'].items() if
                               k.startswith('header.')]
                    parent_comp['headers'].extend(headers)
                elif comp_type == 'RandomVariableConfig':
                    parent_comp['random_variables'].append(comp['params'])
                elif comp_type == 'Listener':
                    listener_params = {
                        'name': comp['name'],
                        'filename': comp['params'].get('filename', ''),
                        'log_errors_only': comp['params'].get('log_errors_only', 'false').lower() == 'true',
                        'log_successes_only': comp['params'].get('log_successes_only', 'false').lower() == 'true'
                    }
                    parent_comp['listeners'].append(listener_params)
                elif comp_type == 'ResponseAssertion':
                    # 處理執行緒群組層級的斷言：先暫存
                    self.logger.info(f"發現一個執行緒群組層級的斷言 '{comp['name']}'，將其暫存。")
                    rule = comp['params'].get('pattern_matching_rule', 'Contains')
                    patterns = [v for k, v in comp['params'].items() if k.startswith('pattern_')]
                    possible_rules = {'contains', 'matches', 'equals', 'substring', 'not', 'or'}
                    filtered_patterns = [p for p in patterns if p.lower() not in possible_rules]
                    rule_lower = rule.lower()
                    test_type = 2
                    if 'matches' in rule_lower:
                        test_type = 1
                    elif 'equals' in rule_lower:
                        test_type = 8
                    parent_comp['tg_level_assertions'].append({
                        'name': comp['name'], 'test_type': test_type, 'patterns': filtered_patterns,
                        'is_or': comp['params'].get('use_or_logic', 'false').lower() == 'true',
                        'is_not': 'not' in rule_lower,
                        'assume_success': comp['params'].get('assume_success', 'false').lower() == 'true'
                    })

            elif parent_type == 'HttpRequest':
                # 處理請求層級的斷言
                if comp_type == 'ResponseAssertion':
                    rule = comp['params'].get('pattern_matching_rule', 'Contains')
                    patterns = [v for k, v in comp['params'].items() if k.startswith('pattern_')]
                    possible_rules = {'contains', 'matches', 'equals', 'substring', 'not', 'or'}
                    filtered_patterns = [p for p in patterns if p.lower() not in possible_rules]
                    rule_lower = rule.lower()
                    test_type = 2
                    if 'matches' in rule_lower:
                        test_type = 1
                    elif 'equals' in rule_lower:
                        test_type = 8
                    parent_comp['assertions'].append({
                        'name': comp['name'], 'test_type': test_type, 'patterns': filtered_patterns,
                        'is_or': comp['params'].get('use_or_logic', 'false').lower() == 'true',
                        'is_not': 'not' in rule_lower,
                        'assume_success': comp['params'].get('assume_success', 'false').lower() == 'true'
                    })

        # --- 第三階段：後處理，分發暫存的執行緒群組層級斷言 ---
        self.logger.info("正在分發執行緒群組層級的斷言...")
        for tg_comp in analysis['thread_groups']:
            if tg_comp.get('tg_level_assertions'):
                assertions_to_add = tg_comp['tg_level_assertions']
                if assertions_to_add and tg_comp['http_requests']:
                    self.logger.info(
                        f"在 ThreadGroup '{tg_comp['name']}' 中找到 {len(assertions_to_add)} 個全域斷言，準備附加到 {len(tg_comp['http_requests'])} 個請求中。")
                    for http_request in tg_comp['http_requests']:
                        for assertion in assertions_to_add:
                            # 使用 .copy() 確保每個請求獲得的是獨立的斷言字典副本
                            http_request['assertions'].append(assertion.copy())
                elif assertions_to_add:
                    self.logger.warning(
                        f"ThreadGroup '{tg_comp['name']}' 有 {len(assertions_to_add)} 個全域斷言，但其下沒有任何 HTTP 請求可附加。")

        self.logger.info("================== 需求解析器執行完畢 ==================")
        self.logger.debug(f"最終解析結果: {json.dumps(analysis, indent=2, ensure_ascii=False)}")
        return analysis

    def _process_csv_files(self, files_data: List[Dict]) -> Dict[str, Dict]:
        """
        處理所有上傳的 CSV 檔案。

        此函式迭代所有傳入的檔案資料，篩選出 CSV 檔案，
        並呼叫 `_safe_process_single_csv` 進行單一檔案的解析。
        :param files_data: 一個檔案字典的列表。
        :return: 一個以檔名為鍵，檔案詳細資訊為值的字典。
        """
        csv_configs = {}
        if not files_data:
            self.logger.warning("沒有傳入任何檔案資料，無法處理 CSV 檔案。")
            return csv_configs

        self.logger.info(f"開始處理 {len(files_data)} 個檔案中的 CSV 檔案...")
        for file_info in files_data:
            try:
                # 兼容不同前端傳入的檔名 key
                filename = file_info.get('filename', file_info.get('name', ''))
                if not filename or not filename.lower().endswith('.csv'):
                    continue

                self.logger.info(f"發現 CSV 檔案: '{filename}'，進行解析...")
                config = self._safe_process_single_csv(file_info)
                if config:
                    # 使用檔名作為 key，方便後續快速查找
                    csv_configs[filename] = config
                else:
                    self.logger.warning(f"檔案 '{filename}' 解析失敗或為空，已跳過。")

            except Exception as e:
                # 捕獲迴圈中的意外錯誤，確保一個檔案的失敗不會影響其他檔案
                filename_for_log = file_info.get('filename', '未知檔案')
                self.logger.error(f"處理檔案 '{filename_for_log}' 時發生未預期錯誤: {e}", exc_info=True)

        self.logger.info(f"CSV 檔案處理完成，共成功解析 {len(csv_configs)} 個檔案。")
        return csv_configs

    def _safe_process_single_csv(self, file_info: Dict) -> Optional[Dict]:
        """
        安全且健壯地處理單一 CSV 檔案的內容。

        此函式使用標準的 `io` 和 `csv` 模組，將檔案內容字串轉換為
        結構化的資訊，包括標頭、資料行數和原始內容。
        :param file_info: 代表單一檔案的字典，應包含檔名和內容。
        :return: 一個包含 CSV 詳細資訊的字典，如果處理失敗則返回 None。
        """
        filename = file_info.get('filename', file_info.get('name', 'unknown.csv'))

        try:
            # 從多個可能的 key 中獲取檔案內容字串
            content_str = ''
            if 'content' in file_info and isinstance(file_info['content'], str):
                content_str = file_info['content']
            elif 'data' in file_info and isinstance(file_info['data'], str):
                content_str = file_info['data']

            if not content_str or not content_str.strip():
                self.logger.warning(f"CSV 檔案 '{filename}' 內容為空。")
                return None

            # 使用 io.StringIO 將字串內容模擬成一個檔案，以便 csv 模組可以讀取
            file_stream = io.StringIO(content_str)

            # 使用 csv.reader 進行解析，這是處理 CSV 的標準做法
            csv_reader = csv.reader(file_stream)

            # 讀取第一行作為標頭
            try:
                headers = next(csv_reader)
                # 清理標頭，去除前後空格和空字串
                cleaned_headers = [h.strip() for h in headers if h and h.strip()]
            except StopIteration:
                # 檔案為空，沒有任何行
                self.logger.warning(f"CSV 檔案 '{filename}' 為空，無法讀取標頭。")
                return {
                    'headers': [], 'sample_data': [], 'total_rows': 0,
                    'filepath': filename, 'raw_content': content_str
                }

            # 讀取剩餘的所有資料行
            data_rows = list(csv_reader)
            total_data_rows = len(data_rows)

            # 提取最多 5 行作為樣本資料
            sample_data = data_rows[:5]

            self.logger.info(
                f"CSV 解析成功: '{filename}' -> 標頭: {cleaned_headers}, 資料行數: {total_data_rows}"
            )

            return {
                'headers': cleaned_headers,
                'sample_data': sample_data,
                'total_rows': total_data_rows,
                'filepath': filename,
                'raw_content': content_str  # 包含原始內容，用於後續邏輯判斷
            }

        except csv.Error as e:
            self.logger.error(f"解析 CSV 檔案 '{filename}' 時發生格式錯誤: {e}")
            return None
        except Exception as e:
            self.logger.error(f"處理單一 CSV 檔案 '{filename}' 時發生未預期錯誤: {e}", exc_info=True)
            return None

    def _process_json_files(self, files_data: List[Dict]) -> Dict:
        """
        處理所有上傳的 JSON 檔案。

        此函式迭代所有傳入的檔案資料，篩選出 JSON 檔案，
        並呼叫 `_safe_process_single_json` 進行單一檔案的解析。
        :param files_data: 一個檔案字典的列表。
        :return: 一個以檔名為鍵，檔案詳細資訊為值的字典。
        """
        json_contents = {}

        if not files_data:
            return json_contents

        for file_info in files_data:
            try:
                filename = file_info.get('filename', file_info.get('name', ''))
                if not filename or not filename.lower().endswith('.json'):
                    continue

                content = self._safe_process_single_json(file_info)
                if content:
                    json_contents[filename] = content

            except Exception as e:
                self.logger.error(f"處理 JSON 檔案 {filename} 失敗: {e}")
                json_contents[filename] = {'error': str(e), 'raw_content': '', 'variables': []}

        return json_contents

    def _safe_process_single_json(self, file_info: Dict) -> Optional[Dict]:
        """
        安全地處理單一 JSON 檔案。

        :param file_info: 代表單一檔案的字典，應包含檔名和內容。
        :return: 一個包含 JSON 詳細資訊的字典，如果處理失敗則返回 None。
        """
        try:
            self.logger.info(f"處理 JSON 檔案: {file_info.get('filename', file_info.get('name', '未知'))}")
            self.logger.info(f"檔案資訊鍵值: {list(file_info.keys())}")

            content = ''

            # 🎯 多重策略獲取內容
            strategies = [
                ('content', lambda x: x.get('content')),
                ('data', lambda x: self._extract_data_content(x.get('data'))),
                ('body', lambda x: x.get('body')),
                ('text', lambda x: x.get('text')),
                ('raw_content', lambda x: x.get('raw_content')),
            ]

            for strategy_name, extractor in strategies:
                try:
                    extracted = extractor(file_info)
                    if extracted:
                        content = str(extracted) if not isinstance(extracted, str) else extracted
                        self.logger.info(f"使用策略 '{strategy_name}' 成功獲取內容，長度: {len(content)}")
                        break
                except Exception as e:
                    self.logger.warning(f"策略 '{strategy_name}' 失敗: {e}")

            if not content:
                self.logger.error(f"所有內容提取策略都失敗，檔案資訊: {file_info}")
                return None

            # 標準化換行符，將所有 \r\n 和 \r 替換為 \n
            content = content.replace('\r\n', '\n').replace('\r', '\n')

            # 🎯 確保是有效的JSON格式
            self.logger.info(f"原始內容前100字符: {content[:100]}")

            # 嘗試解析 JSON
            parsed_json = None
            try:
                parsed_json = json.loads(content)
                self.logger.info(f"JSON 解析成功")
            except json.JSONDecodeError as e:
                self.logger.warning(f"JSON 解析失敗，保留原始內容: {e}")
                # 如果不是有效JSON，仍然保留原始內容

            # 清理和提取變數
            cleaned_json = self._clean_json_values(parsed_json) if parsed_json else None
            variables = self._extract_json_variables(cleaned_json) if cleaned_json else []

            result = {
                'raw_content': content,
                'parsed': cleaned_json,
                'variables': variables
            }

            self.logger.info(f"JSON 處理成功！raw_content 長度: {len(content)}, 變數數量: {len(variables)}")
            return result

        except Exception as e:
            self.logger.error(f"處理 JSON 檔案失敗: {e}", exc_info=True)
            return None

    def _extract_data_content(self, data):
        """
        一個輔助函式，用於從 file_info 字典中的 'data' 鍵提取內容。

        它能處理 'data' 鍵的值是字典、字串或其他類型的情況，並統一返回字串。
        :param data: 'data' 鍵對應的值。
        :return: 內容字串或 None。
        """
        if not data:
            return None

        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False, indent=2)
        elif isinstance(data, str):
            return data
        else:
            return str(data)

    def _clean_json_values(self, obj):
        """
        遞迴地清理一個 Python 物件（通常來自解析後的 JSON）中的無效值。

        主要用於將 `float` 類型的 `NaN` 或 `Infinity` 值轉換為 `None`，以避免後續 JSON 序列化失敗。
        :param obj: 要清理的 Python 物件 (字典、列表等)。
        :return: 清理後的物件。
        """
        if isinstance(obj, dict):
            return {key: self._clean_json_values(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_json_values(item) for item in obj]
        elif isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        else:
            return obj

    def _extract_json_variables(self, json_obj) -> List[str]:
        """
        遞迴地從一個 Python 物件中提取所有 JMeter 風格的變數名稱。

        它會尋找所有形如 `${...}` 的字串值，並將括號內的變數名收集到一個列表中。
        :param json_obj: 解析後的 JSON 物件 (字典或列表)。
        :return: 一個包含所有變數名的列表。
        """
        if json_obj is None:
            return []

        variables = []

        def extract_vars(obj):
            try:
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                            var_name = value[2:-1]
                            if var_name and var_name not in variables:
                                variables.append(var_name)
                        elif isinstance(value, (dict, list)):
                            extract_vars(value)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_vars(item)
            except Exception as e:
                self.logger.warning(f"提取變數時發生錯誤: {e}")

        extract_vars(json_obj)
        return variables

    def validate_xml(self, xml_content: str) -> Tuple[bool, str]:
        """
        一個品質保證函式，用於驗證最終生成的 JMX 字串是否為有效的 XML。

        在將最終的 JMX 內容返回給使用者之前，它會使用 Python 的 XML 解析器嘗試解析一次。
        如果解析成功，代表 XML 格式正確；如果失敗，則能提前捕獲錯誤。
        :param xml_content: 要驗證的 XML 字串。
        :return: 一個元組 (布林值, 訊息)，布林值表示是否有效，訊息為驗證結果。
        """
        try:
            if not xml_content or not xml_content.strip():
                return False, "XML content is empty or whitespace."

            content = xml_content.strip()
            if not content.startswith('<?xml'):
                return False, "Validation failed: Missing XML declaration '<?xml ...?>'."
            if not content.endswith('</jmeterTestPlan>'):
                return False, "Validation failed: Content does not end with '</jmeterTestPlan>'."

            open_tags = content.count('<hashTree>')
            close_tags = content.count('</hashTree>')
            if open_tags != close_tags:
                return False, f"Validation failed: Mismatched <hashTree> tags (open: {open_tags}, close: {close_tags})."

            ET.fromstring(content)
            self.logger.info("XML 結構驗證通過。")
            return True, "XML validation successful."

        except ET.ParseError as e:
            error_line = str(e).split(',')[1].strip() if ',' in str(e) else str(e)
            self.logger.error(f"XML 驗證失敗: 語法解析錯誤 -> {error_line}", exc_info=True)
            return False, f"XML ParseError: The generated XML is not well-formed. Details: {error_line}"
        except Exception as e:
            self.logger.error(f"XML 驗證過程中發生未預期的錯誤: {e}", exc_info=True)
            return False, f"An unexpected error occurred during XML validation: {str(e)}"

    def _parameterize_json_body(self, json_body: str, csv_info: CsvInfo) -> str:
        """
        智慧地將 JSON Body 內容參數化。

        當一個 HTTP 請求需要使用 CSV 檔案進行參數化時，此函式會被呼叫。
        它採用雙重策略：
        1. **鍵匹配**：如果 JSON 的鍵名與 CSV 的變數名匹配，直接將其值替換為 `${變數名}`。
        2. **值匹配**：如果鍵不匹配，則嘗試將 JSON 的值與 CSV 第一行資料的值進行匹配，如果匹配成功，則替換為對應的 `${變數名}`。
        :param json_body: 原始的 JSON Body 字串。
        :param csv_info: 包含 CSV 變數和內容的 CsvInfo 物件。
        :return: 參數化後的 JSON Body 字串。
        """
        self.logger.info(f"開始使用【智慧型雙重策略】參數化 JSON，來源 CSV: '{csv_info.filename}'")

        if not json_body or not csv_info.raw_content or not csv_info.variable_names:
            self.logger.warning("JSON Body 或 CSV 內容/變數為空，跳過參數化。")
            return json_body or ""

        try:
            # 步驟 1: 解析 JSON 字串為 Python 物件
            data_obj = json.loads(json_body)

            # 步驟 2: 準備兩種替換策略所需的資料
            # 策略一：建立一個高效的 CSV 變數名集合 (用於鍵匹配)
            variable_set = set(csv_info.variable_names)

            # 策略二：從 CSV 讀取第一行資料，建立 "值 -> ${變數}" 的對應字典 (用於值匹配)
            file_stream = io.StringIO(csv_info.raw_content)
            csv_reader = csv.reader(file_stream)
            next(csv_reader, None)  # 跳過標頭
            first_data_row = next(csv_reader, None)

            value_to_placeholder_map = {}
            if first_data_row:
                value_to_placeholder_map = {
                    value.strip(): f"${{{variable}}}"
                    for variable, value in zip(csv_info.variable_names, first_data_row)
                    if value and value.strip()
                }
                self.logger.info(f"建立的「值」替換對應表: {value_to_placeholder_map}")
            else:
                self.logger.warning(f"CSV '{csv_info.filename}' 中沒有資料行，無法使用「值匹配」策略。")

            # 使用一個 list 來追蹤被替換的鍵和原因
            replacements_made = []

            # 步驟 3: 定義一個遞迴函式來走訪並執行雙重替換策略
            def recursive_replace(obj):
                if isinstance(obj, dict):
                    # 使用 list(obj.keys()) 來避免在迭代期間修改字典的問題
                    for key in list(obj.keys()):
                        value = obj[key]

                        # --- 策略一：優先進行「鍵」匹配 ---
                        if key in variable_set:
                            placeholder = f"${{{key}}}"
                            if obj[key] != placeholder:
                                obj[key] = placeholder
                                replacements_made.append(f"'{key}' (鍵匹配)")
                            # 鍵匹配成功後，跳過對該鍵值的後續處理
                            continue

                        # --- 策略二：如果鍵不匹配，則嘗試「值」匹配 ---
                        str_value = str(value)
                        if str_value in value_to_placeholder_map:
                            placeholder = value_to_placeholder_map[str_value]
                            if obj[key] != placeholder:
                                obj[key] = placeholder
                                replacements_made.append(f"'{key}' (值匹配)")
                            # 值匹配成功後，也跳過遞迴
                            continue

                        # --- 如果都沒有匹配，則遞迴深入 ---
                        if isinstance(value, (dict, list)):
                            recursive_replace(value)

                elif isinstance(obj, list):
                    for item in obj:
                        recursive_replace(item)

            # 執行遞迴替換
            recursive_replace(data_obj)

            if replacements_made:
                # 使用 set 去除重複項，然後再轉回 list
                unique_replacements = sorted(list(set(replacements_made)))
                self.logger.info(f"JSON Body 參數化成功！已替換的欄位: {unique_replacements}")
            else:
                self.logger.warning(
                    "JSON Body 內容未發生變化。請檢查 JSON 的鍵名或值是否能對應到 CSV 的變數。")

            # 步驟 4: 將修改後的 Python 物件序列化回格式化的 JSON 字串
            parameterized_body = json.dumps(data_obj, indent=4, ensure_ascii=False)
            self.logger.debug(f"參數化後的 Body: \n{parameterized_body}")
            return parameterized_body

        except json.JSONDecodeError:
            self.logger.error(f"JSON 解析失敗！請檢查 JSON 檔案格式。Body: \n{json_body[:500]}...")
            return json_body
        except Exception as e:
            self.logger.error(f"參數化過程中發生未預期的錯誤: {e}", exc_info=True)
            return json_body

    def _create_test_plan(self, context: GenerationContext):
        """
        建立 JMX 檔案的根節點 `<TestPlan>` 及其對應的 `<hashTree>`。
        :param context: 包含測試計畫名稱等資訊的 GenerationContext 物件。
        :return: 一個包含 TestPlan XML 元素和其 hashTree 的元組。
        """
        test_plan_element = E.TestPlan(
            guiclass="TestPlanGui",
            testclass="TestPlan",
            testname=context.test_plan_name,
            enabled="true"
        )
        test_plan_element.append(E.stringProp({"name": "TestPlan.comments"}, ""))
        test_plan_element.append(E.boolProp({"name": "TestPlan.functional_mode"}, "false"))
        test_plan_element.append(E.boolProp({"name": "TestPlan.tearDown_on_shutdown"}, "true"))
        test_plan_element.append(E.boolProp({"name": "TestPlan.serialize_threadgroups"}, "false"))

        # 建立空的用戶自訂變數區塊
        user_defined_variables = E.elementProp(
            {"name": "TestPlan.user_defined_variables", "elementType": "Arguments"},
            E.collectionProp({"name": "Arguments.arguments"})
        )
        user_defined_variables.set("guiclass", "ArgumentsPanel")
        user_defined_variables.set("testclass", "Arguments")
        user_defined_variables.set("testname", "User Defined Variables")
        user_defined_variables.set("enabled", "true")
        test_plan_element.append(user_defined_variables)

        # 2. 建立一個空的 hashTree
        test_plan_hash_tree = E.hashTree()

        # 3. 將兩者作為元組返回
        return test_plan_element, test_plan_hash_tree

    def _create_http_request_defaults(self, defaults: GlobalHttpDefaultsInfo) -> tuple:
        """
        建立 `<ConfigTestElement>` 元件，即 "HTTP Request Defaults"。
        :param defaults: 包含協定、網域等全域預設值的 GlobalHttpDefaultsInfo 物件。
        :return: 一個包含 ConfigTestElement XML 元素和其 hashTree 的元組。
        """
        element = E.ConfigTestElement(
            E.elementProp(E.collectionProp(name="Arguments.arguments"), name="HTTPsampler.Arguments",
                             elementType="Arguments", guiclass="HTTPArgumentsPanel", testclass="Arguments",
                             enabled="true"),
            E.stringProp(defaults.path, name="HTTPSampler.path"),
            E.stringProp(defaults.domain, name="HTTPSampler.domain"),
            E.stringProp(defaults.protocol, name="HTTPSampler.protocol"),
            E.stringProp(defaults.port, name="HTTPSampler.port"),
            E.stringProp(defaults.connect_timeout, name="HTTPSampler.connect_timeout"),
            E.stringProp(defaults.response_timeout, name="HTTPSampler.response_timeout"),
            guiclass="HttpDefaultsGui",
            testclass="ConfigTestElement",
            testname="HTTP Request Defaults",
            enabled="true"
        )
        return element, E.hashTree()

    def _create_http_header_manager(self, headers: List[GlobalHeaderInfo], name: str = "HTTP Header Manager") -> tuple:
        """
        根據提供的標頭列表，建立一個 `<HeaderManager>` 元件。
        :param headers: 一個包含多個 GlobalHeaderInfo 物件的列表。
        :param name: 此標頭管理器的名稱。
        :return: 一個包含 HeaderManager XML 元素和其 hashTree 的元組。
        """
        header_elements = []
        for header in headers:
            header_elements.append(
                E.elementProp(
                    E.stringProp(header.name, name="Header.name"),
                    E.stringProp(header.value, name="Header.value"),
                    name="", elementType="Header"
                )
            )

        element = E.HeaderManager(
            E.collectionProp(*header_elements, name="HeaderManager.headers"),
            guiclass="HeaderPanel",
            testclass="HeaderManager",
            testname= name,
            enabled="true"
        )
        return element, E.hashTree()

    def _create_random_variable_config(self, var_config: GlobalRandomVariableInfo) -> tuple:
        """
        建立 `<RandomVariableConfig>` 元件。
        :param var_config: 包含隨機變數詳細設定的 GlobalRandomVariableInfo 物件。
        :return: 一個包含 RandomVariableConfig XML 元素和其 hashTree 的元組。
        """
        element = E.RandomVariableConfig(
            E.stringProp(var_config.variable_name, name="variableName"),
            E.stringProp(var_config.output_format, name="outputFormat"),
            E.stringProp(var_config.min_value, name="minimumValue"),
            E.stringProp(var_config.max_value, name="maximumValue"),
            E.stringProp("", name="randomSeed"),
            E.boolProp(str(var_config.per_thread).lower(), name="perThread"),
            guiclass="TestBeanGUI",
            testclass="RandomVariableConfig",
            testname=var_config.name,
            enabled="true"
        )
        return element, E.hashTree()

    def _create_thread_group(self, tg_context: ThreadGroupContext) -> tuple:
        """
        根據 `ThreadGroupContext` 物件建立 `<ThreadGroup>` 元件。

        它會將 context 中的所有參數（如執行緒數、Ramp-Up 時間等）對應到
        正確的 XML 屬性上。
        :param tg_context: 包含執行緒群組所有設定的 ThreadGroupContext 物件。
        :return: 一個包含 ThreadGroup XML 元素和其 hashTree 的元組。
        """
        # 1. 建立 ThreadGroup 元件本身
        thread_group_element = E.ThreadGroup(
            guiclass="ThreadGroupGui",
            testclass="ThreadGroup",
            testname=tg_context.name,
            enabled="true"
        )
        # 使用 context 中的 on_sample_error
        thread_group_element.append(E.stringProp(tg_context.on_sample_error, name="ThreadGroup.on_sample_error"))

        loop_controller = E.elementProp(
            E.stringProp(tg_context.loops_str, name="LoopController.loops"),
            E.boolProp("false", name="LoopController.continue_forever"),
            name="ThreadGroup.main_controller", elementType="LoopController",
            guiclass="LoopControlPanel", testclass="LoopController",
            testname="Loop Controller", enabled="true"
        )
        thread_group_element.append(loop_controller)

        # 使用正確的屬性名稱
        thread_group_element.append(E.stringProp(tg_context.num_threads_str, name="ThreadGroup.num_threads"))
        thread_group_element.append(E.stringProp(tg_context.ramp_time_str, name="ThreadGroup.ramp_time"))
        thread_group_element.append(E.boolProp(str(tg_context.scheduler).lower(), name="ThreadGroup.scheduler"))
        thread_group_element.append(E.stringProp(tg_context.duration_str, name="ThreadGroup.duration"))
        thread_group_element.append(E.stringProp("", name="ThreadGroup.delay"))
        thread_group_element.append(E.boolProp("true", name="ThreadGroup.same_user_on_next_iteration"))

        # 2. 建立一個空的 hashTree
        thread_group_hash_tree = E.hashTree()

        # 3. 將兩者作為元組返回
        return thread_group_element, thread_group_hash_tree

    def _create_csv_data_set_config(self, csv_info: CsvInfo, name: str = "CSV Data Set Config") -> tuple:
        """
        根據 `CsvInfo` 物件建立 `<CSVDataSet>` 元件。

        它會將 CsvInfo 中的所有詳細參數（如檔名、分隔符、分享模式等）應用到 XML 元件中。
        :param csv_info: 包含 CSV 檔案所有設定的 CsvInfo 物件。
        :param name: 此 CSV Data Set Config 的名稱。
        :return: 一個包含 CSVDataSet XML 元素和其 hashTree 的元組。
        """
        element = E.CSVDataSet(
            E.stringProp(csv_info.delimiter, name="delimiter"),
            E.stringProp(csv_info.encoding, name="fileEncoding"),
            E.stringProp(csv_info.filename, name="filename"),
            E.boolProp(str(csv_info.ignoreFirstLine).lower(), name="ignoreFirstLine"),
            E.boolProp(str(csv_info.quotedData).lower(), name="quotedData"),
            E.boolProp(str(csv_info.recycle).lower(), name="recycle"),
            E.stringProp(csv_info.shareMode, name="shareMode"),
            E.boolProp(str(csv_info.stopThread).lower(), name="stopThread"),
            E.stringProp(','.join(csv_info.variable_names), name="variableNames"),
            guiclass="TestBeanGUI",
            testclass="CSVDataSet",
            testname= name,
            enabled="true"
        )
        return element, E.hashTree()

    def _create_http_sampler_proxy(self, req_info: HttpRequestInfo, json_body: str) -> tuple:
        """
        建立 `<HTTPSamplerProxy>` 元件，即 "HTTP Request Sampler"。

        它會將請求的所有資訊（方法、路徑、Body 等）組裝成一個完整的 HTTP 取樣器，
        並支援連線和回應超時的設定。
        :param req_info: 包含 HTTP 請求所有設定的 HttpRequestInfo 物件。
        :param json_body: 經過處理（可能已參數化）的請求 Body 字串。
        :return: 一個包含 HTTPSamplerProxy XML 元素和其 hashTree 的元組。
        """
        escaped_body = saxutils.escape(json_body) if json_body else ""
        children = [
            E.boolProp("true", name="HTTPSampler.postBodyRaw"),
            E.elementProp(
                E.collectionProp(
                    E.elementProp(
                        E.boolProp("false", name="HTTPArgument.always_encode"),
                        E.stringProp(escaped_body, name="Argument.value"),
                        E.stringProp("=", name="Argument.metadata"),
                        name="", elementType="HTTPArgument"
                    ), name="Arguments.arguments"
                ), name="HTTPsampler.Arguments", elementType="Arguments"
            ),
            E.stringProp(req_info.method, name="HTTPSampler.method"),
            E.boolProp("true", name="HTTPSampler.follow_redirects"),
            E.boolProp("false", name="HTTPSampler.auto_redirects"),
            E.boolProp("true", name="HTTPSampler.use_keepalive"),
            E.boolProp("false", name="HTTPSampler.DO_MULTIPART_POST"),
            E.stringProp("6", name="HTTPSampler.concurrentPool"),
            E.stringProp(req_info.encoding, name="HTTPSampler.contentEncoding")
        ]

        # 條件式地加入網路設定
        if req_info.domain: children.append(E.stringProp(req_info.domain, name="HTTPSampler.domain"))
        if req_info.protocol: children.append(E.stringProp(req_info.protocol, name="HTTPSampler.protocol"))
        if req_info.port: children.append(E.stringProp(req_info.port, name="HTTPSampler.port"))
        if req_info.path: children.append(E.stringProp(req_info.path, name="HTTPSampler.path"))

        # 【微調】新增 timeout 參數
        if req_info.connect_timeout:
            children.append(E.stringProp(req_info.connect_timeout, name="HTTPSampler.connect_timeout"))
        if req_info.response_timeout:
            children.append(E.stringProp(req_info.response_timeout, name="HTTPSampler.response_timeout"))

        element = E.HTTPSamplerProxy(
            *children, guiclass="HttpTestSampleGui", testclass="HTTPSamplerProxy",
            testname=req_info.name, enabled="true"
        )
        return element, E.hashTree()

    def _create_response_assertion(self, assertion: AssertionInfo) -> tuple:
        """
        根據 `AssertionInfo` 物件建立 `<ResponseAssertion>` 元件。

        此函式會將斷言的所有細節（如測試類型、比對樣式、邏輯運算）轉換為
        JMeter 所需的 XML 格式，並完整支援 is_or, is_not 等選項。
        :param assertion: 包含斷言所有設定的 AssertionInfo 物件。
        :return: 一個包含 ResponseAssertion XML 元素和其 hashTree 的元組。
        """
        # 1. 處理 is_not 條件，它會修改 test_type
        # JMeter 使用位元運算來組合條件，4 代表 'Not'
        final_test_type = assertion.test_type
        if assertion.is_not:
            final_test_type |= 4  # 按位或運算，添加 NOT 條件 (e.g., Substring 2 -> Not Substring 6)

        # 2. 準備 test_strings 集合
        #    此處直接使用 assertion.patterns 列表，確保不會混入任何多餘的字串。
        test_strings_props = [E.stringProp(str(p)) for p in assertion.patterns]
        collection_prop = E.collectionProp(*test_strings_props, name="Assertion.test_strings")

        # 3. 處理 main_sample_only (對應 Assertion.scope)
        scope = "main" if assertion.main_sample_only else "all"

        # 4. 建立所有屬性（除了 is_or）
        props = [
            collection_prop,
            E.stringProp("", name="Assertion.custom_message"),
            E.stringProp(assertion.test_field, name="Assertion.test_field"),
            E.boolProp(str(assertion.assume_success).lower(), name="Assertion.assume_success"),
            E.intProp(str(final_test_type), name="Assertion.test_type"),
            E.stringProp(scope, name="Assertion.scope")
        ]

        # 5. 【關鍵】根據 is_or 條件，添加額外的 boolProp
        #    這個屬性只在需要 OR 邏輯時才存在。
        if assertion.is_or:
            props.append(E.boolProp("true", name="Assertion.or"))

        # 6. 組合最終的 XML 元件
        element = E.ResponseAssertion(
            *props,
            guiclass="AssertionGui",
            testclass="ResponseAssertion",
            testname=assertion.name,
            enabled=str(assertion.enabled).lower()
        )

        return element, E.hashTree()

    def _create_view_results_tree_listener(self, listener_info: ListenerInfo) -> tuple:
        """
        根據 `ListenerInfo` 物件建立一個可設定的 `<ResultCollector>` (View Results Tree) 元件。

        :param listener_info: 包含監聽器設定的 ListenerInfo 物件。
        :return: 一個包含 ResultCollector XML 元素和其 hashTree 的元組。
        """
        # 1. 建立 ResultCollector 元件
        collector_element = E.ResultCollector(
            guiclass="ViewResultsFullVisualizer",
            testclass="ResultCollector",
            testname=listener_info.name,
            enabled="true"
        )

        # 2. 處理日誌記錄選項
        collector_element.append(E.boolProp(str(listener_info.log_errors_only).lower(), name="ResultCollector.error_logging"))
        if listener_info.log_successes_only:
            # JMeter 中，只記錄成功是透過一個獨立的 flag，而不是 error_logging 的反向
            collector_element.append(E.boolProp("true", name="ResultCollector.success_only_logging"))

        # 3. 建立標準的 saveConfig 物件屬性，這定義了監聽器要儲存哪些欄位
        save_config = E.objProp(
            E.name("saveConfig"),
            E.value(
                E.time("true"), E.latency("true"), E.timestamp("true"),
                E.success("true"), E.label("true"), E.code("true"),
                E.message("true"), E.threadName("true"), E.dataType("true"),
                E.encoding("false"), E.assertions("true"), E.subresults("true"),
                E.responseData("false"), E.samplerData("false"), E.xml("false"),
                E.fieldNames("true"), E.responseHeaders("false"), E.requestHeaders("false"),
                E.responseDataOnError("false"), E.saveAssertionResultsFailureMessage("true"),
                E.assertionsResultsToSave("0"), E.bytes("true"), E.sentBytes("true"),
                E.url("true"), E.threadCounts("true"), E.idleTime("true"),
                E.connectTime("true"),
                **{'class': "SampleSaveConfiguration"}
            )
        )
        collector_element.append(save_config)

        # 4. 設定輸出檔案名稱
        collector_element.append(E.stringProp(listener_info.filename, name="filename"))

        # 5. 建立一個空的 hashTree
        collector_hash_tree = E.hashTree()

        return collector_element, collector_hash_tree

    def _assemble_jmx_from_structured_data(self, context: GenerationContext) -> str:
        """
        根據結構化的 Context 物件，組裝出最終的 JMX (XML) 字串。

        這是 JMX 的「組裝工廠」。它接收 `_prepare_generation_context` 產出的 `GenerationContext` 物件，
        然後遍歷其中的所有元件，呼叫對應的 `_create_*` 輔助函式來生成 XML 片段，
        並將它們按照正確的層級關係組裝起來。
        :param context: 包含所有已解析和處理過的測試計畫資訊的 GenerationContext 物件。
        :return: 一個包含完整 JMX 內容的字串。
        """
        self.logger.info("=== 開始執行 JMX 組裝流程 ===")

        root = E.jmeterTestPlan(version="1.2", properties="5.0", jmeter="5.6.3")
        root_hash_tree = E.hashTree()
        root.append(root_hash_tree)

        test_plan_element, test_plan_hash_tree = self._create_test_plan(context)
        root_hash_tree.append(test_plan_element)
        root_hash_tree.append(test_plan_hash_tree)

        if context.global_settings:
            gs = context.global_settings
            if gs.http_defaults and gs.http_defaults.domain:
                defaults_element, defaults_ht = self._create_http_request_defaults(gs.http_defaults)
                test_plan_hash_tree.append(defaults_element)
                test_plan_hash_tree.append(defaults_ht)
            if gs.headers:
                header_manager_element, header_manager_ht = self._create_http_header_manager(gs.headers,
                                                                                             name="Global HTTP Headers")
                test_plan_hash_tree.append(header_manager_element)
                test_plan_hash_tree.append(header_manager_ht)
            if gs.random_variables:
                for var_config in gs.random_variables:
                    rvc_element, rvc_ht = self._create_random_variable_config(var_config)
                    test_plan_hash_tree.append(rvc_element)
                    test_plan_hash_tree.append(rvc_ht)

        for tg_context in context.thread_groups:
            self.logger.info(f"正在處理 ThreadGroup: {tg_context.name}")
            self.logger.info(f"此 ThreadGroup 的 HTTP Requests 數量: {len(tg_context.http_requests)}")

            tg_element, tg_hash_tree = self._create_thread_group(tg_context)
            test_plan_hash_tree.append(tg_element)
            test_plan_hash_tree.append(tg_hash_tree)

            if tg_context.headers:
                header_manager_element, header_manager_ht = self._create_http_header_manager(tg_context.headers)
                tg_hash_tree.append(header_manager_element)
                tg_hash_tree.append(header_manager_ht)

            if tg_context.random_variables:
                for var_config in tg_context.random_variables:
                    rvc_element, rvc_ht = self._create_random_variable_config(var_config)
                    tg_hash_tree.append(rvc_element)
                    tg_hash_tree.append(rvc_ht)

            if tg_context.csv_data_sets:
                for csv_info in tg_context.csv_data_sets:
                    csv_element, csv_ht = self._create_csv_data_set_config(csv_info, name=csv_info.name)
                    tg_hash_tree.append(csv_element)
                    tg_hash_tree.append(csv_ht)

            if tg_context.http_requests:
                for req_info in tg_context.http_requests:
                    self.logger.info(f"  -> 正在組裝 HTTP Sampler: {req_info.name}")

                    final_body_content = ""  # 初始化最終的 Body 內容

                    if req_info.source_json_filename:
                        # 如果是檔案引用，生成 __FileToString 函數字串
                        # 使用 JMeter 屬性 ${__P(testDataPath,.)} 來指定檔案的根目錄，增加靈活性
                        final_body_content = f"${{__FileToString(${{__P(testDataPath,.)}}/{req_info.source_json_filename},UTF-8)}}"
                        self.logger.info(f"    -> Body 來源: 檔案引用 -> {final_body_content}")
                    elif req_info.json_body:
                        # 否則，使用舊的邏輯，處理嵌入的 Body
                        final_body_content = req_info.json_body
                        self.logger.info(f"    -> Body 來源: 嵌入式內容")
                        # 參數化邏輯只對嵌入式 Body 生效
                        if req_info.is_parameterized and tg_context.csv_data_sets:
                            for csv_info in tg_context.csv_data_sets:
                                final_body_content = self._parameterize_json_body(final_body_content, csv_info)

                    # 將處理好的 final_body_content 傳給建立函式
                    sampler_element, sampler_hash_tree = self._create_http_sampler_proxy(
                        req_info=req_info,
                        json_body=final_body_content
                    )

                    tg_hash_tree.append(sampler_element)
                    tg_hash_tree.append(sampler_hash_tree)

                    if req_info.assertions:
                        for assertion_info in req_info.assertions:
                            assertion_element, assertion_ht = self._create_response_assertion(assertion_info)
                            sampler_hash_tree.append(assertion_element)
                            sampler_hash_tree.append(assertion_ht)

            if tg_context.listeners:
                for listener_info in tg_context.listeners:
                    listener_element, listener_ht = self._create_view_results_tree_listener(listener_info)
                    tg_hash_tree.append(listener_element)
                    tg_hash_tree.append(listener_ht)

        for listener_info in context.listeners:
            listener_element, listener_ht = self._create_view_results_tree_listener(listener_info)
            test_plan_hash_tree.append(listener_element)
            test_plan_hash_tree.append(listener_ht)

        self.logger.info("JMX 元件組裝完成。")
        return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding='UTF-8').decode('utf-8')

    async def convert_requirements_to_template(self, requirements: str, files_data: List[Dict] = None) -> str:
        """
        使用 LLM 將自然語言需求轉換為結構化的 JMX 需求模板。

        此函式是與 LLM 互動的入口，負責將自由格式的文字轉換成後續程式可以解析的固定格式。
        :param requirements: 使用者輸入的自然語言需求。
        :param files_data: 一個包含已上傳檔案資訊的字典列表。
        :return: 一個包含結構化模板內容的字串。
        :raises RuntimeError: 如果 LLM 呼叫或後續清理失敗。
        """
        self.logger.info("開始執行 LLM 需求轉換任務：自然語言 -> 結構化模板")

        # 步驟 1: 建立一個專為此轉換任務設計的提示詞
        prompt = self._build_conversion_prompt(requirements, files_data)
        self.logger.debug(f"建立的轉換提示詞:\n---\n{prompt}\n---")

        try:
            # 步驟 2: 呼叫 LLM 服務來執行轉換
            self.logger.info("正在呼叫 LLM 進行轉換...")
            response = self.llm_service.generate_text(prompt)
            self.logger.info("LLM 回應接收成功。")
            self.logger.debug(f"LLM 原始回應:\n---\n{response}\n---")

            # 步驟 3: 清理 LLM 的回應，移除可能的多餘部分 (如 markdown)
            template_str = self._clean_llm_template_response(response)
            self.logger.info("已清理 LLM 回應，準備返回結構化模板。")

            return template_str

        except Exception as e:
            self.logger.error(f"在使用 LLM 轉換需求時發生錯誤: {e}", exc_info=True)
            raise RuntimeError(f"無法將需求轉換為模板: {e}")

    def _build_conversion_prompt(self, requirements: str, files_data: List[Dict] = None) -> str:
        """
        建立用於指導 LLM 進行需求轉換的提示詞 (Prompt)。

        這是「提示詞工程」的核心，負責動態產生一段詳細的文字，指導 LLM 如何工作。
        它包含了角色設定、核心規則、任務描述和輸出範例。
        :param requirements: 使用者輸入的自然語言需求。
        :param files_data: 一個包含已上傳檔案資訊的字典列表。
        :return: 完整的提示詞字串。
        """
        attached_files = [f.get('filename', f.get('name', '')) for f in files_data if f] if files_data else []
        files_context = "\n".join([f"- `{name}`" for name in attached_files]) if attached_files else "無"

        prompt = textwrap.dedent(f"""
        [INST]
        <<SYS>>
        您是一位精通 JMeter 的專家助理。您的唯一任務是將用戶提供的自然語言需求，精確地轉換為指定的結構化文字模板格式。

        **核心規則:**
        1.  **嚴格遵循格式**: 您的輸出**必須**僅包含結構化模板內容，不得包含任何對話、解釋或 Markdown 標記 (例如 ```)。
        2.  **【關鍵】名稱必須精確**: 所有元件的名稱 (例如 `[TestPlan: msp-svc-checkid]`) **必須**嚴格使用範例中提供的名稱，不得使用 JMeter 的預設名稱。
        3.  **【關鍵】檔案引用規則**: 如果 `HttpRequest` 需要使用檔案作為請求 Body，您**必須**使用 `body_file = "檔案名稱"` 的格式。**絕對禁止**將檔案的實際內容直接填入 `body` 參數中。
        4.  **正確的層級關係**: 元件的 `parent` 屬性必須正確設定。
        5.  **【關鍵】斷言層級規則**: 如果用戶需求中的斷言沒有明確指定要附加到哪一個 `HttpRequest`，則其 `parent` 屬性**必須**設定為其所屬的 `ThreadGroup` 名稱。
        6.  **【關鍵】嚴格的內容規則**: **絕對禁止**在沒有用戶明確指示（例如，提供 CSV 檔案進行參數化）的情況下，主動將請求 Body 中的任何值修改為 JMeter 變數 (例如 `${{variable}}`)。Body 內容必須保持原始狀態，除非有明確的覆寫指令。
        7.  **【新增】伺服器資訊同義詞規則**: 用戶可能會使用「Server Name or IP」、「伺服器位址」、「主機」等詞語來描述伺服器。這些都應被對應到 `domain` 參數。
        <</SYS>>

        **### 任務: 將以下用戶需求轉換為結構化模板 ###**

        **用戶需求描述:**
        ---
        {requirements}
        ---

        **可用的附件檔案列表:**
        ---
        {files_context}
        ---

        **### 目標輸出格式 (您必須完全仿照此格式輸出) ###**

        ```text
        # ======================================================================
        # JMeter 測試計畫生成需求模板 
        # ======================================================================

        [TestPlan: msp-svc-checkid]
        tearDown_on_shutdown = true

        # --- 【注意名稱】 ---
        [HttpHeaderManager: GlobalHeaders]
        parent = msp-svc-checkid
        header.Content-type = application/json
        header.x-cub-it-key = zgnf1hJIZVxtIxfjLl2a0T9vl5f98o9b

        # --- 【全域伺服器設定】 ---
        [GlobalHttpRequestDefaults: DefaultHttpSettings]
        parent = msp-svc-checkid
        # 注意：用戶需求中的 "Server Name or IP" 或 "伺服器位址" 都應對應到此 domain 參數
        domain = your-global-server.com
        protocol = https

        [ThreadGroup: MSP-B-CHECKIDC001]
        parent = msp-svc-checkid
        threads = ${{__P(threads,3)}}
        rampup = ${{__P(rampUp,1)}}
        use_scheduler = true
        duration = ${{__P(duration,10)}}

        # --- 【注意 body_file 的使用與內容的原始性】 ---
        [HttpRequest: REQ_MSP-B-CHECKIDC001]
        parent = MSP-B-CHECKIDC001
        method = POST
        path = /rest # <-- 當 GlobalHttpRequestDefaults 已設定 domain，這裡只需提供 path
        # Body 內容不應被主動參數化
        body_file = MOCK-B-CHECKIDC001.json # <-- 正確用法

        # --- 【注意名稱】 ---
        [CsvDataSet: CSV_For_CHECKIDC001]
        parent = MSP-B-CHECKIDC001
        filename = MOCK-B-CHECKIDC001.csv
        variable_names = type,ID

        [ResponseAssertion: 驗證回覆-TXNSEQ]
        parent = REQ_MSP-B-CHECKIDC001 # <-- 若斷言目標不明確，parent 應設為 ThreadGroup 名稱
        pattern_matching_rule = Contains
        pattern_1 = ZXZTEST-123456

        [ResponseAssertion: 驗證回覆-RETURNCODE]
        parent = REQ_MSP-B-CHECKIDC001
        pattern_matching_rule = Contains
        use_or_logic = true
        pattern_1 = "RETURNCODE":"0000"

        # --- 【注意監聽器名稱和屬性】 ---
        [Listener: Successes]
        parent = msp-svc-checkid
        filename = Successes_Content.xml
        log_successes_only = true

        [Listener: Errors]
        parent = msp-svc-checkid
        filename = Error_Content.xml
        log_errors_only = true
        ```
        [/INST]
        """)
        return prompt

    def _clean_llm_template_response(self, response: str) -> str:
        """
        清理 LLM 返回的模板字串，移除常見的多餘部分。
        :param response: 來自 LLM 的原始回應字串。
        :return: 清理後的模板字串。
        """
        # 尋找模板的起始標誌
        start_marker = "# ======================================================================"
        start_index = response.find(start_marker)

        if start_index == -1:
            # 如果找不到起始標誌，嘗試尋找第一個 [Component: Name]
            match = re.search(r"^\s*\[[a-zA-Z]+:.+?\]", response, re.MULTILINE)
            if match:
                start_index = match.start()
            else:
                self.logger.warning("在 LLM 回應中找不到模板起始標誌，返回原始回應。")
                return response.strip()

        # 從找到的起始位置截取
        cleaned_response = response[start_index:]

        # 移除結尾可能出現的 markdown
        if cleaned_response.endswith("```"):
            cleaned_response = cleaned_response[:-3].strip()

        return cleaned_response