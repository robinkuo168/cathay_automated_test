# services/document_processor.py
from io import BytesIO
from .logger import get_logger
from docx2python import docx2python

class DocumentProcessorService:
    def __init__(self):
        """初始化 DocumentProcessorService，負責處理 Word 文件的提取。"""
        self.logger = get_logger(__name__)

    def extract_text_from_docx(self, file_content: BytesIO) -> str:
        """
        使用 docx2python 從 .docx 文件中提取純文字內容，包含表格。
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
        處理上傳的 .docx 文件，提取文字內容。
        成功時返回文字字串，失敗時拋出異常。
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