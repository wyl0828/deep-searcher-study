import uuid
from typing import Any, Dict, List, Optional

from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, CollectionInfo, RetrievalResult


class AzureSearch(BaseVectorDB):
    default_metric_type = "AZURE_RELEVANCE"

    def __init__(self, endpoint, index_name, api_key, vector_field):
        super().__init__(default_collection=index_name)
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        self.client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=AzureKeyCredential(api_key),
        )
        self.vector_field = vector_field
        self.endpoint = endpoint
        self.index_name = index_name
        self.api_key = api_key

    def init_collection(self):
        """Initialize Azure Search index with proper schema"""
        from azure.core.credentials import AzureKeyCredential
        from azure.core.exceptions import ResourceNotFoundError
        from azure.search.documents.indexes import SearchIndexClient
        from azure.search.documents.indexes.models import (
            SearchableField,
            SearchField,
            SearchIndex,
            SimpleField,
        )

        index_client = SearchIndexClient(
            endpoint=self.endpoint, credential=AzureKeyCredential(self.api_key)
        )

        # Create the index (simplified for compatibility with older SDK versions)
        fields = [
            SimpleField(name="id", type="Edm.String", key=True),
            SearchableField(name="content", type="Edm.String"),
            SearchField(
                name="content_vector",
                type="Collection(Edm.Single)",
                searchable=True,
                vector_search_dimensions=1536,
            ),
        ]

        # Create index with fields
        index = SearchIndex(name=self.index_name, fields=fields)

        try:
            # Try to delete existing index
            try:
                index_client.delete_index(self.index_name)
            except ResourceNotFoundError:
                pass

            # Create the index
            index_client.create_index(index)
        except Exception as exc:
            log.error(log.safe_exception_message("azure_search_create_index", exc))

    def insert_data(self, documents: List[dict]):
        """Batch insert documents with vector embeddings"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.api_key),
        )

        actions = [
            {
                "@search.action": "upload" if doc.get("id") else "merge",
                "id": doc.get("id", str(uuid.uuid4())),
                "content": doc["text"],
                "content_vector": doc["vector"],
            }
            for doc in documents
        ]

        result = search_client.upload_documents(actions)
        return [x.succeeded for x in result]

    def search_data(
        self, collection: Optional[str], vector: List[float], top_k: int = 50
    ) -> List[RetrievalResult]:
        """Azure Cognitive Search implementation with compatibility for older SDK versions"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=collection or self.index_name,
            credential=AzureKeyCredential(self.api_key),
        )

        # Validate that vector is not empty
        if not vector or len(vector) == 0:
            log.warning("azure_search_vector_empty")
            return []

        # Ensure vector has the right dimensions
        if len(vector) != 1536:
            log.warning(
                f"azure_search_vector_dimension_mismatch actual={len(vector)} expected=1536"
            )
            return []

        # Execute search with direct parameters - simpler approach
        try:
            log.debug(f"azure_search_started top_k={top_k}")

            # Directly use the search_by_vector method for compatibility
            body = {
                "search": "*",
                "select": "id,content",
                "top": top_k,
                "vectorQueries": [
                    {
                        "vector": vector,
                        "fields": self.vector_field,
                        "k": top_k,
                        "kind": "vector",
                    }
                ],
            }

            # Use the REST API directly
            result = search_client._client.documents.search_post(
                search_request=body, headers={"api-key": self.api_key}
            )

            # Format results
            search_results = []
            if hasattr(result, "results"):
                for doc in result.results:
                    try:
                        doc_dict = doc.as_dict() if hasattr(doc, "as_dict") else doc
                        content = doc_dict.get("content", "")
                        doc_id = doc_dict.get("id", "")
                        score = doc_dict.get("@search.score", 0.0)

                        result = RetrievalResult.from_metric_value(
                            embedding=[],  # We don't get the vectors back
                            text=content,
                            reference=doc_id,
                            metadata={"source": doc_id},
                            metric_type=self.default_metric_type,
                            value=score,
                            score_kind="rank_score",
                        )
                        search_results.append(result)
                    except Exception as exc:
                        log.error(
                            log.safe_exception_message("azure_search_result_mapping", exc)
                        )

            return search_results
        except Exception as exc:
            log.error(log.safe_exception_message("azure_search_primary", exc))

            # Try another approach if the first one fails
            try:
                log.debug("azure_search_fallback_started")
                results = search_client.search(search_text="*", select=["id", "content"], top=top_k)

                # Process results
                alt_results = []
                for doc in results:
                    try:
                        # Handle different result formats
                        if isinstance(doc, dict):
                            content = doc.get("content", "")
                            doc_id = doc.get("id", "")
                            score = doc.get("@search.score", 0.0)
                        else:
                            content = getattr(doc, "content", "")
                            doc_id = getattr(doc, "id", "")
                            score = getattr(doc, "@search.score", 0.0)

                        result = RetrievalResult.from_metric_value(
                            embedding=[],
                            text=content,
                            reference=doc_id,
                            metadata={"source": doc_id},
                            metric_type=self.default_metric_type,
                            value=score,
                            score_kind="rank_score",
                        )
                        alt_results.append(result)
                    except Exception as exc:
                        log.error(
                            log.safe_exception_message("azure_search_fallback_mapping", exc)
                        )

                return alt_results
            except Exception as exc:
                log.error(log.safe_exception_message("azure_search_fallback", exc))
                return []

    def clear_db(self):
        """Delete all documents in the index"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        search_client = SearchClient(
            endpoint=self.endpoint,
            index_name=self.index_name,
            credential=AzureKeyCredential(self.api_key),
        )

        docs = search_client.search(search_text="*", include_total_count=True, select=["id"])
        ids = [doc["id"] for doc in docs]

        if ids:
            search_client.delete_documents([{"id": id} for id in ids])

        return len(ids)

    def get_all_collections(self) -> List[str]:
        """List all search indices in Azure Cognitive Search"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient

        try:
            index_client = SearchIndexClient(
                endpoint=self.endpoint, credential=AzureKeyCredential(self.api_key)
            )
            return [index.name for index in index_client.list_indexes()]
        except Exception as exc:
            log.error(log.safe_exception_message("azure_search_list_indices", exc))
            return []

    def get_collection_info(self, name: str) -> Dict[str, Any]:
        """Retrieve index metadata"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient

        index_client = SearchIndexClient(
            endpoint=self.endpoint, credential=AzureKeyCredential(self.api_key)
        )
        return index_client.get_index(name).__dict__

    def collection_exists(self, name: str) -> bool:
        """Check index existence"""
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.get_collection_info(name)
            return True
        except ResourceNotFoundError:
            return False

    def list_collections(self, *args, **kwargs) -> List[CollectionInfo]:
        """List all Azure Search indices with metadata"""
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents.indexes import SearchIndexClient

        try:
            index_client = SearchIndexClient(
                endpoint=self.endpoint, credential=AzureKeyCredential(self.api_key)
            )

            collections = []
            for index in index_client.list_indexes():
                collections.append(
                    CollectionInfo(
                        collection_name=index.name,
                        description=f"Azure Search Index with {len(index.fields) if hasattr(index, 'fields') else 0} fields",
                    )
                )
            return collections

        except Exception as exc:
            log.error(log.safe_exception_message("azure_search_list_collections", exc))
            return []
