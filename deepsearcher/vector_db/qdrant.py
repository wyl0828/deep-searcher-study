import uuid
from typing import List, Optional, Union

import numpy as np

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, CollectionInfo, RetrievalResult
from deepsearcher.vector_db.exceptions import (
    CollectionManifestInvalid,
    CollectionManifestWriteFailed,
    UnsafeCollectionReplacement,
    VectorDBError,
    VectorInitializationFailed,
    VectorInsertFailed,
    VectorListFailed,
    VectorSearchFailed,
)

DEFAULT_COLLECTION_NAME = "deepsearcher"

TEXT_PAYLOAD_KEY = "text"
REFERENCE_PAYLOAD_KEY = "reference"
METADATA_PAYLOAD_KEY = "metadata"
MANIFEST_PAYLOAD_KEY = "_deepsearcher_collection_manifest"
MANIFEST_POINT_ID = "00000000-0000-0000-0000-000000000001"


class Qdrant(BaseVectorDB):
    """Vector DB implementation powered by [Qdrant](https://qdrant.tech/)"""

    default_metric_type = "COSINE"
    supports_collection_manifests = True

    def __init__(
        self,
        location: Optional[str] = None,
        url: Optional[str] = None,
        port: Optional[int] = 6333,
        grpc_port: int = 6334,
        prefer_grpc: bool = False,
        https: Optional[bool] = None,
        api_key: Optional[str] = None,
        prefix: Optional[str] = None,
        timeout: Optional[int] = None,
        host: Optional[str] = None,
        path: Optional[str] = None,
        default_collection: str = DEFAULT_COLLECTION_NAME,
    ):
        """
        Initialize the Qdrant client with flexible connection options.

        Args:
            location (Optional[str], optional):
                - If ":memory:" - use in-memory Qdrant instance.
                - If str - use it as a URL parameter.
                - If None - use default values for host and port.
                Defaults to None.

            url (Optional[str], optional):
                URL for Qdrant service, can include scheme, host, port, and prefix.
                Allows flexible connection string specification.
                Defaults to None.

            port (Optional[int], optional):
                Port of the REST API interface.
                Defaults to 6333.

            grpc_port (int, optional):
                Port of the gRPC interface.
                Defaults to 6334.

            prefer_grpc (bool, optional):
                If True, use gRPC interface whenever possible in custom methods.
                Defaults to False.

            https (Optional[bool], optional):
                If True, use HTTPS (SSL) protocol.
                Defaults to None.

            api_key (Optional[str], optional):
                API key for authentication in Qdrant Cloud.
                Defaults to None.

            prefix (Optional[str], optional):
                If not None, add prefix to the REST URL path.
                Example: 'service/v1' results in 'http://localhost:6333/service/v1/{qdrant-endpoint}'
                Defaults to None.

            timeout (Optional[int], optional):
                Timeout for REST and gRPC API requests.
                Default is 5 seconds for REST and unlimited for gRPC.
                Defaults to None.

            host (Optional[str], optional):
                Host name of Qdrant service.
                If url and host are None, defaults to 'localhost'.
                Defaults to None.

            path (Optional[str], optional):
                Persistence path for QdrantLocal.
                Defaults to None.

            default_collection (str, optional):
                Default collection name to be used.
        """
        try:
            from qdrant_client import QdrantClient
        except ImportError as original_error:
            raise ImportError(
                "Qdrant client is not installed. Install it using: pip install qdrant-client\n"
            ) from original_error

        super().__init__(default_collection)
        self._collection_metric_types: dict[str, str] = {}
        self.client = QdrantClient(
            location=location,
            url=url,
            port=port,
            grpc_port=grpc_port,
            prefer_grpc=prefer_grpc,
            https=https,
            api_key=api_key,
            prefix=prefix,
            timeout=timeout,
            host=host,
            path=path,
        )

    def _collection_metric_type(self, collection: str) -> str:
        cached = self._collection_metric_types.get(collection)
        if cached:
            return cached
        try:
            collection_info = self.client.get_collection(collection_name=collection)
            vectors = collection_info.config.params.vectors
            if isinstance(vectors, dict):
                vector_params = next(iter(vectors.values()), None)
            else:
                vector_params = vectors
            distance = getattr(vector_params, "distance", None)
            value = getattr(distance, "value", distance)
            metric_type = str(value or self.default_metric_type).strip().upper()
            if metric_type not in {"COSINE", "DOT", "EUCLID", "MANHATTAN"}:
                metric_type = self.default_metric_type
        except Exception:
            metric_type = self.default_metric_type
        self._collection_metric_types[collection] = metric_type
        return metric_type

    def collection_exists(self, collection: str) -> bool:
        return bool(self.client.collection_exists(collection_name=collection))

    def get_collection_manifest(
        self,
        collection: str,
    ) -> CollectionManifest | None:
        if not self.collection_exists(collection):
            return None
        points = self.client.retrieve(
            collection_name=collection,
            ids=[MANIFEST_POINT_ID],
            with_payload=True,
            with_vectors=False,
        )
        if not isinstance(points, list) or not points:
            return None
        payload = getattr(points[0], "payload", None)
        raw_manifest = payload.get(MANIFEST_PAYLOAD_KEY) if isinstance(payload, dict) else None
        if raw_manifest is None:
            return None
        try:
            if isinstance(raw_manifest, str):
                return CollectionManifest.from_json(raw_manifest)
            if isinstance(raw_manifest, dict):
                return CollectionManifest.from_dict(raw_manifest)
            raise ValueError("unsupported manifest payload")
        except (TypeError, ValueError) as exc:
            raise CollectionManifestInvalid(
                operation="read_manifest",
                collection=collection,
            ) from exc

    def set_collection_manifest(
        self,
        collection: str,
        manifest: CollectionManifest,
    ) -> None:
        from qdrant_client import models

        try:
            self.client.upsert(
                collection_name=collection,
                points=[
                    models.PointStruct(
                        id=MANIFEST_POINT_ID,
                        vector=[0.0] * manifest.embedding.dimension,
                        payload={MANIFEST_PAYLOAD_KEY: manifest.to_dict()},
                    )
                ],
                wait=True,
            )
        except Exception as exc:
            raise CollectionManifestWriteFailed(
                operation="write_manifest",
                collection=collection,
            ) from exc

    def init_collection(
        self,
        dim: int,
        collection: Optional[str] = None,
        description: Optional[str] = "",
        force_new_collection: bool = False,
        text_max_length: int = 65_535,
        reference_max_length: int = 2048,
        distance_metric: str = "Cosine",
        *args,
        **kwargs,
    ):
        """
        Initialize a collection in Qdrant.

        Args:
            dim (int): Dimension of the vector embeddings.
            collection (Optional[str], optional): Collection name.
            description (Optional[str], optional): Collection description. Defaults to "".
            force_new_collection (bool, optional): Whether to force create a new collection if it already exists. Defaults to False.
            text_max_length (int, optional): Maximum length for text field. Defaults to 65_535.
            reference_max_length (int, optional): Maximum length for reference field. Defaults to 2048.
            distance_metric (str, optional): Metric type for vector similarity search. Defaults to "Cosine".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        from qdrant_client import models

        collection = collection or self.default_collection

        try:
            collection_exists = self.client.collection_exists(collection_name=collection)

            if force_new_collection and collection_exists:
                raise UnsafeCollectionReplacement(
                    operation="initialize",
                    collection=collection,
                )

            if not collection_exists:
                self.client.create_collection(
                    collection_name=collection,
                    vectors_config=models.VectorParams(size=dim, distance=distance_metric),
                    *args,
                    **kwargs,
                )

                log.color_print(f"Created collection [{collection}] successfully")
                metric_value = getattr(distance_metric, "value", distance_metric)
                self._collection_metric_types[collection] = str(metric_value).strip().upper()
                return {
                    "created": True,
                    "collection": collection,
                }
            return {
                "created": False,
                "collection": collection,
            }
        except UnsafeCollectionReplacement:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("qdrant_init", exc))
            raise VectorInitializationFailed(
                operation="initialize",
                collection=collection,
            ) from exc

    def insert_data(
        self,
        collection: Optional[str],
        chunks: List[Chunk],
        batch_size: int = 256,
        *args,
        **kwargs,
    ):
        """
        Insert data into a Qdrant collection.

        Args:
            collection (Optional[str]): Collection name.
            chunks (List[Chunk]): List of Chunk objects to insert.
            batch_size (int, optional): Number of chunks to insert in each batch. Defaults to 256.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        from qdrant_client import models

        try:
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i : i + batch_size]

                points = [
                    models.PointStruct(
                        id=uuid.uuid4().hex,
                        vector=chunk.embedding,
                        payload={
                            TEXT_PAYLOAD_KEY: chunk.text,
                            REFERENCE_PAYLOAD_KEY: chunk.reference,
                            METADATA_PAYLOAD_KEY: chunk.metadata,
                        },
                    )
                    for chunk in batch_chunks
                ]

                self.client.upsert(
                    collection_name=collection or self.default_collection, points=points
                )
        except Exception as exc:
            log.error(log.safe_exception_message("qdrant_insert", exc))
            raise VectorInsertFailed(
                operation="insert",
                collection=collection or self.default_collection,
            ) from exc

    def search_data(
        self,
        collection: Optional[str],
        vector: Union[np.array, List[float]],
        top_k: int = 5,
        *args,
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Search for similar vectors in a Qdrant collection.

        Args:
            collection (Optional[str]): Collection name..
            vector (Union[np.array, List[float]]): Query vector for similarity search.
            top_k (int, optional): Number of results to return. Defaults to 5.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[RetrievalResult]: List of retrieval results containing similar vectors.
        """
        try:
            embedding_profile = kwargs.pop("embedding_profile", None)
            if isinstance(embedding_profile, EmbeddingProfile):
                self.assert_collection_compatible(
                    collection or self.default_collection,
                    embedding_profile,
                )
            from qdrant_client import models

            results = self.client.query_points(
                collection_name=collection or self.default_collection,
                query=vector,
                query_filter=models.Filter(
                    must_not=[
                        models.HasIdCondition(
                            has_id=[MANIFEST_POINT_ID],
                        )
                    ]
                ),
                limit=top_k,
                with_payload=True,
                with_vectors=True,
            ).points

            metric_type = self._collection_metric_type(collection or self.default_collection)
            return [
                RetrievalResult.from_metric_value(
                    embedding=result.vector,
                    text=result.payload.get(TEXT_PAYLOAD_KEY, ""),
                    reference=result.payload.get(REFERENCE_PAYLOAD_KEY, ""),
                    metadata=result.payload.get(METADATA_PAYLOAD_KEY, {}),
                    metric_type=metric_type,
                    value=result.score,
                )
                for result in results
            ]
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("qdrant_search", exc))
            raise VectorSearchFailed(
                operation="search",
                collection=collection or self.default_collection,
            ) from exc

    def list_collections(self, *args, **kwargs) -> List[CollectionInfo]:
        """
        List all collections in the Qdrant database.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[CollectionInfo]: List of collection information objects.
        """
        collection_infos = []

        try:
            collections = self.client.get_collections().collections
            for collection in collections:
                manifest = self.get_collection_manifest(collection.name)
                collection_infos.append(
                    CollectionInfo(
                        collection_name=collection.name,
                        description=(
                            manifest.user_description if manifest is not None else collection.name
                        ),
                        manifest=manifest,
                    )
                )
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("qdrant_list_collections", exc))
            raise VectorListFailed(
                operation="list",
                collection=None,
            ) from exc

        return collection_infos

    def clear_db(self, collection: Optional[str] = None, *args, **kwargs):
        """
        Clear (drop) a collection from the Qdrant database.

        Args:
            collection (str, optional): Collection name to drop.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        try:
            self.client.delete_collection(collection_name=collection or self.default_collection)
        except Exception as exc:
            log.warning(log.safe_exception_message("qdrant_clear", exc))
