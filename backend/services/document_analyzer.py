# backend/document_analyzer.py
import docx
import re
import json
from typing import Dict, List, Optional
import logging
from .llm_service import LLMService

class DocumentAnalyzer:
    def __init__(self, llm_service: LLMService = None):
        self.llm_service = llm_service or LLMService()
        self.document_analyzer = DocumentAnalyzer()
        self.logger = get_logger(__name__)

    def extract_content_from_docx(self, file_path: str) -> Dict:
        """從 Word 文檔提取結構化內容"""
        try:
            # 提取純文字
            text_content = docx2txt.extract(file_path)

            # 提取表格數據
            doc = docx.Document(file_path)
            tables_data = self._extract_tables(doc)

            # 提取圖片（如果有 Grafana 截圖）
            images_info = self._extract_images_info(doc)

            return {
                'text_content': text_content,
                'tables': tables_data,
                'images': images_info,
                'structured_data': self._parse_performance_data(text_content, tables_data)
            }
        except Exception as e:
            self.logger.error(f"文檔解析失敗: {e}")
            raise

    def _extract_tables(self, doc) -> List[Dict]:
        """提取表格數據"""
        tables = []
        for table in doc.tables:
            table_data = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            tables.append(table_data)
        return tables

    def _parse_performance_data(self, text: str, tables: List) -> Dict:
        """解析效能數據"""
        return {
            'tps_data': self._extract_tps_data(text, tables),
            'response_time_data': self._extract_response_time_data(text, tables),
            'error_rate_data': self._extract_error_rate_data(text, tables),
            'resource_config': self._extract_resource_config(text),
            'test_duration': self._extract_test_duration(text),
            'concurrent_users': self._extract_concurrent_users(text)
        }

    def _extract_tps_data(self, text: str, tables: List) -> List[Dict]:
        """提取 TPS 相關數據"""
        tps_pattern = r'TPS[：:]\s*(\d+\.?\d*)'
        matches = re.findall(tps_pattern, text, re.IGNORECASE)

        # 從表格中尋找 TPS 數據
        tps_data = []
        for table in tables:
            for row in table:
                if any('tps' in cell.lower() for cell in row):
                    # 解析 TPS 相關行
                    pass

        return tps_data

    def _extract_resource_config(self, text: str, tables: List) -> Dict:
        """提取資源配置資訊"""
        return {
            'cpu_config': self._extract_cpu_config(text),
            'memory_config': self._extract_memory_config(text),
            'network_config': self._extract_network_config(text)
        }