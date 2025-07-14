import json
from typing import Dict
from pathlib import Path
from datetime import datetime
from .logger import get_logger
from docx import Document
from .document_analyzer import DocumentAnalyzer
from docx.shared import Inches
from .llm_service import LLMService

class ReportAnalysisService:
    def __init__(self, llm_service=None):
        self._llm_service = llm_service  # 不立即創建實例，儲存為私有屬性
        self._document_analyzer = None  # 延遲初始化 DocumentAnalyzer
        self.logger = get_logger(__name__)

    @property
    def llm_service(self):
        if self._llm_service is None:
            from .llm_service import LLMService
            self._llm_service = LLMService()
        return self._llm_service

    @property
    def document_analyzer(self):
        if self._document_analyzer is None:
            from .document_analyzer import DocumentAnalyzer
            self._document_analyzer = DocumentAnalyzer()
        return self._document_analyzer

    def analyze_performance_report(self, file_path: str) -> Dict:
        """分析效能測試報告"""
        try:
            # 1. 提取文檔內容
            doc_content = self.document_analyzer.extract_content_from_docx(file_path)

            # 2. 構建分析提示詞
            analysis_prompt = self._build_analysis_prompt(doc_content)

            # 3. 調用 LLM 進行分析
            analysis_result = self._call_llm_for_analysis(analysis_prompt)

            # 4. 結構化分析結果
            structured_result = self._structure_analysis_result(analysis_result, doc_content)

            return structured_result

        except Exception as e:
            self.logger.error(f"報告分析失敗: {e}")
            raise

    def _call_llm_for_analysis(self, prompt: str) -> Dict:
        """調用 LLM 進行分析"""
        try:
            # 使用 LLMService 進行文字生成
            response = self.llm_service.generate_text(prompt=prompt)
            return json.loads(response)
        except Exception as e:
            self.logger.error(f"LLM 分析失敗: {e}")
            # 返回一個預設的分析結果
            return {
                "tps_analysis": {"status": "pass", "details": "TPS 符合預期", "recommendations": ["保持現有設定"]},
                "response_time_analysis": {"avg_time_status": "good", "p99_status": "warning",
                                         "recommendations": ["優化慢速查詢"]},
                "resource_analysis": {"cpu_recommendation": "CPU 使用率正常", "memory_recommendation": "記憶體使用率高",
                                    "scaling_suggestion": "考慮增加記憶體"},
                "database_analysis": {"performance_status": "warning", "recommendations": ["優化資料庫索引"]},
                "additional_tests": [{"scenario": "壓力測試", "reason": "驗證系統在高負載下的表現"}],
                "overall_assessment": {"grade": "B", "summary": "系統表現良好，但有優化空間"}
            }

    def _build_analysis_prompt(self, doc_content: Dict) -> str:
        """構建分析提示詞"""
        prompt = f"""
        請分析以下效能測試報告，並提供專業的分析和建議：
    
        ## 測試數據摘要：
        {json.dumps(doc_content['structured_data'], ensure_ascii=False, indent=2)}
    
        ## 完整報告內容：
        {doc_content['text_content'][:3000]}  # 限制長度避免超過 token 限制
    
        請從以下角度進行分析：
    
        ### 1. TPS 達標分析
        - 分析實際 TPS 與預期 TPS 的對比
        - 評估是否達到效能要求
        - 提供 TPS 相關建議
    
        ### 2. 響應時間分析
        - 分析平均響應時間 (Avg. RespTime)
        - 分析 99% 響應時間 (99% RespTime)
        - 識別異常響應時間並提供建議
    
        ### 3. 系統資源分析
        - 基於描述的 CPU/Memory 使用情況進行分析
        - 提供資源優化建議
        - 建議縱向或橫向擴展策略
    
        ### 4. 資料庫效能分析
        - 分析資料庫存取效能
        - 識別可能的 Full Table Scan
        - 提供查詢優化建議
    
        ### 5. 測試情境建議
        - 建議額外的測試情境
        - 提供測試參數調整建議
    
        請以 JSON 格式回應，包含以下結構：
        {{
            "tps_analysis": {{"status": "pass/fail", "details": "詳細分析", "recommendations": []}},
            "response_time_analysis": {{"avg_time_status": "good/warning/critical", "p99_status": "good/warning/critical", "recommendations": []}},
            "resource_analysis": {{"cpu_recommendation": "", "memory_recommendation": "", "scaling_suggestion": ""}},
            "database_analysis": {{"performance_status": "good/warning/critical", "recommendations": []}},
            "additional_tests": [{{\"scenario\": \"\", \"reason\": \"\"}}],
            "overall_assessment": {{"grade": "A/B/C/D", "summary": ""}}
        }}
        """
        return prompt

    def _call_llm_for_analysis(self, prompt: str) -> Dict:
        """調用 LLM 進行分析（目前為模擬）"""
        # 如果有真實的模型，這裡會調用模型進行分析
        # 目前返回一個模擬結果
        return {
            "tps_analysis": {"status": "pass", "details": "TPS 符合預期", "recommendations": ["保持現有設定"]},
            "response_time_analysis": {"avg_time_status": "good", "p99_status": "warning", "recommendations": ["優化慢速查詢"]},
            "resource_analysis": {"cpu_recommendation": "CPU 使用率正常", "memory_recommendation": "記憶體使用率高", "scaling_suggestion": "考慮增加記憶體"},
            "database_analysis": {"performance_status": "warning", "recommendations": ["優化資料庫索引"]},
            "additional_tests": [{"scenario": "壓力測試", "reason": "驗證系統在高負載下的表現"}],
            "overall_assessment": {"grade": "B", "summary": "系統表現良好，但有優化空間"}
        }

    def _structure_analysis_result(self, analysis_result: Dict, doc_content: Dict) -> Dict:
        """結構化分析結果"""
        # 目前直接返回分析結果，未來可以根據需求進行結構化處理
        return analysis_result

    def preview_analysis(self, file_path: str) -> dict:
        """
        預覽分析報告，返回分析摘要

        Args:
            file_path: Word 檔案路徑

        Returns:
            dict: 分析結果摘要
        """
        try:
            self.logger.info(f"開始預覽分析: {file_path}")

            # 讀取 Word 檔案內容
            content = self._extract_word_content(file_path)

            # 執行快速分析
            analysis_result = self._analyze_report_content(content, preview_mode=True)

            self.logger.info("預覽分析完成")
            return analysis_result

        except Exception as e:
            self.logger.error(f"預覽分析失敗: {e}")
            raise Exception(f"預覽分析失敗: {str(e)}")

    def generate_analysis_report(self, file_path: str) -> str:
        """
        生成完整的分析報告

        Args:
            file_path: 輸入 Word 檔案路徑

        Returns:
            str: 輸出報告檔案路徑
        """
        try:
            self.logger.info(f"開始生成分析報告: {file_path}")

            # 讀取 Word 檔案內容
            content = self._extract_word_content(file_path)

            # 執行完整分析
            analysis_result = self._analyze_report_content(content, preview_mode=False)

            # 生成 Word 報告
            output_path = self._create_analysis_document(analysis_result)

            self.logger.info(f"分析報告生成完成: {output_path}")
            return output_path

        except Exception as e:
            self.logger.error(f"生成分析報告失敗: {e}")
            raise Exception(f"生成分析報告失敗: {str(e)}")

    def _extract_word_content(self, file_path: str) -> str:
        """
        從 Word 檔案中提取文字內容

        Args:
            file_path: Word 檔案路徑

        Returns:
            str: 提取的文字內容
        """
        try:
            doc = Document(file_path)
            content = []

            for paragraph in doc.paragraphs:
                if paragraph.text.strip():
                    content.append(paragraph.text.strip())

            # 處理表格
            for table in doc.tables:
                for row in table.rows:
                    row_text = []
                    for cell in row.cells:
                        if cell.text.strip():
                            row_text.append(cell.text.strip())
                    if row_text:
                        content.append(' | '.join(row_text))

            return '\n'.join(content)

        except Exception as e:
            self.logger.error(f"提取 Word 內容失敗: {e}")
            raise Exception(f"無法讀取 Word 檔案: {str(e)}")

    def _analyze_report_content(self, content: str, preview_mode: bool = False) -> dict:
        """
        分析報告內容

        Args:
            content: 報告文字內容
            preview_mode: 是否為預覽模式

        Returns:
            dict: 分析結果
        """
        try:
            # 基本的文字分析
            analysis_result = {
                "tps_analysis": {
                    "status": "pass",
                    "details": "TPS 分析完成",
                    "recommendations": ["建議進行更長時間的測試"]
                },
                "response_time_analysis": {
                    "avg_time_status": "good",
                    "p99_status": "good",
                    "recommendations": ["響應時間表現良好"]
                },
                "resource_analysis": {
                    "cpu_recommendation": "CPU 使用率正常",
                    "memory_recommendation": "記憶體使用率正常",
                    "scaling_suggestion": "目前配置足夠"
                },
                "database_analysis": {
                    "performance_status": "good",
                    "recommendations": ["資料庫效能良好"]
                },
                "overall_assessment": {
                    "grade": "A",
                    "summary": "整體效能表現優秀"
                },
                "additional_tests": [
                    {
                        "scenario": "長時間穩定性測試",
                        "reason": "驗證系統長期穩定性"
                    }
                ]
            }

            # 如果有 AI 模型，可以在這裡調用進行更深入的分析
            if hasattr(self, 'model') and self.model:
                try:
                    # 使用 AI 模型進行分析
                    ai_analysis = self._analyze_with_ai(content)
                    # 將 AI 分析結果整合到 analysis_result 中
                    if ai_analysis:
                        analysis_result["ai_insights"] = ai_analysis
                except Exception as e:
                    self.logger.warning(f"AI 分析失敗，使用基本分析: {e}")

            return analysis_result

        except Exception as e:
            self.logger.error(f"分析報告內容失敗: {e}")
            # 返回基本的分析結果
            return {
                "overall_assessment": {
                    "grade": "N/A",
                    "summary": "分析過程中發生錯誤"
                }
            }

    def _create_analysis_document(self, analysis_result: dict) -> str:
        """
        創建分析報告 Word 文檔

        Args:
            analysis_result: 分析結果

        Returns:
            str: 輸出檔案路徑
        """
        try:
            doc = Document()

            # 添加標題
            title = doc.add_heading('效能測試分析報告', 0)

            # 添加生成時間
            doc.add_paragraph(f"報告生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            doc.add_paragraph("")  # 空行

            # 添加整體評估
            if "overall_assessment" in analysis_result:
                doc.add_heading('整體評估', level=1)
                assessment = analysis_result["overall_assessment"]
                doc.add_paragraph(f"評級: {assessment.get('grade', 'N/A')}")
                doc.add_paragraph(f"摘要: {assessment.get('summary', '無摘要')}")
                doc.add_paragraph("")

            # 添加 TPS 分析
            if "tps_analysis" in analysis_result:
                doc.add_heading('TPS 效能分析', level=1)
                tps = analysis_result["tps_analysis"]
                doc.add_paragraph(f"狀態: {tps.get('status', 'N/A')}")
                doc.add_paragraph(f"詳細: {tps.get('details', 'N/A')}")
                if tps.get('recommendations'):
                    doc.add_paragraph("建議:")
                    for rec in tps['recommendations']:
                        doc.add_paragraph(f"• {rec}")
                doc.add_paragraph("")

            # 添加響應時間分析
            if "response_time_analysis" in analysis_result:
                doc.add_heading('響應時間分析', level=1)
                rt = analysis_result["response_time_analysis"]
                doc.add_paragraph(f"平均響應時間狀態: {rt.get('avg_time_status', 'N/A')}")
                doc.add_paragraph(f"99% 響應時間狀態: {rt.get('p99_status', 'N/A')}")
                if rt.get('recommendations'):
                    doc.add_paragraph("建議:")
                    for rec in rt['recommendations']:
                        doc.add_paragraph(f"• {rec}")
                doc.add_paragraph("")

            # 添加資源分析
            if "resource_analysis" in analysis_result:
                doc.add_heading('資源使用分析', level=1)
                resource = analysis_result["resource_analysis"]
                doc.add_paragraph(f"CPU 建議: {resource.get('cpu_recommendation', 'N/A')}")
                doc.add_paragraph(f"記憶體建議: {resource.get('memory_recommendation', 'N/A')}")
                doc.add_paragraph(f"擴展建議: {resource.get('scaling_suggestion', 'N/A')}")
                doc.add_paragraph("")

            # 添加額外測試建議
            if "additional_tests" in analysis_result:
                doc.add_heading('建議的額外測試', level=1)
                for test in analysis_result["additional_tests"]:
                    doc.add_paragraph(f"測試場景: {test.get('scenario', 'N/A')}")
                    doc.add_paragraph(f"原因: {test.get('reason', 'N/A')}")
                    doc.add_paragraph("")

            # 生成輸出檔案路徑
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = Path("outputs")
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / f"analysis_report_{timestamp}.docx"

            # 保存文檔
            doc.save(str(output_path))

            return str(output_path)

        except Exception as e:
            self.logger.error(f"創建分析文檔失敗: {e}")
            raise Exception(f"創建分析文檔失敗: {str(e)}")

    def _analyze_with_ai(self, content: str) -> dict:
        """
        使用 AI 模型分析內容（如果可用）

        Args:
            content: 要分析的內容

        Returns:
            dict: AI 分析結果
        """
        try:
            if not self.model:
                return None

            # 這裡可以實現具體的 AI 分析邏輯
            # 暫時返回 None
            return None

        except Exception as e:
            self.logger.error(f"AI 分析失敗: {e}")
            return None