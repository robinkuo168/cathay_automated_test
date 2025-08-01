import re
import json
import pandas as pd
from typing import List, Dict, Optional, Any
from io import StringIO
import numpy as np
import math
from .logger import get_logger

class FileProcessorService:
    def __init__(self):
        """
        初始化 FileProcessorService。

        此建構函式會設定服務所需的依賴項和配置，例如日誌記錄器、
        支援的檔案編碼列表、最大檔案大小限制等，為後續的文件處理操作做準備。
        """
        self.logger = get_logger(__name__)
        self.supported_encodings = ['utf-8', 'big5', 'gbk', 'cp1252', 'latin1']
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.max_preview_length = 2000

    async def process_uploaded_files(self, files) -> List[Dict]:
        """
        處理一個包含多個上傳檔案的列表，是此服務的主要進入點。

        此函式會非同步地迭代所有傳入的檔案，對每個檔案進行大小檢查，
        然後呼叫 `_process_single_file` 進行獨立處理。
        它能優雅地處理單一檔案的失敗，確保一個檔案的錯誤不會中斷整個批次處理。
        :param files: 一個從 FastAPI 接收到的 UploadFile 物件列表。
        :return: 一個字典列表，其中每個字典代表一個檔案的處理結果。
        """
        processed_files = []

        for file in files:
            try:
                # 檢查檔案大小
                if hasattr(file, 'size') and file.size and file.size > self.max_file_size:
                    self.logger.warning(f"檔案 {file.filename} 超過大小限制")
                    continue

                self.logger.info(f"開始處理檔案: {file.filename}")

                # 重置檔案指針
                if hasattr(file, 'seek'):
                    await file.seek(0)

                # 處理單一檔案
                result = await self._process_single_file(file)
                if result:
                    processed_files.append(result)

            except Exception as e:
                self.logger.error(f"處理檔案 {file.filename} 失敗: {e}")
                # 添加錯誤記錄但繼續處理其他檔案
                processed_files.append({
                    "filename": file.filename,
                    "type": "error",
                    "error": str(e),
                    "data": None,
                    "content": ""
                })

        return processed_files

    async def _process_single_file(self, file) -> Optional[Dict]:
        """
        根據檔案類型，將單一檔案的處理分派給對應的專用函式。

        這是一個調度函式 (dispatcher)，它會讀取檔案的位元組內容，
        並根據檔案的副檔名 (如 .csv, .json) 呼叫相應的 `_process_*_file` 方法。
        :param file: 一個 FastAPI 的 UploadFile 物件。
        :return: 一個包含處理結果的字典，如果檔案為空或處理失敗則返回 None。
        """
        try:
            content_bytes = await file.read()
            if not content_bytes:
                self.logger.warning(f"檔案 {file.filename} 內容為空")
                return None

            filename = file.filename.lower()

            # 根據檔案類型處理
            if filename.endswith('.csv'):
                return self._process_csv_file(content_bytes, file.filename)
            elif filename.endswith('.json'):
                return self._process_json_file(content_bytes, file.filename)
            elif filename.endswith(('.txt', '.log')):
                return self._process_text_file(content_bytes, file.filename)
            else:
                self.logger.warning(f"不支援的檔案類型: {filename}")
                return {
                    "filename": file.filename,
                    "type": "unsupported",
                    "error": "不支援的檔案類型",
                    "data": None,
                    "content": ""
                }

        except Exception as e:
            self.logger.error(f"處理檔案 {file.filename} 時發生錯誤: {e}")
            return None

    def _process_csv_file(self, content_bytes: bytes, filename: str) -> Dict:
        """
        專門處理 CSV 檔案的內容。

        此函式負責將 CSV 檔案的位元組內容解碼為文字，使用 pandas 函式庫進行健壯的解析
        （能自動嘗試多種分隔符），然後清理資料並提取結構化資訊，如欄位、行數和資料範例。
        :param content_bytes: 檔案的原始位元組內容。
        :param filename: 原始檔案名稱，用於日誌和回傳。
        :return: 一個包含 CSV 結構化分析結果的字典。
        """
        try:
            # 嘗試解碼
            text_content = self._decode_content(content_bytes, filename)
            if not text_content:
                raise ValueError("無法解碼檔案內容")

            # 使用 pandas 讀取 CSV
            try:
                df = pd.read_csv(StringIO(text_content))
            except Exception as e:
                # 嘗試其他分隔符
                for sep in [',', ';', '\t', '|']:
                    try:
                        df = pd.read_csv(StringIO(text_content), sep=sep)
                        self.logger.info(f"CSV 檔案 {filename} 使用分隔符 '{sep}' 解析成功")
                        break
                    except:
                        continue
                else:
                    raise ValueError(f"無法解析 CSV 檔案: {e}")

            # 清理 DataFrame
            df_cleaned = self._clean_dataframe(df)

            # 構建返回資料
            data = {
                "columns": df_cleaned.columns.tolist(),
                "rows": df_cleaned.head(10).to_dict('records'),
                "row_count": len(df_cleaned),
                "sample_data": df_cleaned.head(3).to_dict('records') if len(df_cleaned) > 0 else [],
                "column_types": {col: str(df_cleaned[col].dtype) for col in df_cleaned.columns}
            }

            return {
                "filename": filename,
                "type": "csv",
                "data": data,
                "content": self._get_content_preview(text_content),
                "encoding": "utf-8",  # 簡化編碼資訊
                "size": len(content_bytes)
            }

        except Exception as e:
            self.logger.error(f"處理 CSV 檔案 {filename} 失敗: {e}")
            return {
                "filename": filename,
                "type": "csv",
                "error": str(e),
                "data": None,
                "content": ""
            }

    def _process_json_file(self, content_bytes: bytes, filename: str) -> Dict:
        """
        專門處理 JSON 檔案的內容。

        此函式負責將 JSON 檔案的位元組內容解碼為文字，解析為 Python 物件，
        清理無效值（如 NaN），並進一步分析其結構和其中包含的 JMeter 風格變數。
        :param content_bytes: 檔案的原始位元組內容。
        :param filename: 原始檔案名稱，用於日誌和回傳。
        :return: 一個包含 JSON 內容、結構分析和變數列表的字典。
        """
        try:
            # 解碼內容
            text_content = self._decode_content(content_bytes, filename)
            if not text_content:
                raise ValueError("無法解碼檔案內容")

            # 解析 JSON
            try:
                data = json.loads(text_content)
            except json.JSONDecodeError as e:
                raise ValueError(f"JSON 格式錯誤: {e}")

            # 清理資料
            cleaned_data = self._clean_json_data(data)

            # 提取變數資訊
            variables = self._extract_json_variables(cleaned_data)

            # 分析 JSON 結構
            structure_info = self._analyze_json_structure(cleaned_data)

            return {
                "filename": filename,
                "type": "json",
                "data": {
                    "content": cleaned_data,
                    "variables": variables,
                    "structure": structure_info
                },
                "content": self._get_content_preview(text_content),
                "encoding": "utf-8",
                "size": len(content_bytes)
            }

        except Exception as e:
            self.logger.error(f"處理 JSON 檔案 {filename} 失敗: {e}")
            return {
                "filename": filename,
                "type": "json",
                "error": str(e),
                "data": None,
                "content": ""
            }

    def _process_text_file(self, content_bytes: bytes, filename: str) -> Dict:
        """
        專門處理純文字檔案（如 .txt, .log）的內容。

        此函式負責將檔案的位元組內容解碼為文字，並對其進行基本的統計分析，
        例如計算行數、字元數和非空行數。
        :param content_bytes: 檔案的原始位元組內容。
        :param filename: 原始檔案名稱，用於日誌和回傳。
        :return: 一個包含文字內容基本分析結果的字典。
        """
        try:
            # 解碼內容
            text_content = self._decode_content(content_bytes, filename)
            if not text_content:
                raise ValueError("無法解碼檔案內容")

            lines = text_content.split('\n')

            # 分析文字內容
            analysis = {
                "line_count": len(lines),
                "char_count": len(text_content),
                "non_empty_lines": len([line for line in lines if line.strip()]),
                "preview_lines": lines[:10] if lines else []
            }

            return {
                "filename": filename,
                "type": "text",
                "data": analysis,
                "content": self._get_content_preview(text_content),
                "encoding": "utf-8",
                "size": len(content_bytes)
            }

        except Exception as e:
            self.logger.error(f"處理文字檔案 {filename} 失敗: {e}")
            return {
                "filename": filename,
                "type": "text",
                "error": str(e),
                "data": None,
                "content": ""
            }

    def _decode_content(self, content_bytes: bytes, filename: str) -> Optional[str]:
        """
        一個健壯的工具函式，用於將位元組內容解碼為字串。

        它會按順序嘗試多種常見的編碼格式（如 utf-8, big5），直到成功為止。
        這大大提高了處理來自不同系統的檔案時的成功率。
        :param content_bytes: 檔案的原始位元組內容。
        :param filename: 原始檔案名稱，僅用於日誌記錄。
        :return: 解碼後的字串，如果所有嘗試都失敗則返回 None。
        """
        for encoding in self.supported_encodings:
            try:
                text_content = content_bytes.decode(encoding)
                self.logger.debug(f"檔案 {filename} 使用 {encoding} 編碼解析成功")
                return text_content
            except UnicodeDecodeError:
                continue

        self.logger.error(f"無法解碼檔案 {filename}")
        return None

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        一個資料清理工具函式，用於處理 pandas DataFrame 中的常見問題值。

        它會填充缺失值（NaN），並將數值欄位中的無限大（inf）替換為 0，
        確保後續處理和序列化不會出錯。
        :param df: 一個 pandas DataFrame 物件。
        :return: 一個清理後的 DataFrame 副本。
        """
        df_cleaned = df.copy()

        # 處理不同資料類型的欄位
        for column in df_cleaned.columns:
            if df_cleaned[column].dtype == 'object':  # 字串類型
                df_cleaned[column] = df_cleaned[column].fillna('').astype(str)
                # 清理字串中的問題值
                df_cleaned[column] = df_cleaned[column].apply(self._clean_string_value)
            else:  # 數值類型
                df_cleaned[column] = df_cleaned[column].fillna(0)
                # 處理無限值
                df_cleaned[column] = df_cleaned[column].replace([np.inf, -np.inf], 0)

        return df_cleaned

    def _clean_string_value(self, value: str) -> str:
        """
        一個工具函式，用於清理單一字串值。

        它會移除字串中可能導致後續處理（特別是 XML 或 JSON 生成）失敗的
        不可見控制字元。
        :param value: 要清理的原始字串。
        :return: 清理後的字串。
        """
        if pd.isna(value) or value is None:
            return ''

        str_value = str(value).strip()

        # 移除控制字符但保留常見的空白字符
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', str_value)

        return cleaned

    def _clean_json_data(self, obj):
        """
        一個遞迴的工具函式，用於深度清理已解析的 JSON 物件。

        它會遍歷字典和列表，主要目的是將 Python 中合法但在標準 JSON 中非法的
        浮點數值（如 NaN, Infinity）轉換為 `None`。
        :param obj: 要清理的 Python 物件（字典或列表）。
        :return: 清理後的物件。
        """
        if isinstance(obj, dict):
            return {key: self._clean_json_data(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._clean_json_data(item) for item in obj]
        elif isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
            return obj
        elif pd.isna(obj):
            return None
        else:
            return obj

    def _extract_json_variables(self, json_obj) -> List[str]:
        """
        一個遞迴的工具函式，用於從已解析的 JSON 物件中深度提取所有 JMeter 風格的變數。

        它會遍歷整個物件結構，尋找所有形如 `${...}` 的字串，並收集這些變數的名稱。
        :param json_obj: 要分析的 Python 物件（字典或列表）。
        :return: 一個包含所有找到的唯一變數名稱的列表。
        """
        variables = set()

        def extract_vars(obj):
            try:
                if isinstance(obj, dict):
                    for key, value in obj.items():
                        if isinstance(value, str) and '${' in value and '}' in value:
                            # 提取所有 ${...} 模式的變數
                            var_matches = re.findall(r'\$\{([^}]+)\}', value)
                            variables.update(var_matches)
                        elif isinstance(value, (dict, list)):
                            extract_vars(value)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_vars(item)
            except Exception as e:
                self.logger.warning(f"提取變數時發生錯誤: {e}")

        extract_vars(json_obj)
        return sorted(list(variables))

    def _analyze_json_structure(self, json_obj) -> Dict:
        """
        一個遞迴的工具函式，用於分析 JSON 物件的結構。

        它會生成一個描述 JSON 結構的巢狀字典，包含每個層級的類型、鍵、長度等資訊，
        並對內容進行取樣以避免結果過於龐大。
        :param json_obj: 要分析的 Python 物件（字典或列表）。
        :return: 一個描述 JSON 結構的字典。
        """

        def analyze_object(obj, depth=0):
            if depth > 10:  # 防止過深的遞歸
                return {"type": "too_deep", "depth": depth}

            if isinstance(obj, dict):
                return {
                    "type": "object",
                    "keys": list(obj.keys()),
                    "key_count": len(obj),
                    "depth": depth,
                    "children": {key: analyze_object(value, depth + 1) for key, value in list(obj.items())[:5]}
                    # 只分析前5個鍵
                }
            elif isinstance(obj, list):
                return {
                    "type": "array",
                    "length": len(obj),
                    "depth": depth,
                    "sample_items": [analyze_object(item, depth + 1) for item in obj[:3]]  # 只分析前3個項目
                }
            else:
                return {
                    "type": type(obj).__name__,
                    "value": str(obj)[:100] if obj is not None else None,  # 限制值的長度
                    "depth": depth
                }

            try:
                return analyze_object(json_obj)
            except Exception as e:
                self.logger.warning(f"分析 JSON 結構時發生錯誤: {e}")
                return {"type": "error", "message": str(e)}

    def _get_content_preview(self, content: str) -> str:
        """
        一個工具函式，用於生成一個安全的、用於預覽的內容摘要。

        它會截取內容的前 N 個字元，移除可能破壞前端顯示的控制字元，
        並在內容被截斷時加上省略號。
        :param content: 完整的原始文字內容。
        :return: 一個安全、簡短的預覽字串。
        """
        if not content:
            return ""

        # 限制預覽長度
        preview = content[:self.max_preview_length]

        # 清理控制字符
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', preview)

        # 如果內容被截斷，添加省略號
        if len(content) > self.max_preview_length:
            cleaned += "..."

        return cleaned