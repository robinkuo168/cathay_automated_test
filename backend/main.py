from fastapi import FastAPI, UploadFile, File, HTTPException, status, Request, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, validator, Field
from typing import List, Optional, Any, Dict, Union
import logging
import tempfile
import os
import threading
from functools import lru_cache
import uuid
import re
import urllib.parse
from contextvars import ContextVar
import asyncio
import io
import sys
import datetime
from pathlib import Path
import asyncio
from contextlib import asynccontextmanager
import shutil
from backend.services import JMXGeneratorService, FileProcessorService, LogService
from backend.services import ReportAnalysisService, LLMService
from backend.services import DocumentProcessorService, SynDataGenService
from backend.services.elasticsearch_service import ElasticsearchService
from backend.services.langflow_service import LangflowService

def setup_logging():
    """
    在應用程式啟動時設定全域日誌記錄器 (Logger)。

    此函式會配置根日誌記錄器 (root logger)，設定其日誌等級為 INFO，
    並添加一個將日誌輸出到標準輸出 (stdout) 的處理器 (handler)。
    它還會清除任何已存在的處理器，以防止在開發模式下 (如 uvicorn --reload) 重複添加。
    """
    # 取得根日誌記錄器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清除可能已存在的 handlers，以防重複設定 (例如在 uvicorn --reload 模式下)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 創建 handler 並設定格式
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)

    # 為根日誌記錄器添加 handler
    root_logger.addHandler(handler)

# logger 變數的定義可以保留，它會自動從 root logger 繼承設定
logger = logging.getLogger(__name__)

# 請求追蹤
request_id_var: ContextVar[str] = ContextVar('request_id', default="unknown")

# 存儲 LLMService 實例的字典
_llm_services = {}
_llm_services_lock = threading.Lock()

# 其他服務鎖定
_jmx_service_lock = threading.Lock()
_jmx_service = None
_doc_processor_service_lock = threading.Lock()
_doc_processor_service = None
_spec_analysis_service_lock = threading.Lock()
_spec_analysis_service = None
_elasticsearch_service = None
_elasticsearch_service_lock = threading.Lock()
_langflow_service = None
_langflow_service_lock = threading.Lock()

def get_llm_service(model_name: str = "default", config: Optional[Dict] = None) -> LLMService:
    """
    獲取或創建一個執行緒安全的 LLMService 實例 (工廠函式)。

    此函式使用單例模式 (Singleton) 和鎖 (lock) 來確保對於同一個 `model_name`，
    在整個應用程式生命週期中只會創建一個 LLMService 實例，從而避免資源浪費。
    :param model_name: 模型服務的唯一名稱，用於區分不同的 LLM 設定。
    :param config: 可選的配置字典，用於在首次創建時自定義模型參數。
    :return: 一個 LLMService 的實例。
    :raises Exception: 如果 LLM 服務在初始化過程中失敗。
    """
    global _llm_services

    if model_name not in _llm_services:
        with _llm_services_lock:
            if model_name not in _llm_services:
                try:
                    _llm_services[model_name] = LLMService(config=config)
                    logger.info(f"LLM 服務初始化成功 (Model: {model_name})")
                except Exception as e:
                    logger.error(f"LLM 服務初始化失敗 (Model: {model_name}): {e}")
                    raise

    return _llm_services[model_name]

@lru_cache(maxsize=1)
def get_jmx_service(model_name: str = "default") -> JMXGeneratorService:
    """
    獲取或創建一個執行緒安全的 JMXGeneratorService 實例 (工廠函式)。

    此函式使用單例模式和 lru_cache 來確保 JMX 服務的默認實例只被創建一次。
    如果指定了非默認的 `model_name`，則會創建一個新的、使用特定 LLM 的服務實例。
    :param model_name: 要使用的底層 LLM 服務名稱。
    :return: 一個 JMXGeneratorService 的實例。
    :raises Exception: 如果 JMX 服務在初始化過程中失敗。
    """
    global _jmx_service

    # 如果沒有指定特定的模型名稱，使用默認的單例模式
    if model_name == "default":
        if _jmx_service is None:
            with _jmx_service_lock:
                if _jmx_service is None:
                    try:
                        llm_svc = get_llm_service()
                        _jmx_service = JMXGeneratorService(llm_service=llm_svc)
                        logger.info("JMX 服務初始化成功 (默認模型)")
                    except Exception as e:
                        logger.error(f"JMX 服務初始化失敗: {e}")
                        raise
        return _jmx_service
    else:
        # 對於非默認模型，每次都創建新的服務實例
        try:
            llm_svc = get_llm_service(model_name)
            service = JMXGeneratorService(llm_service=llm_svc)
            logger.info(f"JMX 服務初始化成功 (模型: {model_name})")
            return service
        except Exception as e:
            logger.error(f"JMX 服務初始化失敗 (模型: {model_name}): {e}")
            raise

@lru_cache(maxsize=1)
def get_doc_processor_service():
    """
    獲取或創建一個執行緒安全的 DocumentProcessorService 實例 (工廠函式)。

    使用單例模式和 lru_cache 確保服務只被初始化一次。
    :return: 一個 DocumentProcessorService 的實例。
    :raises Exception: 如果服務在初始化過程中失敗。
    """
    global _doc_processor_service
    if _doc_processor_service is None:
        with _doc_processor_service_lock:
            if _doc_processor_service is None:
                try:
                    _doc_processor_service = DocumentProcessorService()
                    logger.info("DocumentProcessor 服務初始化成功")
                except Exception as e:
                    logger.error(f"DocumentProcessor 服務初始化失敗: {e}")
                    raise
    return _doc_processor_service

@lru_cache(maxsize=1)
def get_spec_analysis_service():
    """
    獲取或創建一個執行緒安全的 SynDataGenService 實例 (工廠函式)。

    此服務專門用於從文件中提取規格 (Header/Body)。
    使用單例模式和 lru_cache 確保服務只被初始化一次。
    :return: 一個 SynDataGenService 的實例。
    :raises Exception: 如果服務在初始化過程中失敗。
    """
    global _spec_analysis_service
    if _spec_analysis_service is None:
        with _spec_analysis_service_lock:
            if _spec_analysis_service is None:
                try:
                    llm_svc = get_llm_service()
                    _spec_analysis_service = SynDataGenService(llm_service=llm_svc)
                    logger.info("SpecAnalysis 服務初始化成功")
                except Exception as e:
                    logger.error(f"SpecAnalysis 服務初始化失敗: {e}")
                    raise
    return _spec_analysis_service

