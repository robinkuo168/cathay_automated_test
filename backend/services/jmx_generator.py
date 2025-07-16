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
from xml.dom import minidom
import xml.sax.saxutils as saxutils
from xml.sax.saxutils import escape as saxutils_escape
from dataclasses import asdict
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
        """
        載入所有必要的、結構正確的 JMX 組件模板。
        """
        self.logger.info("正在載入所有 JMX 組件模板...")
        templates = {
            "xml_header": """<?xml version="1.0" encoding="UTF-8"?>""",
            "test_plan_structure": """<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
      <hashTree>
        <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="{test_name}" enabled="true">
          <stringProp name="TestPlan.comments">{comments}</stringProp>
          <boolProp name="TestPlan.functional_mode">false</boolProp>
          <boolProp name="TestPlan.tearDown_on_shutdown">{tear_down_on_shutdown}</boolProp>
          <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
          <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
        </TestPlan>
        <hashTree>
          {content}
        </hashTree>
      </hashTree>
    </jmeterTestPlan>""",
            "thread_group": """<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{name}" enabled="true">
            <stringProp name="ThreadGroup.on_sample_error">{on_sample_error}</stringProp>
            <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">
              <stringProp name="LoopController.loops">{loops}</stringProp>
              <boolProp name="LoopController.continue_forever">false</boolProp>
            </elementProp>
            <stringProp name="ThreadGroup.num_threads">{num_threads}</stringProp>
            <stringProp name="ThreadGroup.ramp_time">{ramp_time}</stringProp>
            <boolProp name="ThreadGroup.scheduler">{scheduler}</boolProp>
            <stringProp name="ThreadGroup.duration">{duration}</stringProp>
            <stringProp name="ThreadGroup.delay"></stringProp>
            <boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>
          </ThreadGroup>
          <hashTree>
            {content}
          </hashTree>""",
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
              <stringProp name="HTTPSampler.path">{path}</stringProp>
              <stringProp name="HTTPSampler.method">{method}</stringProp>
              <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
              <boolProp name="HTTPSampler.auto_redirects">false</boolProp>
              <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
              <boolProp name="HTTPSampler.DO_MULTIPART_POST">false</boolProp>
              <stringProp name="HTTPSampler.concurrentPool">6</stringProp>
            </HTTPSamplerProxy>""",
            "csv_data_set_config": """<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="CSV Data Set Config" enabled="true">
              <stringProp name="delimiter">{delimiter}</stringProp>
              <stringProp name="fileEncoding">UTF-8</stringProp>
              <stringProp name="filename">{filename}</stringProp>
              <boolProp name="ignoreFirstLine">{ignore_first_line}</boolProp>
              <boolProp name="quotedData">{allow_quoted_data}</boolProp>
              <boolProp name="recycle">{recycle}</boolProp>
              <stringProp name="shareMode">{share_mode}</stringProp>
              <boolProp name="stopThread">{stop_thread}</boolProp>
              <stringProp name="variableNames">{variable_names}</stringProp>
            </CSVDataSet>
            <hashTree/>""",
            "http_defaults": """<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">
            <elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" enabled="true">
              <collectionProp name="Arguments.arguments"/>
            </elementProp>
            <stringProp name="HTTPSampler.domain">{domain}</stringProp>
            <stringProp name="HTTPSampler.protocol">{protocol}</stringProp>
            <stringProp name="HTTPSampler.port">{port}</stringProp>
            <stringProp name="HTTPSampler.contentEncoding">{content_encoding}</stringProp>
            <stringProp name="HTTPSampler.path">{path}</stringProp>
            <stringProp name="HTTPSampler.connect_timeout">{connect_timeout}</stringProp>
            <stringProp name="HTTPSampler.response_timeout">{response_timeout}</stringProp>
          </ConfigTestElement>
          <hashTree/>""",
            "header_manager": """<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">
            <collectionProp name="HeaderManager.headers">
              {headers}
            </collectionProp>
          </HeaderManager>
          <hashTree/>""",
            "header_element": """<elementProp name="" elementType="Header">
                <stringProp name="Header.name">{name}</stringProp>
                <stringProp name="Header.value">{value}</stringProp>
              </elementProp>""",
            "random_variable_config": """<RandomVariableConfig guiclass="TestBeanGUI" testclass="RandomVariableConfig" testname="{name}" enabled="true">
                <stringProp name="maximumValue">{max_value}</stringProp>
                <stringProp name="minimumValue">{min_value}</stringProp>
                <stringProp name="outputFormat">{output_format}</stringProp>
                <boolProp name="perThread">{per_thread}</boolProp>
                <stringProp name="randomSeed"></stringProp>
                <stringProp name="variableName">{variable_name}</stringProp>
            </RandomVariableConfig>
            <hashTree/>""",
            "response_assertion": """<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="{name}" enabled="true">
                <collectionProp name="Asserion.test_strings">
                  {patterns_to_test}
                </collectionProp>
                <stringProp name="Assertion.custom_message"></stringProp>
                <stringProp name="Assertion.test_field">Assertion.response_data</stringProp>
                <boolProp name="Assertion.assume_success">false</boolProp>
                <intProp name="Assertion.test_type">{test_type}</intProp>
            </ResponseAssertion>""",
            "assertion_pattern": """<stringProp name="{hash_code}">{pattern}</stringProp>""",
            "result_collector": """<ResultCollector guiclass="ViewResultsFullVisualizer" testclass="ResultCollector" testname="{name}" enabled="true">
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
        }
        self.logger.info(f"✅ 所有 {len(templates)} 個 JMX 模板載入完成。")
        return templates

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
        生成 JMX 檔案（採用新流程）
        """
        try:
            context = self._prepare_generation_context(requirements, files_data)
            self.logger.info(f"✅ 生成上下文準備完成，測試計畫: '{context.test_plan_name}'")
            # 【除錯建議】如果您需要檢查 context 內容，可以在這裡加入日誌
            # self.logger.info(f"DEBUG CONTEXT: {context}")
        except ValueError as e:
            self.logger.error(f"❌ 輸入資料準備失敗: {e}")
            raise e

        validation_errors = []
        for attempt in range(max_retries):
            try:
                self.logger.info(f"🚀 開始第 {attempt + 1}/{max_retries} 次內容生成嘗試...")
                prompt = self._build_content_generation_prompt(context, attempt, validation_errors)
                response = self.llm_service.generate_text(prompt=prompt)
                test_plan_data = self._parse_llm_content_response(response)
                jmx_content = self._assemble_jmx_from_structured_data(test_plan_data, context)
                is_valid, message = self.validate_xml(jmx_content)
                if not is_valid:
                    validation_errors.append(f"組裝後的 JMX 結構無效: {message}")
                    self.logger.warning(f"第 {attempt + 1} 次嘗試 - JMX 組裝驗證失敗: {message}")
                    continue

                self.logger.info(f"✅ 第 {attempt + 1} 次生成與組裝成功！")
                return jmx_content

            except (json.JSONDecodeError, ValueError) as e:
                self.logger.error(f"第 {attempt + 1} 次嘗試 - 解析 LLM 回應失敗: {e}", exc_info=True)
                validation_errors.append(f"LLM 回應的 JSON 格式錯誤: {str(e)}")
            except Exception as e:
                self.logger.error(f"第 {attempt + 1} 次生成過程中發生異常: {e}", exc_info=True)
                validation_errors.append(f"執行異常: {str(e)}")

        self.logger.error("所有重試均告失敗。")
        raise Exception("無法生成有效的 JMX 檔案，已達最大重試次數。")

    def _build_content_generation_prompt(self, context: 'GenerationContext', attempt: int,
                                         validation_errors: list[str]) -> str:
        """
        【⭐ 最終修正版】建立一個高度結構化、嚴格約束的提示詞。
        - 提供精確的藍圖 (Blueprint)，指導 LLM 進行填空而非創作。
        - 明確禁止硬式編碼，強制使用來自 context 的變數和結構。
        - 提供與目標參考檔案完全一致的範例，以獲得最精確的結果。
        """
        self.logger.info(f"=== 步驟 2 (通用流程): 建立內容生成提示詞 (第 {attempt + 1} 次嘗試) ===")

        # --- 1. 建立一個高度精確的 JSON 結構指南 (藍圖)，與參考 JMX 檔案對齊 ---
        json_structure_guide = {
            "test_plan_name": context.test_plan_name,
            "tear_down_on_shutdown": True,
            "global_user_defined_variables": [],
            "global_headers": [
                # 【關鍵修正】修正 Content-Type，使其與參考檔案完全一致
                {"name": "Content-Type", "value": "application/json; charset=UTF-8"},
                {"name": "x-cub-it-key", "value": "zgnf1hJIZVxtIxfjLl2a0T9vl5f98o9b"}
            ],
            "http_defaults": {
                # 【關鍵修正】直接提供正確的 domain，引導 LLM
                "domain": "msp-gw-rest-overtest.apps.epaas.cathayuat.intra.uwccb",
                "protocol": "https",
                "path": "/rest",
                "connect_timeout": 5000,
                "response_timeout": 5000
            },
            "random_variables": [{
                "name": "TXNSEQ",
                "variable_name": "TXNSEQ",
                "output_format": "",
                "min_value": "00000000",
                "max_value": "99999999",
                "per_thread": False
            }],
            "thread_groups": []
        }

        # --- 2. 為每個 Thread Group 建立精確的子結構 ---
        for tg_context in context.thread_groups:
            tg_template = {
                "name": tg_context.name,
                "on_sample_error": "continue",
                # 【關鍵修正】使用與參考檔案一致的參數名稱和值
                "num_threads": "${__P(threadsMIU,3)}",
                "ramp_time": "${__P(rampUp,1)}",
                "loops": "${__P(loop,-1)}",
                "scheduler": True,
                "duration": "${__P(duration,5)}",
                "csv_data_configs": [],
                "http_requests": []
            }

            if tg_context.csv_configs:
                for csv in tg_context.csv_configs:
                    tg_template["csv_data_configs"].append({
                        "filename": csv.filename,
                        "variable_names": ",".join(csv.variable_names),
                        "delimiter": ",",
                        "ignore_first_line": True
                    })

            if tg_context.http_requests:
                for req in tg_context.http_requests:
                    # 【關鍵修正】提供與參考檔案完全一致的斷言範例
                    assertion_returncode = {
                        "name": "Response Assertion-Return code",
                        "test_field": "Assertion.response_data",
                        # 34 = Substring | Or (檢查回應中是否包含任一字串)
                        "test_type": 34,
                        "patterns": ["\"RETURNCODE\":\"0000\"", "\"RETURNCODE\":\"E009\""]
                    }
                    assertion_txseq = {
                        "name": "Response Assertion-TXNSEQ",
                        "test_field": "Assertion.response_data",
                        # 2 = Contains (檢查回應中是否包含變數值)
                        "test_type": 2,
                        "patterns": ["${TXNSEQ}"]
                    }

                    req_template = {
                        "name": req.name,
                        "path": "/rest",
                        "method": "POST",
                        "body": json.loads(req.json_body) if req.json_body and req.json_body.strip() else {},
                        "post_processors": [],
                        "assertions": [assertion_returncode, assertion_txseq]
                    }
                    tg_template["http_requests"].append(req_template)
            json_structure_guide["thread_groups"].append(tg_template)

        context_as_json_string = json.dumps(asdict(context), indent=2, ensure_ascii=False)

        # --- 3. 建立最終的提示詞 ---
        prompt = f"""You are a precise JMeter test script architect. Your task is to populate a given JSON structure. You MUST follow all instructions meticulously.

    === YOUR TASK ===
    Based on the "STRUCTURED DATA TO USE" and "ORIGINAL REQUIREMENTS", populate the "JSON STRUCTURE GUIDE" below. You MUST NOT invent or change values unless a field is marked as "FILL_IN_...".

    === STRUCTURED DATA TO USE (from initial analysis) ===
    {context_as_json_string}

    === ORIGINAL REQUIREMENTS ===
    {context.requirements}

    === JSON STRUCTURE GUIDE & INSTRUCTIONS (YOUR BLUEPRINT) ===
    This is your blueprint. Fill it out exactly as specified.
    - **CRITICAL**: The `body` for `http_requests` is already provided and parameterized. You MUST use it AS-IS. DO NOT modify it.
    - **CRITICAL**: For fields with `${{__P(...)}}` syntax (like `num_threads`), you MUST use the provided string verbatim.
    - **CRITICAL**: For `assertions`, use the provided examples as a template. The `test_type` MUST be an integer.
    - **FORBIDDEN**: DO NOT hardcode any values in the request `body` or `assertions` that look like test data (e.g., "164783213", "ZXZTEST-123456"). All dynamic data MUST be represented as `${{variable_name}}`.
    - If a component like `global_user_defined_variables` is not needed, you MUST use an empty list `[]`.

    ```json
    {json.dumps(json_structure_guide, indent=2, ensure_ascii=False)}
    ```

    === 🔥 FINAL, NON-NEGOTIABLE INSTRUCTIONS 🔥 ===
    Your ONLY output is a single, complete, and valid JSON object based on the guide above.
    Start with {{{{ and end with }}}}.
    DO NOT include any explanations, comments, or markdown code blocks like ```json.
    """
        if attempt > 0 and validation_errors:
            error_summary = "; ".join(list(set(validation_errors))[-3:])
            prompt += f"\n\n🚨 RETRY ATTEMPT #{attempt + 1}. YOUR PREVIOUS RESPONSE FAILED. REASON: {error_summary}. YOU MUST FIX THIS AND ADHERE STRICTLY TO THE BLUEPRINT."

        return prompt

    def _parse_llm_content_response(self, response: str) -> Dict:
        """
        解析 LLM 返回的 JSON 內容，具備自動修復能力。
        """
        self.logger.info("--- 步驟 3 (新流程): 解析 LLM 的 JSON 回應 ---")
        match = re.search(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if match:
            json_str = match.group(1)
            self.logger.info("✅ 成功從 markdown 區塊中提取 JSON。")
        else:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if not match:
                self.logger.error(f"在 LLM 回應中找不到任何 JSON 物件。回應內容: {response[:500]}...")
                raise ValueError("在 LLM 回應中找不到有效的 JSON 物件。")
            json_str = match.group(0)
            self.logger.info("從原始回應中提取了 JSON 字串，現在嘗試解析...")

        cleaned_json_str = json_str.strip()
        try:
            return json.loads(cleaned_json_str)
        except json.JSONDecodeError as e:
            self.logger.warning(f"標準 JSON 解析失敗 ({e})，嘗試自動修復...")
            try:
                import ast
                return ast.literal_eval(cleaned_json_str)
            except Exception as ast_e:
                self.logger.error(f"自動修復失敗 ({ast_e})，JSON 字串格式嚴重錯誤。")
                raise e from None

    def _prepare_generation_context(self, requirements: str, files_data: List[Dict]) -> GenerationContext:
        """
        【⭐ 最終修正版】
        預處理函數：將原始輸入轉換為結構化的 GenerationContext，確保附件與執行緒群組正確綁定。
        """
        self.logger.info("=== 步驟 1: 開始準備生成上下文 (採用健壯參數化流程) ===")
        processed_files = self._safe_process_files(files_data)
        req_analysis = self._analyze_requirements_dynamically(requirements)

        if not req_analysis.get('thread_groups'):
            raise ValueError("需求分析失敗：無法從需求中解析出任何 Thread Group 名稱。")

        thread_group_contexts = []

        # 建立已使用檔案的追蹤器，避免檔案被重複分配
        used_json_files = set()
        used_csv_files = set()

        # 按名稱排序以確保匹配的穩定性
        all_json_files = sorted(req_analysis['json_files'])
        all_csv_files = sorted(req_analysis['csv_files'])

        for tg_name in sorted(req_analysis['thread_groups']):
            self.logger.info(f"🔄 --- 正在處理 Thread Group: '{tg_name}' ---")

            # 1. 為當前 Thread Group 尋找最匹配的檔案
            # 策略：優先尋找檔名包含 Thread Group 名稱的檔案，其次按順序分配未使用的檔案

            json_filename = next((f for f in all_json_files if tg_name in f and f not in used_json_files), None)
            if not json_filename:
                json_filename = next((f for f in all_json_files if f not in used_json_files), None)

            csv_filename = next((f for f in all_csv_files if tg_name in f and f not in used_csv_files), None)
            if not csv_filename:
                csv_filename = next((f for f in all_csv_files if f not in used_csv_files), None)

            if json_filename: used_json_files.add(json_filename)
            if csv_filename: used_csv_files.add(csv_filename)

            http_req_name = next((r for r in req_analysis['http_requests'] if r == tg_name), tg_name)
            self.logger.info(f"為 '{tg_name}' 匹配到的檔案 -> JSON: '{json_filename}', CSV: '{csv_filename}'")

            # 2. 獲取檔案內容
            json_info = processed_files['json_contents'].get(json_filename) if json_filename else None
            original_json_body = json_info.get('raw_content') if json_info else None
            csv_config_data = next(
                (c for c in processed_files.get('csv_configs', []) if c.get('filename') == csv_filename),
                None) if csv_filename else None

            # 3. 執行參數化
            final_json_body, csv_info_obj, is_parameterized = original_json_body, None, False
            if original_json_body and csv_config_data:
                self.logger.info(f"✅ 為 '{tg_name}' 找到匹配的 JSON/CSV，開始參數化。")
                csv_info_obj = CsvInfo(
                    filename=csv_filename,
                    variable_names=csv_config_data.get('variable_names', []),
                    total_rows=csv_config_data.get('total_rows', 0),
                    raw_content=csv_config_data.get('raw_content')
                )
                final_json_body = self._parameterize_json_body(original_json_body, csv_info_obj)
                is_parameterized = True
            else:
                self.logger.warning(f"⚠️ 為 '{tg_name}' 未能找到完整的 JSON/CSV 配對，將跳過參數化。")

            # 4. 建立結構化物件
            http_req_info = HttpRequestInfo(name=http_req_name, json_body=final_json_body,
                                            source_json_filename=json_filename, is_parameterized=is_parameterized)
            tg_context = ThreadGroupContext(name=tg_name)
            tg_context.http_requests.append(http_req_info)
            if csv_info_obj:
                tg_context.csv_configs.append(csv_info_obj)
            thread_group_contexts.append(tg_context)
            self.logger.info(f"✅ --- Thread Group '{tg_name}' 處理完成 ---")

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
        """
        【⭐ 最終修正版 v2】動態分析需求，提取關鍵資訊。
        - 放寬了 Test Plan 名稱的提取規則。
        """
        analysis = {
            'test_plan_name': '',
            'thread_groups': [],
            'http_requests': [],
            'json_files': [],
            'csv_files': []
        }

        # ⭐【關鍵修正】使用更寬鬆、更穩健的正則表達式來提取 Test Plan 名稱。
        # 這可以匹配 "在『測試計畫』中，名稱..." 等更多樣的句式。
        match = re.search(r'測試計畫.*?名稱.*?[『「]([^』」]+)[』」]', requirements)
        if match:
            analysis['test_plan_name'] = match.group(1).strip()
        else:
            # 如果找不到，可以設置一個預設值或記錄警告
            self.logger.warning("在需求文件中未能提取到 '測試計畫名稱'，將使用預設值。")
            analysis['test_plan_name'] = 'Generated Test Plan'

        # 提取所有符合特定模式的名稱
        potential_names = re.findall(r'\b[A-Z]{2,}[-C][-A-Z0-9]+\b', requirements)

        # 分類 Thread Group 和 HTTP Request
        tg_lines = [line for line in requirements.split('\n') if 'thread group' in line.lower() or '執行緒群組' in line]
        for line in tg_lines:
            names_in_line = re.findall(r'\b[A-Z]{2,}[-C][-A-Z0-9]+\b', line)
            analysis['thread_groups'].extend(names_in_line)

        http_lines = [line for line in requirements.split('\n') if
                      'http request' in line.lower() or 'http 請求' in line]
        for line in http_lines:
            names_in_line = re.findall(r'\b[A-Z]{2,}[-C][-A-Z0-9]+\b', line)
            analysis['http_requests'].extend(names_in_line)

        # 如果按行分類失敗，則將所有找到的名稱都視為兩者
        if not analysis['thread_groups']: analysis['thread_groups'] = potential_names
        if not analysis['http_requests']: analysis['http_requests'] = potential_names

        # 提取附件檔名
        analysis['json_files'] = re.findall(r'([A-Z0-9_-]+\.json)', requirements, re.IGNORECASE)
        analysis['csv_files'] = re.findall(r'([A-Z0-9_-]+\.csv)', requirements, re.IGNORECASE)

        # 清理和去重
        for key in analysis:
            if isinstance(analysis[key], list):
                analysis[key] = sorted(list(set(analysis[key])))

        self.logger.info(f"需求分析結果 (v2): {analysis}")
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
            - DO NOT include any explanations, comments, or markdown code blocks like ```xml.
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
        """
        驗證最終生成的 JMX 字串是否為有效的 XML。
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
            self.logger.info("✅ XML 結構驗證通過。")
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

    def _assemble_jmx_from_structured_data(self, test_plan_data: Dict, context: 'GenerationContext') -> str:
        """
        【⭐ 最終修正版】JMX 組裝器：從結構化資料組裝 JMX 檔案。
        - 強制覆寫關鍵參數 (timeout, perThread, ignoreFirstLine, CSV path, same_user_on_next_iteration) 以確保正確性。
        - 修正了元件查找邏輯，確保能正確處理多個 Thread Group，避免生成重複元件。
        """
        self.logger.info("=== 步驟 4 (通用流程): 開始從結構化資料組裝 JMX (採用嚴格控制模式) ===")
        test_plan_components = []

        # --- 1. 組裝全域元件 ---

        # 組裝 Header Manager
        global_headers_data = test_plan_data.get("global_headers", [])
        if global_headers_data:
            headers_xml_parts = [
                self.jmx_templates["header_element"].format(
                    name=saxutils_escape(h.get("name", "")),
                    value=saxutils_escape(h.get("value", ""))
                ) for h in global_headers_data if h and h.get("name")
            ]
            if headers_xml_parts:
                test_plan_components.append(
                    self.jmx_templates["header_manager"].format(headers="\n              ".join(headers_xml_parts))
                )
                self.logger.info(f"  -> ✅ 已組裝 {len(headers_xml_parts)} 個全域 HTTP 標頭。")

        # 組裝 HTTP Defaults
        http_defaults_data = test_plan_data.get("http_defaults", {})
        if http_defaults_data and http_defaults_data.get("domain"):
            # 【關鍵修正】強制使用正確的 timeout 語法，確保生成 ${__P(...)}
            connect_timeout_str = f"${{__P(connTimeOut,{http_defaults_data.get('connect_timeout', 5000)})}}"
            response_timeout_str = f"${{__P(respTimeOut,{http_defaults_data.get('response_timeout', 5000)})}}"

            http_defaults_xml = self.jmx_templates["http_defaults"].format(
                domain=http_defaults_data.get("domain", ""),
                protocol=http_defaults_data.get("protocol", "https"),
                port=http_defaults_data.get("port", ""),
                content_encoding="UTF-8",
                path=http_defaults_data.get("path", ""),
                connect_timeout=connect_timeout_str,
                response_timeout=response_timeout_str
            )
            test_plan_components.append(http_defaults_xml)
            self.logger.info(f"  -> ✅ 已組裝 HTTP Defaults，目標為: {http_defaults_data.get('domain')}")

        # 組裝 Random Variables
        random_variables_data = test_plan_data.get("random_variables", [])
        if random_variables_data:
            for rv_data in random_variables_data:
                if rv_data and rv_data.get("variable_name"):
                    rv_xml = self.jmx_templates["random_variable_config"].format(
                        name=saxutils_escape(rv_data.get("name", "Random Variable")),
                        variable_name=saxutils_escape(rv_data.get("variable_name", "")),
                        output_format=rv_data.get("output_format", ""),
                        min_value=rv_data.get("min_value", "1"),
                        max_value=rv_data.get("max_value", "99999999"),
                        # 【關鍵修正】強制覆寫 per_thread 為 'false'，以符合參考檔案的全域唯一邏輯
                        per_thread="false"
                    )
                    test_plan_components.append(rv_xml)
                    self.logger.info(f"  -> ✅ 已組裝隨機變數: '{rv_data.get('name')}' (強制 perThread=false)")

        # --- 2. 迭代組裝執行緒群組 (修正了結構性問題) ---
        all_llm_tgs = {tg.get("name"): tg for tg in test_plan_data.get("thread_groups", []) if tg.get("name")}

        for tg_context in context.thread_groups:
            tg_name = tg_context.name
            self.logger.info(f"🔄 正在組裝 Thread Group: '{tg_name}'")

            tg_data_from_llm = all_llm_tgs.get(tg_name, {})
            if not tg_data_from_llm:
                self.logger.warning(f"在 LLM 回應中找不到名為 '{tg_name}' 的 Thread Group 資料，將使用預設值。")

            thread_group_children = []

            # 組裝 CSV Data Set Config
            if tg_context.csv_configs:
                for csv_info in tg_context.csv_configs:
                    # 【關鍵修正】強制覆寫關鍵 CSV 參數，不再信任 LLM
                    csv_xml = self.jmx_templates["csv_data_set_config"].format(
                        filename=f"..\\TestData\\{csv_info.filename}",
                        variable_names=",".join(csv_info.variable_names),
                        delimiter=",",
                        ignore_first_line="true",  # 強制為 true
                        allow_quoted_data="false", recycle="true", stop_thread="false", share_mode="shareMode.all"
                    )
                    thread_group_children.append(csv_xml)
                    self.logger.info(
                        f"  -> ✅ 已為 '{tg_name}' 組裝 CSV: {csv_info.filename} (強制設定路徑和 ignoreFirstLine)")

            # 組裝 HTTP Requests 及其子元件
            if tg_context.http_requests:
                all_llm_reqs = {req.get("name"): req for req in tg_data_from_llm.get("http_requests", []) if
                                req.get("name")}
                for http_req_info in tg_context.http_requests:
                    req_name = http_req_info.name
                    req_data_from_llm = all_llm_reqs.get(req_name, {})
                    if not req_data_from_llm:
                        self.logger.warning(f"在 Thread Group '{tg_name}' 中找不到請求 '{req_name}' 的資料，將跳過。")
                        continue

                    sampler_children = []
                    # 組裝 Assertions
                    assertions_xml = self._assemble_assertions(req_data_from_llm.get("assertions", []))
                    if assertions_xml:
                        sampler_children.append(assertions_xml)
                        self.logger.info(f"    -> ✅ 已為 '{req_name}' 組裝 Response Assertions。")

                    # 組裝 HTTP Request XML 本身
                    body_content = http_req_info.json_body or "{}"
                    escaped_body = saxutils_escape(body_content)
                    http_request_xml = self.jmx_templates["http_request_with_body"].format(
                        name=saxutils_escape(req_name),
                        path=saxutils_escape(req_data_from_llm.get("path", "/rest")),
                        method=req_data_from_llm.get("method", "POST"),
                        body_data=escaped_body
                    )
                    self.logger.info(f"  -> ✅ 已組裝 HTTP Request: '{req_name}'")

                    # 組合 Sampler 和其所有子元件
                    if sampler_children:
                        sampler_children_xml = "\n              ".join(sampler_children)
                        full_sampler_xml = f"{http_request_xml}\n            <hashTree>\n              {sampler_children_xml}\n            </hashTree>"
                        thread_group_children.append(full_sampler_xml)
                    else:
                        thread_group_children.append(f"{http_request_xml}\n            <hashTree/>")

            # 組裝 Thread Group 本身
            # 【關鍵修正】強制覆寫 same_user_on_next_iteration 為 false，與參考檔案一致
            modified_tg_template = self.jmx_templates["thread_group"].replace(
                '<boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>',
                '<boolProp name="ThreadGroup.same_user_on_next_iteration">false</boolProp>'
            )

            tg_content_xml = "\n            ".join(thread_group_children)
            thread_group_xml = modified_tg_template.format(
                name=saxutils_escape(tg_name),
                on_sample_error=tg_data_from_llm.get("on_sample_error", "continue"),
                loops=tg_data_from_llm.get("loops", "${__P(loop,-1)}"),
                num_threads=tg_data_from_llm.get("num_threads", "${__P(threads,3)}"),
                ramp_time=tg_data_from_llm.get("ramp_time", "${__P(rampUp,1)}"),
                scheduler=str(tg_data_from_llm.get("scheduler", "true")).lower(),
                duration=tg_data_from_llm.get("duration", "${__P(duration,10)}"),
                content=tg_content_xml
            )
            test_plan_components.append(thread_group_xml)

        # --- 3. 組裝全域 Listeners (預設加入 View Results Tree) ---
        test_plan_components.append(
            self.jmx_templates["result_collector"].format(name="View Results Tree")
        )
        self.logger.info("  -> ✅ 已預設加入 'View Results Tree' Listener。")

        # --- 4. 組裝最終的 Test Plan ---
        final_content_xml = "\n          ".join(test_plan_components)
        final_jmx = self.jmx_templates["test_plan_structure"].format(
            test_name=saxutils_escape(context.test_plan_name),
            comments="Generated by a universal JMXGeneratorService.",
            tear_down_on_shutdown=str(test_plan_data.get("tear_down_on_shutdown", "true")).lower(),
            content=final_content_xml
        )

        self.logger.info("✅ JMX 通用組裝完成！")
        return self.jmx_templates["xml_header"] + "\n" + final_jmx

    def _assemble_assertions(self, assertions_data: List[Dict]) -> str:
        """
        根據結構化資料組裝一或多個 Response Assertion 的 XML 字串。
        """
        if not assertions_data:
            return ""

        all_assertions_xml_parts = []
        for assertion_details in assertions_data:
            if not assertion_details or not assertion_details.get("name"):
                continue

            patterns_list = assertion_details.get("patterns", [])
            if not patterns_list:
                continue

            patterns_to_test_xml_parts = [
                self.jmx_templates["assertion_pattern"].format(
                    hash_code=str(hash(p)),
                    pattern=saxutils_escape(p)
                ) for p in patterns_list if p
            ]

            if not patterns_to_test_xml_parts:
                continue

            full_assertion_xml = self.jmx_templates["response_assertion"].format(
                name=saxutils_escape(assertion_details.get("name", "Response Assertion")),
                patterns_to_test="\n                  ".join(patterns_to_test_xml_parts),
                test_field=assertion_details.get("test_field", "Assertion.response_data"),
                test_type=assertion_details.get("test_type", 2)
            )
            all_assertions_xml_parts.append(f"{full_assertion_xml}\n              <hashTree/>")

        return "\n              ".join(all_assertions_xml_parts)

    def _find_http_request_info_in_context(self, context: GenerationContext, name: str) -> Optional[HttpRequestInfo]:
        """根據請求名稱從 GenerationContext 中安全地查找 HttpRequestInfo。"""
        for tg_context in context.thread_groups:
            for req_info in tg_context.http_requests:
                if req_info.name == name:
                    return req_info
        return None

    def _find_req_data_by_name(self, test_plan_data: Dict, name: str) -> Dict:
        """輔助函式：根據名稱從 LLM 的輸出中查找對應的請求資料。"""
        for tg in test_plan_data.get("thread_groups", []):
            if tg.get("http_request", {}).get("name") == name:
                return tg["http_request"]
        self.logger.warning(f"在 LLM 輸出中未找到名為 '{name}' 的請求資料，將返回空字典。")
        return {}