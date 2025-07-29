# /backend/services/langflow_service.py
import os
import httpx
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional
from dotenv import load_dotenv
from fastapi import HTTPException
import io
import requests

load_dotenv()
logger = logging.getLogger(__name__)

class LangFlowAPIKeyManager:
    """
    Manages LangFlow API keys with a robust, file-based persistence strategy.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        # 將 API 金鑰儲存在一個簡單的文字檔中
        self.api_key_file = 'langflow_api_key.txt'

    def load_api_key(self) -> Optional[str]:
        """從本地檔案載入 API 金鑰。"""
        if os.path.exists(self.api_key_file):
            with open(self.api_key_file, 'r') as f:
                key = f.read().strip()
                if key:
                    logger.info(f"🔑 從 '{self.api_key_file}' 載入已儲存的 API 金鑰。")
                    return key
        return None

    def save_api_key(self, api_key: str):
        """將新的 API 金鑰儲存到本地檔案。"""
        with open(self.api_key_file, 'w') as f:
            f.write(api_key)
        logger.info(f"新的 API 金鑰已成功儲存至 '{self.api_key_file}'。")

    async def generate_api_key(self, key_name: Optional[str] = None) -> Optional[str]:
        """產生一個新的 API 金鑰。"""
        if not key_name:
            key_name = f"main-api-key-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        api_key_data = {"name": key_name}
        # Langflow 的 API 端點有時不一致，嘗試兩個常見的變體
        endpoints_to_try = ["/api/v1/api_key/", "/api/v1/api-key/"]

        logger.info(f"正在產生新的 API 金鑰: {key_name}")
        for endpoint in endpoints_to_try:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(f"{self.base_url}{endpoint}", json=api_key_data)
                    if response.status_code in [200, 201]:
                        api_key = response.json().get('api_key')
                        if api_key:
                            logger.info("✅ API 金鑰產生成功！")
                            return api_key
            except Exception as e:
                logger.warning(f"嘗試端點 {endpoint} 失敗: {e}")

        logger.error("在所有端點上都無法產生 API 金鑰。")
        return None

    async def test_api_key(self, api_key: str) -> bool:
        """測試一個 API 金鑰是否有效。"""
        if not api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.base_url}/api/v1/flows", headers={"x-api-key": api_key})
                if response.status_code == 200:
                    logger.info("API 金鑰測試通過！")
                    return True
                logger.warning(f"API 金鑰測試失敗，狀態碼: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"測試 API 金鑰時發生錯誤: {e}")
            return False

    async def setup_api_key(self) -> Optional[str]:
        """
        設定 API 金鑰的核心邏輯。
        它會先嘗試載入並測試現有的金鑰，如果失敗，才會產生新的金鑰。
        """
        logger.info("🚀 正在設定 API 金鑰...")

        # 步驟 1: 嘗試載入已儲存的金鑰
        existing_key = self.load_api_key()

        # 步驟 2: 如果有已儲存的金鑰，測試其有效性
        if existing_key and await self.test_api_key(existing_key):
            logger.info("🎉 使用現有的有效 API 金鑰。")
            return existing_key

        # 步驟 3: 如果沒有金鑰或金鑰已失效，則產生一個新的
        logger.info("找不到有效的現有金鑰，正在產生新的金鑰...")
        new_api_key = await self.generate_api_key("main-chatbot-key")

        # 步驟 4: 測試新產生的金鑰並儲存
        if new_api_key and await self.test_api_key(new_api_key):
            self.save_api_key(new_api_key)
            logger.info("🎉 新的 API 金鑰設定完成！")
            return new_api_key

        logger.error("❌ API 金鑰設定失敗。無法產生或驗證新的金鑰。")
        return None

class LangflowService:
    def __init__(self):
        # 從環境變數讀取設定
        self.base_url = os.getenv("LANGFLOW_BASE_URL")
        self.project_name = os.getenv("LANGFLOW_PROJECT_NAME")

        if not self.base_url:
            raise ValueError("Langflow 的環境變數 LANGFLOW_BASE_URL 未設定！")

        self.api_key = None
        self.project_id = None
        self.chat_flow_id = None
        self.api_key_manager = LangFlowAPIKeyManager(self.base_url)

    async def setup_api_key(self) -> bool:
        """使用新的管理器設定 API 金鑰。"""
        api_key = await self.api_key_manager.setup_api_key()
        if api_key:
            self.api_key = api_key
            return True
        return False

    async def get_project_id(self) -> str:
        """Fetch project ID from Langflow API"""
        url = f"{self.base_url}/api/v1/projects/"
        headers = {"x-api-key": self.api_key}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            projects = response.json()
            if not projects:
                raise Exception("No projects found in Langflow")

            if self.project_name:
                project = next((p for p in projects if p["name"] == self.project_name), projects[0])
            else:
                project = projects[0]

            logger.info(f"Using project: {project['name']} (ID: {project['id']})")
            return project["id"]

    async def get_latest_flow_id(self) -> Optional[str]:
        """Fetch the latest flow ID from the project"""
        if not self.project_id:
            self.project_id = await self.get_project_id()

        url = f"{self.base_url}/api/v1/flows/"
        headers = {"x-api-key": self.api_key}
        params = {"project_id": self.project_id}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            flows = response.json()
            if not flows:
                return None

            latest_flow = flows[-1]
            logger.info(
                f"Found {len(flows)} flows. Latest is '{latest_flow.get('name', 'Unknown')}' with ID: {latest_flow['id']}")
            return latest_flow["id"]

    async def initialize_flow(self):
        """
        初始化或刷新 Langflow 服務。
        此過程包含：
        1. 確保設定了有效的 API 金鑰。
        2. 刪除最新的流程以確保狀態乾淨。
        3. 從 Elasticsearch 獲取最新的代理 (Agent) 定義。
        4. 將新的代理定義作為新流程上傳到 Langflow。
        5. 更新服務以使用新建立的流程 ID。
        """
        logger.info("🔧 正在初始化或刷新 Langflow 流程...")

        # 步驟 1: 確保 API 金鑰已設定且有效
        if not self.api_key:
            logger.info("API 金鑰未設定，正在執行設定程序...")
            if not await self.setup_api_key():
                raise Exception("致命錯誤：無法設定 Langflow API 金鑰。")
        else:
            logger.info("✅ 服務已有 API 金鑰。")

        # 步驟 2: 獲取專案 ID
        if not self.project_id:
            self.project_id = await self.get_project_id()
            logger.info(f"✅ 專案 ID 已設定為: {self.project_id}")

        # 步驟 3: 刪除最新的流程以確保狀態乾淨
        try:
            most_recent_flow_id = await self.get_latest_flow_id()
            if most_recent_flow_id:
                logger.info(f"找到最新的流程 '{most_recent_flow_id}'，嘗試刪除...")
                await self.delete_flow(most_recent_flow_id)
            else:
                logger.info("找不到任何現有流程可刪除，將直接上傳新流程。")
        except HTTPException as e:
            if e.status_code == 404 or "No flows found" in str(e.detail):
                logger.info("找不到任何現有流程可刪除，這在首次運行時是正常的。")
            else:
                logger.error(f"無法刪除現有流程，但仍將繼續執行: {e.detail}")
        except Exception as e:
            logger.error(f"刪除流程時發生未預期錯誤，但仍將繼續執行: {e}")

        # 步驟 4: 從 Elasticsearch 獲取最新的代理定義
        logger.info("🚚 正在從 Elasticsearch 獲取最新的代理定義...")
        # 這裡的 import 是為了避免模組層級的循環依賴
        from .elasticsearch_service import ElasticsearchService
        try:
            es_service = ElasticsearchService()
            # 注意：您的 ElasticsearchService 需要有 get_agent_json_bytes 方法
            json_bytes = await es_service.get_agent_json_bytes()
        except Exception as e:
            logger.error(f"從 Elasticsearch 獲取代理定義失敗: {e}")
            raise HTTPException(status_code=500, detail="無法從 Elasticsearch 獲取代理定義。")

        # 步驟 5: 將新的代理版本作為新流程上傳
        logger.info("📤 正在將新的代理流程上傳至 Langflow...")
        await self.upload_flow_from_bytes(json_bytes)

        # 步驟 6: 獲取新上傳流程的 ID
        logger.info("🔍 正在檢索新上傳流程的 ID...")
        latest_flow_id = await self.get_latest_flow_id()
        if not latest_flow_id:
            raise Exception("致命錯誤：上傳後無法獲取流程 ID，聊天機器人將無法工作。")

        # 步驟 7: 更新服務以使用新的流程 ID
        await self.update_flow_id(latest_flow_id)
        logger.info(f"🎉 流程初始化完成。服務現在使用流程 ID: {self.chat_flow_id}")

    async def delete_flow(self, flow_id: str) -> bool:
        """根據 ID 刪除一個流程"""
        url = f"{self.base_url}/api/v1/flows/{flow_id}"
        headers = {"x-api-key": self.api_key}
        logger.info(f"正在刪除流程，ID: {flow_id}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(url, headers=headers)
            if response.status_code in [200, 204]:
                logger.info(f"成功刪除流程: {flow_id}")
                return True
            else:
                logger.error(f"刪除流程失敗: {response.status_code} - {response.text}")
                return False

    async def update_flow_id(self, new_flow_id: str):
        """更新服務實例中的 chat_flow_id。"""
        old_flow_id = self.chat_flow_id
        self.chat_flow_id = new_flow_id
        logger.info(f"流程 ID 已從 {old_flow_id} 更新為 {new_flow_id}")

    async def upload_flow_from_bytes(self, json_bytes: bytes, filename: str = "agent-flow.json") -> Dict:
        """從位元組上傳一個流程到 Langflow。"""
        if not self.project_id:
            self.project_id = await self.get_project_id()

        url = f"{self.base_url}/api/v1/flows/upload/"
        headers = {"x-api-key": self.api_key}
        params = {"project_id": self.project_id}
        files = {"file": (filename, io.BytesIO(json_bytes), "application/json")}

        try:
            # 對於 multipart/form-data，使用 requests 通常更簡單
            response = requests.post(url, headers=headers, params=params, files=files, timeout=60)
            response.raise_for_status()
            logger.info("從位元組上傳流程成功！")
            return response.json()
        except requests.exceptions.RequestException as e:
            error_text = e.response.text if e.response else str(e)
            logger.error(f"上傳請求錯誤: {error_text}")
            raise HTTPException(status_code=500, detail=f"上傳時發生網路錯誤: {error_text}")

    async def send_chat_message(self, message: str, session_id: str) -> str:
        """發送聊天訊息到 Langflow 並獲取回應。"""
        if not self.chat_flow_id:
            raise HTTPException(status_code=503, detail="聊天服務未初始化，沒有設定流程 ID。")

        url = f"{self.base_url}/api/v1/run/{self.chat_flow_id}"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key
        }
        # 完成 payload 的定義
        payload = {
            "input_value": message,
            "output_type": "chat",
            "input_type": "chat"
        }

        logger.info(f"正在向 Langflow (流程 ID: {self.chat_flow_id}) 發送訊息: {message}")
        logger.debug(f"Payload: {json.dumps(payload, indent=2)}")

        try:
            # 使用 httpx 非同步發送請求
            async with httpx.AsyncClient(timeout=200.0) as client:
                response = await client.post(url, json=payload, headers=headers)

            logger.info(f"Langflow 回應狀態碼: {response.status_code}")
            logger.debug(f"Langflow 原始回應: '{response.text}'")

            # 檢查回應狀態碼
            if response.status_code != 200:
                logger.error(f"聊天 API 錯誤: {response.status_code} - {response.text}")
                raise HTTPException(status_code=500, detail="與 Langflow 聊天時發生錯誤。")

            # 檢查回應內容是否為空
            if not response.text or not response.text.strip():
                logger.error("從 Langflow 收到空的回應。")
                raise HTTPException(status_code=500, detail="從 Langflow 收到空的回應。")

            # 解析 JSON 回應
            try:
                data = response.json()
                logger.debug(f"解析後的回應 JSON: {json.dumps(data, indent=2)}")
            except json.JSONDecodeError:
                logger.error(f"無法解析來自 Langflow 的 JSON 回應。原始回應: '{response.text}'")
                raise HTTPException(status_code=500, detail="來自 Langflow 的回應格式無效。")

            # 根據 Langflow 的回應結構提取聊天回覆
            try:
                # 主要提取策略
                response_text = data["outputs"][0]["outputs"][0]["results"]["message"]["text"]
                return response_text
            except (KeyError, IndexError, TypeError) as e:
                logger.warning(f"標準的回應提取策略失敗: {e}。嘗試備用策略...")

                # 備用提取策略
                if isinstance(data, dict):
                    if "message" in data and isinstance(data["message"], dict) and "text" in data["message"]:
                        return str(data["message"]["text"])
                    for key in ["text", "message", "response", "content"]:
                        if key in data and isinstance(data[key], str):
                            return data[key]
                    if "outputs" in data and isinstance(data["outputs"], list) and data["outputs"]:
                        first_output = data["outputs"][0]
                        if isinstance(first_output, dict):
                            for key in ["text", "message", "response", "content"]:
                                if key in first_output and isinstance(first_output[key], str):
                                    return first_output[key]

                logger.error("所有提取策略均失敗，無法從回應中找到聊天內容。")
                raise HTTPException(status_code=500, detail="無法解析 Langflow 的聊天回應結構。")

        except httpx.TimeoutException:
            logger.error("與 Langflow 聊天時發生超時錯誤。")
            raise HTTPException(status_code=504, detail="聊天請求超時。")
        except httpx.RequestError as e:
            logger.error(f"與 Langflow 聊天時發生網路錯誤: {e}")
            raise HTTPException(status_code=500, detail="聊天時發生網路錯誤。")
        except HTTPException:
            # 重新拋出已知的 HTTP 異常
            raise
        except Exception as e:
            logger.error(f"發送聊天訊息時發生未預期錯誤: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"發生未預期錯誤: {e}")