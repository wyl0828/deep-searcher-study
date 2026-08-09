import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Literal, Optional, Union

import numpy as np
from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
    WeightedRanker,
)
from pymilvus.exceptions import (
    CollectionNotExistException,
    ConnectError,
    ConnectionNotExistException,
    MilvusUnavailableException,
)

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, CollectionInfo, RetrievalResult
from deepsearcher.vector_db.exceptions import (
    CollectionActivationFailed,
    CollectionManifestInvalid,
    CollectionManifestWriteFailed,
    CollectionNotFound,
    CollectionRollbackFailed,
    UnsafeCollectionReplacement,
    VectorDBError,
    VectorDBUnavailable,
    VectorDimensionMismatch,
    VectorInitializationFailed,
    VectorInsertFailed,
    VectorListFailed,
    VectorSearchFailed,
)

_UNAVAILABLE_MARKERS = (
    "connection",
    "connect to server",
    "deadline exceeded",
    "failed to connect",
    "server unavailable",
    "service unavailable",
    "statuscode.unavailable",
)
_COLLECTION_NOT_FOUND_MARKERS = (
    "collection not exist",
    "collection not found",
    "can't find collection",
    "cannot find collection",
)
_DIMENSION_MISMATCH_MARKERS = (
    "dimension mismatch",
    "vector dimension",
    "expected vector size",
)
_INTERNAL_VERSION_PATTERN = re.compile(
    r"^(?P<prefix>.+)__(?P<role>v|previous)_(?P<timestamp>\d{14})_(?P<nonce>[0-9a-f]{8})$"
)
_MANIFEST_PROPERTY = "deepsearcher_collection_manifest"
DEFAULT_RRF_K = 60
RetrievalMode = Literal["auto", "dense", "bm25", "hybrid"]
HybridRanker = Literal["rrf", "weighted", "weighted_rrf"]
_RETRIEVAL_MODES = frozenset({"auto", "dense", "bm25", "hybrid"})
_HYBRID_RANKERS = frozenset({"rrf", "weighted", "weighted_rrf"})


def _normalize_retrieval_mode(value: str | None) -> RetrievalMode:
    normalized = str(value or "auto").strip().lower()
    if normalized == "sparse":
        normalized = "bm25"
    if normalized not in _RETRIEVAL_MODES:
        raise ValueError("retrieval_mode must be one of: auto, dense, bm25, hybrid")
    return normalized


def _normalize_hybrid_ranker(value: str | None) -> HybridRanker:
    normalized = str(value or "rrf").strip().lower()
    if normalized not in _HYBRID_RANKERS:
        raise ValueError("hybrid_ranker must be one of: rrf, weighted, weighted_rrf")
    return normalized


def _hit_identity(hit: dict) -> tuple[object, ...]:
    hit_id = hit.get("id")
    if hit_id is not None:
        return ("id", hit_id)
    entity = hit.get("entity") or {}
    metadata = entity.get("metadata") or {}
    return (
        "content",
        entity.get("reference"),
        metadata.get("document_id"),
        metadata.get("page_number"),
        metadata.get("chunk_index"),
        entity.get("text"),
    )


def _weighted_rrf_hits(
    sparse_hits: list[dict],
    dense_hits: list[dict],
    *,
    sparse_weight: float,
    dense_weight: float,
    rrf_k: int,
    dense_anchor_count: int,
    limit: int,
) -> list[dict]:
    """Fuse two ranked hit lists while optionally preserving dense head anchors."""
    scores: dict[tuple[object, ...], float] = {}
    hits: dict[tuple[object, ...], dict] = {}
    dense_ranks: dict[tuple[object, ...], int] = {}
    sparse_ranks: dict[tuple[object, ...], int] = {}
    for rank, hit in enumerate(dense_hits, start=1):
        key = _hit_identity(hit)
        hits.setdefault(key, hit)
        dense_ranks.setdefault(key, rank)
        scores[key] = scores.get(key, 0.0) + dense_weight / (rrf_k + rank)
    for rank, hit in enumerate(sparse_hits, start=1):
        key = _hit_identity(hit)
        hits.setdefault(key, hit)
        sparse_ranks.setdefault(key, rank)
        scores[key] = scores.get(key, 0.0) + sparse_weight / (rrf_k + rank)

    ranked = sorted(
        hits,
        key=lambda key: (
            -scores[key],
            dense_ranks.get(key, len(dense_hits) + 1),
            sparse_ranks.get(key, len(sparse_hits) + 1),
        ),
    )
    anchor_keys = list(dense_ranks)[: min(dense_anchor_count, limit)]
    ordered = anchor_keys + [key for key in ranked if key not in set(anchor_keys)]
    return [
        {
            **hits[key],
            "distance": scores[key],
        }
        for key in ordered[:limit]
    ]


