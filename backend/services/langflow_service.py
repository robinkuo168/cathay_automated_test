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
from typing import Dict, List, Optional  # Make sure List is imported

# Import ElasticsearchService at the top
from .elasticsearch_service import ElasticsearchService

load_dotenv()
logger = logging.getLogger(__name__)

class LangFlowAPIKeyManager:
    """
    Manages LangFlow API keys with a robust, file-based persistence strategy.
    """

    def __init__(self, base_url: str):
        """
        初始化 LangFlow API 金鑰管理器。

        :param base_url: Langflow 服務的基礎 URL。
        """
        self.base_url = base_url.rstrip('/')
        # 將 API 金鑰儲存在一個簡單的文字檔中
        self.api_key_file = 'langflow_api_key.txt'

    def load_api_key(self) -> Optional[str]:
        """
        從本地檔案載入已儲存的 API 金鑰。

        :return: 一個包含 API 金鑰的字串，如果檔案不存在或為空則返回 None。
        """
        if os.path.exists(self.api_key_file):
            with open(self.api_key_file, 'r') as f:
                key = f.read().strip()
                if key:
                    logger.info(f"🔑 從 '{self.api_key_file}' 載入已儲存的 API 金鑰。")
                    return key
        return None

    def save_api_key(self, api_key: str):
        """
        將一個新的 API 金鑰寫入本地檔案進行持久化儲存。

        :param api_key: 要儲存的 API 金鑰字串。
        """
        with open(self.api_key_file, 'w') as f:
            f.write(api_key)
        logger.info(f"新的 API 金鑰已成功儲存至 '{self.api_key_file}'。")

    async def delete_all_api_keys(self) -> bool:
        """Delete all existing API keys"""
        try:
            logger.info("🗑️ Starting to delete all existing API keys...")
            
            # Get list of all API keys
            api_keys = await self.list_api_keys_data()
            
            if not api_keys:
                logger.info("✅ No API keys found to delete")
                return True
            
            logger.info(f"Found {len(api_keys)} API keys to delete")
            
            # Delete each API key
            deleted_count = 0
            for key in api_keys:
                key_id = key.get('id')
                key_name = key.get('name', 'Unnamed')
                
                if key_id:
                    if await self.delete_api_key(key_id):
                        logger.info(f"✅ Deleted API key: {key_name} (ID: {key_id})")
                        deleted_count += 1
                    else:
                        logger.error(f"❌ Failed to delete API key: {key_name} (ID: {key_id})")
                else:
                    logger.warning(f"⚠️ API key {key_name} has no ID, skipping")
            
            logger.info(f"🎯 Successfully deleted {deleted_count} out of {len(api_keys)} API keys")
            return deleted_count == len(api_keys)
            
        except Exception as e:
            logger.error(f"❌ Error deleting API keys: {str(e)}")
            return False
    
    async def delete_api_key(self, key_id: str) -> bool:
        """Delete a specific API key by ID"""
        endpoints_to_try = [
            f"/api/v1/api_key/{key_id}",
            f"/api/v1/api_key/{key_id}/",
            f"/api/v1/api-key/{key_id}",
            f"/api/v1/api-key/{key_id}/"
        ]
        
        for endpoint in endpoints_to_try:
            delete_url = f"{self.base_url}{endpoint}"
            
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.delete(delete_url)
                    
                    if response.status_code in [200, 204]:
                        return True
                    elif response.status_code == 404:
                        logger.info(f"  API key {key_id} not found (may already be deleted)")
                        return True
                    elif response.status_code != 405:  # Skip method not allowed
                        logger.warning(f"  Delete failed with status {response.status_code}: {response.text}")
                        
            except Exception as e:
                logger.warning(f"  Error trying endpoint {endpoint}: {str(e)}")
                continue
        
        return False
    
    async def list_api_keys_data(self) -> List[Dict]:
        """List all existing API keys and return data"""
        endpoints_to_try = [
            "/api/v1/api_key/",
            "/api/v1/api_key",
            "/api/v1/api-key/",
            "/api/v1/api-key"
        ]
        
        for endpoint in endpoints_to_try:
            list_url = f"{self.base_url}{endpoint}"
            
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(list_url)
                    
                    if response.status_code == 200:
                        if response.text.strip():
                            data = response.json()
                            
                            # Handle ApiKeysResponse schema
                            if isinstance(data, dict) and 'api_keys' in data:
                                return data['api_keys']
                            elif isinstance(data, list):
                                return data
                            else:
                                # Try common field names
                                for field in ['keys', 'data', 'items', 'results']:
                                    if isinstance(data, dict) and field in data:
                                        return data[field]
                                return [data] if isinstance(data, dict) else []
                        else:
                            return []
                            
            except Exception as e:
                logger.warning(f"Error listing from {endpoint}: {str(e)}")
                continue
        
        logger.warning("Could not list API keys from any endpoint")
        return []

    async def setup_single_api_key(self) -> Optional[str]:
        """
        Main method: Delete all existing API keys and create a single new one
        
        Returns:
            The new API key or None if failed
        """
        try:
            logger.info("🚀 Setting up single API key...")
            
            # Step 1: Delete all existing API keys
            logger.info("Step 1: Deleting all existing API keys...")
            if await self.delete_all_api_keys():
                logger.info("✅ All existing API keys deleted successfully")
            else:
                logger.warning("⚠️ Some API keys may not have been deleted")
            
            # Step 2: Generate a new API key
            logger.info("Step 2: Generating new API key...")
            new_api_key = await self.generate_api_key("main-chatbot-key")
            
            if new_api_key:
                # Step 3: Test the new API key
                logger.info("Step 3: Testing new API key...")
                if await self.test_api_key(new_api_key):
                    logger.info("🎉 Single API key setup completed successfully!")
                    return new_api_key
                else:
                    logger.error("❌ New API key failed testing")
                    return None
            else:
                logger.error("❌ Failed to generate new API key")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error in setup_single_api_key: {str(e)}")
            return None

    async def generate_api_key(self, key_name: Optional[str] = None) -> Optional[str]:
        """
        透過呼叫 Langflow API 來產生一個新的 API 金鑰。

        此函式包含了重試邏輯，會嘗試多個常見的 API 端點路徑 (`/api_key/`, `/api-key/`)
        以提高與不同 Langflow 版本的相容性。
        :param key_name: (可選) 要為新金鑰指定的名稱。
        :return: 一個包含新 API 金鑰的字串，如果產生失敗則返回 None。
        """
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
        """
        測試一個給定的 API 金鑰是否有效。

        它會使用此金鑰嘗試訪問一個受保護的 Langflow 端點 (`/api/v1/flows`)，
        並根據 HTTP 狀態碼判斷金鑰是否有效。
        :param api_key: 要測試的 API 金鑰。
        :return: 如果金鑰有效，返回 True，否則返回 False。
        """
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
        設定 API 金鑰的核心流程 - 使用新的清理策略
        """
        logger.info("🚀 正在設定 API 金鑰...")

        # 步驟 1: 嘗試載入已儲存的金鑰
        existing_key = self.load_api_key()

        # 步驟 2: 如果有已儲存的金鑰，測試其有效性
        if existing_key and await self.test_api_key(existing_key):
            logger.info("🎉 使用現有的有效 API 金鑰。")
            return existing_key

        # 步驟 3: 如果沒有金鑰或金鑰已失效，則使用清理策略產生新的
        logger.info("找不到有效的現有金鑰，正在使用清理策略產生新的金鑰...")
        new_api_key = await self.setup_single_api_key()

        if new_api_key:
            self.save_api_key(new_api_key)
            logger.info("🎉 新的 API 金鑰設定完成！")
            return new_api_key

        logger.error("❌ API 金鑰設定失敗。無法產生或驗證新的金鑰。")
        return None

class LangflowService:
    # MODIFICATION: Update __init__ to accept ElasticsearchService
    def __init__(self, es_service: ElasticsearchService):
        """
        初始化 LangflowService。

        此建構函式會從環境變數讀取 Langflow 的基礎 URL 和專案名稱，
        並初始化一個 `LangFlowAPIKeyManager` 實例來管理 API 金鑰。
        它還需要一個 ElasticsearchService 實例來從資料庫獲取 Agent Flow。
        :param es_service: 一個已初始化的 ElasticsearchService 實例。
        :raises ValueError: 如果 `LANGFLOW_BASE_URL` 環境變數未設定。
        """
        self.base_url = os.getenv("LANGFLOW_BASE_URL","https://langflow-chatbot.1xww0crhz5k4.jp-tok.codeengine.appdomain.cloud")
        self.project_name = os.getenv("LANGFLOW_PROJECT_NAME","Starter Project")

        if not self.base_url:
            raise ValueError("Langflow 的環境變數 LANGFLOW_BASE_URL 未設定！")

        # MODIFICATION: Store the injected ElasticsearchService instance
        self.es_service = es_service
        self.api_key = None
        self.project_id = None
        self.chat_flow_id = None
        self.api_key_manager = LangFlowAPIKeyManager(self.base_url)
    
    '''
    async def get_project_id(self) -> str:
        """
        從 Langflow API 獲取目標專案的 ID。

        如果環境變數中設定了 `LANGFLOW_PROJECT_NAME`，它會尋找同名的專案；
        否則，它會直接使用 API 回傳的第一個專案。
        :return: 專案的唯一 ID 字串。
        :raises Exception: 如果在 Langflow 中找不到任何專案。
        """
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
'''

    async def get_project_id(self) -> str:
        """Fetch project ID from Langflow API"""
        try:
            url = f"{self.base_url}/api/v1/projects/"
            
            headers = {
                "accept": "application/json",
                "x-api-key": self.api_key
            }
            
            logger.info("Fetching project ID from Langflow...")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, headers=headers)
                
                if response.status_code != 200:
                    logger.error(f"Failed to fetch projects: {response.status_code} - {response.text}")
                    raise HTTPException(status_code=500, detail="Failed to fetch project ID")
                
                projects = response.json()
                logger.info(f"Available projects: {json.dumps(projects, indent=2)}")
                
                if not projects:
                    raise HTTPException(status_code=404, detail="No projects found")
                
                # Find project by name or use first project
                if self.project_name:
                    project = next((p for p in projects if p["name"] == self.project_name), None)
                    if not project:
                        logger.warning(f"Project '{self.project_name}' not found, using first project")
                        project = projects[0]
                else:
                    project = projects[0]
                
                project_id = project["id"]
                logger.info(f"Using project: {project['name']} (ID: {project_id})")
                return project_id
                
        except httpx.TimeoutException:
            logger.error("Timeout while fetching project ID")
            raise HTTPException(status_code=504, detail="Timeout fetching project ID")
        except httpx.RequestError as e:
            logger.error(f"Request error while fetching project ID: {str(e)}")
            raise HTTPException(status_code=500, detail="Network error fetching project ID")

    async def get_latest_flow_id(self) -> Optional[str]:
        """
        從指定的專案中，獲取最新建立或更新的流程 (Flow) 的 ID。

        :return: 最新流程的 ID 字串，如果專案中沒有任何流程則返回 None。
        """
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

    # MODIFICATION: Rewrite initialize_flow to use es_service
    async def initialize_flow(self):
        """
        初始化或刷新 Langflow 服務的總指令。

        這是一個關鍵的啟動流程，負責確保聊天機器人處於最新的、可運作的狀態。它包含以下步驟：
        1. 確保 API 金鑰已設定且有效。
        2. 獲取專案 ID。
        3. (可選) 刪除專案中最新的流程，以確保狀態乾淨。
        4. 從 Elasticsearch 獲取最新的代理 (Agent) 定義。
        5. 將新的代理定義作為新流程上傳到 Langflow。
        6. 更新服務以使用這個新建立的流程 ID。
        :raises Exception: 如果在任何一個關鍵步驟失敗，例如無法設定 API 金鑰或上傳流程。
        """
        logger.info("🔧 正在初始化或刷新 Langflow 流程...")

        # 步驟 0: 首先設定 API 金鑰 (CRITICAL: This must come first!)
        logger.info("步驟 0: 設定 API 金鑰...")
        if not self.api_key or not await self.api_key_manager.test_api_key(self.api_key):
            logger.info("API 金鑰未設定或無效，正在設定新的...")
            new_api_key = await self.api_key_manager.setup_single_api_key()
            if not new_api_key:
                raise Exception("致命錯誤：無法設定 Langflow API 金鑰。")
            self.api_key = new_api_key
            logger.info("✅ API 金鑰設定成功")
        else:
            logger.info("✅ 現有 API 金鑰有效")

        # 步驟 1: 獲取專案 ID (NOW this comes after API key setup)
        if not self.project_id:
            self.project_id = await self.get_project_id()
            logger.info(f"✅ 專案 ID 已設定為: {self.project_id}")

        # 步驟 2: 刪除最新的流程以確保狀態乾淨
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

        # 步驟 3: 從 Elasticsearch 獲取最新的代理定義
        logger.info("🚚 正在從 Elasticsearch 獲取最新的代理定義...")
        try:
            # 使用注入的 es_service 實例
            json_bytes = await self.es_service.get_agent_json_bytes()
        except Exception as e:
            logger.error(f"從 Elasticsearch 獲取代理定義失敗: {e}")
            raise HTTPException(status_code=500, detail="無法從 Elasticsearch 獲取代理定義。")

        # 步驟 4: 將新的代理版本作為新流程上傳
        logger.info("📤 正在將新的代理流程上傳至 Langflow...")
        await self.upload_flow_from_bytes(json_bytes)

        # 步驟 5: 獲取新上傳流程的 ID
        logger.info("🔍 正在檢索新上傳流程的 ID...")
        latest_flow_id = await self.get_latest_flow_id()
        if not latest_flow_id:
            raise Exception("致命錯誤：上傳後無法獲取流程 ID，聊天機器人將無法工作。")

        # 步驟 6: 更新服務以使用新的流程 ID
        await self.update_flow_id(latest_flow_id)
        logger.info(f"🎉 流程初始化完成。服務現在使用流程 ID: {self.chat_flow_id}")

    async def delete_flow(self, flow_id: str) -> bool:
        """
        根據指定的 ID，從 Langflow 中刪除一個流程。

        :param flow_id: 要刪除的流程的唯一 ID。
        :return: 如果刪除成功，返回 True，否則返回 False。
        """
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
        """
        更新服務實例中，當前用於聊天的流程 ID。

        :param new_flow_id: 新的流程 ID 字串。
        """
        old_flow_id = self.chat_flow_id
        self.chat_flow_id = new_flow_id
        logger.info(f"流程 ID 已從 {old_flow_id} 更新為 {new_flow_id}")

    async def upload_flow_from_bytes(self, json_bytes: bytes, filename: str = "agent-flow.json") -> Dict:
        """
        將一個以位元組 (bytes) 形式存在的流程定義檔案，上傳至 Langflow。

        :param json_bytes: 包含流程定義 JSON 的位元組內容。
        :param filename: (可選) 在上傳請求中為此檔案指定的名稱。
        :return: 一個包含 Langflow API 回應的字典。
        :raises HTTPException: 如果上傳請求失敗。
        """
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
        """
        將使用者的聊天訊息發送到當前啟用的 Langflow 流程，並獲取機器人的回應。

        此函式負責建構 Langflow API 所需的請求主體 (payload)，發送請求，
        並對複雜的 JSON 回應進行健壯的解析，以提取出最終的聊天回覆文字。
        它包含了多種備用解析策略，以應對 Langflow 可能的不同回應格式。
        :param message: 使用者輸入的訊息字串。
        :param session_id: 當前對話的唯一會話 ID。
        :return: 機器人回覆的文字內容。
        :raises HTTPException: 如果聊天服務未初始化、請求超時、或無法從 Langflow 的回應中解析出有效的回覆。
        """
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
