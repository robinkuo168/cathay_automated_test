import os
import json
import threading
import re
import math
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import pandas as pd
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
from ibm_watsonx_ai.credentials import Credentials
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
from .logger import get_logger
from .llm_service import LLMService
import io
import csv
from dataclasses import dataclass, field

load_dotenv()

@dataclass
class CsvInfo:
    filename: str
    variable_names: List[str] = field(default_factory=list)
    total_rows: int = 0
    raw_content: Optional[str] = None

@dataclass
class HttpRequestInfo:
    name: str
    json_body: Optional[str] = None
    source_json_filename: Optional[str] = None
    method: str = "POST"
    # 新增標記，追蹤此請求是否已成功參數化
    is_parameterized: bool = False

@dataclass
class ThreadGroupContext:
    name: str
    http_requests: List[HttpRequestInfo] = field(default_factory=list)
    csv_configs: List[CsvInfo] = field(default_factory=list)

@dataclass
class GenerationContext:
    test_plan_name: str
    thread_groups: List[ThreadGroupContext]
    requirements: str
    raw_processed_files: Dict

class JMXGeneratorService:
    def __init__(self, llm_service: Optional[LLMService] = None, model_name: str = "default"):
        """
        初始化 JMXGeneratorService
        :param llm_service: 可選的 LLMService 實例，如果為 None 則會自動創建
        :param model_name: 要使用的模型名稱，預設為 "default"
        """
        self._llm_service = llm_service
        self._model_name = model_name
        self.logger = get_logger(__name__)
        self.jmx_templates = self._load_jmx_templates()
        
    @property
    def llm_service(self) -> LLMService:
        if self._llm_service is None:
            self.logger.info(f"初始化 LLMService (Model: {self._model_name})")
            try:
                # 從 main 模組導入 get_llm_service 函數
                from main import get_llm_service
                self._llm_service = get_llm_service(self._model_name)
            except ImportError:
                self.logger.warning("無法從 main 模組導入 get_llm_service，使用默認 LLMService 初始化")
                self._llm_service = LLMService()
        return self._llm_service

    def _load_jmx_templates(self) -> Dict:
        """載入 JMX 模板 - 修正 HTTP Request 格式"""
        return {
            "xml_header": '<?xml version="1.0" encoding="UTF-8"?>',
            # ... 其他模板 ...

            "http_request_with_body": """<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{name}" enabled="true">
      <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
      <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
          <elementProp name="" elementType="HTTPArgument">
            <boolProp name="HTTPArgument.always_encode">false</boolProp>
            <stringProp name="Argument.value">{body_data}</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>
      <stringProp name="HTTPSampler.protocol">{protocol}</stringProp>
      <stringProp name="HTTPSampler.domain">{domain}</stringProp>
      <stringProp name="HTTPSampler.port">{port}</stringProp>
      <stringProp name="HTTPSampler.path">{path}</stringProp>
      <stringProp name="HTTPSampler.method">{method}</stringProp>
      <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
      <boolProp name="HTTPSampler.auto_redirects">false</boolProp>
      <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
      <boolProp name="HTTPSampler.save_response_data">false</boolProp>
      <stringProp name="HTTPSampler.contentEncoding">UTF-8</stringProp>
    </HTTPSamplerProxy>
    <hashTree/>"""
        }

    def _create_jmx_from_template(self, test_name: str, comments: str = "", content: str = "") -> str:
        """從模板創建完整的 JMX 內容"""
        return (
            self.jmx_templates["xml_header"] + "\n" +
            self.jmx_templates["test_plan_structure"].format(
                test_name=test_name,
                comments=comments,
                content=content
                )
            )

    def generate_jmx_with_retry(self, requirements: str, files_data: List[Dict] = None, max_retries: int = 3) -> str:
        """
        生成 JMX 檔案（帶重試機制）
        """
        try:
            context = self._prepare_generation_context(requirements, files_data)
            self.logger.info(f"✅ 生成上下文準備完成，測試計畫: '{context.test_plan_name}'")
        except ValueError as e:
            self.logger.error(f"❌ 輸入資料準備失敗，無法繼續生成: {e}")
            return self._get_fallback_jmx(requirements, self._safe_process_files(files_data))

        validation_errors = []
        for attempt in range(max_retries):
            try:
                self.logger.info(f"🚀 開始第 {attempt + 1}/{max_retries} 次生成嘗試...")
                prompt = self._build_prompt(context, attempt, validation_errors)
                response = self.llm_service.generate_text(prompt=prompt)
                jmx_content = self._extract_and_clean_jmx(response, context)

                is_valid, message = self.validate_xml(jmx_content)
                if not is_valid:
                    validation_errors.append(f"XML格式錯誤: {message}")
                    self.logger.warning(f"第 {attempt + 1} 次嘗試 - XML 驗證失敗: {message}")
                    continue

                content_valid, content_message = self._validate_jmx_content_requirements(jmx_content, context)
                if not content_valid:
                    validation_errors.append(f"內容不符需求: {content_message}")
                    self.logger.warning(f"第 {attempt + 1} 次嘗試 - 內容驗證失敗: {content_message}")
                    continue

                self.logger.info(f"✅ 第 {attempt + 1} 次生成成功！")
                return jmx_content

            except Exception as e:
                self.logger.error(f"第 {attempt + 1} 次生成過程中發生異常: {e}", exc_info=True)
                validation_errors.append(f"執行異常: {str(e)}")

        self.logger.error("所有重試均告失敗。")
        raise Exception("無法生成有效的 JMX 檔案，已達最大重試次數。")

    def _prepare_generation_context(self, requirements: str, files_data: List[Dict]) -> GenerationContext:
        """
        【重構版】
        預處理函數：將原始輸入轉換為結構化的 GenerationContext。
        此版本將 JSON 參數化作為核心前置處理步驟。
        """
        self.logger.info("=== 步驟 1: 開始準備生成上下文 (採用健壯參數化流程) ===")
        processed_files = self._safe_process_files(files_data)
        req_analysis = self._analyze_requirements_dynamically(requirements)

        if not req_analysis.get('thread_groups'):
            raise ValueError("需求分析失敗：無法從需求中解析出任何 Thread Group 名稱。")

        thread_group_contexts = []

        # 從需求分析中獲取所有預期的 HTTP Request 名稱
        all_http_requests_from_analysis = req_analysis.get('http_requests', [])

        # 主迴圈：為每一個 Thread Group 建立上下文
        for tg_name in req_analysis['thread_groups']:
            self.logger.info(f"🔄 --- 正在處理 Thread Group: '{tg_name}' ---")

            # 1. 確定相關檔案名稱
            # 假設 HTTP Request 名稱與 Thread Group 名稱一致
            http_req_name = next((r for r in all_http_requests_from_analysis if r == tg_name), tg_name)
            json_filename = f"{http_req_name}.json"
            csv_filename = f"{tg_name}.csv"

            # 2. 獲取原始檔案內容
            json_info = processed_files['json_contents'].get(json_filename)
            original_json_body = json_info.get('raw_content') if json_info else None

            # 使用更安全的方式尋找 CSV 設定
            csv_config_data = next(
                (c for c in processed_files.get('csv_configs', []) if c.get('filename') == csv_filename), None)

            # 3. 【核心邏輯】執行參數化並準備最終資料
            final_json_body = original_json_body
            csv_info_obj = None
            is_parameterized = False

            if original_json_body and csv_config_data:
                self.logger.info(f"為 '{tg_name}' 找到匹配的 JSON ('{json_filename}') 和 CSV ('{csv_filename}')。")

                # 建立 CsvInfo 物件以傳遞給參數化函式
                csv_info_obj = CsvInfo(
                    filename=csv_filename,
                    variable_names=csv_config_data.get('variable_names', []),
                    total_rows=csv_config_data.get('total_rows', 0),
                    raw_content=csv_config_data.get('raw_content')
                )

                # 🚀 呼叫我們新的、健壯的參數化函式
                final_json_body = self._parameterize_json_body(original_json_body, csv_info_obj)
                is_parameterized = True  # 標記已執行參數化流程

            else:
                self.logger.warning(f"⚠️  為 '{tg_name}' 未能找到完整的 JSON/CSV 配對，將跳過參數化。")
                if not original_json_body:
                    self.logger.warning(f"   - 缺少 JSON 檔案: '{json_filename}'")
                if not csv_config_data:
                    self.logger.warning(f"   - 缺少 CSV 檔案: '{csv_filename}'")

            # 4. 建立結構化物件
            # 使用【最終】的 JSON body (可能是原始的，也可能是參數化後的)
            http_req_info = HttpRequestInfo(
                name=http_req_name,
                json_body=final_json_body,
                source_json_filename=json_filename if json_info else None,
                is_parameterized=is_parameterized
            )

            tg_context = ThreadGroupContext(name=tg_name)
            tg_context.http_requests.append(http_req_info)

            # 只有成功建立 CsvInfo 物件時才將其加入
            if csv_info_obj:
                tg_context.csv_configs.append(csv_info_obj)

            thread_group_contexts.append(tg_context)
            self.logger.info(f"✅ --- Thread Group '{tg_name}' 處理完成 ---")

        # 5. 返回最終的、完整的上下文物件
        return GenerationContext(
            test_plan_name=req_analysis.get('test_plan_name', 'Generated Test Plan'),
            thread_groups=thread_group_contexts,
            requirements=requirements,
            raw_processed_files=processed_files
        )

    def _assess_requirements_complexity(self, requirements: str) -> int:
        """評估需求複雜度（避免新增函式，內嵌邏輯）"""
        score = 0

        # 基本複雜度指標
        if len(requirements) > 500:
            score += 2
        if requirements.count('Thread Group') > 1:
            score += 2
        if 'Header Manager' in requirements:
            score += 1
        if 'CSV Data Set' in requirements:
            score += 1
        if 'Response Assertion' in requirements:
            score += 1
        if 'POST' in requirements.upper():
            score += 1
        if '${' in requirements:  # 包含變數
            score += 2

        return min(score, 10)

    def _count_jmx_components(self, jmx_content: str) -> int:
        """計算 JMX 內容中的組件數量"""
        import re

        # 定義主要組件的模式
        component_patterns = [
            r'<TestPlan\s+',  # TestPlan
            r'<HeaderManager\s+',  # Header Manager
            r'<ThreadGroup\s+',  # Thread Group
            r'<HTTPSamplerProxy\s+',  # HTTP Request
            r'<CSVDataSet\s+',  # CSV Data Set Config
            r'<ResponseAssertion\s+',  # Response Assertion
            r'<ResultCollector\s+.*testclass="ResultCollector"',  # View Results Tree
        ]

        total_count = 0
        for pattern in component_patterns:
            matches = re.findall(pattern, jmx_content, re.IGNORECASE)
            total_count += len(matches)

        return total_count

    def _safe_process_files(self, files_data: List[Dict] = None) -> Dict:
        """安全地處理檔案資料"""
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

    def _analyze_requirements_dynamically(self, requirements: str) -> Dict:
        """動態分析需求，提取關鍵資訊（通用版本）"""
        import re

        analysis = {
            'test_plan_name': '',
            'thread_groups': [],
            'http_requests': [],
            'csv_configs': [],
            'response_assertions': [],
            'view_results_trees': [],
            'expected_components': 0
        }

        try:
            # 1. 提取測試計畫名稱（多種格式支援）
            testplan_patterns = [
                r'測試計畫[^，,\n]*名稱[欄位]*[填入為]*[『「]([^』」]+)[』」]',
                r'名稱[欄位]*填入[『「]([^』」]+)[』」]',
                r"Test Plan.*name.*[『「]([^』」]+)[』」]",
                r"testname.*[『「]([^』」]+)[』」]"
            ]

            for pattern in testplan_patterns:
                match = re.search(pattern, requirements, re.IGNORECASE)
                if match:
                    analysis['test_plan_name'] = match.group(1).strip()
                    break

            # 2. 提取 Thread Group 名稱（通用模式）
            tg_patterns = [
                # 中文模式
                r'thread group[^，,\n]*名稱[為分別為]*[『「]*([A-Z0-9_-]+)[』」]*',
                r'執行緒群組[^，,\n]*名稱[為分別為]*[『「]*([A-Z0-9_-]+)[』」]*',
                r'增加.*thread group.*名稱為\s*[『「]*([A-Z0-9_-]+)[』」]*',
                # 處理 "名稱分別為 A 及 B" 的格式
                r'名稱分別為\s*([A-Z0-9_-]+)\s*及\s*([A-Z0-9_-]+)',
                r'名稱分別為\s*([A-Z0-9_-]+)[、，]\s*([A-Z0-9_-]+)',
                # 英文模式
                r'Thread Group.*name[s]*[:\s]*[『「]*([A-Z0-9_-]+)[』」]*',
            ]

            for pattern in tg_patterns:
                matches = re.findall(pattern, requirements, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        # 處理多組匹配（如 "A 及 B"）
                        for item in match:
                            if item.strip():
                                analysis['thread_groups'].append(item.strip())
                    else:
                        if match.strip():
                            analysis['thread_groups'].append(match.strip())

            # 3. 提取 HTTP Request 名稱（通用模式）
            http_patterns = [
                # 中文模式
                r'http request[^，,\n]*名稱[為分別為]*\s*[『「]*([A-Z0-9_-]+)[』」]*',
                r'HTTP請求[^，,\n]*名稱[為分別為]*\s*[『「]*([A-Z0-9_-]+)[』」]*',
                r'增加.*http request.*名稱為\s*[『「]*([A-Z0-9_-]+)[』」]*',
                r'底下增加\s*http request[^，,\n]*名稱為\s*([A-Z0-9_-]+)',
                # 英文模式
                r'HTTP Request.*name[s]*[:\s]*[『「]*([A-Z0-9_-]+)[』」]*',
                r'HTTP Sampler.*name[s]*[:\s]*[『「]*([A-Z0-9_-]+)[』」]*',
            ]

            for pattern in http_patterns:
                matches = re.findall(pattern, requirements, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        for item in match:
                            if item.strip():
                                analysis['http_requests'].append(item.strip())
                    else:
                        if match.strip():
                            analysis['http_requests'].append(match.strip())

            # 4. 智能推斷：如果 HTTP Request 和 Thread Group 在同一段落，可能同名
            if len(analysis['http_requests']) < len(analysis['thread_groups']):
                lines = requirements.split('\n')
                for i, line in enumerate(lines):
                    for tg_name in analysis['thread_groups']:
                        # 檢查是否在相鄰行中提到相同名稱的 http request
                        context_lines = lines[max(0, i - 2):min(len(lines), i + 3)]
                        context = ' '.join(context_lines)
                        if (tg_name in line and 'thread group' in line.lower() and
                                'http request' in context.lower()):
                            if tg_name not in analysis['http_requests']:
                                analysis['http_requests'].append(tg_name)

            # 5. 提取 CSV 配置資訊（通用模式）
            csv_patterns = [
                r'CSV.*資料.*設定',
                r'CSV.*Data.*Set.*Config',
                r'([A-Z0-9_-]+\.csv)',
                r'檔名.*填入.*[『「]*([^』」\s]+\.csv)[』」]*',
                r'附件.*[『「]*([^』」\s]+\.csv)[』」]*',
            ]

            for pattern in csv_patterns:
                matches = re.findall(pattern, requirements, re.IGNORECASE)
                for match in matches:
                    if match.strip().endswith('.csv'):
                        analysis['csv_configs'].append(match.strip())

            # 6. 提取 Response Assertion 資訊
            assertion_patterns = [
                r'Response Assertion.*[『「]*([^』」\n]+)[』」]*',
                r'回應.*斷言.*[『「]*([^』」\n]+)[』」]*',
                r'驗證.*回[覆應].*[『「]*([^』」\n]+)[』」]*',
            ]

            for pattern in assertion_patterns:
                matches = re.findall(pattern, requirements, re.IGNORECASE)
                analysis['response_assertions'].extend([m.strip() for m in matches if m.strip()])

            # 7. 提取 View Results Tree 資訊
            view_patterns = [
                r'View Results Tree.*[『「]*([^』」\n]+)[』」]*',
                r'檢視.*結果.*樹狀.*[『「]*([^』」\n]+)[』」]*',
                r'結果.*檢視.*[『「]*([^』」\n]+)[』」]*',
            ]

            for pattern in view_patterns:
                matches = re.findall(pattern, requirements, re.IGNORECASE)
                analysis['view_results_trees'].extend([m.strip() for m in matches if m.strip()])

        except Exception as e:
            self.logger.warning(f"需求分析時發生錯誤: {str(e)}")
            # 發生錯誤時嘗試備用解析
            return self._fallback_parse_requirements(requirements)

        # 清理和去重
        analysis['thread_groups'] = list(set([x for x in analysis['thread_groups'] if x]))
        analysis['http_requests'] = list(set([x for x in analysis['http_requests'] if x]))
        analysis['csv_configs'] = list(set([x for x in analysis['csv_configs'] if x]))
        analysis['response_assertions'] = list(set([x for x in analysis['response_assertions'] if x]))
        analysis['view_results_trees'] = list(set([x for x in analysis['view_results_trees'] if x]))

        # 計算預期組件數量
        analysis['expected_components'] = (
                len(analysis['thread_groups']) +
                len(analysis['http_requests']) +
                len(analysis['csv_configs']) +
                len(analysis['response_assertions']) +
                len(analysis['view_results_trees'])
        )

        return analysis

    def _debug_requirement_analysis(self, requirements: str) -> Dict:
        """調試需求分析結果"""
        self.logger.info("=== 開始需求分析調試 ===")

        analysis = self._analyze_requirements_dynamically(requirements)

        # 記錄分析結果
        self.logger.info(f"測試計畫名稱: '{analysis['test_plan_name']}'")
        self.logger.info(f"Thread Groups ({len(analysis['thread_groups'])}): {analysis['thread_groups']}")
        self.logger.info(f"HTTP Requests ({len(analysis['http_requests'])}): {analysis['http_requests']}")
        self.logger.info(f"CSV 配置 ({len(analysis['csv_configs'])}): {analysis['csv_configs']}")
        self.logger.info(
            f"Response Assertions ({len(analysis['response_assertions'])}): {analysis['response_assertions']}")
        self.logger.info(
            f"View Results Trees ({len(analysis['view_results_trees'])}): {analysis['view_results_trees']}")
        self.logger.info(f"預期組件總數: {analysis['expected_components']}")

        # 檢查關鍵字出現情況
        self.logger.info("=== 關鍵字檢查 ===")
        keywords = ['thread group', 'http request', 'csv', 'assertion', 'view results']
        for keyword in keywords:
            count = requirements.lower().count(keyword.lower())
            self.logger.info(f"'{keyword}' 出現次數: {count}")

        self.logger.info("=== 需求分析調試完成 ===")
        return analysis

    def _fallback_parse_requirements(self, requirements: str) -> Dict:
        """備用需求解析邏輯（當正則表達式失敗時使用）"""
        self.logger.warning("使用備用需求解析邏輯")

        analysis = {
            'test_plan_name': '',
            'thread_groups': [],
            'http_requests': [],
            'csv_configs': [],
            'response_assertions': [],
            'view_results_trees': [],
            'expected_components': 0
        }

        lines = requirements.split('\n')
        req_text = requirements.lower()

        # 簡單的關鍵字匹配
        for line in lines:
            line_lower = line.lower()
            line_clean = line.strip()

            # 查找可能的組件名稱（通常是大寫字母+數字+連字符的格式）
            import re
            component_names = re.findall(r'\b[A-Z]{2,}[-_][A-Z0-9-_]+\b', line)

            for name in component_names:
                # 根據上下文判斷是什麼類型的組件
                if 'thread group' in line_lower and name not in analysis['thread_groups']:
                    analysis['thread_groups'].append(name)
                elif 'http request' in line_lower and name not in analysis['http_requests']:
                    analysis['http_requests'].append(name)

            # 查找 CSV 檔案
            csv_files = re.findall(r'\b[A-Z0-9-_]+\.csv\b', line)
            analysis['csv_configs'].extend(csv_files)

            # 查找測試計畫名稱
            if '測試計畫' in line and '名稱' in line:
                # 簡單提取引號內的內容
                name_match = re.search(r'[『「]([^』」]+)[』」]', line)
                if name_match:
                    analysis['test_plan_name'] = name_match.group(1)

        # 清理去重
        for key in ['thread_groups', 'http_requests', 'csv_configs']:
            analysis[key] = list(set([x for x in analysis[key] if x]))

        analysis['expected_components'] = len(analysis['thread_groups']) + len(analysis['http_requests'])

        self.logger.info(f"備用解析結果: {analysis}")
        return analysis

    def _build_prompt(self, context: GenerationContext, attempt: int = 0, validation_errors: List[str] = None) -> str:
        """ 建立提示詞 """
        self.logger.info("=== 步驟 2: 建立提示詞 ===")

        base_prompt = f"""You are an expert JMeter test script generator...
        === Original Requirements ===
        {context.requirements}
        """

        base_prompt += "\n=== Structured Test Plan Information ===\n"
        base_prompt += f"Test Plan Name: {context.test_plan_name}\n"

        for tg_context in context.thread_groups:
            # ✅ 變更 1: 使用更強烈、更明顯的分隔符，為每個 Thread Group 建立獨立的指令上下文「牆」。
            base_prompt += f"\n\n==================================================\n"
            base_prompt += f"=== INSTRUCTIONS FOR THREAD GROUP: '{tg_context.name}' ===\n"
            base_prompt += f"==================================================\n"

            if tg_context.http_requests:
                for http_req in tg_context.http_requests:
                    base_prompt += f"\n  - HTTP Request Name: {http_req.name}\n"
                    if http_req.json_body:
                        escaped_json = http_req.json_body.replace('&', '&amp;').replace('<', '&lt;').replace('>',
                                                                                                             '&gt;').replace(
                            '"', '&quot;')
                        base_prompt += f"    Source JSON: {http_req.source_json_filename}\n"
                        base_prompt += f"""    🎯 CRITICAL: Use the following full JSON content for the body:
        ```json
        {http_req.json_body}
        ```
        And format it in XML as:
        <stringProp name="Argument.value">{escaped_json}</stringProp>
        """
                    else:
                        base_prompt += "    ❌ WARNING: No JSON body found for this request.\n"

            if tg_context.csv_configs:
                for csv_config in tg_context.csv_configs:
                    base_prompt += f"\n  - CSV Data Set Config for THIS Thread Group:\n"
                    base_prompt += f"    Filename: {csv_config.filename}\n"

                    # ✅ 變更 2: 將陳述句改為強制命令，並明確指出此命令僅適用於當前的 Thread Group。
                    # 這能有效防止 LLM 將第一個 Thread Group 的變數套用到第二個。
                    base_prompt += f"    🎯 MANDATORY: For the CSVDataSet inside the '{tg_context.name}' Thread Group, you MUST use these exact variable names:\n"
                    base_prompt += f"    Variable Names: {','.join(csv_config.variable_names)}\n"

                    # ✅ 變更 3: 增加一個明確的指令來設定 ignoreFirstLine，作為雙重保險。
                    base_prompt += f"    You MUST also set 'ignoreFirstLine' to 'true' for this CSV config.\n"
            else:
                base_prompt += "\n  - No associated CSV file found for this group.\n"

        if attempt > 0 and validation_errors:
            error_summary = "; ".join(list(set(validation_errors))[-3:])
            base_prompt += f"\n🚨 RETRY ATTEMPT #{attempt + 1} - YOU FAILED PREVIOUSLY. YOU MUST FIX THESE ERRORS: {error_summary}\n"

        base_prompt += """
        === 🔥 FINAL, NON-NEGOTIABLE INSTRUCTIONS 🔥 ===
        1.  Generate the complete JMX file based on all the structured information and requirements provided above.
        2.  Pay extreme attention to correct XML structure, especially matching all opening and closing tags like <hashTree> and </hashTree>.
        3.  CRITICAL: Your entire response MUST be ONLY the XML content of the JMX file.
            - Start directly with `<?xml version="1.0" encoding="UTF-8"?>`.
            - End directly with `</jmeterTestPlan>`.
            - DO NOT include any explanations, comments, apologies, or markdown code blocks like ```xml.
        """

        self.logger.info(f"提示詞建立完成，總長度: {len(base_prompt)}")
        return base_prompt

    def _validate_jmx_content_requirements(self, jmx_content: str, context: 'GenerationContext') -> Tuple[bool, str]:
        """
        驗證 JMX 內容是否符合需求
        """
        errors = []

        try:
            # 1. 檢查 Body Data 格式 (這部分邏輯不變)
            if 'HTTPsampler.BodyData' in jmx_content and 'elementType="ElementProp"' in jmx_content:
                errors.append("發現錯誤的 Body Data 格式（HTTPsampler.BodyData），應使用 Arguments 結構")

            # 2. 檢查 HTTP Request Body Data 內容完整性
            correct_body_pattern = r'<stringProp name="Argument\.value">(.*?)</stringProp>'
            body_matches = re.findall(correct_body_pattern, jmx_content, re.DOTALL)

            # 檢查是否有 POST 請求，但完全沒有 Body
            # 我們可以從 context 得知預期有多少個 HTTP Request
            expected_http_requests = sum(len(tg.http_requests) for tg in context.thread_groups)

            if expected_http_requests > 0 and not body_matches:
                # 僅當 JMX 中確實存在 POST 方法的 Sampler 時才報錯
                if '<stringProp name="HTTPSampler.method">POST</stringProp>' in jmx_content:
                    errors.append("POST 請求缺少 Body Data 內容")
            else:
                for i, body_content in enumerate(body_matches, 1):
                    clean_body = body_content.strip()
                    if not clean_body or clean_body.lower() == 'none':
                        errors.append(f"HTTP Request #{i} Body Data 為空或為 'None'")
                    elif len(clean_body) < 10:
                        errors.append(f"HTTP Request #{i} Body Data 內容過短，可能不完整")

            # 3. 使用 context 驗證組件是否存在
            # 檢查 Thread Group
            if context.thread_groups and '<ThreadGroup' not in jmx_content:
                errors.append("需求中提到 Thread Group 但 JMX 中找不到")

            # 檢查 HTTP Request
            if expected_http_requests > 0 and '<HTTPSamplerProxy' not in jmx_content:
                errors.append("需求中提到 HTTP Request 但 JMX 中找不到")

            # 檢查 CSV
            expected_csv_configs = sum(len(tg.csv_configs) for tg in context.thread_groups)
            if expected_csv_configs > 0 and '<CSVDataSet' not in jmx_content:
                errors.append("有提供 CSV 檔案但 JMX 中找不到 CSV Data Set Config")

            # 4. 檢查 hashTree 標籤是否匹配 (這部分邏輯不變)
            open_hashtree = jmx_content.count('<hashTree>')
            close_hashtree = jmx_content.count('</hashTree>')
            if open_hashtree != close_hashtree:
                errors.append(f"hashTree 標籤不匹配 (開始: {open_hashtree}, 結束: {close_hashtree})")

        except Exception as e:
            # 捕獲驗證過程中的任何其他程式碼錯誤
            self.logger.error(f"內容驗證函數內部發生錯誤: {e}", exc_info=True)
            errors.append(f"驗證過程發生內部錯誤: {str(e)}")

        if errors:
            return False, "; ".join(errors)
        else:
            return True, "內容驗證通過"

    def _format_csv_info_safe(self, csv_configs: Dict) -> str:
        """通用格式化 CSV 資訊"""
        if not csv_configs:
            return "無可用的 CSV 檔案\n"

        formatted_info = ""
        for filename, config in csv_configs.items():
            if 'error' in config:
                formatted_info += f"檔案: {filename} (錯誤: {config['error']})\n"
                continue

            formatted_info += f"檔案名稱: {filename}\n"
            formatted_info += f"變數名稱: {','.join(config.get('headers', []))}\n"
            formatted_info += f"總行數: {config.get('total_rows', 0)}\n"

            # 顯示樣本資料
            sample_data = config.get('sample_data', [])
            if sample_data:
                formatted_info += "樣本資料:\n"
                for i, row in enumerate(sample_data[:3], 1):
                    formatted_info += f"  第{i}行: {dict(zip(config.get('headers', []), row))}\n"

            formatted_info += "---\n"

        return formatted_info

    def _extract_and_clean_jmx(self, response: str, context: GenerationContext) -> str:
        """
        提取、清理並智能修正 JMX 內容 - 重構後使用 GenerationContext。
        """
        self.logger.info("=== 步驟 4: 提取、清理與修正 JMX ===")
        jmx_content = self._extract_jmx_from_response(response)
        cleaned_content = self._clean_xml_declarations(jmx_content)
        fixed_content = self._fix_testplan_structure(cleaned_content)
        body_fixed_content = self._fix_body_data_format(fixed_content)

        # 【核心修改】智能修正 CSV 設定現在傳入 context
        csv_fixed_content = self._intelligently_fix_csv_settings(body_fixed_content, context)

        final_content = self._fix_basic_xml_issues(csv_fixed_content)
        return final_content

    def _intelligently_fix_csv_settings(self, jmx_content: str, context: GenerationContext) -> str:
        """
        智能校驗並修正 JMX 中的 CSV Data Set Config 設定。
        現在會強制將所有被用於參數化的 CSV 的 ignoreFirstLine 設為 true。
        """
        try:
            self.logger.info("====== 開始智能修正 CSV 設定 ======")

            # 找出所有在 context 中被用於參數化的 CSV 檔案名稱
            parameterized_csv_files = set()
            for tg in context.thread_groups:
                for req in tg.http_requests:
                    if req.is_parameterized:
                        for csv_conf in tg.csv_configs:
                            parameterized_csv_files.add(csv_conf.filename)

            if not parameterized_csv_files:
                self.logger.info("上下文中無參數化的 CSV 資訊，跳過修正。")
                return jmx_content

            self.logger.info(f"需要強制修正 ignoreFirstLine=true 的 CSV 檔案: {parameterized_csv_files}")

            csv_dataset_pattern = re.compile(r'(<CSVDataSet.*?>.*?</CSVDataSet>)', re.DOTALL)
            modified_content = jmx_content

            # 使用 re.sub 的 callback 函式來進行替換，更安全
            def replace_callback(match):
                csv_block = match.group(1)
                filename_match = re.search(r'<stringProp name="filename">(.*?)</stringProp>', csv_block)

                if filename_match:
                    csv_filename = filename_match.group(1)
                    # 如果這個 CSV 檔案在我們的待修正列表中
                    if csv_filename in parameterized_csv_files:
                        # 檢查並強制修正 ignoreFirstLine
                        if '<boolProp name="ignoreFirstLine">false</boolProp>' in csv_block:
                            self.logger.warning(f"偵測到邏輯矛盾！強制修正 '{csv_filename}' 的 ignoreFirstLine 為 true。")
                            return csv_block.replace(
                                '<boolProp name="ignoreFirstLine">false</boolProp>',
                                '<boolProp name="ignoreFirstLine">true</boolProp>'
                            )
                # 如果不需修改，返回原始區塊
                return csv_block

            modified_content = csv_dataset_pattern.sub(replace_callback, jmx_content)

            self.logger.info("====== 智能修正 CSV 設定結束 ======")
            return modified_content

        except Exception as e:
            self.logger.error(f"智能修正 CSV 設定時發生嚴重錯誤: {e}", exc_info=True)
            return jmx_content

    def _fix_testplan_structure(self, content: str) -> str:
        """修復 TestPlan 結構中的常見問題"""
        try:
            # 修復 TestPlan.user_define_classpath 的 elementType
            pattern = r'<elementProp name="TestPlan\.user_define_classpath" elementType="collectionProp">'
            replacement = '<elementProp name="TestPlan.user_define_classpath" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" enabled="true">'
            content = re.sub(pattern, replacement, content)

            # 修復對應的 collectionProp 結構
            pattern = r'<collectionProp name="TestPlan\.user_define_classpath"/>'
            replacement = '<collectionProp name="Arguments.arguments"/>'
            content = re.sub(pattern, replacement, content)

            self.logger.info("TestPlan 結構修復完成")
            return content

        except Exception as e:
            self.logger.error(f"修復 TestPlan 結構失敗: {e}")
            return content

    def _fix_body_data_format(self, content: str) -> str:
        """修正 HTTP Request Body Data 格式"""
        try:
            import re

            # 查找錯誤的 Body Data 格式
            wrong_pattern = r'<elementProp name="HTTPsampler\.BodyData" elementType="ElementProp">(.*?)</elementProp>'
            matches = re.findall(wrong_pattern, content, re.DOTALL)

            if not matches:
                self.logger.info("未發現需要修正的 Body Data 格式")
                return content

            self.logger.info(f"發現 {len(matches)} 個需要修正的 Body Data 格式")

            # 逐個修正每個錯誤格式
            fixed_content = content
            for i, match_content in enumerate(matches):
                # 提取 Body Data 的值
                value_pattern = r'<stringProp name="ElementProp\.value">(.*?)</stringProp>'
                value_matches = re.findall(value_pattern, match_content, re.DOTALL)

                if value_matches:
                    body_value = value_matches[0]

                    # 構建正確的格式
                    correct_format = f"""<boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
      <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
          <elementProp name="" elementType="HTTPArgument">
            <boolProp name="HTTPArgument.always_encode">false</boolProp>
            <stringProp name="Argument.value">{body_value}</stringProp>
            <stringProp name="Argument.metadata">=</stringProp>
          </elementProp>
        </collectionProp>
      </elementProp>"""

                    # 替換錯誤格式
                    wrong_full_pattern = r'<elementProp name="HTTPsampler\.BodyData" elementType="ElementProp">.*?</elementProp>'
                    fixed_content = re.sub(wrong_full_pattern, correct_format, fixed_content, count=1, flags=re.DOTALL)

                    self.logger.info(f"已修正第 {i + 1} 個 Body Data 格式")

            return fixed_content

        except Exception as e:
            self.logger.error(f"修正 Body Data 格式失敗: {e}")
            return content

    def _clean_xml_declarations(self, content: str) -> str:
        """統一的 XML 聲明清理方法"""
        if not content or not isinstance(content, str):
            return content

        try:
            # 方法1: 使用正則表達式移除所有 XML 聲明，然後添加單一聲明
            cleaned = re.sub(r'<\?xml[^>]*\?>\s*', '', content)
            result = '<?xml version="1.0" encoding="UTF-8"?>\n' + cleaned.lstrip()

            # 驗證結果
            xml_count = result.count('<?xml')
            if xml_count == 1:
                self.logger.info("XML 聲明清理成功 (正則表達式方法)")
                return result
            else:
                self.logger.warning(f"正則表達式方法失敗，XML 聲明數量: {xml_count}，嘗試行分割方法")

        except Exception as e:
            self.logger.warning(f"正則表達式清理失敗: {e}，嘗試行分割方法")

        try:
            # 方法2: 使用行分割方法 (原 _force_single_xml_declaration 的邏輯)
            lines = content.split('\n')
            content_lines = [line for line in lines if not line.strip().startswith('<?xml')]
            result = '<?xml version="1.0" encoding="UTF-8"?>\n' + '\n'.join(content_lines)

            # 再次驗證
            xml_count = result.count('<?xml')
            if xml_count == 1:
                self.logger.info("XML 聲明清理成功 (行分割方法)")
                return result
            else:
                self.logger.error(f"行分割方法也失敗，XML 聲明數量: {xml_count}")

        except Exception as e:
            self.logger.error(f"行分割清理也失敗: {e}")

        # 最後的備用方法：強制替換
        try:
            # 找到第一個非 XML 聲明的內容
            content_start = 0
            for i, char in enumerate(content):
                if content[i:i + 5] == '<?xml':
                    # 找到 XML 聲明的結束
                    end_pos = content.find('?>', i)
                    if end_pos != -1:
                        content_start = end_pos + 2
                        # 跳過空白字符
                        while content_start < len(content) and content[content_start].isspace():
                            content_start += 1
                    break
                elif not char.isspace():
                    break

            clean_content = content[content_start:] if content_start > 0 else content
            result = '<?xml version="1.0" encoding="UTF-8"?>\n' + clean_content

            self.logger.info("XML 聲明清理成功 (備用方法)")
            return result

        except Exception as e:
            self.logger.error(f"所有 XML 清理方法都失敗: {e}")
            return content

    def _fix_basic_xml_issues(self, content: str) -> str:
        """修復基本的 XML 問題（不破壞結構）"""
        try:
            # 只修復明顯的問題，不進行破壞性的轉義
            fixed = content

            # 修復未閉合的自閉合標籤（只針對特定標籤）
            self_closing_tags = ['collectionProp', 'stringProp', 'boolProp', 'intProp']
            for tag in self_closing_tags:
                # 修復未閉合的空標籤
                pattern = f'<{tag}([^>]*?)(?<!/)>\\s*</{tag}>'
                replacement = f'<{tag}\\1/>'
                fixed = re.sub(pattern, replacement, fixed)

            return fixed

        except Exception as e:
            self.logger.error(f"修復基本 XML 問題失敗: {e}")
            return content

    def _validate_jmx_content(self, content: str) -> bool:
        """驗證 JMX 內容"""
        if not content or not content.strip():
            return False

        try:
            # 檢查基本結構
            if not content.strip().startswith('<?xml'):
                return False

            if '</jmeterTestPlan>' not in content:
                return False

            # 檢查 XML 聲明數量
            if content.count('<?xml') != 1:
                return False

            # 嘗試解析 XML
            root = ET.fromstring(content)

            # 檢查根元素
            if root.tag != 'jmeterTestPlan':
                return False

            # 檢查是否有 TestPlan
            if root.find('.//TestPlan') is None:
                return False

            self.logger.info("JMX 內容驗證通過")
            return True

        except ET.ParseError as e:
            self.logger.error(f"XML 解析失敗: {e}")
            return False
        except Exception as e:
            self.logger.error(f"JMX 驗證失敗: {e}")
            return False

    def _get_fallback_jmx(self, requirements: str, processed_files: Dict = None) -> str:
        """獲取智能備用 JMX 模板"""

        # 從需求提取基本資訊
        test_name = self._extract_test_name_from_requirements(requirements)

        # 檢查是否需要多個 Thread Group
        thread_group_count = max(1, requirements.lower().count('thread group'))

        # 構建內容
        content_parts = []

        # 添加 Header Manager（如果需求中提到）
        if 'header manager' in requirements.lower() or 'content-type' in requirements.lower() or 'application/json' in requirements.lower():
            header_manager = """<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">
            <collectionProp name="HeaderManager.headers">
              <elementProp name="" elementType="Header">
                <stringProp name="Header.name">Content-Type</stringProp>
                <stringProp name="Header.value">application/json</stringProp>
              </elementProp>
            </collectionProp>
          </HeaderManager>
          <hashTree/>"""
            content_parts.append(header_manager)

        # 添加 Thread Groups
        for i in range(thread_group_count):
            thread_group_content = []

            # 添加 CSV Data Set Config（如果有 CSV 檔案）
            if processed_files and processed_files.get("csv_configs"):
                csv_configs = processed_files["csv_configs"]
                if i < len(csv_configs):
                    csv_config = csv_configs[i]
                    filename = csv_config.get('filename', f'data{i + 1}.csv')
                    variable_names = csv_config.get('variable_names', [])

                    csv_element = f"""<CSVDataSet guiclass="testBeanGUI" testclass="CSVDataSet" testname="CSV Data Set Config" enabled="true">
              <stringProp name="delimiter">,</stringProp>
              <stringProp name="fileEncoding">UTF-8</stringProp>
              <stringProp name="filename">{filename}</stringProp>
              <boolProp name="ignoreFirstLine">false</boolProp>
              <boolProp name="quotedData">false</boolProp>
              <boolProp name="recycle">true</boolProp>
              <stringProp name="shareMode">shareMode.all</stringProp>
              <boolProp name="stopThread">false</boolProp>
              <stringProp name="variableNames">{','.join(variable_names)}</stringProp>
            </CSVDataSet>
            <hashTree/>"""
                    thread_group_content.append(csv_element)

            # 構建 HTTP Request 的 Body Data
            json_body = ""
            if processed_files and processed_files.get("json_contents"):
                json_files = list(processed_files["json_contents"].items())
                if i < len(json_files):
                    filename, content = json_files[i]
                    json_body = content.get('raw_content', '') if content else ''

            # 如果沒有 JSON 內容，使用基本結構
            if not json_body:
                json_body = '''{
      "message": "test request",
      "data": {
        "param1": "${param1}",
        "param2": "${param2}"
      }
    }'''

            # XML 轉義處理
            escaped_json = json_body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"',
                                                                                                             '&quot;')

            # 構建 HTTP Request（使用正確的 Arguments 格式）
            http_request = f"""<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="HTTP Request {i + 1}" enabled="true">
            <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
            <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
              <collectionProp name="Arguments.arguments">
                <elementProp name="" elementType="HTTPArgument">
                  <boolProp name="HTTPArgument.always_encode">false</boolProp>
                  <stringProp name="Argument.value">{escaped_json}</stringProp>
                  <stringProp name="Argument.metadata">=</stringProp>
                </elementProp>
              </collectionProp>
            </elementProp>
            <stringProp name="HTTPSampler.domain">api.example.com</stringProp>
            <stringProp name="HTTPSampler.port"></stringProp>
            <stringProp name="HTTPSampler.protocol">https</stringProp>
            <stringProp name="HTTPSampler.contentEncoding">UTF-8</stringProp>
            <stringProp name="HTTPSampler.path">/api/test</stringProp>
            <stringProp name="HTTPSampler.method">{"POST" if json_body else "GET"}</stringProp>
            <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
            <boolProp name="HTTPSampler.auto_redirects">false</boolProp>
            <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
            <boolProp name="HTTPSampler.save_response_data">false</boolProp>
          </HTTPSamplerProxy>
          <hashTree/>"""
            thread_group_content.append(http_request)

            # 添加 Response Assertion（如果需求中提到）
            if 'response assertion' in requirements.lower() or 'assertion' in requirements.lower():
                response_assertion = """<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Response Assertion" enabled="true">
              <collectionProp name="Asserion.test_strings">
                <stringProp name="49586">200</stringProp>
              </collectionProp>
              <stringProp name="Assertion.custom_message"></stringProp>
              <stringProp name="Assertion.test_field">Assertion.response_code</stringProp>
              <boolProp name="Assertion.assume_success">false</boolProp>
              <intProp name="Assertion.test_type">2</intProp>
            </ResponseAssertion>
            <hashTree/>"""
                thread_group_content.append(response_assertion)

            # 構建完整的 Thread Group
            thread_group = self.jmx_templates["thread_group"].format(
                name=f"Thread Group {i + 1}",
                loops="${__P(loop,1)}",
                threads="${__P(threads,1)}",
                ramp_time="${__P(rampUp,1)}",
                scheduler="false",
                duration="",
                delay="",
                extra_content='\n        '.join(thread_group_content)
            )
            content_parts.append(thread_group)

        # 添加 View Results Tree（如果需求中提到）
        if 'view results tree' in requirements.lower() or 'results tree' in requirements.lower():
            listener = """<ResultCollector guiclass="ViewResultsFullVisualizer" testclass="ResultCollector" testname="View Results Tree" enabled="true">
            <boolProp name="ResultCollector.error_logging">false</boolProp>
            <objProp>
              <name>saveConfig</name>
              <value class="SampleSaveConfiguration">
                <time>true</time>
                <latency>true</latency>
                <timestamp>true</timestamp>
                <success>true</success>
                <label>true</label>
                <code>true</code>
                <message>true</message>
                <threadName>true</threadName>
                <dataType>true</dataType>
                <encoding>false</encoding>
                <assertions>true</assertions>
                <subresults>true</subresults>
                <responseData>false</responseData>
                <samplerData>false</samplerData>
                <xml>false</xml>
                <fieldNames>true</fieldNames>
                <responseHeaders>false</responseHeaders>
                <requestHeaders>false</requestHeaders>
                <responseDataOnError>false</responseDataOnError>
                <saveAssertionResultsFailureMessage>true</saveAssertionResultsFailureMessage>
                <assertionsResultsToSave>0</assertionsResultsToSave>
                <bytes>true</bytes>
                <sentBytes>true</sentBytes>
                <url>true</url>
                <threadCounts>true</threadCounts>
                <idleTime>true</idleTime>
                <connectTime>true</connectTime>
              </value>
            </objProp>
            <stringProp name="filename"></stringProp>
          </ResultCollector>
          <hashTree/>"""
            content_parts.append(listener)

        return self._create_jmx_from_template(
            test_name=test_name,
            comments=f"智能備用測試計劃 - 基於需求自動生成",
            content='\n      '.join(content_parts)
        )

    def _extract_test_name_from_requirements(self, requirements: str) -> str:
        """從需求中提取測試名稱"""
        if len(requirements) > 10:
            words = requirements.split()[:3]
            return " ".join(words) + " Test"
        return "Generated Test Plan"

    def _extract_jmx_from_response(self, response: str) -> str:
        """從模型響應中提取 JMX 內容"""
        if not response or not response.strip():
            raise ValueError("模型響應為空")

        try:
            # 清理響應內容
            cleaned_response = response.strip()

            # 處理轉義的 XML 內容
            unescaped = cleaned_response.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')

            # 嘗試提取 XML 內容的模式
            patterns = [
                r'<\?xml.*?</jmeterTestPlan>',
                r'<jmeterTestPlan.*?</jmeterTestPlan>',
                r'```xml\s*(.*?)\s*```',
                r'```\s*(.*?)\s*```'
            ]

            for pattern in patterns:
                matches = re.findall(pattern, unescaped, re.DOTALL | re.IGNORECASE)
                if matches:
                    content = matches[0].strip()

                    # 確保內容以 <?xml 開頭
                    if not content.startswith('<?xml') and '<jmeterTestPlan' in content:
                        content = '<?xml version="1.0" encoding="UTF-8"?>\n' + content

                    # 基本驗證
                    if self._basic_xml_check(content):
                        return content

            raise ValueError("無法從響應中提取有效的 JMX 內容")

        except Exception as e:
            self.logger.error(f"提取 JMX 內容失敗: {e}")
            raise

    def _basic_xml_check(self, content: str) -> bool:
        """基本的 XML 檢查"""
        try:
            return (content.strip().startswith('<?xml') or content.strip().startswith('<jmeterTestPlan')) and \
                '</jmeterTestPlan>' in content and \
                len(content) > 100
        except:
            return False

    def _process_csv_files(self, files_data: List[Dict]) -> Dict[str, Dict]:
        """
        處理所有上傳的 CSV 檔案。
        此函數迭代所有傳入的檔案資料，篩選出 CSV 檔案，
        並呼叫 _safe_process_single_csv 進行單一檔案的解析。
        Args:
            files_data: 一個檔案字典的列表，每個字典代表一個上傳的檔案。

        Returns:
            一個字典，其中：
            - Key 是 CSV 檔案的名稱 (e.g., "MOCK-B-CHECKIDC001.csv")。
            - Value 是 _safe_process_single_csv 返回的詳細資訊字典。
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

        此函數使用標準的 `io` 和 `csv` 模組，將檔案內容字串轉換為
        結構化的資訊，包括標頭、資料行數和原始內容。

        Args:
            file_info: 代表單一檔案的字典。

        Returns:
            一個包含 CSV 詳細資訊的字典，如果處理失敗則返回 None。
            成功時返回的字典結構：
            {
                'headers': List[str],      # 清理過的標頭列表
                'sample_data': List[List[str]], # 最多 5 行的樣本資料
                'total_rows': int,         # 資料行的總數 (不含標頭)
                'filepath': str,           # 檔案路徑/名稱
                'raw_content': str         # 未經修改的原始檔案內容字串
            }
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
                f"✅ CSV 解析成功: '{filename}' -> 標頭: {cleaned_headers}, 資料行數: {total_data_rows}"
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

    def _clean_csv_header(self, header: str) -> str:
        """清理 CSV 標頭"""
        if not header or str(header).lower() in ['nan', 'null', 'none', '']:
            return ''
        return str(header).strip().strip('"').strip("'")

    def _clean_csv_value(self, value: str) -> str:
        """清理 CSV 值"""
        if not value or str(value).lower() in ['nan', 'null', 'none', '']:
            return ''

        try:
            float_val = float(value)
            if math.isnan(float_val) or math.isinf(float_val):
                return ''
        except (ValueError, TypeError):
            pass

        return str(value).strip().strip('"').strip("'")

    def _process_json_files(self, files_data: List[Dict]) -> Dict:
        """處理 JSON 檔案"""
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
        """安全地處理單一 JSON 檔案 - 增強版"""
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
                        self.logger.info(f"✅ 使用策略 '{strategy_name}' 成功獲取內容，長度: {len(content)}")
                        break
                except Exception as e:
                    self.logger.warning(f"策略 '{strategy_name}' 失敗: {e}")

            if not content:
                self.logger.error(f"所有內容提取策略都失敗，檔案資訊: {file_info}")
                return None

            # 🎯 確保是有效的JSON格式
            self.logger.info(f"原始內容前100字符: {content[:100]}")

            # 嘗試解析 JSON
            parsed_json = None
            try:
                parsed_json = json.loads(content)
                self.logger.info(f"✅ JSON 解析成功")
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
        """從data字段提取內容"""
        if not data:
            return None

        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False, indent=2)
        elif isinstance(data, str):
            return data
        else:
            return str(data)

    def _clean_json_values(self, obj):
        """清理 JSON 物件中的問題值"""
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
        """從 JSON 中提取變數名稱"""
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
        """驗證 XML 內容的有效性

        Args:
            xml_content: 要驗證的 XML 內容

        Returns:
            Tuple[bool, str]: (是否有效, 驗證訊息)
        """
        try:
            # 檢查內容是否為空
            if not xml_content or not xml_content.strip():
                return False, "XML 內容為空"

            # 檢查基本格式
            content = xml_content.strip()

            # 檢查 XML 聲明是否存在
            if not content.startswith('<?xml'):
                return False, "缺少 XML 聲明"

            # 檢查 XML 聲明數量，確保只有一個
            xml_count = content.count('<?xml')
            if xml_count > 1:
                return False, f"發現多個 XML 聲明 ({xml_count} 個)"

            # 檢查基本結構，確保有根元素
            if '<jmeterTestPlan' not in content:
                return False, "缺少 jmeterTestPlan 根元素"

            if '</jmeterTestPlan>' not in content:
                return False, "缺少 jmeterTestPlan 結束標籤"

            # 嘗試解析 XML 內容
            root = ET.fromstring(content)

            # 檢查根元素是否正確
            if root.tag != 'jmeterTestPlan':
                return False, f"根元素應為 jmeterTestPlan，實際為 {root.tag}"

            # 檢查必要屬性是否存在
            if 'version' not in root.attrib:
                return False, "jmeterTestPlan 缺少 version 屬性"

            # 檢查 TestPlan 元素是否存在
            test_plan = root.find('.//TestPlan')
            if test_plan is None:
                return False, "找不到 TestPlan 元素"

            # 檢查 TestPlan.user_define_classpath 的結構是否正確
            classpath_prop = test_plan.find('./elementProp[@name="TestPlan.user_define_classpath"]')
            if classpath_prop is not None:
                if classpath_prop.get('elementType') != 'Arguments':
                    return False, "TestPlan.user_define_classpath 的 elementType 應為 Arguments"
                collection_prop = classpath_prop.find('./collectionProp[@name="Arguments.arguments"]')
                if collection_prop is None:
                    return False, "TestPlan.user_define_classpath 中缺少正確的 collectionProp 結構"

            # 檢查 hashTree 結構是否存在
            hash_trees = root.findall('.//hashTree')
            if not hash_trees:
                return False, "缺少 hashTree 結構"

            # 檢查 hashTree 標籤是否成對出現
            open_tags = content.count('<hashTree>')
            close_tags = content.count('</hashTree>')
            if open_tags != close_tags:
                return False, f"hashTree 標籤不匹配 (開始: {open_tags}, 結束: {close_tags})"

            # 檢查常見的 JMeter 組件是否存在，作為附加資訊
            components_found = []
            if root.find('.//ThreadGroup') is not None:
                components_found.append("ThreadGroup")
            if root.find('.//HTTPSamplerProxy') is not None or root.find('.//HTTPSampler') is not None:
                components_found.append("HTTP Sampler")
            if root.find('.//ResponseAssertion') is not None:
                components_found.append("Response Assertion")

            # 構建驗證訊息
            validation_message = "XML 結構有效"
            if components_found:
                validation_message += f"，包含組件: {', '.join(components_found)}"
            else:
                validation_message += "，但未發現測試組件"

            return True, validation_message

        except ET.ParseError as e:
            self.logger.error(f"XML 解析失敗: {e}")
            return False, f"XML 解析錯誤: {str(e)}"
        except Exception as e:
            self.logger.error(f"XML 驗證時發生錯誤: {e}")
            return False, f"驗證過程發生錯誤: {str(e)}"

    def _parameterize_json_body(self, json_body: str, csv_info: CsvInfo) -> str:
        """
        安全地將 JSON Body 參數化。
        1. 優先策略：如果 JSON 的 key 與 CSV 的變數名匹配，直接替換。
        2. 備用策略：如果 key 不匹配，則嘗試匹配 value (舊有邏輯)，以處理特殊情況。
        """
        self.logger.info(f"🚀 開始使用【智慧型雙重策略】參數化 JSON，來源 CSV: '{csv_info.filename}'")

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
                self.logger.info(f"✅ JSON Body 參數化成功！已替換的欄位: {unique_replacements}")
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