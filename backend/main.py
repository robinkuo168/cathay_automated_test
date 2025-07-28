from fastapi import FastAPI, UploadFile, File, HTTPException, status, Request, BackgroundTasks, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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
    在應用程式啟動時設定全域日誌。
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
    獲取或創建指定名稱的 LLMService 實例
    :param model_name: 模型名稱，用於區分不同的實例
    :param config: 可選的配置字典，用於自定義模型參數
    :return: LLMService 實例
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
    獲取 JMX 服務實例，可以指定使用的 LLM 模型
    :param model_name: 要使用的 LLM 模型名稱
    :return: JMXGeneratorService 實例
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
    """執行緒安全的 DocumentProcessorService 初始化"""
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
    """執行緒安全的 SpecAnalysisService 初始化"""
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
    """執行緒安全的 ElasticsearchService 初始化"""
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
    """執行緒安全的 LangflowService 初始化"""
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
    """延遲初始化報告分析服務"""
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
    應用程式生命週期管理。
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

    logger.info("✅ 應用程式已成功啟動並準備就緒。")
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
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan  # 使用新的 lifespan 管理器
)

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
    """為每個請求添加唯一 ID"""
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    start_time = asyncio.get_event_loop().time()
    response = await call_next(request)
    process_time = asyncio.get_event_loop().time() - start_time

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)

    logger.info(f"[{request_id}] {request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
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
    """帶請求 ID 的日誌記錄"""
    request_id = request_id_var.get("unknown")
    if log_service:
        log_service.add_log(level, f"[{request_id}] {message}")
    else:
        logger.log(getattr(logging, level.upper()), f"[{request_id}] {message}")

def create_response(success: bool, message: str, data: Any = None, error: str = None) -> dict:
    """創建標準響應"""
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
    """HTTP 異常處理器"""
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
    """全域異常處理器"""
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
    """根路徑"""
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
    """健康檢查"""
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

@app.on_event("startup")
async def startup_event():
    """應用程式啟動事件"""
    try:
        # 確保上傳和輸出目錄存在
        UPLOAD_DIR.mkdir(exist_ok=True)
        OUTPUT_DIR.mkdir(exist_ok=True)

        # 預先加載默認模型
        default_config = {
            "model_id": os.getenv("MODEL_ID", "meta-llama/llama-3-3-70b-instruct"),
            "max_tokens": 4000,
            "temperature": 0.1
        }

        # 初始化默認模型
        default_service = get_llm_service("default", default_config)
        default_service.initialize()

        # 可以在此添加其他預設模型的初始化
        # fast_service = get_llm_service("fast", {"max_tokens": 2000, "temperature": 0.7})
        # fast_service.initialize()

        logger.info(f"應用程式啟動完成，已加載模型: {list(_llm_services.keys())}")
    except Exception as e:
        logger.error(f"啟動時發生錯誤: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"健康檢查失敗: {str(e)}"
        )

# JMX 生成端點路徑
@app.post("/api/generate-jmx", response_model=JMXResponse)
async def generate_jmx(request: JMXRequest):
    """生成 JMX 檔案 - 修正端點路徑"""
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
    檔案上傳端點。
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
    """驗證 XML 格式"""
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
    """預覽分析報告"""
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
    """生成完整的效能分析報告"""
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
    """獲取系統日誌"""
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
    """處理上傳的 .docx 文件並提取文字內容"""
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
    從文件內容中同時提取 Header JSON 範例和 Body Markdown 表格。
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
    """校對 Markdown 表格"""
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
    根據使用者輸入，校對包含 Header JSON 範例的 Markdown 字串。
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
    根據使用者輸入，校對已生成的合成資料。
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
    """在背景執行的 LLM 生成任務"""
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
    接收請求，立即返回 task_id，並在背景啟動耗時的生成任務。
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
    根據 task_id 查詢任務的當前狀態。
    """
    task = tasks.get(task_id)
    if not task:
        # 即使任務還沒在字典中創建，也返回 processing，給背景任務一點時間
        return {"status": "processing", "message": "Task initializing..."}
    return task

@app.post("/api/es/upload")
async def es_upload_files(
    files: List[UploadFile] = File(...),
    index_name: str = Form(default="cathay_project1_chunks"),
    deleteExisting: str = Form(default="false")
):
    """上傳多個檔案至 Elasticsearch"""
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
async def es_search_documents(
    query: str = Form(...),
    index_name: str = Form(default="cathay_project1_chunks"),
    k: int = Form(default=5)
):
    """在 Elasticsearch 中進行向量搜尋"""
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
    處理聊天訊息，將其發送到 Langflow 並返回回應。
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