@lru_cache(maxsize=1)
def get_elasticsearch_service() -> ElasticsearchService:
    """
    獲取或創建一個執行緒安全的 ElasticsearchService 實例 (工廠函式)。

    使用單例模式和 lru_cache 確保服務只被初始化一次。
    :return: 一個 ElasticsearchService 的實例。
    :raises Exception: 如果服務在初始化過程中失敗。
    """
    global _elasticsearch_service
    if _elasticsearch_service is None:
        with _elasticsearch_service_lock:
            if _elasticsearch_service is None:
                try:
                    _elasticsearch_service = ElasticsearchService()
                    logger.info("Elasticsearch 服務初始化成功")
                except Exception as e:
                    logger.error(f"Elasticsearch 服務初始化失敗: {e}")
                    raise
    return _elasticsearch_service

@lru_cache(maxsize=1)
def get_langflow_service() -> LangflowService:
    """
    獲取或創建一個執行緒安全的 LangflowService 實例 (工廠函式)。

    使用單例模式和 lru_cache 確保服務只被初始化一次。
    :return: 一個 LangflowService 的實例。
    :raises Exception: 如果服務在初始化過程中失敗。
    """
    global _langflow_service
    if _langflow_service is None:
        with _langflow_service_lock:
            if _langflow_service is None:
                try:
                    _langflow_service = LangflowService()
                    logger.info("Langflow 服務初始化成功")
                except Exception as e:
                    logger.error(f"Langflow 服務初始化失敗: {e}")
                    raise
    return _langflow_service

# 創建必要的目錄
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# 用於儲存聊天歷史的記憶體字典
sessions: Dict[str, List[Dict]] = {}

# 服務初始化
try:
    report_analysis_service = None
    file_service = FileProcessorService()
    log_service = LogService()
    logger.info("基礎服務初始化成功")
except Exception as e:
    logger.error(f"服務初始化失敗: {e}")
    report_analysis_service = None
    file_service = None
    log_service = None

def get_report_analysis_service():
    """
    延遲初始化並獲取 ReportAnalysisService 實例。

    此函式採用延遲加載 (lazy loading) 模式，只有在第一次被呼叫時才會真正創建服務實例。
    這有助於加快應用程式的啟動速度。
    :return: 一個 ReportAnalysisService 的實例。
    :raises HTTPException: 如果服務初始化失敗，則會向客戶端返回 500 錯誤。
    """
    global report_analysis_service
    if report_analysis_service is None:
        try:
            llm_svc = get_llm_service()
            report_analysis_service = ReportAnalysisService(llm_service=llm_svc)
            logger.info("報告分析服務初始化成功")
        except Exception as e:
            logger.error(f"報告分析服務初始化失敗: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"報告分析服務不可用: {str(e)}"
            )
    return report_analysis_service

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 應用程式的生命週期管理器。

    此函式使用 @asynccontextmanager 來管理應用程式的啟動和關閉事件。
    在 `yield` 之前的程式碼會在應用程式啟動時執行，用於初始化日誌、創建目錄、預加載模型等。
    在 `yield` 之後的程式碼會在應用程式關閉時執行。
    :param app: FastAPI 應用程式實例。
    :raises RuntimeError: 如果在啟動過程中發生任何嚴重錯誤，將阻止應用程式啟動。
    """
    # --- 啟動時執行的程式碼 ---
    setup_logging()
    logger.info("應用程式啟動中... (日誌系統已設定)")

    try:
        # 確保上傳和輸出目錄存在
        UPLOAD_DIR.mkdir(exist_ok=True)
        OUTPUT_DIR.mkdir(exist_ok=True)

        # 預先加載默認 LLM 模型
        default_config = {
            "model_id": os.getenv("MODEL_ID", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"),
            "max_tokens": 4000,
            "temperature": 0.1
        }
        get_llm_service("default", default_config).initialize()
        logger.info(f"已預先加載默認 LLM 模型")

        # 初始化並測試 Elasticsearch 服務
        get_elasticsearch_service().test_connection()
        logger.info("Elasticsearch 連線測試成功。")

        # 初始化 Langflow 服務並設定流程
        logger.info("正在初始化 Langflow 流程...")
        langflow_svc = get_langflow_service()
        await langflow_svc.initialize_flow()
        logger.info("Langflow 流程初始化完成。")

    except Exception as e:
        # 如果在啟動過程中發生任何錯誤，記錄下來並阻止應用程式啟動
        logger.critical(f"應用程式啟動失敗，發生嚴重錯誤: {e}", exc_info=True)
        raise RuntimeError(f"應用程式啟動失敗: {e}") from e

    logger.info("應用程式已成功啟動並準備就緒。")
    yield
    # --- 關閉時執行的程式碼 ---
    logger.info("應用程式關閉中...")
    if log_service:
        log_service.add_log("INFO", "API 服務關閉")

# FastAPI 應用程式
app = FastAPI(
    title="Auto Testing API",
    version="1.0.0",
    description="自動化測試 API",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan
)

if os.getenv("APP_ENV") != "production":
    logger.info("非生產環境，正在載入本地開發前端服務...")

    # 獲取專案根目錄 (即 backend 的上一層)
    PROJECT_ROOT = Path(__file__).parent.parent

    # 掛載靜態檔案目錄 (CSS, JS, 圖片)
    app.mount("/assets", StaticFiles(directory=PROJECT_ROOT / "frontend" / "assets"), name="assets")

    # 設定 Jinja2 模板引擎，用於讀取 HTML 檔案
    templates = Jinja2Templates(directory=str(PROJECT_ROOT / "frontend" / "pages"))


    # --- 前端頁面路由 (僅用於本地開發) ---
    @app.get("/", response_class=HTMLResponse)
    async def serve_root_as_index(request: Request):
        """
        在本地開發環境中，將根路徑 ("/") 的請求導向到 index.html 頁面。
        :param request: FastAPI 的 Request 物件。
        :return: 一個包含 index.html 內容的 TemplateResponse。
        """
        return templates.TemplateResponse("index.html", {"request": request})


    @app.get("/pages/{page_name}.html", response_class=HTMLResponse)
    async def serve_html_pages(request: Request, page_name: str):
        """
        在本地開發環境中，動態提供 `frontend/pages` 目錄下的 HTML 頁面。

        例如，當使用者訪問 `/pages/chatbot_interface.html` 時，此函式會回傳對應的 HTML 檔案。
        :param request: FastAPI 的 Request 物件。
        :param page_name: 從 URL 路徑中捕獲的頁面名稱 (不含 .html)。
        :return: 一個包含請求的 HTML 頁面內容的 TemplateResponse。
        :raises HTTPException(404): 如果請求的 HTML 檔案不存在。
        """
        template_path = PROJECT_ROOT / "frontend" / "pages" / f"{page_name}.html"
        if not template_path.is_file():
            raise HTTPException(status_code=404, detail=f"頁面 '{page_name}.html' 不存在")
        return templates.TemplateResponse(f"{page_name}.html", {"request": request})


    logger.info("本地開發前端服務載入完成。")

# CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 請求 ID 中間件
@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    """
    一個 FastAPI 中間件，為每個傳入的請求執行以下操作：
    1. 生成一個唯一的 UUID 作為請求 ID。
    2. 將請求 ID 存儲在 ContextVar 中，以便在整個請求處理鏈中訪問。
    3. 計算請求的處理時間。
    4. 在響應標頭中加入 `X-Request-ID` 和 `X-Process-Time`。
    5. 記錄請求的詳細資訊 (方法、路徑、狀態碼、處理時間)。
    :param request: FastAPI 的 Request 物件。
    :param call_next: 一個函式，用於將請求傳遞給下一個處理程序 (即路徑函式)。
    :return: 最終的 Response 物件。
    """
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    start_time = asyncio.get_event_loop().time()
    response = await call_next(request)
    process_time = asyncio.get_event_loop().time() - start_time

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)

    if not request.url.path.startswith('/assets'):
        logger.info(
            f"[{request_id}] {request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    return response

# 響應模型
class APIResponse(BaseModel):
    """標準 API 響應模型"""
    success: bool
    data: Optional[Any] = None
    message: str
    error: Optional[str] = None
    request_id: Optional[str] = None

class JMXResponse(APIResponse):
    """JMX 生成響應模型"""
    data: Optional[dict] = None

class FileUploadResponse(APIResponse):
    """檔案上傳響應模型"""
    data: Optional[dict] = None

class ValidationResponse(APIResponse):
    """XML 驗證響應模型"""
    data: Optional[dict] = None

class LogsResponse(APIResponse):
    """日誌響應模型"""
    data: Optional[dict] = None

class JMXRequest(BaseModel):
    """JMX 生成請求模型"""
    requirements: str
    files: Optional[List[dict]] = None

    @validator("requirements")
    def validate_requirements(cls, v):
        """驗證需求是否有效"""
        if not v or not v.strip():
            raise ValueError("需求不能為空")
        if len(v.strip()) < 10:
            raise ValueError('需求描述至少需要 10 個字符')
        if len(v.strip()) > 10000:
            raise ValueError('需求描述不能超過 10000 個字符')
        return v.strip()

class XMLValidationRequest(BaseModel):
    """XML 驗證請求模型"""
    xml_content: str

    @validator("xml_content")
    def validate_xml_content(cls, v):
        """驗證 XML 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("XML 內容不能為空")
        return v.strip()

