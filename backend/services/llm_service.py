import os
import threading
from typing import Dict, Optional
from ibm_watsonx_ai.foundation_models import ModelInference
from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
from ibm_watsonx_ai.credentials import Credentials
from dotenv import load_dotenv
from .logger import get_logger

load_dotenv()

class LLMService:
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化 LLM 服務
        :param config: 可選的配置字典，用於自定義模型參數
        """
        self.config = config or {}
        self.model = None
        self._model_lock = threading.Lock()
        self._initialized = False
        self.logger = get_logger(__name__)

    def initialize(self):
        """顯式初始化模型，可以在需要時才調用"""
        if not self._initialized:
            with self._model_lock:
                if not self._initialized:
                    self._initialize_model()
                    self._initialized = True

    def _ensure_model_initialized(self):
        """確保模型已初始化，如果沒有則初始化"""
        if not self._initialized:
            self.initialize()

    def _initialize_model(self):
        """初始化 WatsonX 模型"""
        try:
            config = self._get_config()
            self._validate_config(config)

            # 確保 model_id 是小寫
            model_id = config["model_id"].lower()
            
            # 準備認證參數
            credentials_params = {
                "url": config["url"],
                "api_key": config["api_key"]
            }
            
            # 如果提供了 instance_id，則添加到認證參數中
            if config.get("instance_id"):
                credentials_params["instance_id"] = config["instance_id"]
            
            self.model = ModelInference(
                model_id=model_id,
                params={
                    GenParams.DECODING_METHOD: "greedy",
                    GenParams.MAX_NEW_TOKENS: config.get("max_tokens", 4000),
                    GenParams.TEMPERATURE: config.get("temperature", 0.1),
                    GenParams.TOP_P: config.get("top_p", 1.0),
                    GenParams.TOP_K: config.get("top_k", 50),
                    GenParams.REPETITION_PENALTY: config.get("repetition_penalty", 1.0)
                },
                credentials=Credentials(**credentials_params),
                project_id=config["project_id"]
            )
            self.logger.info(f"WatsonX 模型初始化成功 (Model: {config['model_id']})")
        except Exception as e:
            self.logger.error(f"模型初始化失敗: {e}")
            raise

    def _get_config(self) -> Dict:
        """獲取合併後的配置"""
        # 獲取環境變數並將 model_id 轉換為小寫
        model_id = os.getenv("MODEL_ID", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8").lower()
        
        default_config = {
            "url": os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            "api_key": os.getenv("WATSONX_API_KEY"),
            "project_id": os.getenv("WATSONX_PROJECT_ID"),
            "instance_id": os.getenv("INSTANCE_ID"),  # 添加 instance_id
            "model_id": model_id,
            "max_tokens": 4000,
            "temperature": 0.1,
            "top_p": 1.0,
            "top_k": 50,
            "repetition_penalty": 1.0
        }
        
        # 合併默認配置和用戶提供的配置
        return {**default_config, **self.config}

    def _validate_config(self, config: Dict):
        """驗證配置"""
        if not config["api_key"]:
            raise ValueError("WATSONX_API_KEY 環境變數未設定")
        if not config["project_id"]:
            raise ValueError("WATSONX_PROJECT_ID 環境變數未設定")

        self.logger.info(f"WatsonX URL: {config['url']}")
        self.logger.info(f"Model ID: {config['model_id']}")
        self.logger.info(f"API Key 已設定: {'是' if config['api_key'] else '否'}")
        self.logger.info(f"Project ID 已設定: {'是' if config['project_id'] else '否'}")

    def generate_text(self, prompt: str, **kwargs) -> str:
        """
        生成文字內容。
        :param prompt: 提示詞。
        :param kwargs: 可選的生成參數 (例如 temperature, max_new_tokens)，會覆蓋初始化時的預設值。
        :return: 生成的文字。
        """
        self._ensure_model_initialized()
        try:
            # 【修正】將傳入的 kwargs 作為參數覆蓋字典 (params) 傳遞給底層模型。
            # 如果沒有傳入 kwargs，則 params 為 None，模型將使用初始化時的預設參數。
            override_params = kwargs if kwargs else None

            response = self.model.generate_text(prompt=prompt, params=override_params)
            if not response or not response.strip():
                raise ValueError("模型返回空響應")
            return response
        except Exception as e:
            self.logger.error(f"文字生成失敗: {e}")
            raise