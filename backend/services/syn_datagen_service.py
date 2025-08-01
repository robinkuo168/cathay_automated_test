import os
from typing import Dict, Optional, List, Any, Union
from .llm_service import LLMService
from .logger import get_logger
import re
import textwrap
import asyncio
from dotenv import load_dotenv
import json
import uuid
import random
import string
from datetime import datetime
from Crypto.Cipher import DES3
from Crypto.Util.Padding import pad
import binascii
from faker import Faker

load_dotenv()

class SynDataGenService:
    def __init__(self, llm_service: Optional[LLMService] = None, model_name: str = "default"):
        """
        初始化 SynDataGenService (合成資料生成服務)。

        此建構函式會設定服務所需的依賴項，例如日誌記錄器、LLM 服務 (延遲載入)、
        最大生成筆數限制，以及在 LLM 無法從文件中提取加密參數時所使用的備援金鑰 (fallback key)。
        :param llm_service: (可選) 一個 LLMService 實例。
        :param model_name: 要使用的底層 LLM 服務名稱。
        """
        self.logger = get_logger(__name__)
        self._llm_service = llm_service
        self._model_name = model_name
        self.max_text_length = 8000

        try:
            self.max_rows = int(os.getenv('MAX_SYNC_ROWS', '100'))
            self.logger.info(f"最大合成資料生成筆數設定為: {self.max_rows}")
        except ValueError:
            self.logger.warning("MAX_SYNC_ROWS 環境變數設定無效，使用預設值 100")
            self.max_rows = 100

        # 從環境變數讀取預設的 Key/IV，用於 LLM 無法從文件中提取時
        default_key = os.getenv('DES3_KEY', 'ThisIsAValidByteTestKey!')
        default_iv = os.getenv('DES3_IV', '12345678')

        if len(default_key.encode('utf-8')) not in [16, 24]:
            self.logger.warning(f"備援 DES3_KEY 長度不符，可能導致加密失敗。")
        if len(default_iv.encode('utf-8')) != 8:
            self.logger.warning(f"備援 DES3_IV 長度不符，可能導致加密失敗。")

        self.fallback_des3_key = default_key
        self.fallback_des3_iv = default_iv

        self.logger.info("備援用的 3DES 加密金鑰和 IV 已載入。")

    @property
    def llm_service(self) -> LLMService:
        """
        一個延遲載入 (lazy-loading) 的屬性，用於獲取 LLMService 實例。

        它確保 LLMService 只在第一次被需要時才進行初始化，
        避免了不必要的資源消耗和啟動延遲。
        :return: 一個 LLMService 的實例。
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

    def _extract_first_json_object(self, text: str) -> Optional[str]:
        """
        一個健壯的工具函式，用於從可能包含額外文本的字串中，提取第一個完整的 JSON 物件或陣列。

        此函式特別設計用來處理 LLM 可能返回的不完美輸出，例如在 JSON 後面還有結束語，
        或括號不匹配的情況。
        :param text: 來自 LLM 的原始回應字串。
        :return: 一個包含 JSON 物件或陣列的字串，如果找不到則返回 None。
        """

        try:
            match = re.search(r'\{.*\}|\[.*\]', text, re.DOTALL)
            if not match:
                return None

            potential_json = match.group(0).strip()
            start_char = potential_json[0]
            end_char = '}' if start_char == '{' else ']'
            open_braces = 0

            # 手動遍歷以找到第一個匹配的閉合括號，避免因後續無關文本導致解析失敗
            for i, char in enumerate(potential_json):
                if char == start_char:
                    open_braces += 1
                elif char == end_char:
                    open_braces -= 1
                if open_braces == 0:
                    return potential_json[:i + 1]

            return None  # 括號不匹配
        except Exception as e:
            self.logger.error(f"提取 JSON 物件/陣列時發生錯誤: {e}")
            return None

    async def generate_header_json_from_doc(self, text: str, filename: str = "unknown") -> Optional[Union[Dict, List[Dict]]]:
        """
        從文件中穩健地提取所有「上行／請求電文範例」的 JSON 物件。

        此函式能自動處理單一或多個 JSON 範例的情況：
        - 如果找到多個範例，它會將它們以一個 JSON 陣列 (list of dicts) 的形式返回。
        - 如果只找到一個範例，它會直接返回該 JSON 物件 (dict)。
        - 如果找不到任何有效範例，則返回一個空列表 `[]`。
        :param text: 包含 API 規格的文件內容。
        :param filename: 正在處理的文件名稱，用於日誌記錄。
        :return: 一個單一的 JSON 物件 (dict)、一個 JSON 物件列表 (list[dict])、一個空列表 `[]`，如果失敗則返回 None。
        """
        self.logger.info(f"開始為檔案 '{filename}' 提取所有請求 JSON 範例 (Model: {self._model_name})...")

        # 【優化】強化 Prompt，明確要求物件陣列，並定義找不到時的行為
        prompt = textwrap.dedent(f"""
            [INST]<<SYS>>
            You are a meticulous data extraction specialist. Your task is to find ALL upstream request JSON examples within a specific, well-defined section of a document.
            **Your instructions are a precise, step-by-step process:**
            1.  **STARTING POINT:** First, locate the exact heading "上行／請求電文範例". This is where your search begins.
            2.  **SCANNING:** Scan the text immediately following this heading. You will find multiple distinct JSON objects. They are often preceded by descriptive text.
            3.  **STOPPING POINT:** Continue scanning and extracting ALL JSON objects until you encounter the next major heading, "下行／回應電文規格". This heading marks the end of the relevant section. **Do NOT extract any JSON from or after this stopping point.**
            4.  **COLLECTION:** Gather every JSON object you found between the starting point and the stopping point.
            5.  **OUTPUT FORMAT:** Combine all the collected JSON objects into a single, valid JSON array `[...]`. The array must contain only JSON objects (e.g., {{"key": "value"}}). Even if you only find one, it must be inside an array. **If you find no valid JSON examples, return an empty array `[]`.**
            6.  **FINAL RESPONSE:** Your response MUST ONLY be the raw JSON array. Do not add any explanations, comments, or markdown fences (like ```json).
            <</SYS>>
            **DOCUMENT TEXT TO ANALYZE:**
            ---
            {text[:self.max_text_length]}
            ---
            **CORRECTED JSON ARRAY:**
            [/INST]
        """)

        try:
            llm_output = await asyncio.to_thread(self.llm_service.generate_text, prompt)
            self.logger.debug(f"LLM 原始輸出 for '{filename}':\n---\n{llm_output}\n---")

            json_string = self._extract_first_json_object(llm_output)
            if not json_string:
                self.logger.warning(f"LLM 未能為檔案 '{filename}' 返回可解析的 JSON 陣列結構。")
                return None

            parsed_data = json.loads(json_string)

            # 【優化】首先檢查回傳的是否為列表
            if not isinstance(parsed_data, list):
                self.logger.warning(
                    f"LLM 未能返回有效的 JSON 陣列，而是返回了 {type(parsed_data)} 型別。檔案: '{filename}'")
                return None

            # 【核心修改】過濾掉陣列中非字典的元素，這能直接解決您遇到的問題
            valid_examples = [item for item in parsed_data if isinstance(item, dict)]

            if len(valid_examples) < len(parsed_data):
                # 記錄下被過濾掉的無效元素，方便追蹤 LLM 的行為
                invalid_items = [item for item in parsed_data if not isinstance(item, dict)]
                self.logger.warning(
                    f"從 LLM 返回的陣列中過濾掉了 {len(invalid_items)} 個非 JSON 物件的元素。 "
                    f"無效內容: {str(invalid_items)[:500]}. 檔案: '{filename}'"
                )

            # 【優化】根據過濾後的有效範例數量進行判斷
            num_valid_examples = len(valid_examples)
            self.logger.info(f"成功為檔案 '{filename}' 提取並解析了 {num_valid_examples} 個有效範例。")

            if num_valid_examples == 0:
                # 情況一：找不到任何有效範例，或 LLM 返回空陣列
                self.logger.info(f"在檔案 '{filename}' 中未找到有效範例，將返回空陣列。")
                return []  # 返回空陣列，而不是 None，讓呼叫端可以明確處理

            elif num_valid_examples == 1:
                # 情況二：只有一個有效範例，直接回傳該物件 (dict)
                self.logger.info("檢測到單一有效範例，將直接返回 JSON 物件。")
                return valid_examples[0]

            else:  # num_valid_examples > 1
                # 情況三：有多個有效範例，回傳整個列表 (list of dicts)
                self.logger.info(f"檢測到 {num_valid_examples} 個有效範例，將返回 JSON 物件陣列。")
                return valid_examples

        except json.JSONDecodeError as e:
            self.logger.error(f"為檔案 '{filename}' 提取的 JSON 陣列格式錯誤或無效: {e}", exc_info=True)
            return None
        except Exception as e:
            self.logger.error(f"為檔案 '{filename}' 提取請求 JSON 時發生未知錯誤: {e}", exc_info=True)
            return None

    async def generate_body_markdown_from_doc(self, text: str, filename: str = "unknown") -> Optional[str]:
        """
        使用 LLM 從文件內容中，尋找並轉換「上行／請求電文規格」表格為 Markdown 格式。

        :param text: 包含 API 規格的文件內容。
        :param filename: 正在處理的文件名稱，用於日誌記錄。
        :return: 一個包含 Markdown 表格的字串，如果失敗則返回 None。
        """
        self.logger.info(f"開始為檔案 {filename} 生成 Body Markdown (LLM-First)...")
        try:
            prompt = textwrap.dedent(f"""
                [INST]<<SYS>>
                You are an expert document table converter. Your task is to find a specific table in a document and convert it to a Markdown table.
                **RULES:**
                1.  Analyze the entire document text to find the table under the section "API Body" -> "Upstream Request Specification" (API Body -> 上行／請求電文規格).
                2.  Convert ONLY this specific table into a clean Markdown format.
                3.  The Markdown columns MUST be: LVL, 欄位名稱, 資料型態, 最大長度, 必要, 欄位名稱及說明.
                4.  Your entire response must be ONLY the raw Markdown table.
                5.  **CRITICAL:** Do NOT output your reasoning process, introductions, or any text other than the final Markdown table. Your response must start with `|`.
                <</SYS>>
                **DOCUMENT TEXT:**
                ---
                {text[:self.max_text_length]}
                ---
                [/INST]
            """)

            markdown_output = await asyncio.to_thread(self.llm_service.generate_text, prompt)

            cleaned_markdown = markdown_output.replace("```markdown", "").replace("```", "").strip()

            # 進行一個更嚴格的檢查，確保回傳的是一個有效的表格
            if not cleaned_markdown or not cleaned_markdown.startswith('|') or len(cleaned_markdown.split('\n')) < 2:
                self.logger.warning(
                    f"LLM 未能為 {filename} 生成有效的 Body Markdown 表格。原始輸出: '{markdown_output}'")
                return None

            self.logger.info(f"成功為檔案 {filename} 生成 Body Markdown。")
            return cleaned_markdown
        except Exception as e:
            self.logger.error(f"為檔案 {filename} 生成 Body Markdown 時發生錯誤: {e}", exc_info=True)
            return None

    async def review_markdown_with_llm(self, markdown: str, user_input: str, filename: str = "unknown") -> Dict:
        """
        根據使用者輸入，使用 LLM 校對和修改 Body 規格的 Markdown 表格。

        :param markdown: 當前的 Markdown 表格字串。
        :param user_input: 使用者的修改指令。
        :param filename: 正在處理的文件名稱，用於日誌記錄。
        :return: 一個包含校對後 Markdown 和確認狀態的字典。
        """
        self.logger.info(f"開始為檔案 {filename} 校對 Markdown 表格")
        try:
            prompt = textwrap.dedent(f"""
                You are an AI assistant that modifies Markdown tables. Return a complete, updated Markdown table based on the user's request.
                **RULES:**
                1. DO NOT include any explanations or comments.
                2. Your entire response MUST be ONLY the Markdown table.
                3. The response MUST start with a `|` character.
                ---
                **### CURRENT MARKDOWN TABLE ###**
                ```markdown
                {markdown}
                ```
                ---
                **### USER REQUEST ###**
                {user_input}
                ---
                **### UPDATED MARKDOWN TABLE ###**
            """)
            output = await asyncio.to_thread(self.llm_service.generate_text, prompt)
            content_to_parse = output.strip()
            code_block_match = re.search(r"```(?:markdown)?\s*\n(.*?)\n\s*```", content_to_parse, re.DOTALL)
            if code_block_match:
                content_to_parse = code_block_match.group(1).strip()

            table_match = re.search(r'(\|.*\|(\n\|.*\|)*)', content_to_parse, re.DOTALL)
            new_markdown = table_match.group(0).strip() if table_match else content_to_parse

            confirmed = new_markdown.strip() == markdown.strip()
            result = {
                "filename": filename, "type": "markdown_review",
                "data": {"markdown": new_markdown, "confirmed": confirmed},
                "content": new_markdown[:1000], "size": len(new_markdown)
            }
            self.logger.info(f"檔案 {filename} 的 Markdown 表格校對完成，確認狀態: {confirmed}")
            return result
        except Exception as e:
            self.logger.error(f"為檔案 {filename} 校對 Markdown 表格失敗: {str(e)}", exc_info=True)
            return {"filename": filename, "type": "markdown_review", "error": str(e), "data": None, "content": ""}

    async def review_header_json_with_llm(self, header_markdown: str, user_input: str,
                                          filename: str = "unknown") -> Dict:
        """
        根據使用者輸入，使用 LLM 校對和修改包含 Header JSON 範例的 Markdown 字串。

        :param header_markdown: 當前包含一或多個請求 JSON 範例的 Markdown 字串。
        :param user_input: 使用者的修改指令 (例如 "請將所有範例中的 'version' 欄位值改為 '2.0'")。
        :param filename: 正在處理的文件名稱，用於日誌記錄。
        :return: 一個包含校對後 Markdown 和確認狀態的字典。
        """
        self.logger.info(f"開始為檔案 '{filename}' 校對 Header JSON...")
        try:
            # Prompt 的設計參考了 review_markdown_with_llm，但針對 JSON 修改進行了優化
            prompt = textwrap.dedent(f"""
                You are an AI assistant that meticulously modifies JSON data presented within Markdown code blocks.
                Your task is to update the provided JSON examples based on the user's request, while preserving the original Markdown structure.

                **RULES:**
                1.  Your response MUST be ONLY the complete, updated Markdown content.
                2.  Preserve the original structure, including `###` titles and ```json code fences.
                3.  If the user's request is general (e.g., "update the date"), apply it to ALL JSON examples found.
                4.  DO NOT include any explanations, comments, or any text outside of the final Markdown content. Your response must start with `###` or ```json.

                ---
                **### CURRENT HEADER JSON (in Markdown format) ###**
                ```markdown
                {header_markdown}
                ```
                ---
                **### USER REQUEST ###**
                {user_input}
                ---
                **### UPDATED HEADER JSON (in Markdown format) ###**
            """)

            output = await asyncio.to_thread(self.llm_service.generate_text, prompt)
            content_to_parse = output.strip()

            # 與 review_markdown_with_llm 相同的穩健性處理：移除 LLM 可能添加的外層 markdown 標籤
            code_block_match = re.search(r"```(?:markdown)?\s*\n([\s\S]*?)\n\s*```", content_to_parse, re.DOTALL)
            if code_block_match:
                new_header_markdown = code_block_match.group(1).strip()
            else:
                new_header_markdown = content_to_parse

            # 比較修改前後的內容是否相同
            confirmed = new_header_markdown.strip() == header_markdown.strip()

            result = {
                "filename": filename,
                "type": "header_json_review",  # 使用新的 type 來區分
                "data": {"header_markdown": new_header_markdown, "confirmed": confirmed},
                "content": new_header_markdown[:1000],  # 提供預覽內容
                "size": len(new_header_markdown)
            }
            self.logger.info(f"檔案 '{filename}' 的 Header JSON 校對完成，確認狀態: {confirmed}")
            return result

        except Exception as e:
            self.logger.error(f"為檔案 '{filename}' 校對 Header JSON 失敗: {str(e)}", exc_info=True)
            return {
                "filename": filename,
                "type": "header_json_review",
                "error": str(e),
                "data": None,
                "content": ""
            }

    async def review_synthetic_data_with_llm(self, synthetic_markdown: str, user_input: str,
                                             filename: str = "synthetic_review") -> Dict:
        """
        根據使用者輸入，使用 LLM 校對和修改已生成的合成資料 Markdown 表格。

        此函式在 LLM 更新完 Markdown 表格後，會自動呼叫內部工具函式，
        將更新後的 Markdown 重新轉換為對應的 CSV 格式內容。
        :param synthetic_markdown: 當前的合成資料 Markdown 表格。
        :param user_input: 使用者的修改指令。
        :param filename: 正在處理的文件名稱，用於日誌記錄。
        :return: 一個包含校對後 Markdown 和 CSV 內容的字典。
        """
        self.logger.info(f"開始為檔案 '{filename}' 校對合成資料...")
        try:
            prompt = textwrap.dedent(f"""
                [INST]<<SYS>>
                You are an AI assistant that meticulously modifies data presented in a Markdown table.
                Your task is to update the provided data table based on the user's request.

                **RULES:**
                1.  Your response MUST be ONLY the complete, updated Markdown table.
                2.  Preserve the original table structure and headers.
                3.  Apply the user's request to all relevant rows if applicable.
                4.  DO NOT include any explanations, comments, or any text other than the final Markdown table. Your response must start with a `|` character.
                <</SYS>>

                ---
                **### CURRENT DATA TABLE (Markdown) ###**
                ```markdown
                {synthetic_markdown}
                ```
                ---
                **### USER REQUEST ###**
                {user_input}
                ---
                **### UPDATED DATA TABLE (Markdown) ###**
                [/INST]
            """)

            # 呼叫 LLM 生成新的 Markdown 表格
            updated_markdown = await asyncio.to_thread(self.llm_service.generate_text, prompt)

            # 清理 LLM 可能回傳的程式碼區塊標記
            cleaned_markdown = updated_markdown.replace("<|end_of_text|>", "") \
                .replace("```markdown", "") \
                .replace("```", "") \
                .strip()

            if not cleaned_markdown.startswith('|'):
                raise ValueError("LLM 返回的格式不正確，未以 Markdown 表格開頭。")

            # 【重要】根據更新後的 Markdown 重新生成 CSV
            updated_csv = self._convert_markdown_to_csv(cleaned_markdown)

            self.logger.info(f"檔案 '{filename}' 的合成資料校對完成。")
            return {
                "success": True,
                "data": {
                    "synthetic_data_markdown": cleaned_markdown,
                    "synthetic_data_csv": updated_csv
                }
            }

        except Exception as e:
            self.logger.error(f"為檔案 '{filename}' 校對合成資料失敗: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def generate_data_from_markdown(self, body_markdown: str, header_json_markdown: str, full_doc_text: str,
                                          context_id: str, num_records: int) -> dict:
        """
        合成資料生成的總指揮，支援多個請求範例。

        此函式協調整個生成流程，能處理多個請求範例，並將生成任務分配給它們，以產生更多樣化的資料。
        核心步驟包括：
        1. 提取加密參數。
        2. 解析所有 Header JSON 範例。
        3. 迭代所有範例，為每個範例呼叫 LLM 生成一批資料。
        4. 對需要加密的欄位進行後處理。
        5. 將所有生成的資料打包成統一的 Markdown 和 CSV 格式。
        :param body_markdown: Body 規格的 Markdown 表格。
        :param header_json_markdown: 包含一或多個 Header JSON 範例的字串。
        :param full_doc_text: 完整的原始文件內容，用於提取加密參數等上下文。
        :param context_id: 用於日誌記錄的上下文 ID (通常是檔名)。
        :param num_records: 要生成的總資料筆數。
        :return: 一個包含操作結果 (`success`, `data` 或 `error`) 的字典。
        """
        self.logger.info(f"開始為上下文 '{context_id}' 生成 {num_records} 筆合成資料 (多範例模式)...")
        try:
            # 步驟 1: 決定加密參數 (邏輯不變)
            self.logger.info("正在嘗試從文件中提取加密參數...")
            final_encryption_params = {"key": self.fallback_des3_key, "iv": self.fallback_des3_iv}
            extracted_params = await self._extract_encryption_params_with_llm(full_doc_text)
            if self._validate_encryption_params(extracted_params):
                final_encryption_params = extracted_params
            else:
                self.logger.warning("將使用預設的備援加密參數。")

            # 步驟 2: 解析並驗證所有 Header JSON 範例
            valid_examples = []
            try:
                if not header_json_markdown or not header_json_markdown.strip():
                    raise ValueError("傳入的 Header JSON 字串為空。")

                # 此處的 parsed_header 可能是 dict 或 list[dict]
                parsed_header = json.loads(header_json_markdown)
                if isinstance(parsed_header, list):
                    if not parsed_header:
                        raise ValueError("傳入的 Header JSON 陣列為空。")
                    # 過濾確保陣列中都是字典
                    valid_examples = [item for item in parsed_header if isinstance(item, dict)]
                elif isinstance(parsed_header, dict):
                    valid_examples = [parsed_header]

                if not valid_examples:
                    raise ValueError("在 Header JSON 中找不到任何有效的物件範例。")

                self.logger.info(f"成功解析 {len(valid_examples)} 個有效的請求範例。")

            except (json.JSONDecodeError, TypeError, ValueError) as e:
                self.logger.error(f"解析 Header JSON 時失敗: {e}", exc_info=True)
                return {"success": False, "error": f"Header (JSON) 內容無效或格式不符: {e}"}

            # 步驟 3: 迭代所有範例，分配任務並生成資料
            all_generated_records = []
            num_examples = len(valid_examples)

            for i, request_example_dict in enumerate(valid_examples):
                # 分配每個範例要生成的筆數
                records_for_this_example = num_records // num_examples
                if i < num_records % num_examples:
                    records_for_this_example += 1

                if records_for_this_example == 0:
                    continue

                self.logger.info(f"正在使用第 {i + 1}/{num_examples} 個範例生成 {records_for_this_example} 筆資料...")

                # 為當前範例提取 Body 物件
                body_key, body_object = self._extract_request_body_from_example(request_example_dict)
                if not body_key or not body_object:
                    self.logger.warning(f"第 {i + 1} 個範例無法識別出 Body 物件，將跳過。")
                    continue
                body_example_json_str = json.dumps(body_object, indent=2, ensure_ascii=False)

                # 掃描 Markdown，找出需要加密的欄位
                encrypted_fields = self._find_encrypted_fields(body_markdown)

                # 呼叫 LLM 生成半成品資料
                generated_records_batch = await self._batch_generate_creative_data_with_llm(
                    body_markdown=body_markdown,
                    body_example_json=body_example_json_str,
                    num_records=records_for_this_example,
                    encrypted_fields=encrypted_fields
                )
                if not generated_records_batch:
                    self.logger.warning(f"第 {i + 1} 個範例未能生成任何資料。")
                    continue

                # 後處理加密，並將結果加入總列表
                # `generated_records_batch` 已經是 Python 字典的列表，直接迭代處理即可。
                for idx, record_dict in enumerate(generated_records_batch):
                    try:
                        # 【修復】增加一道防護，確保列表中的元素確實是字典
                        if not isinstance(record_dict, dict):
                            self.logger.warning(
                                f"範例 {i + 1} 的第 {idx + 1} 筆生成資料不是有效的物件(字典)，將跳過。內容: {str(record_dict)[:200]}")
                            continue

                        # 【修復】直接使用 record_dict，不再需要 json.loads()
                        processed_record = self._process_encryption_placeholders(record_dict, final_encryption_params)

                        # 將拍平後的資料加入總列表，並傳入該批次的 body_key
                        all_generated_records.append(self._flatten_dict(processed_record, parent_key=body_key))

                    except Exception as e:
                        # 將 record_dict 轉為字串再切片，避免對字典切片引發錯誤
                        self.logger.error(
                            f"處理範例 {i + 1} 的第 {idx + 1} 筆生成資料時發生未知錯誤: {e}。內容: {str(record_dict)[:200]}",
                            exc_info=True)
                        continue

            if not all_generated_records:
                return {"success": False, "error": "所有範例均未能成功生成資料。"}

            # 步驟 4: 將最終的、結構可能不同的資料打包成統一的 Markdown 和 CSV
            self.logger.info(f"總共生成 {len(all_generated_records)} 筆資料，正在打包成表格...")
            markdown_table = self._convert_flattened_data_to_markdown(all_generated_records)
            csv_content = self._convert_markdown_to_csv(markdown_table)

            self.logger.info(f"成功為上下文 '{context_id}' 生成並打包混合模型資料。")
            return {"success": True, "data": {"markdown_content": markdown_table, "csv_content": csv_content}}

        except Exception as e:
            self.logger.error(f"為上下文 '{context_id}' 生成合成資料失敗: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _batch_generate_creative_data_with_llm(self, body_markdown: str, body_example_json: str, num_records: int,
                                                     encrypted_fields: List[str] = None) -> List[Dict]:
        """
        一個內部輔助函式，呼叫 LLM 來批次生成具有創造性的合成資料。

        此函式採用「混合模型」策略：它指導 LLM 生成所有欄位的真實感資料，
        但對於需要加密的欄位，它會要求 LLM 生成「未加密的原始資料」並用特殊格式標記，
        將實際的加密操作留給後續步驟處理。
        :param body_markdown: Body 規格的 Markdown 表格，作為 LLM 的規則參考。
        :param body_example_json: 單一請求 Body 的 JSON 範例，作為 LLM 的結構參考。
        :param num_records: 要生成的資料筆數。
        :param encrypted_fields: 需要進行特殊處理的加密欄位列表。
        :return: 一個包含多筆生成資料的字典列表。
        """
        self.logger.info(f"正在請求 LLM (混合模型) 生成 {num_records} 筆資料...")

        # 根據是否存在加密欄位，動態構建 Prompt 的一部分
        encryption_instructions = ""
        if encrypted_fields:
            field_list_str = ", ".join([f"`{f}`" for f in encrypted_fields])
            encryption_instructions = textwrap.dedent(f"""
            **SPECIAL INSTRUCTION FOR ENCRYPTED FIELDS:**
            - The following fields require special handling: {field_list_str}.
            - For these fields, DO NOT attempt to generate the final encrypted value.
            - Instead, you MUST generate the **original, unencrypted source data** (e.g., for `CustomerId`, generate a realistic ID number).
            - You MUST format the output for these specific fields as a JSON object with a single key `_unencrypted_source_`.
            - Example for `CustomerId`: `{{"_unencrypted_source_": "A123456789"}}`. My system will handle the actual encryption later.
            """)

        prompt = textwrap.dedent(f"""
            [INST]<<SYS>>
            You are a world-class synthetic data generation expert. Your task is to create multiple, unique, and highly realistic data records based on a structural example and a set of detailed rules.

            **CRITICAL RULES:**
            1.  **Generate Exactly `{num_records}` Records:** Create a JSON array containing exactly `{num_records}` unique data objects.
            2.  **Adhere to Structure:** The structure of each object you generate MUST strictly follow the `JSON STRUCTURE BLUEPRINT`.
            3.  **Follow Field Rules:** For each field, you MUST meticulously follow the description, data type, and constraints specified in the `MARKDOWN FIELD RULES`.
            4.  **Be Creative and Diverse:** Do not repeat the same values across different records.
            5.  **Final Output Format:** Your entire response MUST be ONLY the raw JSON array `[...]`. Do not include any explanations, comments, or markdown fences.

            {encryption_instructions}
            <</SYS>>

            ---
            **### SOURCE 1: JSON STRUCTURE BLUEPRINT (Follow this structure) ###**
            ```json
            {body_example_json}
            ```
            ---
            **### SOURCE 2: MARKDOWN FIELD RULES (Follow these descriptions) ###**
            ```markdown
            {body_markdown}
            ```
            ---
            Please generate `{num_records}` unique data records now. Remember to follow all instructions, especially the special handling for encrypted fields if specified.
            [/INST]
        """)

        try:
            llm_output = await asyncio.to_thread(self.llm_service.generate_text, prompt)
            json_string = self._extract_first_json_object(llm_output)

            if not json_string:
                raise ValueError("LLM failed to generate valid JSON array output.")

            data = json.loads(json_string)
            if not isinstance(data, list):
                return [data] if isinstance(data, dict) else []

            self.logger.info(f"LLM 成功生成 {len(data)} 筆半成品資料。")
            return data
        except Exception as e:
            self.logger.error(f"從 LLM 批次生成資料時出錯: {e}", exc_info=True)
            raise

    def _convert_markdown_to_csv(self, markdown_text: str) -> str:
        """
        一個工具函式，用於將 Markdown 表格字串轉換為 CSV 格式的字串。

        :param markdown_text: 包含 Markdown 表格的字串。
        :return: 一個 CSV 格式的字串。
        """
        if not markdown_text: return ""
        lines = markdown_text.strip().split('\n')
        lines = [line for line in lines if not re.match(r'^\s*\|?[-|:\s]+$', line)]
        csv_rows = []
        for line in lines:
            clean_line = line.strip()
            if clean_line.startswith('|'): clean_line = clean_line[1:]
            if clean_line.endswith('|'): clean_line = clean_line[:-1]
            cells = [cell.strip() for cell in clean_line.split('|')]
            csv_rows.append(",".join([f'"{cell}"' for cell in cells]))
        return "\n".join(csv_rows)

    async def _extract_encryption_params_with_llm(self, full_doc_text: str) -> Optional[Dict]:
        """
        一個內部輔助函式，使用 LLM 從文件全文中提取 3DES 加密的金鑰 (Key) 和初始向量 (IV)。

        此函式使用的提示詞經過優化，能適應文件中章節編號不一致和關鍵字多樣性的情況。
        :param full_doc_text: 文件的完整文字內容。
        :return: 一個包含 'key' 和 'iv' 的字典，如果找不到則返回 None。
        """
        self.logger.info("正在請求 LLM (適應性) 提取加密參數 (Key/IV)...")

        # 【Prompt 強化】引導 LLM 尋找多個可能的章節標題和關鍵字
        prompt = textwrap.dedent(f"""
            [INST]<<SYS>>
            You are a highly-skilled data extraction specialist. Your task is to find the 3DES encryption `key` and `iv` from the provided document text. The document may have inconsistent section numbering.

            **### INSTRUCTIONS ###**
            1.  Carefully search the document for a section related to encryption. Look for headings like **"1.1.8 資料加密規則"**, **"1.1.7 資料加密規則"**, or simply **"資料加密規則"**.
            2.  Within that section, find the exact values for the **3DES KEY** and **3DES IV**. They might be labeled with keywords such as "KEY", "金鑰", "IV", or "初始向量".
            3.  The key is often a string containing the service name. The IV is often a date or a short number sequence.
            4.  Format your response as a single, clean JSON object.
            5.  **CRITICAL: If you cannot find the explicit values for BOTH key and iv, you MUST return an empty JSON object `{{}}`. Do not guess, invent, or return partial values.**

            **### EXAMPLE ###**
            **Input Text Snippet:**
            "...
            1.1.7 資料加密規則
            本交易內文加密方式採 3DES CBC Mode...
            3DES KEY: CUB-ACO-BONUSPNT-QLST-KEY
            3DES IV: 20210222
            ..."

            **Correct Output (Your entire response must be this JSON):**
            ```json
            {{
                "key": "CUB-ACO-BONUSPNT-QLST-KEY",
                "iv": "20210222"
            }}
            ```
            <</SYS>>

            **### ACTUAL DOCUMENT TEXT ###**
            ---
            {full_doc_text[:self.max_text_length]}
            ---
            [/INST]
        """)

        try:
            llm_output = await asyncio.to_thread(self.llm_service.generate_text, prompt)
            json_string = self._extract_first_json_object(llm_output)

            # 如果沒有返回 JSON 或返回的是空字串，視為未找到
            if not json_string:
                self.logger.warning("LLM 未能提取到加密參數 JSON。可能未找到或返回非JSON格式。")
                return None

            params = json.loads(json_string)

            # 如果返回的是空物件 {}，也視為未找到
            if not params:
                self.logger.warning("LLM 返回了空的加密參數物件 {}。")
                return None

            # 檢查關鍵欄位是否存在
            if not params.get("key") or not params.get("iv"):
                self.logger.warning(f"LLM 返回了不完整的加密參數: {params}。將使用預設值。")
                return None

            self.logger.info(f"成功從 LLM 提取到加密參數: {params}")
            return params
        except json.JSONDecodeError:
            self.logger.warning(f"LLM 返回的不是有效的 JSON，無法解析。原始輸出: '{llm_output}'")
            return None
        except Exception as e:
            self.logger.error(f"解析 LLM 提取的加密參數時發生未知錯誤: {e}", exc_info=True)
            return None

    def _flatten_dict(self, d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
        """
        一個遞迴的工具函式，用於將巢狀的字典「拍平」成單層字典。

        例如：`{'a': {'b': 1}}` 會被轉換為 `{'a.b': 1}`。
        :param d: 要拍平的字典。
        :param parent_key: (內部遞迴使用) 父層的鍵。
        :param sep: 用於連接鍵的分隔符。
        :return: 一個拍平後的字典。
        """
        items = []
        for k, v in d.items():
            new_key = parent_key + sep + k if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)

    def _extract_request_body_from_example(self, request_example_dict: Dict) -> tuple[Optional[str], Optional[Dict]]:
        """
        一個內部輔助函式，用於從完整的請求範例中，智慧地提取出 Body 的鍵名和物件。

        它會優先尋找 'TRANRQ', 'TRANBODY', 'SvcRq' 等常見的 Body 鍵名。
        如果找不到，則假設除了 'MWHEADER' 之外的另一個頂層鍵就是 Body。
        :param request_example_dict: 已解析的請求 JSON 字典。
        :return: 一個包含 (body_key, body_object) 的元組，例如 ('TRANRQ', {...})。如果找不到則返回 (None, None)。
        """
        self.logger.info("正在從請求範例中提取 Body 物件...")

        # 常見的 Body 鍵名列表，可以根據需要擴充
        common_body_keys = ['TRANRQ', 'TRANBODY', 'SvcRq', 'SvcBody', 'tranRq', 'tranBody']

        for key in common_body_keys:
            if key in request_example_dict and isinstance(request_example_dict[key], dict):
                self.logger.info(f"找到已知的 Body 鍵名: '{key}'")
                return key, request_example_dict[key]

        # 如果找不到常見鍵名，則使用備援邏輯
        self.logger.warning("未找到常見 Body 鍵名，嘗試備援邏輯...")
        header_keys = ['MWHEADER', 'mwHeader', 'Header']
        non_header_keys = [k for k in request_example_dict.keys() if k not in header_keys]

        if len(non_header_keys) == 1 and isinstance(request_example_dict[non_header_keys[0]], dict):
            body_key = non_header_keys[0]
            self.logger.info(f"備援邏輯成功：找到非 Header 的唯一鍵名 '{body_key}' 作為 Body。")
            return body_key, request_example_dict[body_key]

        self.logger.error(f"無法從請求範例中明確識別出 Body 物件。頂層鍵: {list(request_example_dict.keys())}")
        return None, None

    def _find_encrypted_fields(self, body_markdown: str) -> List[str]:
        """
        一個內部輔助函式，用於掃描 Markdown 表格，找出哪些欄位需要加密。

        它通過在欄位描述中尋找 "加密"、"encrypt" 等關鍵字來識別目標欄位。
        :param body_markdown: 包含欄位規則的 Markdown 表格。
        :return: 一個包含需要加密的欄位名稱的列表。
        """
        encrypted_fields = []
        encryption_keywords = ["加密", "encrypt", "3des", "aes"]

        # 簡單地按行解析 Markdown
        lines = body_markdown.strip().split('\n')
        for line in lines:
            if not line.strip().startswith('|'):
                continue

            parts = [p.strip() for p in line.split('|') if p.strip()]
            if len(parts) >= 3:  # 假設格式是 | LVL | 欄位名稱 | ... | 說明 |
                field_name = parts[1]
                description = parts[-1]

                if any(keyword in description.lower() for keyword in encryption_keywords):
                    self.logger.info(f"偵測到加密欄位: '{field_name}'，因為其描述包含關鍵字。")
                    encrypted_fields.append(field_name)

        return list(set(encrypted_fields))  # 返回唯一的欄位名稱

    def _process_encryption_placeholders(self, record: dict, params: dict) -> dict:
        """
        一個內部輔助函式，用於處理由 LLM 生成的加密佔位符物件。

        它會遍歷記錄，找到格式為 `{"_unencrypted_source_": "..."}` 的值，
        提取原始資料進行加密，然後用加密後的字串替換掉原始的物件。
        :param record: 一筆包含待處理資料的字典。
        :param params: 包含 'key' 和 'iv' 的加密參數字典。
        :return: 一個處理完加密欄位的完整字典。
        """
        processed_record = record.copy()
        key = params.get("key")
        iv = params.get("iv")

        if not key or not iv:
            self.logger.error("在處理加密佔位符時缺少 Key 或 IV，將跳過加密。")
            return processed_record

        # 遍歷副本的鍵值對
        for field, value in processed_record.items():

            # 【核心修正】檢查 value 是否為字典，並且包含我們的特殊鍵
            if isinstance(value, dict) and '_unencrypted_source_' in value:

                # 從字典中提取需要加密的原始資料
                data_to_encrypt = value.get('_unencrypted_source_')

                # 確保有資料可以加密
                if data_to_encrypt is None or not isinstance(data_to_encrypt, str):
                    self.logger.warning(f"在欄位 '{field}' 中找到加密物件，但其來源資料無效: {value}。將保留原樣。")
                    continue

                self.logger.debug(f"在欄位 '{field}' 中找到加密物件，準備加密資料: '{data_to_encrypt}'")

                try:
                    # 執行 3DES 加密
                    encrypted_value = self._tool_encrypt_data(data_to_encrypt, key, iv)

                    # 【關鍵修正】用加密後的值 (字串) 替換掉整個字典物件
                    processed_record[field] = encrypted_value
                    self.logger.debug(f"欄位 '{field}' 已成功加密。")

                except Exception as e:
                    self.logger.error(f"為欄位 '{field}' 加密資料 '{data_to_encrypt}' 時失敗: {e}", exc_info=True)
                    processed_record[field] = f"[ENCRYPTION_FAILED]"

        return processed_record

    def _convert_flattened_data_to_markdown(self, flattened_data: List[Dict]) -> str:
        """
        一個工具函式，將拍平後的資料列表轉換為 Markdown 表格字串。

        此版本能處理列表中包含不同鍵集合的異構資料 (heterogeneous data)，
        它會自動收集所有記錄中出現過的唯一鍵，並將它們的聯集作為統一的表頭。
        :param flattened_data: 一個拍平後的字典列表。
        :return: 一個 Markdown 表格字串。
        """
        if not flattened_data:
            self.logger.warning("嘗試將空的資料列表轉換為 Markdown，返回空字串。")
            return ""

        # 步驟 1: 收集所有記錄中出現過的所有唯一鍵，作為統一的表頭
        all_keys = {}  # 使用字典來保持插入順序 (Python 3.7+)
        for record in flattened_data:
            for key in record.keys():
                all_keys[key] = None

        headers = list(all_keys.keys())
        if not headers:
            self.logger.warning("資料存在但所有記錄都為空，無法生成表頭。")
            return ""

        # 步驟 2: 建立 Markdown 的表頭和分隔線
        header_line = "| " + " | ".join(headers) + " |"
        separator_line = "| " + " | ".join(["---"] * len(headers)) + " |"

        # 步驟 3: 建立資料行
        data_rows = []
        for record in flattened_data:
            # 根據統一的表頭順序提取值。如果某筆記錄缺少某個鍵，則用空字串代替。
            row_values = [str(record.get(h, '')) for h in headers]
            data_rows.append("| " + " | ".join(row_values) + " |")

        # 將所有部分組合起來
        return "\n".join([header_line, separator_line] + data_rows)

    def _validate_encryption_params(self, params: Optional[Dict]) -> bool:
        """
        一個內部輔-助函式，用於驗證從文件中提取的加密參數是否有效。

        它主要檢查 3DES 所需的 Key 和 IV 是否符合正確的位元組長度。
        :param params: 一個可能包含 'key' 和 'iv' 的字典。
        :return: 如果參數有效則返回 True，否則返回 False。
        """
        if not params or not params.get("key") or not params.get("iv"):
            self.logger.debug("傳入的加密參數為空或缺少 key/iv。")
            return False

        key = params["key"]
        iv = params["iv"]

        # 檢查 Key 長度 (16 或 24 bytes)
        if len(key.encode('utf-8')) not in [16, 24]:
            self.logger.warning(f"從文件提取的 Key 長度無效 ({len(key.encode('utf-8'))} bytes)，不予使用。Key: '{key}'")
            return False

        # 檢查 IV 長度 (8 bytes)
        if len(iv.encode('utf-8')) != 8:
            self.logger.warning(f"從文件提取的 IV 長度無效 ({len(iv.encode('utf-8'))} bytes)，不予使用。IV: '{iv}'")
            return False

        self.logger.info(f"驗證通過！將使用從文件中提取的加密參數。")
        return True

    def _tool_encrypt_data(self, data: str, key: str, iv: str) -> str:
        """
        一個工具函式，使用 3DES CBC Mode 和 Hex 編碼來加密資料。

        :param data: 要加密的原始字串。
        :param key: 3DES 金鑰 (16 或 24 bytes)。
        :param iv: 3DES 初始向量 (8 bytes)。
        :return: 加密並進行 Hex 編碼後的字串。
        :raises ValueError: 如果 Key 或 IV 的長度無效。
        """
        self.logger.debug(f"執行 3DES 加密，Key: '{key[:4]}...', IV: '{iv}'")

        try:
            key_bytes = key.encode('utf-8')
            iv_bytes = iv.encode('utf-8')

            if len(key_bytes) not in [16, 24]:
                raise ValueError(f"3DES 加密金鑰長度無效: 預期為 16 或 24 bytes，實際為 {len(key_bytes)} bytes。")
            if len(iv_bytes) != 8:
                raise ValueError(f"3DES IV 長度無效: 預期為 8 bytes，實際為 {len(iv_bytes)} bytes。")

            cipher = DES3.new(key_bytes, DES3.MODE_CBC, iv_bytes)
            padded_data = pad(data.encode('utf-8'), DES3.block_size)
            encrypted_bytes = cipher.encrypt(padded_data)

            return binascii.hexlify(encrypted_bytes).decode('utf-8')

        except Exception as e:
            self.logger.error(f"3DES 加密過程中發生錯誤: {e}", exc_info=True)
            raise