class MarkdownReviewRequest(BaseModel):
    """Markdown 校對請求模型"""
    markdown: str
    user_input: str

    @validator("markdown")
    def validate_markdown(cls, v):
        """驗證 Markdown 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("Markdown 內容不能為空")
        return v.strip()

    @validator("user_input")
    def validate_user_input(cls, v):
        """驗證使用者輸入是否有效"""
        if not v or not v.strip():
            raise ValueError("使用者輸入不能為空")
        return v.strip()

class HeaderJsonReviewRequest(BaseModel):
    """Header JSON 校對請求模型"""
    header_markdown: str
    user_input: str

    @validator("header_markdown")
    def validate_header_markdown(cls, v):
        """驗證 Header Markdown 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("Header Markdown 內容不能為空")
        return v.strip()

    @validator("user_input")
    def validate_user_input(cls, v):
        """驗證使用者輸入是否有效"""
        if not v or not v.strip():
            raise ValueError("使用者輸入不能為空")
        return v.strip()

class SyntheticDataRequest(BaseModel):
    """合成資料生成請求模型"""
    markdown: str

    @validator("markdown")
    def validate_markdown_content(cls, v):
        """驗證 Markdown 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("Markdown 內容不能為空")
        return v.strip()

class SyntheticDataReviewRequest(BaseModel):
    """合成資料校對請求模型"""
    synthetic_data_markdown: str
    user_input: str

    @validator("synthetic_data_markdown")
    def validate_markdown(cls, v):
        """驗證合成資料 Markdown 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("合成資料 Markdown 內容不能為空")
        return v.strip()

    @validator("user_input")
    def validate_user_input(cls, v):
        """驗證使用者輸入是否有效"""
        if not v or not v.strip():
            raise ValueError("使用者輸入不能為空")
        return v.strip()

class TaskStartRequest(BaseModel):
    """啟動背景任務的請求模型"""
    filename: Optional[str] = "unknown"
    num_rows: int = Field(default=30, gt=0, description="要生成的合成資料筆數")
    body_markdown: str
    header_json_markdown: str
    full_doc_text: str

    @validator("body_markdown")
    def validate_not_empty(cls, v):
        """驗證內容不能為空"""
        if not v or not v.strip():
            raise ValueError("內容不能為空")
        return v.strip()

    @validator("header_json_markdown")
    def validate_header_json_markdown(cls, v: str):
        """驗證 Header JSON Markdown 內容是否有效"""
        if not v or not v.strip():
            raise ValueError("Header JSON Markdown 內容不能為空")
        return v

    @validator("filename")
    def validate_filename(cls, v):
        """驗證檔案名稱是否有效"""
        if not v or not v.strip():
            raise ValueError("檔案名稱不能為空")
        return v.strip()

    @validator("full_doc_text")
    def validate_full_doc_text(cls, v):
        """驗證檔案內容是否有效"""
        if not v or not v.strip():
            raise ValueError("檔案內容不能為空")
        return v.strip()

class SpecAnalysisData(BaseModel):
    header_json: Optional[Union[Dict, List[Dict]]]
    body_markdown: Optional[str] = None
    filename: str

class SpecAnalysisResponse(APIResponse):
    data: Optional[SpecAnalysisData] = None

class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponseData(BaseModel):
    response: str
    session_id: str
    timestamp: str

class ChatResponse(APIResponse):
    data: Optional[ChatResponseData] = None

# 工具函數
def log_with_request_id(level: str, message: str):
    """
    一個工具函式，用於記錄帶有當前請求 ID 的日誌。

    它會從 ContextVar 中獲取當前請求的唯一 ID，並將其附加到日誌訊息的前面，
    確保在日誌中可以輕鬆追蹤單一請求的完整處理流程。
    :param level: 日誌等級 (例如 "INFO", "ERROR")。
    :param message: 要記錄的訊息。
    """
    request_id = request_id_var.get("unknown")
    if log_service:
        log_service.add_log(level, f"[{request_id}] {message}")
    else:
        logger.log(getattr(logging, level.upper()), f"[{request_id}] {message}")

def create_response(success: bool, message: str, data: Any = None, error: str = None) -> dict:
    """
    一個工具函式，用於創建標準化的 API JSON 響應。

    此函式將所有 API 響應統一為固定格式，包含成功狀態、訊息、資料、錯誤和請求 ID，
    有助於前端統一處理和解析。
    :param success: 操作是否成功。
    :param message: 給前端的簡短訊息。
    :param data: (可選) 成功時要回傳的資料。
    :param error: (可選) 失敗時的錯誤描述。
    :return: 一個符合 APIResponse 結構的字典。
    """
    return {
        "success": success,
        "data": data,
        "message": message,
        "error": error,
        "request_id": request_id_var.get("unknown")
    }

