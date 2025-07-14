import re
import json
import pandas as pd
from typing import List, Dict, Optional, Any
from io import StringIO
import numpy as np
from .logger import get_logger

class FileProcessorService:
    def __init__(self):
        self.logger = get_logger(__name__)
        self.supported_encodings = ['utf-8', 'big5', 'gbk', 'cp1252', 'latin1']
        self.max_file_size = 10 * 1024 * 1024  # 10MB
        self.max_preview_length = 2000

    async def process_uploaded_files(self, files) -> List[Dict]:
        """處理上傳的檔案"""
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
        """處理單一檔案"""
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
        """處理 CSV 檔案"""
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
        """處理 JSON 檔案"""
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
        """處理文字檔案"""
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
        """嘗試解碼檔案內容"""
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
        """清理 DataFrame 中的問題值"""
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
        """清理字串值"""
        if pd.isna(value) or value is None:
            return ''

        str_value = str(value).strip()

        # 移除控制字符但保留常見的空白字符
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', str_value)

        return cleaned

    def _clean_json_data(self, obj):
        """遞歸清理 JSON 資料中的問題值"""
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
        """從 JSON 中提取 JMeter 變數"""
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
        """分析 JSON 結構"""

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
        """獲取內容預覽"""
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