# services/document_processor.py
from io import BytesIO
from .logger import get_logger
from docx2python import docx2python

class DocumentProcessorService:
    def __init__(self):
        """
        初始化 DocumentProcessorService。

        此建構函式會設定服務所需的依賴項，例如日誌記錄器，
        為後續的文件處理操作做準備。
        """
        self.logger = get_logger(__name__)

    def extract_text_from_docx(self, file_content: BytesIO) -> str:
        """
        從 .docx 檔案的記憶體串流中，提取包含表格結構的純文字。

        此函式使用 `docx2python` 函式庫，它能有效地將文件內容（包括表格）
        轉換為一個純文字字串，並用 tab 和換行符來維持表格的排版，
        這種格式特別適合後續交由大型語言模型 (LLM) 進行理解和處理。
        :param file_content: 包含 .docx 檔案內容的 BytesIO 記憶體串流。
        :return: 一個包含文件所有文字內容（含表格）的單一字串。
        :raises ValueError: 如果文件內容為空，或在提取過程中發生任何錯誤。
        """
        try:
            self.logger.info("開始使用 docx2python 提取 .docx 文件文字")

            # 使用 docx2python 進行解析
            docx_result = docx2python(file_content)

            # docx_result.text 會將表格內容以 tab 和換行符的形式保留，非常適合 LLM 讀取
            text = docx_result.text

            if not text or not text.strip():
                raise ValueError("文件內容為空或無法提取有效文字")

            self.logger.info("成功使用 docx2python 提取 .docx 文件文字")
            return text
        except Exception as e:
            self.logger.error(f"使用 docx2python 提取 .docx 文件文字失敗: {str(e)}")
            raise ValueError(f"提取文件文字失敗: {str(e)}") from e

    async def process_docx_file(self, file_content: bytes, file_name: str) -> str:
        """
        處理上傳的 .docx 檔案，並回傳其純文字內容。

        這是一個非同步的包裝函式，作為此服務的主要進入點。它負責：
        1. 驗證檔案名稱是否為 .docx。
        2. 將原始的 bytes 內容轉換為記憶體串流 (BytesIO)。
        3. 呼叫核心的 `extract_text_from_docx` 函式來執行提取。
        :param file_content: 上傳檔案的原始位元組 (bytes) 內容。
        :param file_name: 原始檔案的名稱，用於驗證和日誌記錄。
        :return: 提取出的純文字內容。
        :raises ValueError: 如果檔案格式不正確或在處理過程中發生錯誤。
        """
        self.logger.info(f"開始處理文件: {file_name}")

        if not file_name.lower().endswith('.docx'):
            raise ValueError("只支援 .docx 格式的文件")

        try:
            file_stream = BytesIO(file_content)
            # 現在會呼叫我們新的、更強大的提取函式
            text = self.extract_text_from_docx(file_stream)
            self.logger.info(f"文件處理成功: {file_name}")
            return text
        except Exception as e:
            self.logger.error(f"處理文件失敗: {file_name}, 錯誤: {str(e)}")
            raise