tasks = {}

# 全域異常處理器
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    全域異常處理器，專門捕捉並處理 FastAPI 的 HTTPException。

    當程式碼中任何地方拋出 HTTPException 時 (例如，因輸入驗證失敗而拋出 400 錯誤)，
    此處理器會攔截它，並回傳一個標準格式的 JSON 錯誤響應。
    :param request: FastAPI 的 Request 物件。
    :param exc: 捕獲到的 HTTPException 實例。
    :return: 一個包含錯誤細節的 JSONResponse。
    """
    request_id = request_id_var.get("unknown")
    log_with_request_id("ERROR", f"HTTP 異常: {exc.status_code} - {exc.detail}")

    return JSONResponse(
        status_code=exc.status_code,
        content=create_response(
            success=False,
            message="請求處理失敗",
            error=exc.detail
        )
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    全域異常處理器，用於捕捉所有未被處理的預期外錯誤。

    這是一個安全網，確保即使發生了程式碼中未預料到的錯誤，
    應用程式也不會崩潰，而是會向客戶端回傳一個通用的 500 內部伺服器錯誤響應。
    :param request: FastAPI 的 Request 物件。
    :param exc: 捕獲到的 Exception 實例。
    :return: 一個表示內部伺服器錯誤的 JSONResponse。
    """
    request_id = request_id_var.get("unknown")
    error_msg = str(exc)

    logger.error(f"[{request_id}] 未處理的異常: {exc}", exc_info=True)
    log_with_request_id("ERROR", f"未處理的異常: {error_msg}")

    return JSONResponse(
        status_code=500,
        content=create_response(
            success=False,
            message="內部伺服器錯誤",
            error="系統發生未預期的錯誤，請稍後再試"
        )
    )

# API 端點
@app.get("/api", response_model=APIResponse)
async def root():
    """
    API 的根路徑 (`/api`) 端點。

    主要用於快速檢查 API 服務是否正在運行。
    :return: 一個包含服務狀態資訊的標準 API 響應。
    """
    return create_response(
        success=True,
        message="JMeter JMX Generator API 運行中",
        data={
            "service": "JMeter JMX Generator API",
            "version": "1.0.0",
            "status": "running"
        }
    )

@app.get("/api/health", response_model=APIResponse)
async def health_check():
    """
    API 的健康檢查 (`/api/health`) 端點。

    提供比根路徑更詳細的健康狀態，包括：
    - 關鍵環境變數是否已設定。
    - 各個核心服務 (LLM, JMX, Log 等) 的初始化狀態。
    :return: 一個包含詳細健康檢查結果的標準 API 響應。
    :raises HTTPException(500): 如果在檢查過程中發生錯誤。
    """
    try:
        env_status = {
            "WATSONX_API_KEY": "已設定" if os.getenv("WATSONX_API_KEY") else "未設定",
            "WATSONX_PROJECT_ID": "已設定" if os.getenv("WATSONX_PROJECT_ID") else "未設定",
            "WATSONX_URL": os.getenv("WATSONX_URL", "使用預設值")
        }

        service_status = {
            "file_service": "正常" if file_service else "異常",
            "log_service": "正常" if log_service else "異常",
            "llm_service": "延遲初始化",
            "jmx_service": "延遲初始化",
            "report_analysis_service": "延遲初始化",
            "doc_processor_service": "延遲初始化",
            "spec_analysis_service": "延遲初始化"
        }

        try:
            llm_svc = get_llm_service()
            service_status["llm_service"] = "正常" if llm_svc else "異常"
        except Exception as e:
            service_status["llm_service"] = f"異常: {str(e)}"

        try:
            jmx_svc = get_jmx_service()
            service_status["jmx_service"] = "正常" if jmx_svc else "異常"
        except Exception as e:
            service_status["jmx_service"] = f"異常: {str(e)}"

        try:
            doc_svc = get_doc_processor_service()
            service_status["doc_processor_service"] = "正常" if doc_svc else "異常"
        except Exception as e:
            service_status["doc_processor_service"] = f"異常: {str(e)}"

        try:
            spec_svc = get_spec_analysis_service()
            service_status["spec_analysis_service"] = "正常" if spec_svc else "異常"
        except Exception as e:
            service_status["spec_analysis_service"] = f"異常: {str(e)}"

        return create_response(
            success=True,
            message="服務健康檢查完成",
            data={
                "status": "healthy",
                "environment": env_status,
                "services": service_status
            }
        )
    except Exception as e:
        logger.error(f"健康檢查失敗: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"健康檢查失敗: {str(e)}"
        )

