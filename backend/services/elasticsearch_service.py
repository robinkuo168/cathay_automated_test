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

load_dotenv()


class ElasticsearchService:
    def __init__(self, embedding_model: str = "ibm/slate-30m-english-rtrvr-v2"):
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
            verify_certs=True
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
        """Test Elasticsearch connection"""
        try:
            info = self.client.info()
            self.logger.info(f"Connected to Elasticsearch: {info['version']['number']}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Elasticsearch: {e}")
            raise e

    def get_vector_store(self, index_name: str) -> ElasticsearchStore:
        """Get or create ElasticsearchStore instance for given index"""
        if index_name not in self.vector_stores:
            self.vector_stores[index_name] = ElasticsearchStore(
                index_name=index_name,
                embedding=self.embeddings,
                es_connection=self.client
            )
        return self.vector_stores[index_name]

    def delete_all_documents(self, index_name: str) -> bool:
        """Delete all documents from index"""
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

    def process_xlsx_file(self, file_path: str) -> List[Document]:
        """Process Excel file and return LangChain Documents"""
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
        """Process text file and return LangChain Documents"""
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
        """Process YAML file and return LangChain Documents"""
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
        """Process a single file based on its extension"""
        file_path = Path(file_path)
        extension = file_path.suffix.lower()
        if extension in ['.xlsx', '.xls']:
            return self.process_xlsx_file(str(file_path))
        elif extension == '.txt':
            return self.process_txt_file(str(file_path))
        elif extension in ['.yaml', '.yml']:
            return self.process_yaml_file(str(file_path))
        else:
            raise ValueError(f"Unsupported file type: {extension}")

    def check_document_exists(self, document: Document, index_name: str) -> bool:
        """Check if document already exists in the index"""
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
        """Upload documents to Elasticsearch with batch duplicate checking"""
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
        """Upload a single file to Elasticsearch"""
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
        """Upload multiple files to Elasticsearch"""
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
        """Search documents using vector similarity"""
        try:
            vector_store = self.get_vector_store(index_name)
            return vector_store.similarity_search(query, k=k)
        except Exception as e:
            self.logger.error(f"Search failed: {e}")
            return []

    def search_with_score(self, query: str, index_name: str, k: int = 5) -> List[tuple]:
        """Search documents with similarity scores"""
        try:
            vector_store = self.get_vector_store(index_name)
            return vector_store.similarity_search_with_score(query, k=k)
        except Exception as e:
            self.logger.error(f"Search with score failed: {e}")
            return []

    async def get_agent_json(self, index_name: str = "my_agent_versions") -> Dict:
        """從指定的索引中檢索最新的 JSON 文件。"""
        if not self.client.ping():
            raise ConnectionError("無法連接到 Elasticsearch。")

        self.logger.info(f"正在從索引 '{index_name}' 檢索 Agent JSON...")
        try:
            response = self.client.search(
                index=index_name,
                body={
                    "query": {"match_all": {}},
                    "size": 1,
                }
            )
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                self.logger.error(f"在索引 '{index_name}' 中找不到任何文件。")
                raise FileNotFoundError(f"在索引 '{index_name}' 中找不到任何 Agent 定義。")

            self.logger.info(f"成功從索引 '{index_name}' 檢索到文件。")
            return hits[0]["_source"]

        except Exception as e:
            self.logger.error(f"從 Elasticsearch 檢索 Agent JSON 時發生錯誤: {e}")
            raise

    async def get_agent_json_bytes(self, index_name: str = "my_agent_versions") -> bytes:
        """檢索 Agent JSON 並將其轉換為位元組。"""
        try:
            agent_data = await self.get_agent_json(index_name)
            json_string = json.dumps(agent_data, indent=2, ensure_ascii=False)
            json_bytes = json_string.encode('utf-8')
            self.logger.info("成功將 Agent JSON 轉換為位元組。")
            return json_bytes
        except Exception as e:
            self.logger.error(f"將 Agent JSON 轉換為位元組時發生錯誤: {e}")
            raise