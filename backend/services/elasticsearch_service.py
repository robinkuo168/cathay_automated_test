# /backend/services/elasticsearch_service.py
import ssl
import os
import json
import yaml
import hashlib
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional
from pathlib import Path
from elasticsearch import Elasticsearch
from langchain.schema import Document
from langchain_text_splitters import RecursiveJsonSplitter, CharacterTextSplitter
from langchain_elasticsearch import ElasticsearchStore
from langchain_ibm import WatsonxEmbeddings
from ibm_watsonx_ai.metanames import EmbedTextParamsMetaNames
from dotenv import load_dotenv
from .logger import get_logger
from fastapi import HTTPException

load_dotenv()

class ElasticsearchService:
    def __init__(self, embedding_model: str = "ibm/slate-30m-english-rtrvr-v2"):
        """
        初始化 ElasticsearchService。

        此建構函式負責設定所有與 Elasticsearch 互動所需的元件，包括：
        1. 從環境變數讀取連線設定 (主機、帳號、密碼)。
        2. 解析憑證檔案的絕對路徑並進行驗證。
        3. 初始化 Elasticsearch 的 Python 客戶端。
        4. 初始化用於生成向量嵌入的 WatsonxEmbeddings 模型。
        5. 初始化用於分割不同檔案類型 (JSON, TXT) 的文本分割器。
        :param embedding_model: 用於生成向量嵌入的 Watsonx.ai 模型 ID。
        :raises ValueError: 如果 Elasticsearch 的環境變數未完整設定。
        :raises FileNotFoundError: 如果在指定的路徑找不到憑證檔案。
        """
        self.logger = get_logger(__name__)

        # 從環境變數讀取 Elasticsearch 設定
        ES_HOST = os.getenv("ES_HOST")
        ES_PORT = int(os.getenv("ES_PORT", 31041))
        ES_USERNAME = os.getenv("ES_USERNAME")
        ES_PASSWORD = os.getenv("ES_PASSWORD")

        # 1. 獲取憑證的相對路徑
        relative_cert_path = os.getenv("ES_CERT_PATH")

        if not all([ES_HOST, ES_PORT, ES_USERNAME, ES_PASSWORD, relative_cert_path]):
            raise ValueError("Elasticsearch 的環境變數未完整設定！")

        project_root = Path(__file__).parent.parent.parent

        # 3. 將專案根目錄與相對路徑結合，得到絕對路徑
        CERT_PATH = project_root / relative_cert_path

        self.logger.info(f"憑證檔案的絕對路徑解析為: {CERT_PATH}")
        if not CERT_PATH.exists():
            # 在初始化時就檢查檔案是否存在，提前失敗
            self.logger.error(f"嚴重錯誤：在路徑 '{CERT_PATH}' 找不到 Elasticsearch 憑證檔案！")
            raise FileNotFoundError(f"在路徑 '{CERT_PATH}' 找不到 Elasticsearch 憑證檔案！")

        # Elasticsearch connection URL for ElasticsearchStore
        self.es_url = f"https://{ES_USERNAME}:{ES_PASSWORD}@{ES_HOST}:{ES_PORT}"

        # Initialize direct client for management operations
        self.client = Elasticsearch(
            hosts=[{
                "host": ES_HOST,
                "port": int(ES_PORT),
                "scheme": "https"
            }],
            basic_auth=(ES_USERNAME, ES_PASSWORD),
            ca_certs=str(CERT_PATH),  # 使用絕對路徑
            verify_certs=False
        )

        # Initialize embeddings
        params = {
            EmbedTextParamsMetaNames.TRUNCATE_INPUT_TOKENS: 200,
            EmbedTextParamsMetaNames.RETURN_OPTIONS: {"input_text": True},
        }

        # 從環境變數讀取 Watsonx.ai 的設定
        self.embeddings = WatsonxEmbeddings(
            model_id=embedding_model,
            url=os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com"),
            apikey=os.getenv("WATSONX_API_KEY"),
            project_id=os.getenv("WATSONX_PROJECT_ID"),
            params=params
        )

        # Initialize text splitters
        self.json_splitter = RecursiveJsonSplitter(max_chunk_size=300)
        self.text_splitter = CharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=75,
            separator="["
        )

        # Store ElasticsearchStore instances
        self.vector_stores = {}

    def test_connection(self) -> bool:
        """
        測試與 Elasticsearch 服務的連線是否正常。

        :return: 如果連線成功，返回 True。
        :raises Exception: 如果連線失敗，則會拋出底層的連線錯誤。
        """
        try:
            info = self.client.info()
            self.logger.info(f"Connected to Elasticsearch: {info['version']['number']}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Elasticsearch: {e}")
            raise e

    def get_vector_store(self, index_name: str) -> ElasticsearchStore:
        """
        獲取或創建一個與特定索引對應的 ElasticsearchStore 實例。

        此函式使用內部快取 (`self.vector_stores`) 來避免重複創建相同的
        ElasticsearchStore 物件，從而提高效率。
        :param index_name: 目標 Elasticsearch 索引的名稱。
        :return: 一個可用於向量操作的 ElasticsearchStore 實例。
        """
        if index_name not in self.vector_stores:
            self.vector_stores[index_name] = ElasticsearchStore(
                index_name=index_name,
                embedding=self.embeddings,
                es_connection=self.client
            )
        return self.vector_stores[index_name]

    def delete_all_documents(self, index_name: str) -> bool:
        """
        刪除指定索引中的所有文件。

        :param index_name: 要清空的目標索引名稱。
        :return: 如果操作成功，返回 True，否則返回 False。
        """
        try:
            response = self.client.delete_by_query(
                index=index_name,
                body={"query": {"match_all": {}}}
            )
            self.logger.info(f"🗑️  Deleted {response['deleted']} documents from {index_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete documents from {index_name}: {e}")
            return False

    def process_json_file(self, file_path: str) -> List[Document]:
        """
        處理 JSON (.json) 檔案，主要用於 Langflow Agent 版本文件。

        對於 my_agent_versions 索引，我們將整個 JSON 作為單一文件儲存，
        不進行分割，以保持 Agent 配置的完整性。
        :param file_path: JSON 檔案的路徑。
        :return: 一個包含從檔案中提取出的 Document 物件的列表。
        :raises Exception: 如果在讀取或處理檔案時發生錯誤。
        """
        documents = []
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                json_data = json.load(file)

            # For agent versions, store ONLY the complete JSON as a single document
            # Do NOT create chunked versions to avoid multiple documents
            full_doc = Document(
                page_content=json.dumps(json_data, ensure_ascii=False, indent=2),
                metadata={
                    "file_path": str(file_path),
                    "filetype": "This is a JSON/.json file (Agent Version)",
                    "is_full_document": True,
                    "source": str(file_path),
                    "agent_version": json_data.get("version", "unknown"),
                    "agent_name": json_data.get("name", "unknown"),
                    "created_at": json_data.get("created_at", "unknown")
                }
            )
            documents.append(full_doc)

            # REMOVED: Chunked versions creation to keep only 1 document per agent
            # This eliminates the 4 additional chunk documents

        except Exception as e:
            self.logger.error(f"Error processing JSON file {file_path}: {e}")
            raise
        return documents

    def process_xlsx_file(self, file_path: str) -> List[Document]:
        """
        處理 Excel (.xlsx) 檔案，並將其內容轉換為 LangChain 的 Document 物件列表。

        此函式會遍歷 Excel 中的每一個工作表 (sheet)，將每一行轉換為一個獨立的 Document，
        同時也會為整個工作表創建一個包含所有內容的 Document，以支援不同粒度的檢索。
        :param file_path: Excel 檔案的路徑。
        :return: 一個包含從檔案中提取出的所有 Document 的列表。
        :raises Exception: 如果在讀取或處理 Excel 檔案時發生錯誤。
        """
        documents = []
        try:
            excel_file = pd.ExcelFile(file_path)
            for sheet_name in excel_file.sheet_names:
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                for col in df.select_dtypes(include=['datetime64[ns]']).columns:
                    df[col] = df[col].astype(str)
                for col in df.select_dtypes(include=['category']).columns:
                    df[col] = df[col].astype(str)
                df = df.where(pd.notnull(df), None)
                for col in df.select_dtypes(include=[np.number]).columns:
                    df[col] = df[col].apply(lambda x: x.item() if isinstance(x, np.generic) else x)
                excel_data = df.to_dict(orient='records')
                for i, row_data in enumerate(excel_data):
                    doc = Document(
                        page_content=json.dumps(row_data, ensure_ascii=False),
                        metadata={
                            "file_path": f"{sheet_name}#{file_path}",
                            "filetype": "This is a Excel/.xlsx file",
                            "chunk_index": i,
                            "sheet_name": sheet_name,
                            "source": str(file_path)
                        }
                    )
                    documents.append(doc)
                full_doc = Document(
                    page_content=json.dumps(excel_data, ensure_ascii=False),
                    metadata={
                        "file_path": f"{sheet_name}#{file_path}#full",
                        "filetype": "This is a Excel/.xlsx file",
                        "sheet_name": sheet_name,
                        "is_full_document": True,
                        "source": str(file_path)
                    }
                )
                documents.append(full_doc)
        except Exception as e:
            self.logger.error(f"Error processing Excel file {file_path}: {e}")
            raise
        return documents

    def process_txt_file(self, file_path: str) -> List[Document]:
        """
        處理純文字 (.txt) 檔案，將其分割成塊 (chunks)，並轉換為 Document 物件列表。

        :param file_path: 純文字檔案的路徑。
        :return: 一個包含從檔案中提取並分割的所有 Document 的列表。
        :raises Exception: 如果在讀取或處理檔案時發生錯誤。
        """
        documents = []
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                content = file.read()
            text_chunks = self.text_splitter.split_text(content)
            for i, chunk in enumerate(text_chunks):
                doc = Document(
                    page_content=chunk,
                    metadata={
                        "file_path": str(file_path),
                        "filetype": "This is a TEXT/.txt file",
                        "chunk_index": i,
                        "source": str(file_path)
                    }
                )
                documents.append(doc)
        except Exception as e:
            self.logger.error(f"Error processing text file {file_path}: {e}")
            raise
        return documents

    def process_yaml_file(self, file_path: str) -> List[Document]:
        """
        處理 YAML (.yaml) 檔案，將其內容分割成塊，並轉換為 Document 物件列表。

        :param file_path: YAML 檔案的路徑。
        :return: 一個包含從檔案中提取並分割的所有 Document 的列表。
        :raises Exception: 如果在讀取或處理檔案時發生錯誤。
        """
        documents = []
        try:
            with open(file_path, 'rb') as file:
                yaml_data = yaml.safe_load(file)
            yaml_chunks = self.json_splitter.split_json(json_data=yaml_data)
            for i, chunk in enumerate(yaml_chunks):
                doc = Document(
                    page_content=json.dumps(chunk, ensure_ascii=False),
                    metadata={
                        "file_path": str(file_path),
                        "filetype": "This is a YAML/.yaml file",
                        "chunk_index": i,
                        "source": str(file_path)
                    }
                )
                documents.append(doc)
            full_doc = Document(
                page_content=json.dumps(yaml_data, ensure_ascii=False),
                metadata={
                    "file_path": f"{str(file_path)}#full",
                    "filetype": "This is a YAML/.yaml file",
                    "is_full_document": True,
                    "source": str(file_path)
                }
            )
            documents.append(full_doc)
        except Exception as e:
            self.logger.error(f"Error processing YAML file {file_path}: {e}")
            raise
        return documents

    def process_file(self, file_path: str) -> List[Document]:
        """
        根據檔案的副檔名，動態地選擇合適的處理函式來處理單一檔案。

        這是一個調度函式 (dispatcher)，它會根據副檔名呼叫對應的
        `process_xlsx_file`, `process_txt_file`, `process_yaml_file` 或 `process_json_file`。
        :param file_path: 要處理的檔案路徑。
        :return: 一個從檔案中提取出的 Document 物件列表。
        :raises ValueError: 如果檔案類型不被支援。
        """
        file_path = Path(file_path)
        extension = file_path.suffix.lower()
        if extension in ['.xlsx', '.xls']:
            return self.process_xlsx_file(str(file_path))
        elif extension == '.txt':
            return self.process_txt_file(str(file_path))
        elif extension in ['.yaml', '.yml']:
            return self.process_yaml_file(str(file_path))
        elif extension == '.json':
            return self.process_json_file(str(file_path))
        else:
            raise ValueError(f"Unsupported file type: {extension}")

    def check_document_exists(self, document: Document, index_name: str) -> bool:
        """
        檢查一個特定的 Document 物件是否已經存在於指定的索引中，以避免重複上傳。

        它通過對文件內容和元數據進行雜湊 (hash) 來生成一個唯一的標識符，
        並在 Elasticsearch 中查詢該標識符。
        :param document: 要檢查的 LangChain Document 物件。
        :param index_name: 目標 Elasticsearch 索引的名稱。
        :return: 如果文件已存在，返回 True，否則返回 False。
        """
        try:
            content_hash = hashlib.md5(
                (document.page_content + str(document.metadata.get("file_path", ""))).encode()
            ).hexdigest()
            search_body = {
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"metadata.file_path.keyword": document.metadata.get("file_path", "")}},
                            {"term": {"metadata.chunk_index": document.metadata.get("chunk_index", -1)}}
                        ]
                    }
                },
                "size": 1
            }
            response = self.client.search(index=index_name, body=search_body)
            return response["hits"]["total"]["value"] > 0
        except Exception:
            return False

    def upload_documents(self, documents: List[Document], index_name: str, check_duplicates: bool = True) -> bool:
        """
        將一個 Document 物件列表上傳至 Elasticsearch，並可選擇性地進行批次重複檢查。

        如果啟用重複檢查，它會使用高效的 `mget` 操作一次性檢查所有文件是否已存在，
        然後只上傳新的文件，大幅提升了重複上傳時的效率。
        :param documents: 要上傳的 Document 物件列表。
        :param index_name: 目標 Elasticsearch 索引的名稱。
        :param check_duplicates: 是否在上传前檢查重複。
        :return: 如果操作成功，返回 True，否則返回 False。
        """
        try:
            vector_store = self.get_vector_store(index_name)
            if check_duplicates:
                doc_ids = []
                for doc in documents:
                    file_path = doc.metadata.get("file_path", "")
                    content_hash = hashlib.md5(doc.page_content.encode()).hexdigest()
                    combined_key = f"{file_path}:{content_hash}"
                    doc_id = hashlib.md5(combined_key.encode()).hexdigest()
                    doc_ids.append(doc_id)
                try:
                    response = self.client.mget(
                        index=index_name,
                        body={"ids": doc_ids},
                        _source=False
                    )
                    existing_ids = {doc_response["_id"] for doc_response in response["docs"] if
                                    doc_response.get("found", False)}
                    new_documents = [doc for doc, doc_id in zip(documents, doc_ids) if doc_id not in existing_ids]
                    new_doc_ids = [doc_id for doc_id in doc_ids if doc_id not in existing_ids]
                    if new_documents:
                        vector_store.add_documents(new_documents, ids=new_doc_ids)
                        self.logger.info(
                            f"Added {len(new_documents)} new documents (skipped {len(existing_ids)} existing)")
                        return True
                    else:
                        self.logger.info("ℹ️  No new documents to add - all documents already exist")
                        return True
                except Exception as e:
                    self.logger.warning(
                        f"Index '{index_name}' doesn't exist yet or mget failed, adding all documents. Error: {e}")
                    vector_store.add_documents(documents, ids=doc_ids)
                    return True
            else:
                vector_store.add_documents(documents)
                self.logger.info(f"Added {len(documents)} documents to index")
                return True
        except Exception as e:
            self.logger.error(f"Failed to upload documents: {e}")
            return False

    def upload_file(self, file_path: str, index_name: str, check_duplicates: bool = True) -> bool:
        """
        一個方便的包裝函式，用於處理並上傳單一檔案。

        它會先呼叫 `process_file` 將檔案轉換為 Document 列表，然後再呼叫 `upload_documents` 進行上傳。
        :param file_path: 要上傳的檔案路徑。
        :param index_name: 目標 Elasticsearch 索引的名稱。
        :param check_duplicates: 是否在上传前檢查重複。
        :return: 如果操作成功，返回 True，否則返回 False。
        """
        try:
            self.logger.info(f"📄 Processing file: {file_path}")
            documents = self.process_file(file_path)
            if documents:
                return self.upload_documents(documents, index_name, check_duplicates)
            else:
                self.logger.warning(f"No documents generated from {file_path}")
                return True
        except Exception as e:
            self.logger.error(f"Failed to upload file {file_path}: {e}")
            return False

    def upload_multiple_files(self, file_paths: List[str], index_name: str,
                              delete_existing: bool = False, check_duplicates: bool = True) -> bool:
        """
        上傳多個檔案至 Elasticsearch 的主要進入點。

        此函式協調整個上傳流程，包括測試連線、可選地刪除舊索引，
        以及迭代處理每一個檔案。
        :param file_paths: 一個包含多個檔案路徑的列表。
        :param index_name: 目標 Elasticsearch 索引的名稱。
        :param delete_existing: 是否在上传前刪除已存在的同名索引。
        :param check_duplicates: 是否在上传每個檔案時檢查重複。
        :return: 如果所有檔案都成功處理，返回 True，否則返回 False。
        """
        try:
            if not self.test_connection():
                return False
            if delete_existing:
                self.delete_all_documents(index_name)
            success_count = 0
            total_files = len(file_paths)
            for i, file_path in enumerate(file_paths):
                self.logger.info(f"📁 Processing file {i + 1}/{total_files}: {file_path}")
                if self.upload_file(file_path, index_name, check_duplicates):
                    success_count += 1
                else:
                    self.logger.error(f"Failed to process: {file_path}")
            self.logger.info(f"🎉 Upload completed! {success_count}/{total_files} files processed successfully.")
            try:
                stats = self.client.count(index=index_name)
                self.logger.info(f"Total documents in index '{index_name}': {stats['count']}")
            except Exception as e:
                self.logger.warning(f"Could not retrieve index stats: {e}")
            return success_count == total_files
        except Exception as e:
            self.logger.error(f"Upload process failed: {e}")
            return False

    def search_documents(self, query: str, index_name: str, k: int = 5) -> List[Document]:
        """
        在指定的索引中，根據向量相似度執行搜尋。

        :param query: 使用者的自然語言查詢。
        :param index_name: 要搜尋的目標索引名稱。
        :param k: 要返回的最相似結果數量。
        :return: 一個包含最相似的 Document 物件的列表。
        """
        try:
            vector_store = self.get_vector_store(index_name)
            return vector_store.similarity_search(query, k=k)
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return []

    def search_with_score(self, query: str, index_name: str, k: int = 5) -> List[tuple]:
        """
        執行向量相似度搜尋，並在結果中包含每個文件的相似度分數。

        :param query: 使用者的自然語言查詢。
        :param index_name: 要搜尋的目標索引名稱。
        :param k: 要返回的最相似結果數量。
        :return: 一個元組的列表，每個元組包含 (Document, score)。
        """
        try:
            vector_store = self.get_vector_store(index_name)
            return vector_store.similarity_search_with_score(query, k=k)
        except Exception as e:
            self.logger.error(f"Search with score failed: {e}")
            return []

    async def get_agent_json(self, index_name: str = "my_agent_versions") -> Dict:
        """Retrieve the ORIGINAL JSON file from my_agent_versions index"""
        try:

            self.logger.info(f"🔍 Searching for documents in index: {index_name}")

            response = self.client.search(
                index=index_name,
                body={"query": {"match_all": {}}, "size": 1}
            )

            hits = response["hits"]["hits"]
            self.logger.info(f"📊 Found {len(hits)} documents in {index_name}")

            if not hits:
                raise HTTPException(status_code=404, detail=f"No documents found in index {index_name}")

            document = hits[0]["_source"]
            self.logger.info(f"🔑 Document keys: {list(document.keys())}")

            # The JSON is stored in 'text' field (not page_content)
            if "text" in document:
                text_content = document["text"]
                self.logger.info(f"📝 Found text field, length: {len(text_content)}")
                self.logger.info(f"📝 Text content preview: {text_content[:200]}...")

                try:
                    # Parse the JSON string back to original structure
                    original_json = json.loads(text_content)
                    self.logger.info(f"✅ Successfully parsed text as JSON")
                    self.logger.info(
                        f"🔑 Original JSON keys: {list(original_json.keys()) if isinstance(original_json, dict) else 'Not a dict'}")
                    return original_json

                except json.JSONDecodeError as e:
                    self.logger.error(f"❌ Failed to parse text as JSON: {str(e)}")
                    raise HTTPException(status_code=500, detail="Stored JSON data is corrupted")
            else:
                self.logger.error(f"❌ No 'text' field found. Available fields: {list(document.keys())}")
                raise HTTPException(status_code=500, detail="Document missing text field")

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error retrieving agent JSON from Elasticsearch: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve agent JSON: {str(e)}")

    async def get_agent_json_bytes(self) -> bytes:
        """Retrieve JSON from Elasticsearch and return as bytes"""
        try:
            # Get JSON from Elasticsearch
            agent_data = await self.get_agent_json()

            # Convert to JSON string and then to bytes
            json_string = json.dumps(agent_data, indent=2, ensure_ascii=False)
            json_bytes = json_string.encode('utf-8')

            self.logger.info("Agent JSON retrieved and converted to bytes")
            return json_bytes

        except Exception as e:
            self.logger.error(f"Error converting agent JSON to bytes: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to get agent JSON bytes: {str(e)}")