# JMX 生成端點路徑
@app.post("/api/generate-jmx", response_model=JMXResponse)
async def generate_jmx(request: JMXRequest):
    """
    處理 JMX 檔案生成的 API 端點 (`/api/generate-jmx`)。

    接收使用者的自然語言需求和相關檔案，呼叫 JMXGeneratorService 來生成 JMX 檔案內容。
    :param request: 包含 `requirements` 和 `files` 的請求主體。
    :return: 一個包含生成好的 JMX 內容的標準 API 響應。
    :raises HTTPException(400): 如果使用者輸入的需求驗證失敗。
    :raises HTTPException(500): 如果在生成過程中發生內部錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始生成 JMX，需求長度: {len(request.requirements)}")
        service = get_jmx_service()
        files_data = request.files or []

        jmx_content = await service.generate_jmx_with_retry(
            requirements=request.requirements,
            files_data=files_data
        )

        if not jmx_content or not jmx_content.strip():
            raise ValueError("生成的 JMX 內容為空")

        log_with_request_id("INFO", f"JMX 生成成功，內容長度: {len(jmx_content)}")

        return create_response(
            success=True,
            message="JMX 檔案生成成功",
            data={"content": jmx_content}
        )

    except ValueError as e:
        log_with_request_id("ERROR", f"輸入驗證錯誤: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"JMX 生成失敗: {error_msg}")
        logger.error(f"生成 JMX 失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"JMX 生成失敗: {error_msg}"
        )

@app.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """
    處理多檔案上傳的 API 端點 (`/api/upload`)。

    接收一個或多個檔案，進行類型和大小檢查，然後將它們保存到伺服器的 `uploads` 目錄。
    同時，它會讀取檔案內容並在響應中回傳。
    :param files: 一個從表單中獲取的 UploadFile 物件列表。
    :return: 一個包含每個檔案上傳狀態和內容的標準 API 響應。
    :raises HTTPException(500): 如果在檔案處理過程中發生錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始上傳 {len(files)} 個檔案")
        uploaded_files = []
        failed_files = []

        for file in files:
            try:
                allowed_extensions = ['.csv', '.json', '.txt', '.docx', '.xlsx']
                file_extension = Path(file.filename).suffix.lower()

                if file_extension not in allowed_extensions:
                    failed_files.append({
                        "filename": file.filename,
                        "error": f"不支援的檔案格式: {file_extension}"
                    })
                    continue

                # 1. 將檔案內容讀取為 bytes
                content_bytes = await file.read()

                # 2. 檢查檔案大小
                if len(content_bytes) > 10 * 1024 * 1024:  # 10MB
                    failed_files.append({
                        "filename": file.filename,
                        "error": "檔案大小超過 10MB 限制"
                    })
                    continue

                # --- 檔案儲存 ---
                file_path = UPLOAD_DIR / file.filename
                if file_path.exists():
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    name_part = file_path.stem
                    ext_part = file_path.suffix
                    file_path = UPLOAD_DIR / f"{name_part}_{timestamp}{ext_part}"

                with open(file_path, "wb") as buffer:
                    buffer.write(content_bytes)

                # 3. 將 bytes 內容解碼為字串 (str) 以便放入 JSON
                content_str = None
                try:
                    # 使用 UTF-8 解碼
                    content_str = content_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    # 如果檔案不是 UTF-8
                    log_with_request_id("WARNING", f"檔案 {file.filename} 不是有效的 UTF-8 編碼，其內容將不會回傳。")

                # 4. 在回傳的資料中，使用解碼後的字串 `content_str`
                uploaded_files.append({
                    "filename": file.filename,
                    "saved_as": file_path.name,
                    "size": len(content_bytes),
                    "path": str(file_path),
                    "type": file_extension,
                    "status": "success",
                    "data": content_str
                })
                log_with_request_id("INFO", f"檔案上傳成功: {file.filename}")

            except Exception as e:
                failed_files.append({
                    "filename": file.filename,
                    "error": str(e)
                })
                log_with_request_id("ERROR", f"檔案上傳失敗: {file.filename} - {str(e)}")

        total_files = len(files)
        success_count = len(uploaded_files)
        failed_count = len(failed_files)

        response_data = {
            "files": uploaded_files,
            "failed_files": failed_files,
            "total": total_files,
            "processed": success_count,
            "failed": failed_count
        }

        if success_count > 0:
            message = f"成功上傳 {success_count} 個檔案"
            if failed_count > 0:
                message += f"，{failed_count} 個檔案失敗"
            return create_response(success=True, message=message, data=response_data)
        else:
            return create_response(
                success=False,
                message=f"所有 {total_files} 個檔案上傳失敗",
                data=response_data,
                error="沒有檔案成功上傳"
            )

    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"檔案上傳處理失敗: {error_msg}")
        logger.error(f"檔案上傳處理失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"上傳處理失敗: {error_msg}")

@app.post("/api/validate", response_model=ValidationResponse)
async def validate_xml(request: XMLValidationRequest):
    """
    驗證傳入的 XML 字串格式是否正確的 API 端點 (`/api/validate`)。
    :param request: 包含 `xml_content` 的請求主體。
    :return: 一個包含驗證結果 (`valid`, `validation_message`) 的標準 API 響應。
    """
    try:
        log_with_request_id("INFO", f"開始驗證 XML，內容長度: {len(request.xml_content)}")

        service = get_jmx_service()
        is_valid, message = service.validate_xml(request.xml_content)

        log_with_request_id("INFO", f"XML 驗證完成: {'有效' if is_valid else '無效'}")

        # 將所有相關資料放在 data 欄位中
        return create_response(
            success=True,
            message="XML 驗證完成",
            data={
                "valid": is_valid,
                "validation_message": message,
                "length": len(request.xml_content)
            }
        )

    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"XML 驗證失敗: {error_msg}")
        logger.error(f"XML 驗證失敗: {e}", exc_info=True)

        return JSONResponse(
            status_code=200,
            content=create_response(
                success=False,
                message="XML 驗證失敗",
                data={"valid": False},
                error=error_msg
            )
        )

# 報告分析相關端點
@app.post("/api/preview-analysis", response_model=APIResponse)
async def preview_analysis(file: UploadFile = File(...)):
    """
    預覽分析效能報告 (Word 檔案) 的 API 端點 (`/api/preview-analysis`)。

    接收一個 Word 檔案，呼叫 ReportAnalysisService 提取報告的關鍵摘要資訊並回傳。
    :param file: 上傳的 Word 檔案。
    :return: 一個包含分析預覽結果的標準 API 響應。
    :raises HTTPException(400): 如果檔案類型或大小不符合要求。
    :raises HTTPException(500): 如果在分析過程中發生錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始預覽分析報告: {file.filename}")

        # 檢查檔案類型
        allowed_types = [
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword'
        ]

        file_extension = Path(file.filename).suffix.lower()
        allowed_extensions = ['.docx', '.doc']

        if file.content_type not in allowed_types and file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail="請上傳 Word 檔案 (.docx 或 .doc)"
            )

        # 檢查檔案大小 (限制 10MB)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="檔案大小不能超過 10MB"
            )

        # 保存臨時檔案
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name

        try:
            # 獲取文檔分析服務
            analysis_service = get_report_analysis_service()

            # 執行預覽分析
            analysis_result = analysis_service.preview_analysis(temp_file_path)

            log_with_request_id("INFO", "報告預覽分析完成")

            return create_response(
                success=True,
                message="分析完成",
                data=analysis_result
            )

        finally:
            # 清理臨時檔案
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"清理臨時檔案失敗: {e}")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"預覽分析失敗: {error_msg}")
        logger.error(f"預覽分析失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"分析失敗: {error_msg}"
        )

@app.post("/api/analyze-performance-report")
async def analyze_performance_report(file: UploadFile = File(...)):
    """
    完整分析效能報告並生成新報告的 API 端點 (`/api/analyze-performance-report`)。

    接收一個 Word 檔案，呼叫 ReportAnalysisService 進行深度分析，
    並生成一份包含分析結果和建議的新 Word 報告，以檔案下載的方式回傳給使用者。
    :param file: 上傳的 Word 檔案。
    :return: 一個 StreamingResponse，觸發瀏覽器下載生成的報告檔案。
    :raises HTTPException(400): 如果檔案類型或大小不符合要求。
    :raises HTTPException(500): 如果在分析過程中發生錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始生成效能分析報告: {file.filename}")

        # 檢查檔案類型
        allowed_types = [
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/msword'
        ]

        file_extension = Path(file.filename).suffix.lower()
        allowed_extensions = ['.docx', '.doc']

        if file.content_type not in allowed_types and file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail="請上傳 Word 檔案 (.docx 或 .doc)"
            )

        # 檢查檔案大小 (限制 10MB)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="檔案大小不能超過 10MB"
            )

        # 保存臨時檔案
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name

        output_path = None  # 確保 output_path 在 finally 區塊中可見
        try:
            # 獲取報告分析服務
            analysis_service = get_report_analysis_service()

            # 生成分析報告
            output_path = analysis_service.generate_analysis_report(temp_file_path)

            if not os.path.exists(output_path):
                raise HTTPException(
                    status_code=500,
                    detail="分析報告生成失敗"
                )

            # 準備檔案下載
            def iterfile(file_path: str):
                with open(file_path, mode="rb") as file_like:
                    yield from file_like
                # 在迭代結束後刪除檔案
                try:
                    os.unlink(file_path)
                    logger.info(f"已刪除臨時輸出檔案: {file_path}")
                except Exception as e:
                    logger.warning(f"清理輸出檔案失敗: {e}")

            # 生成安全的檔案名稱（只使用英文和數字）
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            # 移除中文字符，只保留英文、數字和基本符號
            safe_original_name = re.sub(r'[^\w\-_\.]', '_', Path(file.filename).stem)
            download_filename = f"analysis_report_{safe_original_name}_{timestamp}.docx"

            # 進一步確保檔名安全
            download_filename = download_filename.encode('ascii', 'ignore').decode('ascii')
            if not download_filename or download_filename == '.docx':
                download_filename = f"analysis_report_{timestamp}.docx"

            log_with_request_id("INFO", f"效能分析報告生成完成: {download_filename}")

            # 使用 RFC 5987 編碼處理中文檔名
            original_name_utf8 = f"analysis_report_{Path(file.filename).stem}_{timestamp}.docx"
            encoded_filename = urllib.parse.quote(original_name_utf8.encode('utf-8'))

            # 設置正確的 Content-Disposition 標頭
            content_disposition = f"attachment; filename=\"{download_filename}\"; filename*=UTF-8''{encoded_filename}"

            return StreamingResponse(
                iterfile(output_path),
                media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                headers={
                    "Content-Disposition": content_disposition,
                    "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                }
            )

        finally:
            # 清理臨時上傳檔案
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.warning(f"清理臨時上傳檔案失敗: {e}")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"生成分析報告失敗: {error_msg}")
        logger.error(f"生成分析報告失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"生成報告失敗: {error_msg}"
        )