class Milvus(BaseVectorDB):
    """Milvus class is a subclass of DB class."""

    client: MilvusClient = None
    default_metric_type = "L2"
    supports_collection_manifests = True

    def __init__(
        self,
        default_collection: str = "deepsearcher",
        uri: str = "http://localhost:19530",
        token: str = "root:Milvus",
        user: str = "",
        password: str = "",
        db: str = "default",
        hybrid: bool = False,
        rrf_k: int = DEFAULT_RRF_K,
        hybrid_ranker: str = "rrf",
        hybrid_sparse_weight: float = 1.0,
        hybrid_dense_weight: float = 1.0,
        hybrid_candidate_multiplier: int = 1,
        hybrid_dense_anchor_count: int = 0,
        **kwargs,
    ):
        """
        Initialize the Milvus client.

        Args:
            default_collection (str, optional): Default collection name. Defaults to "deepsearcher".
            uri (str, optional): URI for connecting to Milvus server. Defaults to "http://localhost:19530".
            token (str, optional): Authentication token for Milvus. Defaults to "root:Milvus".
            user (str, optional): Username for authentication. Defaults to "".
            password (str, optional): Password for authentication. Defaults to "".
            db (str, optional): Database name. Defaults to "default".
            hybrid (bool, optional): Whether to enable hybrid search. Defaults to False.
            rrf_k (int, optional): Reciprocal Rank Fusion constant. Defaults to 60.
            hybrid_ranker: Fusion strategy: rrf, weighted or weighted_rrf.
            hybrid_sparse_weight: Sparse contribution to weighted fusion.
            hybrid_dense_weight: Dense contribution to weighted fusion.
            hybrid_candidate_multiplier: Per-retriever candidates relative to top_k.
            hybrid_dense_anchor_count: Dense head results preserved by weighted_rrf.
            **kwargs: Additional keyword arguments to pass to the MilvusClient.
        """
        super().__init__(default_collection)
        self.default_collection = default_collection
        self.client = MilvusClient(
            uri=uri, user=user, password=password, token=token, db_name=db, timeout=30, **kwargs
        )
        self.hybrid = bool(hybrid)
        self.metric_type = self.default_metric_type
        if isinstance(rrf_k, bool) or int(rrf_k) <= 0:
            raise ValueError("rrf_k must be a positive integer")
        self.rrf_k = int(rrf_k)
        self.hybrid_ranker = _normalize_hybrid_ranker(hybrid_ranker)
        if any(
            isinstance(value, bool) or float(value) <= 0
            for value in (hybrid_sparse_weight, hybrid_dense_weight)
        ):
            raise ValueError("hybrid fusion weights must be positive numbers")
        if isinstance(hybrid_candidate_multiplier, bool) or int(hybrid_candidate_multiplier) <= 0:
            raise ValueError("hybrid_candidate_multiplier must be a positive integer")
        if isinstance(hybrid_dense_anchor_count, bool) or int(hybrid_dense_anchor_count) < 0:
            raise ValueError("hybrid_dense_anchor_count must be a non-negative integer")
        self.hybrid_sparse_weight = float(hybrid_sparse_weight)
        self.hybrid_dense_weight = float(hybrid_dense_weight)
        self.hybrid_candidate_multiplier = int(hybrid_candidate_multiplier)
        self.hybrid_dense_anchor_count = int(hybrid_dense_anchor_count)

    @staticmethod
    def _map_error(
        exc: Exception,
        *,
        operation: str,
        collection: str | None,
        fallback: type[VectorDBError],
    ) -> VectorDBError:
        message = str(exc).lower()
        if isinstance(exc, (ConnectError, ConnectionNotExistException, MilvusUnavailableException)):
            return VectorDBUnavailable(operation=operation, collection=collection)
        if any(marker in message for marker in _UNAVAILABLE_MARKERS):
            return VectorDBUnavailable(operation=operation, collection=collection)
        if isinstance(exc, CollectionNotExistException) or any(
            marker in message for marker in _COLLECTION_NOT_FOUND_MARKERS
        ):
            return CollectionNotFound(operation=operation, collection=collection)
        if any(marker in message for marker in _DIMENSION_MISMATCH_MARKERS):
            return VectorDimensionMismatch(operation=operation, collection=collection)
        return fallback(operation=operation, collection=collection)

    def _collection_exists(self, collection: str, *, operation: str) -> bool:
        try:
            return bool(self.client.has_collection(collection, timeout=5))
        except Exception as exc:
            raise self._map_error(
                exc,
                operation=operation,
                collection=collection,
                fallback=VectorSearchFailed,
            ) from exc

    def collection_exists(self, collection: str) -> bool:
        return self._collection_exists(collection, operation="exists")

    def _find_alias_target(self, alias: str) -> str | None:
        """Resolve an alias without triggering noisy describe-alias errors."""
        for collection in self.client.list_collections():
            aliases = self._list_aliases_for_collection(collection)
            if alias in aliases:
                return collection
        return None

    def _list_aliases_for_collection(self, collection: str) -> list[str]:
        """Normalize Milvus server/client variants to a plain alias-name list."""
        response = self.client.list_aliases(
            collection_name=collection,
            timeout=5,
        )
        if isinstance(response, dict):
            aliases = response.get("aliases", [])
        else:
            aliases = getattr(response, "aliases", response)
        return list(aliases or [])

    @staticmethod
    def _manifest_from_description(
        description: dict,
        *,
        collection: str,
    ) -> CollectionManifest | None:
        properties = description.get("properties") or {}
        raw_manifest = properties.get(_MANIFEST_PROPERTY) if isinstance(properties, dict) else None
        if raw_manifest is None:
            return None
        try:
            return CollectionManifest.from_json(str(raw_manifest))
        except (TypeError, ValueError) as exc:
            raise CollectionManifestInvalid(
                operation="read_manifest",
                collection=collection,
            ) from exc

    def _physical_collection(self, collection: str) -> str | None:
        alias_target = self._find_alias_target(collection)
        if alias_target is not None:
            return alias_target
        if self._collection_exists(collection, operation="resolve_manifest"):
            return collection
        return None

    def get_collection_manifest(
        self,
        collection: str,
    ) -> CollectionManifest | None:
        try:
            physical_collection = self._physical_collection(collection)
            if physical_collection is None:
                return None
            description = self.client.describe_collection(
                collection_name=physical_collection,
                timeout=10,
            )
            return self._manifest_from_description(
                description,
                collection=collection,
            )
        except CollectionManifestInvalid:
            raise
        except VectorDBError:
            raise
        except Exception as exc:
            raise self._map_error(
                exc,
                operation="read_manifest",
                collection=collection,
                fallback=VectorListFailed,
            ) from exc

    def set_collection_manifest(
        self,
        collection: str,
        manifest: CollectionManifest,
    ) -> None:
        try:
            physical_collection = self._physical_collection(collection)
            if physical_collection is None:
                raise CollectionNotFound(
                    operation="write_manifest",
                    collection=collection,
                )
            self.client.alter_collection_properties(
                collection_name=physical_collection,
                properties={_MANIFEST_PROPERTY: manifest.to_json()},
                timeout=10,
            )
        except VectorDBError:
            raise
        except Exception as exc:
            raise CollectionManifestWriteFailed(
                operation="write_manifest",
                collection=collection,
            ) from exc

    @staticmethod
    def _is_internal_version(collection: str) -> bool:
        return _INTERNAL_VERSION_PATTERN.fullmatch(collection) is not None

    @classmethod
    def _version_belongs_to_alias(cls, alias: str, collection: str) -> bool:
        match = _INTERNAL_VERSION_PATTERN.fullmatch(collection)
        if match is None:
            return False
        version_prefix = match.group("prefix")
        return version_prefix == alias[: len(version_prefix)]

    def init_collection(
        self,
        dim: int,
        collection: Optional[str] = "deepsearcher",
        description: Optional[str] = "",
        force_new_collection: bool = False,
        text_max_length: int = 65_535,
        reference_max_length: int = 2048,
        metric_type: str = "L2",
        *args,
        **kwargs,
    ):
        """
        Initialize a collection in Milvus.

        Args:
            dim (int): Dimension of the vector embeddings.
            collection (Optional[str], optional): Collection name. Defaults to "deepsearcher".
            description (Optional[str], optional): Collection description. Defaults to "".
            force_new_collection (bool, optional): Whether to force create a new collection if it already exists. Defaults to False.
            text_max_length (int, optional): Maximum length for text field. Defaults to 65_535.
            reference_max_length (int, optional): Maximum length for reference field. Defaults to 2048.
            metric_type (str, optional): Metric type for vector similarity search. Defaults to "L2".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        if not collection:
            collection = self.default_collection
        if description is None:
            description = ""

        self.metric_type = metric_type

        try:
            has_collection = self.client.has_collection(collection, timeout=5)
            if force_new_collection and has_collection:
                raise UnsafeCollectionReplacement(
                    operation="initialize",
                    collection=collection,
                )
            elif has_collection:
                return {
                    "created": False,
                    "collection": collection,
                }
            schema = self.client.create_schema(
                enable_dynamic_field=False, auto_id=True, description=description
            )
            schema.add_field("id", DataType.INT64, is_primary=True)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)

            if self.hybrid:
                analyzer_params = {"tokenizer": "standard", "filter": ["lowercase"]}
                schema.add_field(
                    "text",
                    DataType.VARCHAR,
                    max_length=text_max_length,
                    analyzer_params=analyzer_params,
                    enable_match=True,
                    enable_analyzer=True,
                )
            else:
                schema.add_field("text", DataType.VARCHAR, max_length=text_max_length)

            schema.add_field("reference", DataType.VARCHAR, max_length=reference_max_length)
            schema.add_field("metadata", DataType.JSON)

            if self.hybrid:
                schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
                bm25_function = Function(
                    name="bm25",
                    function_type=FunctionType.BM25,
                    input_field_names=["text"],
                    output_field_names="sparse_vector",
                )
                schema.add_function(bm25_function)

            index_params = self.client.prepare_index_params()
            index_params.add_index(field_name="embedding", metric_type=metric_type)

            if self.hybrid:
                index_params.add_index(
                    field_name="sparse_vector",
                    index_type="SPARSE_INVERTED_INDEX",
                    metric_type="BM25",
                )

            self.client.create_collection(
                collection,
                schema=schema,
                index_params=index_params,
                consistency_level="Strong",
            )
            log.color_print(f"create collection [{collection}] successfully")
            return {
                "created": True,
                "collection": collection,
            }
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("milvus_init", exc))
            raise self._map_error(
                exc,
                operation="initialize",
                collection=collection,
                fallback=VectorInitializationFailed,
            ) from exc

    def activate_collection_version(
        self,
        alias: str,
        candidate_collection: str,
        *args,
        **kwargs,
    ) -> dict:
        """Activate a completed candidate while retaining the prior version."""
        try:
            if alias == candidate_collection:
                raise ValueError("alias and candidate collection must differ")
            if not self.client.has_collection(candidate_collection, timeout=5):
                raise CollectionNotFound(
                    operation="activate",
                    collection=candidate_collection,
                )

            previous_collection = self._find_alias_target(alias)
            migrated_physical_collection = False
            if previous_collection is not None:
                self.client.alter_alias(
                    collection_name=candidate_collection,
                    alias=alias,
                    timeout=10,
                )
            elif self.client.has_collection(alias, timeout=5):
                previous_collection = self.versioned_collection_name(
                    alias,
                    role="previous",
                )
                self.client.rename_collection(
                    old_name=alias,
                    new_name=previous_collection,
                    timeout=30,
                )
                migrated_physical_collection = True
                try:
                    self.client.create_alias(
                        collection_name=previous_collection,
                        alias=alias,
                        timeout=10,
                    )
                except Exception as exc:
                    try:
                        self.client.rename_collection(
                            old_name=previous_collection,
                            new_name=alias,
                            timeout=30,
                        )
                    except Exception as rollback_exc:
                        raise CollectionRollbackFailed(
                            operation="activate_rollback",
                            collection=alias,
                        ) from rollback_exc
                    raise exc
                self.client.alter_alias(
                    collection_name=candidate_collection,
                    alias=alias,
                    timeout=10,
                )
            else:
                self.client.create_alias(
                    collection_name=candidate_collection,
                    alias=alias,
                    timeout=10,
                )

            log.warning(
                "collection_version_activated "
                f"alias={alias} candidate={candidate_collection} "
                f"previous={previous_collection or '<none>'}"
            )
            return {
                "active_collection": alias,
                "backing_collection": candidate_collection,
                "previous_collection": previous_collection,
                "alias_switched": True,
                "migrated_physical_collection": migrated_physical_collection,
                "rollback_available": previous_collection is not None,
            }
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(
                "collection_version_activation_failed "
                f"alias={alias} candidate={candidate_collection}"
            )
            raise CollectionActivationFailed(
                operation="activate",
                collection=alias,
            ) from exc

    def rollback_collection_version(
        self,
        alias: str,
        target_collection: str,
        *args,
        **kwargs,
    ) -> dict:
        """Atomically move an existing alias back to one retained version."""
        try:
            current_collection = self._find_alias_target(alias)
            if current_collection is None:
                raise CollectionNotFound(operation="rollback", collection=alias)
            if not self._version_belongs_to_alias(alias, target_collection):
                raise ValueError("rollback target is outside the alias version scope")
            if not self.client.has_collection(target_collection, timeout=5):
                raise CollectionNotFound(
                    operation="rollback",
                    collection=target_collection,
                )
            self.client.alter_alias(
                collection_name=target_collection,
                alias=alias,
                timeout=10,
            )
            log.warning(
                "collection_version_rolled_back "
                f"alias={alias} target={target_collection} previous={current_collection}"
            )
            return {
                "active_collection": alias,
                "backing_collection": target_collection,
                "previous_collection": current_collection,
                "alias_switched": True,
                "rollback_available": True,
            }
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(
                f"collection_version_rollback_failed alias={alias} target={target_collection}"
            )
            raise CollectionRollbackFailed(
                operation="rollback",
                collection=alias,
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
        Insert data into a Milvus collection.

        Args:
            collection (Optional[str]): Collection name. If None, uses default_collection.
            chunks (List[Chunk]): List of Chunk objects to insert.
            batch_size (int, optional): Number of chunks to insert in each batch. Defaults to 256.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        if not collection:
            collection = self.default_collection
        texts = [chunk.text for chunk in chunks]
        references = [chunk.reference for chunk in chunks]
        metadatas = [chunk.metadata for chunk in chunks]
        embeddings = [chunk.embedding for chunk in chunks]

        datas = [
            {
                "embedding": embedding,
                "text": text,
                "reference": reference,
                "metadata": metadata,
            }
            for embedding, text, reference, metadata in zip(
                embeddings, texts, references, metadatas
            )
        ]
        batch_datas = [datas[i : i + batch_size] for i in range(0, len(datas), batch_size)]
        try:
            for batch_data in batch_datas:
                self.client.insert(collection_name=collection, data=batch_data)
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("milvus_insert", exc))
            raise self._map_error(
                exc,
                operation="insert",
                collection=collection,
                fallback=VectorInsertFailed,
            ) from exc

    def search_data(
        self,
        collection: Optional[str],
        vector: Union[np.array, List[float]],
        top_k: int = 5,
        query_text: Optional[str] = None,
        *args,
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Search for similar vectors in a Milvus collection.

        Args:
            collection (Optional[str]): Collection name. If None, uses default_collection.
            vector (Union[np.array, List[float]]): Query vector for similarity search.
            top_k (int, optional): Number of results to return. Defaults to 5.
            query_text (Optional[str], optional): Original query text for hybrid search. Defaults to None.
            retrieval_mode: ``auto`` (legacy behavior), ``dense``, ``bm25`` or
                ``hybrid``. ``sparse`` is accepted as an alias for ``bm25``.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[RetrievalResult]: List of retrieval results containing similar vectors.
        """
        if not collection:
            collection = self.default_collection
        requested_mode = _normalize_retrieval_mode(kwargs.pop("retrieval_mode", "auto"))
        retrieval_mode: RetrievalMode = requested_mode
        if retrieval_mode == "auto":
            retrieval_mode = "hybrid" if self.hybrid and query_text else "dense"
        if retrieval_mode in {"bm25", "hybrid"} and not self.hybrid:
            raise ValueError(
                "bm25 and hybrid retrieval require a Milvus collection created with hybrid=True"
            )
        if retrieval_mode in {"bm25", "hybrid"} and not query_text:
            raise ValueError(f"{retrieval_mode} retrieval requires query_text")
        try:
            embedding_profile = kwargs.pop("embedding_profile", None)
            if isinstance(embedding_profile, EmbeddingProfile):
                self.assert_collection_compatible(
                    collection,
                    embedding_profile,
                )
            if not self._collection_exists(collection, operation="search"):
                raise CollectionNotFound(operation="search", collection=collection)
            if retrieval_mode == "hybrid":
                hybrid_ranker = _normalize_hybrid_ranker(getattr(self, "hybrid_ranker", "rrf"))
                candidate_limit = top_k * int(getattr(self, "hybrid_candidate_multiplier", 1))
                sparse_weight = float(getattr(self, "hybrid_sparse_weight", 1.0))
                dense_weight = float(getattr(self, "hybrid_dense_weight", 1.0))
                sparse_search_params = {"metric_type": "BM25"}
                sparse_request = AnnSearchRequest(
                    [query_text], "sparse_vector", sparse_search_params, limit=candidate_limit
                )

                dense_search_params = {"metric_type": self.metric_type}
                dense_request = AnnSearchRequest(
                    [vector], "embedding", dense_search_params, limit=candidate_limit
                )
                output_fields = ["embedding", "text", "reference", "metadata"]
                if hybrid_ranker == "weighted_rrf":
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        sparse_future = executor.submit(
                            self.client.search,
                            collection_name=collection,
                            data=[query_text],
                            anns_field="sparse_vector",
                            search_params=sparse_search_params,
                            limit=candidate_limit,
                            output_fields=output_fields,
                            timeout=10,
                        )
                        dense_future = executor.submit(
                            self.client.search,
                            collection_name=collection,
                            data=[vector],
                            anns_field="embedding",
                            search_params=dense_search_params,
                            limit=candidate_limit,
                            output_fields=output_fields,
                            timeout=10,
                        )
                        sparse_results = sparse_future.result()
                        dense_results = dense_future.result()
                    search_results = [
                        _weighted_rrf_hits(
                            list(sparse_results[0]),
                            list(dense_results[0]),
                            sparse_weight=sparse_weight,
                            dense_weight=dense_weight,
                            rrf_k=self.rrf_k,
                            dense_anchor_count=int(getattr(self, "hybrid_dense_anchor_count", 0)),
                            limit=top_k,
                        )
                    ]
                else:
                    ranker = (
                        WeightedRanker(sparse_weight, dense_weight)
                        if hybrid_ranker == "weighted"
                        else RRFRanker(self.rrf_k)
                    )
                    search_results = self.client.hybrid_search(
                        collection_name=collection,
                        reqs=[sparse_request, dense_request],
                        ranker=ranker,
                        limit=top_k,
                        output_fields=output_fields,
                        timeout=10,
                    )
            elif retrieval_mode == "bm25":
                search_results = self.client.search(
                    collection_name=collection,
                    data=[query_text],
                    anns_field="sparse_vector",
                    search_params={"metric_type": "BM25"},
                    limit=top_k,
                    output_fields=["embedding", "text", "reference", "metadata"],
                    timeout=10,
                )
            else:
                search_results = self.client.search(
                    collection_name=collection,
                    data=[vector],
                    anns_field="embedding",
                    limit=top_k,
                    output_fields=["embedding", "text", "reference", "metadata"],
                    timeout=10,
                )

            result_metric = (
                (
                    "WEIGHTED_RRF"
                    if getattr(self, "hybrid_ranker", "rrf") == "weighted_rrf"
                    else "WEIGHTED"
                    if getattr(self, "hybrid_ranker", "rrf") == "weighted"
                    else "RRF"
                )
                if retrieval_mode == "hybrid"
                else "BM25"
                if retrieval_mode == "bm25"
                else getattr(
                    self,
                    "metric_type",
                    self.default_metric_type,
                )
            )
            result_kind = "rank_score" if retrieval_mode in {"bm25", "hybrid"} else None
            return [
                RetrievalResult.from_metric_value(
                    embedding=b["entity"]["embedding"],
                    text=b["entity"]["text"],
                    reference=b["entity"]["reference"],
                    metadata=b["entity"]["metadata"],
                    metric_type=result_metric,
                    value=b["distance"],
                    score_kind=result_kind,
                )
                for a in search_results
                for b in a
            ]
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("milvus_search", exc))
            raise self._map_error(
                exc,
                operation="search",
                collection=collection,
                fallback=VectorSearchFailed,
            ) from exc

    def list_collections(self, *args, **kwargs) -> List[CollectionInfo]:
        """
        List all collections in the Milvus database.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            List[CollectionInfo]: List of collection information objects.
        """
        collection_infos = []
        dim = kwargs.pop("dim", 0)
        try:
            collections = self.client.list_collections()
            for collection in collections:
                description = self.client.describe_collection(collection)
                manifest = self._manifest_from_description(
                    description,
                    collection=collection,
                )
                aliases = self._list_aliases_for_collection(collection)
                if dim != 0:
                    skip = False
                    for field_dict in description["fields"]:
                        if (
                            field_dict["name"] == "embedding"
                            and field_dict["type"] == DataType.FLOAT_VECTOR
                        ):
                            if field_dict["params"]["dim"] != dim:
                                skip = True
                    if skip:
                        continue
                for alias in aliases:
                    collection_infos.append(
                        CollectionInfo(
                            collection_name=alias,
                            description=(
                                manifest.user_description
                                if manifest is not None
                                else description["description"]
                            ),
                            manifest=manifest,
                        )
                    )
                if not self._is_internal_version(collection):
                    collection_infos.append(
                        CollectionInfo(
                            collection_name=collection,
                            description=(
                                manifest.user_description
                                if manifest is not None
                                else description["description"]
                            ),
                            manifest=manifest,
                        )
                    )
        except VectorDBError:
            raise
        except Exception as exc:
            log.error(log.safe_exception_message("milvus_list_collections", exc))
            raise self._map_error(
                exc,
                operation="list",
                collection=None,
                fallback=VectorListFailed,
            ) from exc
        return collection_infos

    def health_check(self) -> None:
        """Verify Milvus RPC readiness without describing every collection."""
        try:
            self.client.list_collections()
        except VectorDBError:
            raise
        except Exception as exc:
            raise self._map_error(
                exc,
                operation="health_check",
                collection=None,
                fallback=VectorListFailed,
            ) from exc

    def describe_retrieval_profile(self, collection: str) -> dict:
        """Return a secret-free schema/index snapshot for evaluation and audits."""
        try:
            physical_collection = self._physical_collection(collection)
            if physical_collection is None:
                raise CollectionNotFound(
                    operation="describe_retrieval_profile",
                    collection=collection,
                )
            description = self.client.describe_collection(
                collection_name=physical_collection,
                timeout=10,
            )
            fields = []
            for field in description.get("fields") or []:
                raw_type = field.get("type")
                try:
                    type_name = DataType(raw_type).name
                except (TypeError, ValueError):
                    type_name = str(raw_type)
                fields.append(
                    {
                        "name": str(field.get("name") or ""),
                        "type": type_name,
                        "params": dict(field.get("params") or {}),
                    }
                )

            indexes = []
            for index_name in self.client.list_indexes(
                collection_name=physical_collection,
            ):
                index = self.client.describe_index(
                    collection_name=physical_collection,
                    index_name=index_name,
                    timeout=10,
                )
                indexes.append(
                    {
                        key: index.get(key)
                        for key in (
                            "index_name",
                            "field_name",
                            "index_type",
                            "metric_type",
                            "state",
                            "total_rows",
                            "indexed_rows",
                            "pending_index_rows",
                        )
                        if index.get(key) is not None
                    }
                )
            stats = self.client.get_collection_stats(
                collection_name=physical_collection,
                timeout=10,
            )
            field_names = {field["name"] for field in fields}
            capabilities = ["dense"]
            if "sparse_vector" in field_names:
                capabilities.extend(("bm25", "hybrid"))
            return {
                "logical_collection": collection,
                "physical_collection": physical_collection,
                "row_count": int((stats or {}).get("row_count", 0)),
                "fields": fields,
                "indexes": indexes,
                "capabilities": capabilities,
                "configured_default": "hybrid" if self.hybrid else "dense",
                "fusion": {
                    "algorithm": getattr(self, "hybrid_ranker", "rrf").upper(),
                    "k": self.rrf_k,
                    "dense_weight": getattr(self, "hybrid_dense_weight", 1.0),
                    "sparse_weight": getattr(self, "hybrid_sparse_weight", 1.0),
                    "candidate_multiplier": getattr(self, "hybrid_candidate_multiplier", 1),
                    "dense_anchor_count": getattr(self, "hybrid_dense_anchor_count", 0),
                },
            }
        except VectorDBError:
            raise
        except Exception as exc:
            raise self._map_error(
                exc,
                operation="describe_retrieval_profile",
                collection=collection,
                fallback=VectorListFailed,
            ) from exc

    def delete_by_document_id(
        self,
        collection: Optional[str],
        document_id: str,
        *args,
        **kwargs,
    ) -> dict:
        """Delete all chunks whose JSON metadata belongs to a document."""
        if not collection:
            collection = self.default_collection
        if not self.client.has_collection(collection, timeout=5):
            return {"delete_count": 0}
        manifest = self.get_collection_manifest(collection)
        escaped_document_id = document_id.replace("\\", "\\\\").replace('"', '\\"')
        result = self.client.delete(
            collection_name=collection,
            filter=f'metadata["document_id"] == "{escaped_document_id}"',
            timeout=10,
        )
        if manifest is None:
            return result
        updated_manifest = manifest.record_mutation("delete_document", document_id)
        self.set_collection_manifest(collection, updated_manifest)
        if isinstance(result, dict):
            return {
                **result,
                "manifest": updated_manifest.to_dict(),
            }
        return {
            "delete_count": int(getattr(result, "delete_count", 0) or 0),
            "manifest": updated_manifest.to_dict(),
        }

    def delete_collection(self, collection: Optional[str], *args, **kwargs) -> dict:
        if not collection:
            collection = self.default_collection
        alias_target = self._find_alias_target(collection)
        if alias_target is not None:
            self.client.drop_alias(alias=collection, timeout=10)
            deleted_versions = []
            for version in self.client.list_collections():
                if self._version_belongs_to_alias(collection, version):
                    self.client.drop_collection(version)
                    deleted_versions.append(version)
            log.warning(
                f"collection_alias_deleted alias={collection} versions={len(deleted_versions)}"
            )
            return {
                "deleted": True,
                "deleted_versions": deleted_versions,
            }
        if not self.client.has_collection(collection, timeout=5):
            return {"deleted": False}
        self.client.drop_collection(collection)
        log.warning(f"collection_deleted collection={collection}")
        return {"deleted": True}

    def clear_db(self, collection: str = "deepsearcher", *args, **kwargs):
        """
        Clear (drop) a collection from the Milvus database.

        Args:
            collection (str, optional): Collection name to drop. Defaults to "deepsearcher".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        if not collection:
            collection = self.default_collection
        try:
            return self.delete_collection(collection)
        except Exception as exc:
            log.warning(log.safe_exception_message("milvus_clear", exc))
