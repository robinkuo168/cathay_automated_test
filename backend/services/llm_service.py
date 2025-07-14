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

            self.model = ModelInference(
                model_id=config["model_id"],
                params={
                    GenParams.DECODING_METHOD: "greedy",
                    GenParams.MAX_NEW_TOKENS: config.get("max_tokens", 4000),
                    GenParams.TEMPERATURE: config.get("temperature", 0.1),
                    GenParams.TOP_P: config.get("top_p", 1.0),
                    GenParams.TOP_K: config.get("top_k", 50),
                    GenParams.REPETITION_PENALTY: config.get("repetition_penalty", 1.0)
                },
                credentials=Credentials(
                    url=config["url"],
                    api_key=config["api_key"]
                ),
                project_id=config["project_id"]
            )
            self.logger.info(f"WatsonX 模型初始化成功 (Model: {config['model_id']})")
        except Exception as e:
            self.logger.error(f"模型初始化失敗: {e}")
            raise

    def _get_config(self) -> Dict:
        """獲取合併後的配置"""
        default_config = {
            "url": os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            "api_key": os.getenv("WATSONX_API_KEY"),
            "project_id": os.getenv("WATSONX_PROJECT_ID"),
            "model_id": os.getenv("MODEL_ID", "meta-llama/llama-3-3-70b-instruct"),
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
        生成文字內容
        :param prompt: 提示詞
        :param kwargs: 可選的生成參數，會覆蓋默認參數
        :return: 生成的文字
        """
        self._ensure_model_initialized()
        try:
            # 使用傳入的參數或默認參數
            generate_kwargs = {}
            if kwargs:
                generate_kwargs = kwargs
            
            response = self.model.generate_text(prompt=prompt, **generate_kwargs)
            if not response or not response.strip():
                raise ValueError("模型返回空響應")
            return response
        except Exception as e:
            self.logger.error(f"文字生成失敗: {e}")
            raise