# 獲取系統日誌端點
@app.get("/api/logs", response_model=LogsResponse)
async def get_logs(limit: int = 100):
    """
    獲取系統日誌的 API 端點 (`/api/logs`)。
    :param limit: 要獲取的最大日誌條數，預設為 100。
    :return: 一個包含日誌列表的標準 API 響應。
    :raises HTTPException(503): 如果日誌服務當前不可用。
    """
    try:
        if not log_service:
            raise HTTPException(
                status_code=503,
                detail="日誌服務不可用"
            )

        logs = log_service.get_logs(limit)

        return create_response(
            success=True,
            message=f"獲取 {len(logs)} 條日誌",
            data={
                "logs": logs,
                "count": len(logs)
            }
        )

    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"獲取日誌失敗: {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"獲取日誌失敗: {error_msg}"
        )

# 處理 .docx 文件並提取文字
@app.post("/api/process-docx", response_model=APIResponse)
async def process_docx(file: UploadFile = File(...)):
    """
    處理 .docx 檔案並提取其純文字內容的 API 端點 (`/api/process-docx`)。
    :param file: 上傳的 .docx 檔案。
    :return: 一個包含提取出的純文字的標準 API 響應。
    :raises HTTPException(400): 如果檔案類型或大小不符合要求。
    :raises HTTPException(500): 如果在檔案處理過程中發生錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始處理 .docx 檔案: {file.filename}")

        # 檢查檔案類型
        file_extension = Path(file.filename).suffix.lower()
        if file_extension != '.docx':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="請上傳 .docx 格式的檔案"
            )

        # 確保在檢查大小和呼叫服務前，先讀取檔案內容
        content = await file.read()

        # 檢查檔案大小 (限制 10MB)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="檔案大小不能超過 10MB"
            )

        # 獲取服務實例
        doc_service = get_doc_processor_service()

        # 處理檔案 - 服務現在直接回傳文字或拋出異常
        extracted_text = await doc_service.process_docx_file(content, file.filename)

        log_with_request_id("INFO", f".docx 檔案處理成功: {file.filename}")

        # 將提取的文字封裝成前端期望的格式
        return create_response(
            success=True,
            message="檔案處理成功",
            data={"text": extracted_text}
        )

    except ValueError as e:
        log_with_request_id("ERROR", f"處理 .docx 檔案時發生業務錯誤: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"處理檔案失敗: {e}"
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"處理 .docx 檔案失敗: {error_msg}")
        logger.error(f"處理 .docx 檔案失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"處理檔案失敗: {error_msg}"
        )

# 從文件內容生成 Markdown 表格
@app.post("/api/generate-markdown", response_model=SpecAnalysisResponse)
async def generate_markdown(text_data: dict):
    """
    從純文字內容中並行提取 Header (JSON 格式) 和 Body (Markdown 表格) 的 API 端點 (`/api/generate-markdown`)。

    此端點使用 `asyncio.gather` 來同時啟動兩個 LLM 任務，以提高處理效率。
    :param text_data: 一個包含 `text` 和 `filename` 的字典。
    :return: 一個包含 `header_json` 和 `body_markdown` 的標準 API 響應。
    :raises HTTPException(400): 如果輸入的文字為空。
    :raises HTTPException(500): 如果在提取過程中發生錯誤。
    """
    try:
        text = text_data.get("text", "")
        filename = text_data.get("filename", "unknown")

        if not text:
            raise HTTPException(
                status_code=400,
                detail="文字內容不能為空"
            )

        log_with_request_id("INFO", f"開始為檔案 {filename} 提取 Header 和 Body")

        # 獲取服務實例
        spec_service = get_spec_analysis_service()

        # 使用 asyncio.gather 並行執行兩個任務
        header_task = spec_service.generate_header_json_from_doc(text, filename)
        body_task = spec_service.generate_body_markdown_from_doc(text, filename)

        # 等待兩個任務完成
        header_result, body_result = await asyncio.gather(header_task, body_task)

        # 檢查是否有任何一個任務失敗 (但仍然回傳成功的部份)
        if not header_result and not body_result:
             raise ValueError("無法從文件中提取任何 Header 或 Body 資訊。")

        log_with_request_id("INFO", f"Header 和 Body 提取成功，檔案: {filename}")

        # 組合回傳結果
        response_data = {
            "header_json": header_result,
            "body_markdown": body_result,
            "filename": filename
        }

        return create_response(
            success=True,
            message="規格提取成功",
            data=response_data
        )

    except ValueError as e:
        log_with_request_id("ERROR", f"規格提取失敗 (ValueError): {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"規格提取失敗: {error_msg}")
        logger.error(f"規格提取失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"規格提取失敗: {error_msg}"
        )

# 校對 Markdown 表格
@app.post("/api/review-markdown", response_model=APIResponse)
async def review_markdown(request: MarkdownReviewRequest):
    """
    根據使用者輸入，校對已生成的 Body Markdown 表格的 API 端點 (`/api/review-markdown`)。
    :param request: 包含原始 `markdown` 和使用者 `user_input` 的請求主體。
    :return: 一個包含校對後 Markdown 的標準 API 響應。
    :raises HTTPException(400): 如果 LLM 在校對過程中返回錯誤。
    :raises HTTPException(500): 如果在處理過程中發生內部錯誤。
    """
    try:
        log_with_request_id("INFO", f"開始校對 Markdown 表格")

        # 獲取服務實例
        spec_service = get_spec_analysis_service()

        # 校對 Markdown 表格
        result = await spec_service.review_markdown_with_llm(
            request.markdown,
            request.user_input,
            filename="review_request"
        )

        if "error" in result and result["error"]:
            raise HTTPException(
                status_code=400,
                detail=f"校對 Markdown 表格失敗: {result['error']}"
            )

        log_with_request_id("INFO", f"Markdown 表格校對完成")

        return create_response(
            success=True,
            message="Markdown 表格校對完成",
            data=result
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"校對 Markdown 表格失敗: {error_msg}")
        logger.error(f"校對 Markdown 表格失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"校對 Markdown 表格失敗: {error_msg}"
        )

# 校對 JSON Header
@app.post("/api/review-header-json", response_model=APIResponse)
async def review_header_json(request: HeaderJsonReviewRequest):
    """
    根據使用者輸入，校對已生成的 Header JSON 範例的 API 端點 (`/api/review-header-json`)。
    :param request: 包含原始 `header_markdown` 和使用者 `user_input` 的請求主體。
    :return: 一個包含校對後 Header JSON 的標準 API 響應。
    :raises HTTPException(400): 如果 LLM 在校對過程中返回錯誤。
    :raises HTTPException(500): 如果在處理過程中發生內部錯誤。
    """
    try:
        log_with_request_id("INFO", "開始校對 Header JSON")

        # 獲取服務實例
        spec_service = get_spec_analysis_service()

        # 呼叫新的服務函式來校對 Header JSON
        result = await spec_service.review_header_json_with_llm(
            header_markdown=request.header_markdown,
            user_input=request.user_input,
            filename="header_review_request"  # 提供一個用於日誌的虛擬檔名
        )

        # 檢查服務層是否回傳錯誤
        if "error" in result and result["error"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"校對 Header JSON 失敗: {result['error']}"
            )

        log_with_request_id("INFO", "Header JSON 校對完成")

        return create_response(
            success=True,
            message="Header JSON 校對完成",
            data=result  # 直接回傳服務層的完整結果
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"校對 Header JSON 失敗: {error_msg}")
        logger.error(f"校對 Header JSON 失敗: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"校對 Header JSON 失敗: {error_msg}"
        )

# 校對合成資料
@app.post("/api/review-synthetic-data", response_model=APIResponse)
async def review_synthetic_data(request: SyntheticDataReviewRequest):
    """
    根據使用者輸入，校對已生成的合成資料的 API 端點 (`/api/review-synthetic-data`)。
    :param request: 包含原始 `synthetic_data_markdown` 和使用者 `user_input` 的請求主體。
    :return: 一個包含校對後合成資料 (Markdown 和 CSV 格式) 的標準 API 響應。
    :raises HTTPException(400): 如果 LLM 在校對過程中返回錯誤。
    :raises HTTPException(500): 如果在處理過程中發生內部錯誤。
    """
    try:
        log_with_request_id("INFO", "開始校對合成資料")
        spec_service = get_spec_analysis_service()

        # 呼叫新的服務函式
        result = await spec_service.review_synthetic_data_with_llm(
            synthetic_markdown=request.synthetic_data_markdown,
            user_input=request.user_input
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"校對合成資料失敗: {result.get('error', '未知錯誤')}"
            )

        log_with_request_id("INFO", "合成資料校對完成")
        return create_response(
            success=True,
            message="合成資料校對完成",
            data=result.get("data") # 直接回傳包含 markdown 和 csv 的 data 物件
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        log_with_request_id("ERROR", f"校對合成資料時發生錯誤: {error_msg}")
        logger.error(f"校對合成資料時發生錯誤: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"校對合成資料失敗: {error_msg}"
        )

# 生成合成資料
async def run_synthetic_data_generation(task_id: str, body_markdown: str, header_json_markdown: str,
                                          full_doc_text: str,  filename: str, num_rows: int):
    """
    在背景執行的合成資料生成任務。

    這不是一個直接的 API 端點，而是由 `start_generation_task` 啟動的背景工作函式。
    它會呼叫 SynDataGenService 來執行耗時的 LLM 生成操作，並在完成後更新全域 `tasks` 字典的狀態。
    :param task_id: 此任務的唯一 ID。
    :param body_markdown: Body 規格的 Markdown 表格。
    :param header_json_markdown: Header 規格的 JSON 範例。
    :param full_doc_text: 完整的原始文件內容，用於提供更多上下文。
    :param filename: 原始檔案名稱，用於日誌和上下文。
    :param num_rows: 要生成的資料筆數。
    """
    try:
        spec_service = get_spec_analysis_service()
        log_with_request_id("INFO", f"背景任務 {task_id} 開始生成 {num_rows} 筆資料...")

        # 將 full_doc_text 傳遞給服務函式
        result = await spec_service.generate_data_from_markdown(
            body_markdown=body_markdown,
            header_json_markdown=header_json_markdown,
            full_doc_text=full_doc_text,
            context_id=filename,
            num_records=num_rows
        )

        if not result.get("success"):
            raise Exception(result.get("error", "未知錯誤"))

        service_data = result.get("data", {})
        markdown_data = service_data.get("markdown_content", "")
        csv_data = service_data.get("csv_content", "")

        tasks[task_id] = {
            "status": "complete",
            "result": {
                "data": {
                    "synthetic_data_markdown": markdown_data,
                    "synthetic_data_csv": csv_data
                }
            }
        }
        log_with_request_id("INFO", f"背景任務 {task_id} 成功完成")

    except Exception as e:
        error_msg = f"背景任務 {task_id} 失敗: {str(e)}"
        log_with_request_id("ERROR", error_msg)
        logger.error(error_msg, exc_info=True)
        tasks[task_id] = {"status": "error", "error": str(e)}

# 1: 啟動任務
@app.post("/api/start-synthetic-data-task", response_model=APIResponse)
async def start_generation_task(request_data: TaskStartRequest, background_tasks: BackgroundTasks):
    """
    啟動一個背景任務來生成合成資料的 API 端點 (`/api/start-synthetic-data-task`)。

    此端點會立即回傳一個 `task_id` 給前端，然後使用 FastAPI 的 `BackgroundTasks`
    在背景執行耗時的 `run_synthetic_data_generation` 函式，避免請求超時。
    :param request_data: 包含所有生成所需參數的請求主體。
    :param background_tasks: FastAPI 提供的背景任務管理器。
    :return: 一個包含 `task_id` 的標準 API 響應。
    :raises HTTPException(500): 如果在啟動任務時發生錯誤。
    """
    try:
        # 從請求中讀取所有必要的資料
        body_markdown = request_data.body_markdown
        header_json_markdown = request_data.header_json_markdown
        full_doc_text = request_data.full_doc_text  # <<< 讀取新欄位
        filename = request_data.filename
        num_rows = request_data.num_rows

        task_id = str(uuid.uuid4())
        tasks[task_id] = {"status": "processing"}

        background_tasks.add_task(
            run_synthetic_data_generation,
            task_id,
            body_markdown,
            header_json_markdown,
            full_doc_text,
            filename,
            num_rows
        )

        log_with_request_id("INFO", f"已啟動背景任務 {task_id} 用於生成 {num_rows} 筆合成資料")

        return create_response(
            success=True,
            message="任務已成功啟動",
            data={"task_id": task_id}
        )

    except Exception as e:
        error_msg = f"啟動生成任務時發生非預期錯誤: {str(e)}"
        log_with_request_id("ERROR", error_msg)
        logger.error(error_msg, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_msg
        )

# 2: 查詢狀態
@app.get("/api/get-task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    查詢背景任務狀態的 API 端點 (`/api/get-task-status/{task_id}`)。

    前端可以使用此端點，透過 `task_id` 定期輪詢任務的進度 (processing, complete, error)。
    :param task_id: 要查詢的任務 ID。
    :return: 一個包含任務狀態和結果 (如果已完成) 的字典。
    """
    task = tasks.get(task_id)
    if not task:
        # 即使任務還沒在字典中創建，也返回 processing，給背景任務一點時間
        return {"status": "processing", "message": "Task initializing..."}
    return task

