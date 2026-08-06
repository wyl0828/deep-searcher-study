from abc import ABC, abstractmethod
from datetime import datetime, timezone
from math import isfinite
from typing import List, Literal, Union
from uuid import uuid4

import numpy as np

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.vector_db.exceptions import (
    CollectionIngestionProfileMismatch,
    CollectionManifestMissing,
    CollectionManifestUnsupported,
    EmbeddingProfileMismatch,
)

ScoreKind = Literal["distance", "similarity", "rank_score"]

DISTANCE_METRICS = frozenset(
    {
        "L2",
        "EUCLID",
        "EUCLIDEAN",
        "MANHATTAN",
        "JACCARD",
        "HAMMING",
    }
)
SIMILARITY_METRICS = frozenset(
    {
        "COSINE",
        "IP",
        "INNER_PRODUCT",
        "DOT",
        "DOT_PRODUCT",
    }
)


def score_kind_for_metric(metric_type: str) -> ScoreKind:
    """Return the native ordering semantics for a known vector metric."""
    normalized = str(metric_type or "UNKNOWN").strip().upper()
    if normalized in DISTANCE_METRICS:
        return "distance"
    if normalized in SIMILARITY_METRICS:
        return "similarity"
    return "rank_score"


def _numeric_score(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


class RetrievalResult:
    """
    Represents a result retrieved from the vector database.

    This class encapsulates the information about a retrieved document,
    including its embedding, text content, reference, metadata, and native retrieval value.

    Attributes:
        embedding: The vector embedding of the document.
        text: The text content of the document.
        reference: A reference to the source of the document.
        metadata: Additional metadata associated with the document.
        metric_type: The metric or ranking method used by the provider.
        distance: A lower-is-better distance, when the provider returns one.
        similarity: A higher-is-better similarity, when the provider returns one.
        rank_score: A higher-is-better provider ranking score with no distance semantics.
    """

    def __init__(
        self,
        embedding: np.array,
        text: str,
        reference: str,
        metadata: dict,
        score: float | None = None,
        *,
        metric_type: str = "UNKNOWN",
        distance: float | None = None,
        similarity: float | None = None,
        rank_score: float | None = None,
    ):
        """
        Initialize a RetrievalResult object.

        Args:
            embedding: The vector embedding of the document.
            text: The text content of the document.
            reference: A reference to the source of the document.
            metadata: Additional metadata associated with the document.
            score: Legacy untyped provider value. New code should use one semantic field.
            metric_type: Metric or ranking method name.
            distance: Native lower-is-better distance.
            similarity: Native higher-is-better similarity.
            rank_score: Native higher-is-better provider rank score.
        """
        self.embedding = embedding
        self.text = text
        self.reference = reference
        self.metadata = metadata
        explicit_values = (distance, similarity, rank_score)
        if score is not None and any(value is not None for value in explicit_values):
            raise ValueError("legacy score cannot be combined with a semantic score field")
        if sum(value is not None for value in explicit_values) > 1:
            raise ValueError("exactly one semantic score field may be populated")
        if score is not None:
            rank_score = score
        self.metric_type = str(metric_type or "UNKNOWN").strip().upper() or "UNKNOWN"
        self.distance = _numeric_score(distance, "distance")
        self.similarity = _numeric_score(similarity, "similarity")
        self.rank_score = _numeric_score(rank_score, "rank_score")

    @classmethod
    def from_metric_value(
        cls,
        embedding: np.array,
        text: str,
        reference: str,
        metadata: dict,
        *,
        metric_type: str,
        value: float,
        score_kind: ScoreKind | None = None,
    ) -> "RetrievalResult":
        """Build a result while preserving the provider value's native meaning."""
        kind = score_kind or score_kind_for_metric(metric_type)
        return cls(
            embedding=embedding,
            text=text,
            reference=reference,
            metadata=metadata,
            metric_type=metric_type,
            **{kind: value},
        )

    @property
    def score_kind(self) -> ScoreKind | None:
        if self.distance is not None:
            return "distance"
        if self.similarity is not None:
            return "similarity"
        if self.rank_score is not None:
            return "rank_score"
        return None

    @property
    def value(self) -> float | None:
        kind = self.score_kind
        return getattr(self, kind) if kind is not None else None

    @property
    def higher_is_better(self) -> bool | None:
        kind = self.score_kind
        return None if kind is None else kind != "distance"

    @property
    def score(self) -> float | None:
        """Compatibility view of the raw value; it intentionally has no direction semantics."""
        return self.value

    def __repr__(self):
        """
        Return a string representation of the RetrievalResult.

        Returns:
            A string representation of the RetrievalResult object.
        """
        return (
            "RetrievalResult("
            f"metric_type={self.metric_type}, score_kind={self.score_kind}, "
            f"value={self.value}, embedding={self.embedding}, text={self.text}, "
            f"reference={self.reference}), metadata={self.metadata}"
        )


def deduplicate_results(results: List[RetrievalResult]) -> List[RetrievalResult]:
    """
    Remove duplicate results based on text content.

    This function removes duplicate results from a list of RetrievalResult objects
    by keeping only the first occurrence of each unique text content.

    Args:
        results: A list of RetrievalResult objects to deduplicate.

    Returns:
        A list of deduplicated RetrievalResult objects.
    """
    all_text_set = set()
    deduplicated_results = []
    for result in results:
        if result.text not in all_text_set:
            all_text_set.add(result.text)
            deduplicated_results.append(result)
    return deduplicated_results


class CollectionInfo:
    """
    Represents information about a collection in the vector database.

    This class encapsulates the name and description of a collection.

    Attributes:
        collection_name: The name of the collection.
        description: The description of the collection.
    """

    def __init__(
        self,
        collection_name: str,
        description: str,
        *,
        manifest: CollectionManifest | None = None,
    ):
        """
        Initialize a CollectionInfo object.

        Args:
            collection_name: The name of the collection.
            description: The description of the collection.
        """
        self.collection_name = collection_name
        self.description = description
        self.manifest = manifest
        self.governance_status = "verified" if manifest is not None else "legacy_unverified"


class BaseVectorDB(ABC):
    """
    Abstract base class for vector database implementations.

    This class defines the interface for vector database implementations,
    including methods for initializing collections, inserting data, searching,
    listing collections, and clearing the database.

    Attributes:
        default_collection: The name of the default collection.
    """

    default_metric_type = "UNKNOWN"
    supports_collection_manifests = False
    allow_unmanaged_collections = False

    def __init__(
        self,
        default_collection: str = "deepsearcher",
        *args,
        **kwargs,
    ):
        """
        Initialize a BaseVectorDB object.

        Args:
            default_collection: The name of the default collection. Defaults to "deepsearcher".
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        self.default_collection = default_collection

    @abstractmethod
    def init_collection(
        self,
        dim: int,
        collection: str,
        description: str,
        force_new_collection=False,
        *args,
        **kwargs,
    ):
        """
        Initialize a collection in the vector database.

        Args:
            dim: The dimensionality of the vectors in the collection.
            collection: The name of the collection.
            description: The description of the collection.
            force_new_collection: Legacy flag. Implementations must never use it to
                drop an existing collection in place.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        pass

    @abstractmethod
    def insert_data(self, collection: str, chunks: List[Chunk], *args, **kwargs):
        """
        Insert data into a collection in the vector database.

        Args:
            collection: The name of the collection.
            chunks: A list of Chunk objects to insert.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        pass

    @abstractmethod
    def search_data(
        self, collection: str, vector: Union[np.array, List[float]], *args, **kwargs
    ) -> List[RetrievalResult]:
        """
        Search for similar vectors in a collection.

        Args:
            collection: The name of the collection.
            vector: The query vector to search for.
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            A list of RetrievalResult objects representing the search results.
        """
        pass

    def list_collections(self, *args, **kwargs) -> List[CollectionInfo]:
        """
        List all collections in the vector database.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.

        Returns:
            A list of CollectionInfo objects representing the collections.
        """
        pass

    def health_check(self) -> None:
        """Run the smallest provider-neutral operation that proves connectivity."""
        self.list_collections()

    def collection_exists(self, collection: str) -> bool:
        """Return whether a logical or physical collection exists."""
        return any(info.collection_name == collection for info in self.list_collections())

    def get_collection_manifest(
        self,
        collection: str,
    ) -> CollectionManifest | None:
        """Read a persisted collection contract."""
        if self.allow_unmanaged_collections:
            return None
        raise CollectionManifestUnsupported(
            operation="read_manifest",
            collection=collection,
        )

    def set_collection_manifest(
        self,
        collection: str,
        manifest: CollectionManifest,
    ) -> None:
        """Persist a collection contract."""
        raise CollectionManifestUnsupported(
            operation="write_manifest",
            collection=collection,
        )

    def assert_collection_compatible(
        self,
        collection: str,
        embedding: EmbeddingProfile,
    ) -> CollectionManifest | None:
        """Fail closed before a query reaches an unverified embedding space."""
        if self.allow_unmanaged_collections:
            return None
        manifest = self.get_collection_manifest(collection)
        if manifest is None:
            raise CollectionManifestMissing(
                operation="validate_manifest",
                collection=collection,
            )
        mismatches = tuple(manifest.embedding_mismatches(embedding))
        if mismatches:
            raise EmbeddingProfileMismatch(
                operation="validate_embedding",
                collection=collection,
                mismatches=mismatches,
            )
        return manifest

    def assert_collections_compatible(
        self,
        collections: List[str],
        embedding: EmbeddingProfile,
    ) -> dict[str, CollectionManifest | None]:
        """Validate every selected collection before query embedding or search."""
        return {
            collection: self.assert_collection_compatible(collection, embedding)
            for collection in collections
        }

    @staticmethod
    def assert_append_compatible(
        existing: CollectionManifest,
        incoming: CollectionManifest,
        *,
        collection: str,
    ) -> None:
        if (
            existing.embedding_mismatches(incoming.embedding)
            or existing.metric_type != incoming.metric_type
            or existing.chunk_config_version != incoming.chunk_config_version
            or existing.chunk_algorithm != incoming.chunk_algorithm
        ):
            raise CollectionIngestionProfileMismatch(
                operation="append",
                collection=collection,
            )

    def record_collection_mutation(
        self,
        collection: str,
        *,
        operation: str,
        identifier: str,
    ) -> CollectionManifest:
        """Advance the data version after an append/delete mutation."""
        manifest = self.get_collection_manifest(collection)
        if manifest is None:
            raise CollectionManifestMissing(
                operation="mutate_manifest",
                collection=collection,
            )
        updated = manifest.record_mutation(operation, identifier)
        self.set_collection_manifest(collection, updated)
        return updated

    @staticmethod
    def versioned_collection_name(collection: str, *, role: str = "v") -> str:
        """Return a bounded internal name for a new immutable collection version."""
        safe_role = "".join(character for character in role if character.isalnum())[:12] or "v"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = f"__{safe_role}_{timestamp}_{uuid4().hex[:8]}"
        return f"{collection[: max(1, 64 - len(suffix))]}{suffix}"

    def activate_collection_version(
        self,
        alias: str,
        candidate_collection: str,
        *args,
        **kwargs,
    ) -> dict:
        """Atomically point a stable logical name at a completed candidate version."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support collection version activation"
        )

    def rollback_collection_version(
        self,
        alias: str,
        target_collection: str,
        *args,
        **kwargs,
    ) -> dict:
        """Point a stable logical name back at one retained collection version."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support collection version rollback"
        )

    def delete_by_document_id(
        self,
        collection: str,
        document_id: str,
        *args,
        **kwargs,
    ) -> dict:
        """Delete every chunk associated with one source document."""
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support document-level deletion"
        )

    def delete_collection(self, collection: str, *args, **kwargs) -> dict:
        """Delete one collection and all of its stored vectors."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support collection deletion")

    @abstractmethod
    def clear_db(self, *args, **kwargs):
        """
        Clear the vector database.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        pass
