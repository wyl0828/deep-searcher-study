from __future__ import annotations


class VectorDBError(RuntimeError):
    code = "VECTOR_DB_ERROR"
    safe_message = "The vector database operation failed."
    retryable = False

    def __init__(self, *, operation: str, collection: str | None = None):
        super().__init__(self.safe_message)
        self.operation = operation
        self.collection = collection


class VectorDBUnavailable(VectorDBError):
    code = "VECTOR_DB_UNAVAILABLE"
    safe_message = "The vector database is temporarily unavailable."
    retryable = True


class CollectionNotFound(VectorDBError):
    code = "VECTOR_COLLECTION_NOT_FOUND"
    safe_message = "The requested vector collection does not exist."


class UnsafeCollectionReplacement(VectorDBError):
    code = "VECTOR_UNSAFE_COLLECTION_REPLACEMENT"
    safe_message = (
        "In-place replacement of an existing vector collection is disabled. "
        "Build and activate a versioned collection instead."
    )


class CollectionActivationFailed(VectorDBError):
    code = "VECTOR_COLLECTION_ACTIVATION_FAILED"
    safe_message = "The new vector collection version could not be activated."
    retryable = True


class CollectionRollbackFailed(VectorDBError):
    code = "VECTOR_COLLECTION_ROLLBACK_FAILED"
    safe_message = "The vector collection could not be rolled back."
    retryable = True


class CollectionManifestMissing(VectorDBError):
    code = "VECTOR_COLLECTION_MANIFEST_MISSING"
    safe_message = "The vector collection has no verified embedding manifest and must be rebuilt."


class CollectionManifestInvalid(VectorDBError):
    code = "VECTOR_COLLECTION_MANIFEST_INVALID"
    safe_message = "The vector collection manifest is invalid and cannot be trusted."


class CollectionManifestUnsupported(VectorDBError):
    code = "VECTOR_COLLECTION_MANIFEST_UNSUPPORTED"
    safe_message = "The configured vector database cannot enforce embedding manifests."


class CollectionManifestWriteFailed(VectorDBError):
    code = "VECTOR_COLLECTION_MANIFEST_WRITE_FAILED"
    safe_message = "The vector collection manifest could not be stored."
    retryable = True


class EmbeddingProfileMismatch(VectorDBError):
    code = "VECTOR_EMBEDDING_PROFILE_MISMATCH"
    safe_message = "The active embedding model is incompatible with the vector collection."

    def __init__(
        self,
        *,
        operation: str,
        collection: str | None = None,
        mismatches: tuple[str, ...] = (),
    ):
        super().__init__(operation=operation, collection=collection)
        self.mismatches = mismatches


class CollectionIngestionProfileMismatch(VectorDBError):
    code = "VECTOR_COLLECTION_INGESTION_PROFILE_MISMATCH"
    safe_message = "The incoming data profile is incompatible with the existing vector collection."


class VectorDimensionMismatch(VectorDBError):
    code = "VECTOR_DIMENSION_MISMATCH"
    safe_message = "The query vector does not match the collection dimension."


class VectorInitializationFailed(VectorDBError):
    code = "VECTOR_INITIALIZATION_FAILED"
    safe_message = "The vector collection could not be initialized."
    retryable = True


class VectorInsertFailed(VectorDBError):
    code = "VECTOR_INSERT_FAILED"
    safe_message = "The vector data could not be stored."
    retryable = True


class VectorSearchFailed(VectorDBError):
    code = "VECTOR_SEARCH_FAILED"
    safe_message = "The vector search could not be completed."
    retryable = True


class VectorListFailed(VectorDBError):
    code = "VECTOR_LIST_FAILED"
    safe_message = "The vector collections could not be listed."
    retryable = True