@app.post("/api/es/upload")
async def es_upload_files(files: List[UploadFile] = File(...),
    index_name: str = Form(default="cathay_project1_chunks"),
    deleteExisting: str = Form(default="false")
):
    """
    上傳多個檔案至 Elasticsearch 並進行索引的 API 端點 (`/api/es/upload`)。

    此端點會接收檔案，將其分割成塊 (chunks)，生成向量嵌入，然後存入指定的 Elasticsearch 索引。
    :param files: 一個從表單中獲取的 UploadFile 物件列表。
    :param index_name: 目標 Elasticsearch 索引的名稱。
    :param deleteExisting: 是否在上传前刪除已存在的同名索引。
    :return: 一個包含操作結果的標準 API 響應。
    :raises HTTPException(500): 如果在處理過程中發生錯誤。
    """
    uploader = get_elasticsearch_service()
    delete_existing = deleteExisting.lower() in ('true', '1', 'yes')
    temp_files = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            for file in files:
                temp_path = os.path.join(temp_dir, file.filename)
                with open(temp_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                temp_files.append(temp_path)

            success = uploader.upload_multiple_files(
                file_paths=temp_files,
                index_name=index_name,
                delete_existing=delete_existing
            )
            stats = uploader.client.count(index=index_name)
            return create_response(
                success=success,
                message=f"處理了 {len(temp_files)} 個檔案",
                data={"total_docs_in_index": stats.get('count', 'N/A')}
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/es/search")
async def es_search_documents( query: str = Form(...),
    index_name: str = Form(default="cathay_project1_chunks"),
    k: int = Form(default=5)
):
    """
    在 Elasticsearch 中執行向量相似度搜尋的 API 端點 (`/api/es/search`)。
    :param query: 使用者的自然語言查詢。
    :param index_name: 要搜尋的目標索引名稱。
    :param k: 要返回的最相似結果數量。
    :return: 一個包含搜尋結果列表的標準 API 響應。
    :raises HTTPException(500): 如果在搜尋過程中發生錯誤。
    """

    uploader = get_elasticsearch_service()
    try:
        results = uploader.search_with_score(query, index_name, k)
        search_results = [{
            "content": doc.page_content,
            "metadata": doc.metadata,
            "score": float(score)
        } for doc, score in results]
        return create_response(success=True, message="搜尋成功", data={"results": search_results})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat", response_model=ChatResponse)
async def chat(chat_message: ChatMessage):
    """
    處理聊天機器人訊息的 API 端點 (`/api/chat`)。

    此端點作為聊天前端和 Langflow 後端之間的橋樑。它負責：
    1. 管理使用者會話 (session)。
    2. 將使用者的訊息轉發給 Langflow 服務。
    3. 接收 Langflow 的回應並返回給前端。
    4. 記錄對話歷史。
    :param chat_message: 包含 `message` 和可選 `session_id` 的請求主體。
    :return: 一個包含機器人回應和 `session_id` 的標準 API 響應。
    :raises HTTPException(500): 如果在處理過程中發生內部錯誤。
    """
    try:
        # 獲取或創建 session
        session_id = chat_message.session_id or str(uuid.uuid4())
        if session_id not in sessions:
            sessions[session_id] = []

        log_with_request_id("INFO", f"收到來自 session {session_id} 的訊息: {chat_message.message}")

        # 獲取 Langflow 服務並發送訊息
        langflow_svc = get_langflow_service()
        response_text = await langflow_svc.send_chat_message(chat_message.message, session_id)

        # 將對話加入歷史紀錄
        history_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_message": chat_message.message,
            "bot_response": response_text
        }
        sessions[session_id].append(history_entry)

        # 保持每個 session 最多 50 條歷史紀錄
        if len(sessions[session_id]) > 50:
            sessions[session_id] = sessions[session_id][-50:]

        # 準備回傳資料
        response_data = ChatResponseData(
            response=response_text,
            session_id=session_id,
            timestamp=datetime.datetime.now().isoformat()
        )

        return create_response(
            success=True,
            message="訊息處理成功",
            data=response_data
        )

    except HTTPException:
        # 重新拋出已知的 HTTP 異常，讓全域處理器捕捉
        raise
    except Exception as e:
        # 捕捉其他未預期的錯誤
        error_msg = f"處理聊天訊息時發生錯誤: {str(e)}"
        log_with_request_id("ERROR", error_msg)
        logger.error(error_msg, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="處理聊天訊息時發生內部錯誤"
        )