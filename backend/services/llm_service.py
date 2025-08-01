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
        初始化 LLMService。

        此建構函式採用延遲初始化 (lazy initialization) 模式，
        僅設定服務的配置和狀態，而不會立即建立與 WatsonX 的連線。
        實際的模型初始化會在第一次需要時才執行。
        :param config: (可選) 一個配置字典，用於覆蓋從環境變數讀取的預設模型參數。
        """
        self.config = config or {}
        self.model = None
        self._model_lock = threading.Lock()
        self._initialized = False
        self.logger = get_logger(__name__)

    def initialize(self):
        """
        顯式地初始化 LLM 模型。

        這是一個執行緒安全 (thread-safe) 的函式，可以在應用程式啟動時預先呼叫，
        以避免在第一個請求到達時才進行耗時的初始化操作。
        它使用鎖 (lock) 和旗標 (flag) 來確保模型在整個應用程式生命週期中只被初始化一次。
        """
        if not self._initialized:
            with self._model_lock:
                if not self._initialized:
                    self._initialize_model()
                    self._initialized = True

    def _ensure_model_initialized(self):
        """
        一個內部輔助函式，用於在使用模型前確保其已被初始化。

        如果模型尚未初始化，此函式會觸發 `initialize` 流程。
        """
        if not self._initialized:
            self.initialize()

    def _initialize_model(self):
        """
        執行實際的 WatsonX 模型初始化操作。

        此函式負責組合所有配置、建立憑證物件，並實例化 `ModelInference` 類別，
        建立與 WatsonX 服務的實際連線。
        :raises ValueError: 如果必要的環境變數 (如 API Key, Project ID) 未設定。
        :raises Exception: 如果在與 WatsonX 服務連線或初始化模型時發生任何其他錯誤。
        """
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
        """
        一個內部輔-助函式，用於合併和獲取最終的服務配置。

        它會先從環境變數載入一套預設配置，然後用實例初始化時傳入的 `self.config`
        來覆蓋這些預設值，從而實現靈活的配置管理。
        :return: 一個包含所有最終配置參數的字典。
        """
        model_id = os.getenv("MODEL_ID", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8").lower()
        
        default_config = {
            "url": os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            "api_key": os.getenv("WATSONX_API_KEY"),
            "project_id": os.getenv("WATSONX_PROJECT_ID"),
            "instance_id": os.getenv("INSTANCE_ID"),
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
        """
        一個內部輔助函式，用於在初始化模型前驗證配置的完整性。

        :param config: 包含所有配置參數的字典。
        :raises ValueError: 如果缺少必要的配置項，例如 `api_key` 或 `project_id`。
        """
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
        使用已初始化的 LLM 模型生成文字，是此服務最主要的公開方法。

        它會先確保模型已準備就緒，然後將提示詞傳遞給模型。
        它還允許在單次呼叫中，透過關鍵字參數 (kwargs) 臨時覆蓋預設的生成參數。
        :param prompt: 要傳遞給模型的提示詞字串。
        :param kwargs: (可選) 用於單次呼叫的生成參數，例如 `temperature`, `max_new_tokens`。
        :return: 模型生成的回應文字字串。
        :raises ValueError: 如果模型返回了空的或無效的回應。
        :raises Exception: 如果在與 WatsonX API 互動過程中發生錯誤。
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