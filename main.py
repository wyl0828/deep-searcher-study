import argparse
import asyncio
import hmac
import json
import logging
import math
import os
import re
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from time import monotonic
from typing import AsyncIterator, Callable, Dict, List, Literal, Sequence, Union
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from deepsearcher.collection_manifest import EmbeddingProfile
from deepsearcher.configuration import (
    Configuration,
    RuntimeComponents,
    RuntimeInitializationError,
    build_runtime,
)
from deepsearcher.health import RuntimeHealthMonitor, blocked_checks, failed_check
from deepsearcher.provenance import TrustProvenanceSession
from deepsearcher.query_context import ContextualQuery, contextualize_query
from deepsearcher.runtime_registry import (
    DEFAULT_TENANT_ID,
    RuntimeControlError,
    RuntimeControlStore,
    RuntimePatch,
    RuntimeRegistry,
    RuntimeRequestContext,
    RuntimeTenantUnauthorized,
    close_runtime,
)
from deepsearcher.trace import QueryCancelled, TraceCollector
from deepsearcher.trust import temporal_timezone_from_query_settings
from deepsearcher.vector_db.exceptions import (
    CollectionIngestionProfileMismatch,
    CollectionManifestInvalid,
    CollectionManifestMissing,
    CollectionManifestUnsupported,
    CollectionNotFound,
    EmbeddingProfileMismatch,
    UnsafeCollectionReplacement,
    VectorDBError,
    VectorDBUnavailable,
    VectorDimensionMismatch,
)
from deepsearcher.versioning import sanitize_document_governance_metadata

load_dotenv()
logger = logging.getLogger(__name__)
PRODUCT_COLLECTION_PATTERN = re.compile(r"kb_[0-9a-f]{32}")

RuntimeFactory = Callable[[Configuration], RuntimeComponents]
ConfigFactory = Callable[[], Configuration]
RuntimeStoreFactory = Callable[[], RuntimeControlStore]
SAFE_RUNTIME_COMPONENTS = {
    "configuration",
    "llm",
    "embedding",
    "file_loader",
    "web_crawler",
    "vector_db",
    "web_search",
    "searcher",
    "runtime",
    "trust_temporal",
}
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
QUERY_PATHS = {"/query", "/query/stream"}
DEFAULT_QUERY_RATE_LIMIT = 60
DEFAULT_QUERY_RATE_WINDOW_SECONDS = 60


class APIError(RuntimeError):
    """A stable public API error that never exposes its internal cause."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.status_code = status_code
        self.retryable = retryable
        self.headers = headers or {}


class QueryRateLimiter:
    """Small per-process fixed-window guard for query admission."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = max(0, limit)
        self.window_seconds = max(1, window_seconds)
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def admit(self, key: str) -> tuple[bool, int]:
        if self.limit == 0:
            return True, 0
        now = monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - requests[0])))
                return False, retry_after
            requests.append(now)
            return True, 0


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    headers: dict[str, str] | None = None,
    extra: dict | None = None,
    root_extra: dict | None = None,
) -> JSONResponse:
    error = {
        "code": code,
        "message": message,
        "request_id": _request_id(request),
        "retryable": retryable,
    }
    if extra:
        error.update(extra)
    content = {"error": error}
    if root_extra:
        content.update(root_extra)
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )


def _safe_request_route(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _parse_cors_origins(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        values = os.environ.get("DEEPSEARCHER_CORS_ORIGINS", "")
    candidates = values.split(",") if isinstance(values, str) else values
    origins: list[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        parsed = urlsplit(value)
        if (
            value in {"", "*", "null"}
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}"
        if normalized not in origins:
            origins.append(normalized)
    return origins


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def load_from_local_files(*args, **kwargs):
    from deepsearcher.offline_loading import load_from_local_files as implementation

    return implementation(*args, **kwargs)


def load_from_website(*args, **kwargs):
    from deepsearcher.offline_loading import load_from_website as implementation

    return implementation(*args, **kwargs)


def query(*args, **kwargs):
    from deepsearcher.online_query import query as implementation

    return implementation(*args, **kwargs)


def query_with_trace(*args, **kwargs):
    from deepsearcher.online_query import query_with_trace as implementation

    return implementation(*args, **kwargs)


def _safe_runtime_component(exc: RuntimeInitializationError) -> str:
    return exc.component if exc.component in SAFE_RUNTIME_COMPONENTS else "runtime"


def _runtime_error(component: str, cause: Exception) -> RuntimeInitializationError:
    error = RuntimeInitializationError(component)
    error.__cause__ = cause
    return error


def _resolve_tenant(
    request: Request,
    tenant_header: str | None,
    service_token: str | None,
) -> str:
    default_tenant = request.app.state.default_tenant
    tenant_id = (tenant_header or default_tenant).strip().lower()
    expected_token = request.app.state.service_token
    selecting_non_default = tenant_id != default_tenant
    if expected_token:
        if service_token is None or not hmac.compare_digest(service_token, expected_token):
            raise APIError(
                "SERVICE_UNAUTHORIZED",
                "A valid service token is required for this operation.",
                status_code=401,
            )
    elif selecting_non_default:
        raise RuntimeTenantUnauthorized()
    return tenant_id


async def get_runtime(
    request: Request,
    tenant_header: str | None = Header(None, alias="X-DeepSearcher-Tenant"),
    service_token: str | None = Header(None, alias="X-DeepSearcher-Service-Token"),
) -> AsyncIterator[RuntimeComponents]:
    registry = getattr(request.app.state, "runtime_registry", None)
    if registry is not None and getattr(request.app.state, "runtime", None) is not None:
        tenant_id = _resolve_tenant(request, tenant_header, service_token)
        lease = await registry.acquire(tenant_id)
        request.state.runtime_context = lease.context
        try:
            yield lease.runtime
        finally:
            await lease.release()
        return
    error = getattr(request.app.state, "runtime_error", None)
    if isinstance(error, RuntimeInitializationError):
        raise error
    raise RuntimeInitializationError("runtime")


def get_runtime_context(request: Request) -> RuntimeRequestContext:
    context = getattr(request.state, "runtime_context", None)
    if isinstance(context, RuntimeRequestContext):
        return context
    raise RuntimeInitializationError("runtime")


def _require_admin(request: Request, provided_token: str | None) -> None:
    expected_token = request.app.state.admin_token
    if not expected_token:
        raise RuntimeTenantUnauthorized()
    if provided_token is None or not hmac.compare_digest(provided_token, expected_token):
        raise RuntimeTenantUnauthorized()


def _authorize_collection(request: Request, collection_name: str) -> str:
    return get_runtime_context(request).require_collection(collection_name)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.safe_message,
        retryable=exc.retryable,
        headers=exc.headers,
    )


async def vector_db_error_handler(request: Request, exc: VectorDBError) -> JSONResponse:
    if isinstance(exc, VectorDBUnavailable):
        status_code = 503
    elif isinstance(
        exc,
        (
            CollectionIngestionProfileMismatch,
            CollectionManifestInvalid,
            CollectionManifestMissing,
            CollectionManifestUnsupported,
            CollectionNotFound,
            EmbeddingProfileMismatch,
            UnsafeCollectionReplacement,
            VectorDimensionMismatch,
        ),
    ):
        status_code = 409
    else:
        status_code = 502
    return _error_response(
        request,
        status_code=status_code,
        code=exc.code,
        message=exc.safe_message,
        retryable=exc.retryable,
    )


async def runtime_initialization_error_handler(
    request: Request, exc: RuntimeInitializationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=503,
        code=exc.code,
        message=exc.safe_message,
        retryable=exc.retryable,
        extra={"component": _safe_runtime_component(exc)},
    )


async def runtime_control_error_handler(request: Request, exc: RuntimeControlError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.safe_message,
        retryable=exc.retryable,
    )


async def request_validation_error_handler(
    request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=422,
        code="INVALID_REQUEST",
        message="The request payload is invalid.",
        retryable=False,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    public_errors = {
        404: ("NOT_FOUND", "The requested resource was not found."),
        405: ("METHOD_NOT_ALLOWED", "The request method is not allowed."),
    }
    code, message = public_errors.get(
        exc.status_code,
        ("HTTP_ERROR", "The request could not be completed."),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        retryable=False,
        headers=exc.headers,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "api_unhandled_error request_id=%s method=%s route=%s exception_type=%s",
        _request_id(request),
        request.method,
        _safe_request_route(request),
        type(exc).__name__,
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="The server could not complete the request.",
        retryable=False,
    )


def require_service_access(
    request: Request,
    service_token: str | None = Header(None, alias="X-DeepSearcher-Service-Token"),
) -> None:
    expected_token = request.app.state.service_token
    if (
        not expected_token
        or service_token is None
        or not hmac.compare_digest(service_token, expected_token)
    ):
        raise APIError(
            "SERVICE_UNAUTHORIZED",
            "A valid service token is required for this operation.",
            status_code=401,
        )


class ProviderConfigRequest(BaseModel):
    """Provider configuration update request."""

    feature: str
    provider: str
    config: Dict


class TenantRuntimePublishRequest(ProviderConfigRequest):
    expected_version: int | None = Field(default=None, ge=1)
    allowed_collections: List[str] = Field(min_length=1, max_length=512)
    model_policy: str | None = Field(default=None, min_length=1, max_length=160)


class RuntimeRollbackRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class ConversationHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1200)
    grounded: bool = False


class QueryStreamRequest(BaseModel):
    original_query: str = Field(min_length=1, max_length=4000)
    max_iter: int = Field(default=2, ge=1, le=10)
    collection_names: List[str] | None = Field(default=None, max_length=512)
    use_web_search: bool = False
    conversation_history: List[ConversationHistoryMessage] = Field(
        default_factory=list,
        max_length=12,
    )


class QueryRequest(QueryStreamRequest):
    include_trace: bool = False


def _contextualize_request(
    payload: QueryStreamRequest,
    runtime: RuntimeComponents,
    collector: TraceCollector | None = None,
) -> ContextualQuery:
    if not payload.conversation_history:
        return ContextualQuery(
            query=payload.original_query.strip(),
            depends_on_history=False,
            history_turn_count=0,
            fallback_used=False,
            reason="no_history",
            retrieval_queries=(payload.original_query.strip(),),
            dependency_status="standalone",
        )
    context = contextualize_query(
        runtime.llm,
        payload.original_query,
        [item.model_dump() for item in payload.conversation_history],
        trace_collector=collector,
    )
    if collector is not None and context.history_turn_count:
        collector.record_contextualization(
            depends_on_history=context.depends_on_history,
            history_turn_count=context.history_turn_count,
            fallback_used=context.fallback_used,
            reason=context.reason,
            token_usage=context.token_usage,
            dependency_status=context.dependency_status,
            retrieval_query_count=len(context.retrieval_queries),
        )
    return context


class CollectionRebuildRequest(BaseModel):
    paths: List[str] = Field(min_length=1, max_length=512)
    collection_description: str | None = Field(default=None, max_length=2048)
    chunk_size: int = Field(default=1500, ge=1, le=100_000)
    chunk_overlap: int = Field(default=100, ge=0, le=99_999)
    batch_size: int = Field(default=256, ge=1, le=4096)
    document_metadata: List[dict] | None = None


async def set_provider_config(
    payload: ProviderConfigRequest,
    request: Request,
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Publish a new runtime version for the authenticated request tenant."""
    del runtime
    context = get_runtime_context(request)
    registry: RuntimeRegistry = request.app.state.runtime_registry
    binding = await registry.publish(
        tenant_id=context.tenant_id,
        expected_version=context.runtime_version,
        patch=RuntimePatch(
            feature=payload.feature,
            provider=payload.provider,
            config=payload.config,
        ),
    )

    if context.tenant_id == request.app.state.default_tenant:
        active_lease = await registry.acquire(context.tenant_id)
        request.app.state.runtime = active_lease.runtime
        await active_lease.release()
    return {
        "message": "Provider config set successfully",
        "provider": payload.provider,
        "tenant_id": context.tenant_id,
        "runtime_version": binding.active_version,
    }


async def publish_tenant_runtime(
    tenant_id: str,
    payload: TenantRuntimePublishRequest,
    request: Request,
    admin_token: str | None = Header(None, alias="X-DeepSearcher-Admin-Token"),
):
    """Build, validate and atomically publish one tenant's next runtime version."""
    _require_admin(request, admin_token)
    registry: RuntimeRegistry = request.app.state.runtime_registry
    binding = await registry.publish(
        tenant_id=tenant_id,
        expected_version=payload.expected_version,
        patch=RuntimePatch(
            feature=payload.feature,
            provider=payload.provider,
            config=payload.config,
        ),
        allowed_collections=payload.allowed_collections,
        model_policy=payload.model_policy,
    )
    return {
        "tenant_id": binding.tenant_id,
        "runtime_version": binding.active_version,
        "previous_version": binding.previous_version,
        "binding_revision": binding.revision,
        "model_policy": binding.model_policy,
        "allowed_collections": list(binding.allowed_collections),
    }


async def rollback_tenant_runtime(
    tenant_id: str,
    payload: RuntimeRollbackRequest,
    request: Request,
    admin_token: str | None = Header(None, alias="X-DeepSearcher-Admin-Token"),
):
    """Atomically restore the previous published version for one tenant."""
    _require_admin(request, admin_token)
    registry: RuntimeRegistry = request.app.state.runtime_registry
    binding = await registry.rollback(
        tenant_id=tenant_id,
        expected_version=payload.expected_version,
    )
    if tenant_id == request.app.state.default_tenant:
        active_lease = await registry.acquire(tenant_id)
        request.app.state.runtime = active_lease.runtime
        await active_lease.release()
    return {
        "tenant_id": binding.tenant_id,
        "runtime_version": binding.active_version,
        "previous_version": binding.previous_version,
        "binding_revision": binding.revision,
        "model_policy": binding.model_policy,
        "allowed_collections": list(binding.allowed_collections),
    }


def runtime_context(
    request: Request,
    _service_access: None = Depends(require_service_access),
    _runtime: RuntimeComponents = Depends(get_runtime),
):
    """Return the safe request-scoped tenant and runtime policy."""
    context = get_runtime_context(request)
    return {
        "tenant_id": context.tenant_id,
        "runtime_version": context.runtime_version,
        "binding_revision": context.binding_revision,
        "model_policy": context.model_policy,
        "allowed_collections": list(context.allowed_collections),
        "worker_pid": os.getpid(),
    }


def load_files(
    request: Request,
    paths: Union[str, List[str]] = Body(
        ...,
        description="A list of file paths to be loaded.",
        examples=["/path/to/file1", "/path/to/file2", "/path/to/dir1"],
    ),
    collection_name: str = Body(
        None,
        description="Optional name for the collection.",
        examples=["my_collection"],
    ),
    collection_description: str = Body(
        None,
        description="Optional description for the collection.",
        examples=["This is a test collection."],
    ),
    batch_size: int = Body(
        None,
        description="Optional batch size for the collection.",
        examples=[256],
    ),
    replace_document_id: str = Body(
        None,
        description="Optional SHA-256 identifier used to make a document retry idempotent.",
    ),
    document_metadata: dict | None = Body(
        None,
        description="Optional validated business-time metadata for exactly one file.",
    ),
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Load local files using the application-owned runtime components."""
    effective_collection = collection_name or getattr(
        runtime.vector_db, "default_collection", "deepsearcher"
    )
    _authorize_collection(request, effective_collection)
    if replace_document_id is not None and not re.fullmatch(r"[0-9a-f]{64}", replace_document_id):
        raise APIError(
            "INVALID_DOCUMENT_ID",
            "The document identifier is invalid.",
            status_code=400,
        )
    safe_document_metadata = sanitize_document_governance_metadata(document_metadata)
    if safe_document_metadata is None:
        raise APIError(
            "INVALID_DOCUMENT_GOVERNANCE_METADATA",
            "The document governance metadata is invalid.",
            status_code=400,
        )
    if safe_document_metadata and not isinstance(paths, str):
        raise APIError(
            "AMBIGUOUS_DOCUMENT_GOVERNANCE_METADATA",
            "Document governance metadata requires exactly one file path.",
            status_code=400,
        )
    try:
        if replace_document_id is not None:
            runtime.vector_db.delete_by_document_id(
                collection=effective_collection,
                document_id=replace_document_id,
            )
        result = load_from_local_files(
            paths_or_directory=paths,
            collection_name=collection_name,
            collection_description=collection_description,
            batch_size=batch_size if batch_size is not None else 256,
            vector_db_instance=runtime.vector_db,
            embedding_model_instance=runtime.embedding_model,
            file_loader_instance=runtime.file_loader,
            document_metadata=safe_document_metadata,
        )
        return {
            "message": "Files loaded successfully.",
            "collection": result,
        }
    except VectorDBError:
        raise
    except Exception as exc:
        raise APIError(
            "FILE_LOADING_FAILED",
            "File loading failed.",
            status_code=500,
        ) from exc


def delete_document_vectors(
    request: Request,
    collection_name: str,
    document_id: str,
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Delete all vector chunks associated with one uploaded document."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", collection_name):
        raise APIError(
            "INVALID_COLLECTION_NAME",
            "The collection name is invalid.",
            status_code=400,
        )
    if not re.fullmatch(r"[0-9a-f]{64}", document_id):
        raise APIError(
            "INVALID_DOCUMENT_ID",
            "The document identifier is invalid.",
            status_code=400,
        )
    _authorize_collection(request, collection_name)
    try:
        result = runtime.vector_db.delete_by_document_id(
            collection=collection_name,
            document_id=document_id,
        )
        return {
            "message": "Document vectors deleted successfully.",
            "delete_count": int((result or {}).get("delete_count", 0)),
            "manifest": (result or {}).get("manifest"),
        }
    except NotImplementedError as exc:
        raise APIError(
            "DOCUMENT_DELETE_NOT_SUPPORTED",
            "The configured vector database does not support document deletion.",
            status_code=501,
        ) from exc
    except VectorDBError:
        raise
    except Exception as exc:
        raise APIError(
            "DOCUMENT_VECTOR_DELETE_FAILED",
            "Document vector deletion failed.",
            status_code=500,
        ) from exc


def get_vector_collection_manifest(
    request: Request,
    collection_name: str,
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Return the persisted index contract and its runtime compatibility."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", collection_name):
        raise APIError(
            "INVALID_COLLECTION_NAME",
            "The collection name is invalid.",
            status_code=400,
        )
    _authorize_collection(request, collection_name)
    manifest = runtime.vector_db.get_collection_manifest(collection_name)
    if manifest is None:
        raise CollectionManifestMissing(
            operation="read_manifest",
            collection=collection_name,
        )
    runtime_embedding = EmbeddingProfile.from_embedding(runtime.embedding_model)
    mismatches = manifest.embedding_mismatches(runtime_embedding)
    return {
        "collection_name": collection_name,
        "status": "compatible" if not mismatches else "incompatible",
        "compatible": not mismatches,
        "mismatches": mismatches,
        "manifest": manifest.to_dict(),
        "runtime_embedding": runtime_embedding.to_dict(),
    }


def rebuild_vector_collection(
    collection_name: str,
    payload: CollectionRebuildRequest,
    request: Request,
    confirmation: str | None = Header(
        None,
        alias="X-Confirm-Collection",
    ),
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Re-embed a complete product collection before atomically activating it."""
    if PRODUCT_COLLECTION_PATTERN.fullmatch(collection_name) is None:
        raise APIError(
            "COLLECTION_REBUILD_FORBIDDEN",
            "Only application-owned knowledge-base collections can be rebuilt.",
            status_code=403,
        )
    _authorize_collection(request, collection_name)
    if confirmation is None or not hmac.compare_digest(confirmation, collection_name):
        raise APIError(
            "COLLECTION_CONFIRMATION_REQUIRED",
            "Collection rebuild requires an exact confirmation header.",
            status_code=409,
        )
    if payload.chunk_overlap >= payload.chunk_size:
        raise APIError(
            "INVALID_CHUNK_SETTINGS",
            "Chunk overlap must be smaller than chunk size.",
            status_code=400,
        )
    safe_document_metadata = None
    if payload.document_metadata is not None:
        if len(payload.document_metadata) != len(payload.paths):
            raise APIError(
                "DOCUMENT_GOVERNANCE_METADATA_COUNT_MISMATCH",
                "Document metadata count must match the path count.",
                status_code=400,
            )
        safe_document_metadata = []
        for item in payload.document_metadata:
            sanitized = sanitize_document_governance_metadata(item)
            if sanitized is None:
                raise APIError(
                    "INVALID_DOCUMENT_GOVERNANCE_METADATA",
                    "The document governance metadata is invalid.",
                    status_code=400,
                )
            safe_document_metadata.append(sanitized)
    logger.warning(
        "collection_rebuild_requested collection=%s documents=%s",
        collection_name,
        len(payload.paths),
    )
    result = load_from_local_files(
        paths_or_directory=payload.paths,
        collection_name=collection_name,
        collection_description=payload.collection_description,
        force_new_collection=True,
        chunk_size=payload.chunk_size,
        chunk_overlap=payload.chunk_overlap,
        batch_size=payload.batch_size,
        vector_db_instance=runtime.vector_db,
        embedding_model_instance=runtime.embedding_model,
        file_loader_instance=runtime.file_loader,
        document_metadata=safe_document_metadata,
    )
    logger.warning(
        "collection_rebuild_completed collection=%s data_version=%s previous=%s",
        collection_name,
        result["manifest"]["data_version"],
        result.get("previous_collection"),
    )
    return {
        "message": "Collection rebuilt and activated successfully.",
        "collection": result,
    }


def delete_vector_collection(
    request: Request,
    collection_name: str,
    confirmation: str | None = Header(
        None,
        alias="X-Confirm-Collection",
    ),
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Delete a complete knowledge-base collection."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", collection_name):
        raise APIError(
            "INVALID_COLLECTION_NAME",
            "The collection name is invalid.",
            status_code=400,
        )
    if PRODUCT_COLLECTION_PATTERN.fullmatch(collection_name) is None:
        raise APIError(
            "COLLECTION_DELETE_FORBIDDEN",
            "Only application-owned knowledge-base collections can be deleted.",
            status_code=403,
        )
    _authorize_collection(request, collection_name)
    if confirmation is None or not hmac.compare_digest(confirmation, collection_name):
        raise APIError(
            "COLLECTION_CONFIRMATION_REQUIRED",
            "Collection deletion requires an exact confirmation header.",
            status_code=409,
        )
    try:
        logger.warning("collection_delete_requested collection=%s", collection_name)
        result = runtime.vector_db.delete_collection(collection=collection_name)
        logger.warning(
            "collection_delete_completed collection=%s deleted=%s",
            collection_name,
            bool((result or {}).get("deleted", False)),
        )
        return {
            "message": "Collection deleted successfully.",
            "deleted": bool((result or {}).get("deleted", False)),
        }
    except NotImplementedError as exc:
        raise APIError(
            "COLLECTION_DELETE_NOT_SUPPORTED",
            "The configured vector database does not support collection deletion.",
            status_code=501,
        ) from exc
    except VectorDBError:
        raise
    except Exception as exc:
        raise APIError(
            "COLLECTION_DELETE_FAILED",
            "Vector collection deletion failed.",
            status_code=500,
        ) from exc


def load_website(
    request: Request,
    urls: Union[str, List[str]] = Body(
        ...,
        description="A list of URLs of websites to be loaded.",
        examples=["https://milvus.io/docs/overview.md"],
    ),
    collection_name: str = Body(
        None,
        description="Optional name for the collection.",
        examples=["my_collection"],
    ),
    collection_description: str = Body(
        None,
        description="Optional description for the collection.",
        examples=["This is a test collection."],
    ),
    batch_size: int = Body(
        None,
        description="Optional batch size for the collection.",
        examples=[256],
    ),
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Load website content using the application-owned runtime components."""
    effective_collection = collection_name or getattr(
        runtime.vector_db, "default_collection", "deepsearcher"
    )
    _authorize_collection(request, effective_collection)
    try:
        result = load_from_website(
            urls=urls,
            collection_name=collection_name,
            collection_description=collection_description,
            batch_size=batch_size if batch_size is not None else 256,
            vector_db_instance=runtime.vector_db,
            embedding_model_instance=runtime.embedding_model,
            web_crawler_instance=runtime.web_crawler,
        )
        return {
            "message": "Website loaded successfully.",
            "collection": result,
        }
    except VectorDBError:
        raise
    except Exception as exc:
        raise APIError(
            "WEBSITE_LOADING_FAILED",
            "Website loading failed.",
            status_code=500,
        ) from exc


def perform_query(
    payload: QueryRequest,
    request: Request,
    _service_access: None = Depends(require_service_access),
    runtime: RuntimeComponents = Depends(get_runtime),
):
    """Perform a query from a JSON body so question text never enters the URL."""
    original_query = payload.original_query.strip()
    if not original_query:
        raise APIError(
            "QUERY_EMPTY",
            "The query must not be empty.",
            status_code=400,
        )
    requested_collections = payload.collection_names
    context = get_runtime_context(request)
    explicit_collections = context.collections_for_query(requested_collections)
    provenance_session = TrustProvenanceSession(
        runtime,
        context=context,
        collection_names=explicit_collections,
        execution_scope="online",
    )
    provenance = provenance_session.snapshot()
    temporal_timezone = temporal_timezone_from_query_settings(
        getattr(runtime.config, "query_settings", {})
    )
    try:
        kwargs = {
            "searcher": runtime.default_searcher,
            "use_web_search": payload.use_web_search,
            "entailment_checker": getattr(runtime, "entailment_checker", None),
            "temporal_timezone": temporal_timezone,
            "token_control": getattr(runtime.config, "query_settings", {}).get("token_control", {}),
        }
        if explicit_collections is not None:
            kwargs["collection_names"] = explicit_collections

        if payload.include_trace:
            collector = TraceCollector(
                original_query,
                request_id=_request_id(request),
                entailment_checker=getattr(runtime, "entailment_checker", None),
                provenance=provenance,
                provenance_resolver=provenance_session.bind_collections,
                evidence_provenance_resolver=provenance_session.bind_evidence,
                temporal_timezone=temporal_timezone,
                token_control=getattr(runtime.config, "query_settings", {}).get(
                    "token_control", {}
                ),
            )
            contextual = _contextualize_request(payload, runtime, collector)
            result_text, _, consume_token, trace = query_with_trace(
                contextual.query,
                payload.max_iter,
                trace_collector=collector,
                initial_tokens=contextual.token_usage,
                retrieval_queries=contextual.retrieval_queries,
                **kwargs,
            )
            return {
                "result": result_text,
                "consume_token": consume_token,
                "trace": trace,
            }

        contextual = _contextualize_request(payload, runtime)
        result_text, _, consume_token = query(
            contextual.query,
            payload.max_iter,
            initial_tokens=contextual.token_usage,
            retrieval_queries=contextual.retrieval_queries,
            enforce_trust=True,
            provenance=provenance,
            provenance_resolver=provenance_session.bind_collections,
            evidence_provenance_resolver=provenance_session.bind_evidence,
            **kwargs,
        )
        return {"result": result_text, "consume_token": consume_token}
    except VectorDBError:
        raise
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise APIError(
            "QUERY_TIMEOUT",
            "The query exceeded its execution time limit.",
            status_code=504,
            retryable=True,
        ) from exc
    except Exception as exc:
        logger.error(
            "query_failed request_id=%s exception_type=%s",
            _request_id(request),
            type(exc).__name__,
        )
        raise APIError(
            "QUERY_FAILED",
            "The query could not be completed.",
            status_code=500,
            retryable=True,
        ) from exc


def _sse_message(envelope: dict) -> str:
    event_name = str(envelope.get("event") or "message")
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"


def _safe_query_stream_error(exc: Exception) -> dict:
    if isinstance(exc, VectorDBError):
        return {
            "code": exc.code,
            "message": exc.safe_message,
            "retryable": exc.retryable,
        }
    if isinstance(exc, RuntimeInitializationError):
        return {
            "code": exc.code,
            "message": exc.safe_message,
            "retryable": exc.retryable,
        }
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return {
            "code": "QUERY_TIMEOUT",
            "message": "The query exceeded its execution time limit.",
            "retryable": True,
        }
    return {
        "code": "QUERY_FAILED",
        "message": "The query could not be completed.",
        "retryable": True,
    }


def _track_stream_cleanup(application: FastAPI, cleanup_coro) -> None:
    task = asyncio.create_task(cleanup_coro)
    application.state.stream_cleanup_tasks.add(task)
    task.add_done_callback(application.state.stream_cleanup_tasks.discard)


async def perform_query_stream(
    payload: QueryStreamRequest,
    request: Request,
    tenant_header: str | None = Header(None, alias="X-DeepSearcher-Tenant"),
    service_token: str | None = Header(None, alias="X-DeepSearcher-Service-Token"),
    _service_access: None = Depends(require_service_access),
):
    """Stream explicit, redacted query stages without exposing model reasoning."""
    if not payload.original_query.strip():
        raise APIError(
            "QUERY_EMPTY",
            "The query must not be empty.",
            status_code=400,
        )
    registry: RuntimeRegistry | None = getattr(request.app.state, "runtime_registry", None)
    if registry is None or getattr(request.app.state, "runtime", None) is None:
        error = getattr(request.app.state, "runtime_error", None)
        if isinstance(error, RuntimeInitializationError):
            raise error
        raise RuntimeInitializationError("runtime")

    tenant_id = _resolve_tenant(request, tenant_header, service_token)
    lease = await registry.acquire(tenant_id)
    context = lease.context
    try:
        requested = payload.collection_names if payload.collection_names is not None else None
        collection_names = context.collections_for_query(requested)
    except Exception:
        await lease.release()
        raise

    async def stream_events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue()
        cancellation_event = threading.Event()

        def publish(envelope: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, envelope)

        provenance_session = TrustProvenanceSession(
            lease.runtime,
            context=context,
            collection_names=collection_names,
            execution_scope="stream",
        )
        collector = TraceCollector(
            payload.original_query,
            event_callback=publish,
            cancellation_event=cancellation_event,
            request_id=_request_id(request),
            entailment_checker=getattr(lease.runtime, "entailment_checker", None),
            provenance=provenance_session.snapshot(),
            provenance_resolver=provenance_session.bind_collections,
            evidence_provenance_resolver=provenance_session.bind_evidence,
            temporal_timezone=temporal_timezone_from_query_settings(
                getattr(lease.runtime.config, "query_settings", {})
            ),
            token_control=getattr(lease.runtime.config, "query_settings", {}).get(
                "token_control", {}
            ),
        )

        def run_query() -> None:
            try:
                contextual = _contextualize_request(payload, lease.runtime)
                collector.emit_started()
                if contextual.history_turn_count:
                    collector.record_contextualization(
                        depends_on_history=contextual.depends_on_history,
                        history_turn_count=contextual.history_turn_count,
                        fallback_used=contextual.fallback_used,
                        reason=contextual.reason,
                        token_usage=contextual.token_usage,
                        dependency_status=contextual.dependency_status,
                        retrieval_query_count=len(contextual.retrieval_queries),
                    )
                result_text, _, consume_token, trace = query_with_trace(
                    contextual.query,
                    payload.max_iter,
                    collection_names=collection_names,
                    use_web_search=payload.use_web_search,
                    searcher=lease.runtime.default_searcher,
                    trace_collector=collector,
                    initial_tokens=contextual.token_usage,
                    retrieval_queries=contextual.retrieval_queries,
                )
            except QueryCancelled:
                collector.emit_event(
                    "cancelled",
                    {
                        "code": QueryCancelled.code,
                        "message": QueryCancelled.safe_message,
                        "retryable": True,
                    },
                    check_cancelled=False,
                )
            except Exception as exc:
                logger.error(
                    "query_stream_failed request_id=%s exception_type=%s",
                    collector.request_id,
                    type(exc).__name__,
                )
                collector.emit_event(
                    "error",
                    _safe_query_stream_error(exc),
                    check_cancelled=False,
                )
            else:
                collector.emit_event(
                    "completed",
                    {
                        "result": result_text,
                        "consume_token": int(consume_token or 0),
                        "trace": trace,
                    },
                    check_cancelled=False,
                )

        query_task = asyncio.create_task(asyncio.to_thread(run_query))
        released = False

        async def finish_and_release() -> None:
            nonlocal released
            try:
                await query_task
            finally:
                if not released:
                    released = True
                    await lease.release()

        terminal_events = {"completed", "error", "cancelled"}
        last_heartbeat = loop.time()
        try:
            while True:
                if await request.is_disconnected():
                    cancellation_event.set()
                    break
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if query_task.done() and queue.empty():
                        break
                    if loop.time() - last_heartbeat >= 10:
                        last_heartbeat = loop.time()
                        yield ": keep-alive\n\n"
                    continue
                yield _sse_message(envelope)
                if envelope.get("event") in terminal_events:
                    break
        finally:
            cancellation_event.set()
            if query_task.done():
                await finish_and_release()
            else:
                _track_stream_cleanup(request.app, finish_and_release())

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Trace-Retention": "transient",
        },
    )


def liveness(request: Request) -> dict:
    return {
        "version": 1,
        "service": "deepsearcher-api",
        "status": "alive",
        "request_id": _request_id(request),
    }


def _health_response(
    request: Request,
    *,
    mode: str,
    status: str,
    checks: dict,
) -> JSONResponse:
    return JSONResponse(
        status_code=503 if status == "not_ready" else 200,
        content={
            "version": 1,
            "service": "deepsearcher-api",
            "mode": mode,
            "status": status,
            "request_id": _request_id(request),
            "checks": checks,
        },
    )


async def _inspect_runtime_health(request: Request, *, deep: bool) -> JSONResponse:
    registry = getattr(request.app.state, "runtime_registry", None)
    runtime = getattr(request.app.state, "runtime", None)
    mode = "diagnostic" if deep else "readiness"
    if registry is None or runtime is None:
        error = getattr(request.app.state, "runtime_error", None)
        component = (
            _safe_runtime_component(error)
            if isinstance(error, RuntimeInitializationError)
            else "runtime"
        )
        checks = blocked_checks("BLOCKED_BY_RUNTIME")
        checks["runtime"] = failed_check(
            RuntimeInitializationError.code,
            retryable=RuntimeInitializationError.retryable,
        )
        checks["runtime"]["component"] = component
        return _health_response(
            request,
            mode=mode,
            status="not_ready",
            checks=checks,
        )

    lease = None
    try:
        lease = await registry.acquire(request.app.state.default_tenant)
        result = await request.app.state.health_monitor.inspect(lease.runtime, deep=deep)
    except Exception:
        logger.warning(
            "runtime_health_probe_failed request_id=%s mode=%s",
            _request_id(request),
            mode,
            exc_info=False,
        )
        checks = blocked_checks("BLOCKED_BY_RUNTIME")
        checks["runtime"] = failed_check("RUNTIME_UNAVAILABLE", retryable=True)
        return _health_response(
            request,
            mode=mode,
            status="not_ready",
            checks=checks,
        )
    finally:
        if lease is not None:
            await lease.release()

    return _health_response(
        request,
        mode=mode,
        status=result["status"],
        checks=result["checks"],
    )


async def runtime_health(request: Request) -> JSONResponse:
    return await _inspect_runtime_health(request, deep=False)


async def runtime_diagnostics(
    request: Request,
    _access: None = Depends(require_service_access),
) -> JSONResponse:
    return await _inspect_runtime_health(request, deep=True)


def create_app(
    *,
    runtime_factory: RuntimeFactory = build_runtime,
    config_factory: ConfigFactory = Configuration,
    runtime_store_factory: RuntimeStoreFactory = RuntimeControlStore,
    default_tenant: str | None = None,
    service_token: str | None = None,
    admin_token: str | None = None,
    cors_origins: Sequence[str] | str | None = None,
    query_rate_limit: int | None = None,
    query_rate_window_seconds: int | None = None,
) -> FastAPI:
    """Create an API app without accessing model or vector services at import time."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.runtime = None
        application.state.runtime_registry = None
        application.state.runtime_error = None
        application.state.stream_cleanup_tasks = set()
        try:
            try:
                config = application.state.config_factory()
                store = application.state.runtime_store_factory()
            except Exception as exc:
                raise _runtime_error("configuration", exc) from exc
            try:
                registry = RuntimeRegistry(
                    base_config=config,
                    runtime_factory=application.state.runtime_factory,
                    store=store,
                    default_tenant=application.state.default_tenant,
                )
                application.state.runtime_registry = registry
                await registry.start()
                initial_lease = await registry.acquire(application.state.default_tenant)
                application.state.runtime = initial_lease.runtime
                await initial_lease.release()
            except RuntimeInitializationError:
                raise
            except Exception as exc:
                raise _runtime_error("runtime", exc) from exc
        except RuntimeInitializationError as exc:
            application.state.runtime_error = exc

        try:
            yield
        finally:
            cleanup_tasks = list(application.state.stream_cleanup_tasks)
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            registry = getattr(application.state, "runtime_registry", None)
            if registry is not None:
                await registry.shutdown()
            else:
                await close_runtime(application.state.runtime)

    application = FastAPI(lifespan=lifespan, redirect_slashes=False)
    application.state.runtime_factory = runtime_factory
    application.state.config_factory = config_factory
    application.state.runtime_store_factory = runtime_store_factory
    application.state.default_tenant = (
        (default_tenant or os.environ.get("DEEPSEARCHER_DEFAULT_TENANT") or DEFAULT_TENANT_ID)
        .strip()
        .lower()
    )
    application.state.service_token = service_token or os.environ.get("DEEPSEARCHER_SERVICE_TOKEN")
    application.state.admin_token = admin_token or os.environ.get("DEEPSEARCHER_ADMIN_TOKEN")
    application.state.query_rate_limiter = QueryRateLimiter(
        limit=(
            query_rate_limit
            if query_rate_limit is not None
            else _env_int(
                "DEEPSEARCHER_QUERY_RATE_LIMIT",
                DEFAULT_QUERY_RATE_LIMIT,
                minimum=0,
                maximum=100_000,
            )
        ),
        window_seconds=(
            query_rate_window_seconds
            if query_rate_window_seconds is not None
            else _env_int(
                "DEEPSEARCHER_QUERY_RATE_WINDOW_SECONDS",
                DEFAULT_QUERY_RATE_WINDOW_SECONDS,
                minimum=1,
                maximum=86_400,
            )
        ),
    )
    application.state.health_monitor = RuntimeHealthMonitor(
        timeout_seconds=_env_int(
            "DEEPSEARCHER_HEALTH_PROBE_TIMEOUT_SECONDS",
            8,
            minimum=1,
            maximum=60,
        ),
        cache_seconds=_env_int(
            "DEEPSEARCHER_HEALTH_PROVIDER_CACHE_SECONDS",
            300,
            minimum=0,
            maximum=86_400,
        ),
    )

    @application.middleware("http")
    async def api_request_context(request: Request, call_next):
        supplied_request_id = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid4().hex
        )
        if request.method == "POST" and request.url.path in QUERY_PATHS:
            tenant_key = request.headers.get("X-DeepSearcher-Tenant", "").strip().lower()
            expected_token = request.app.state.service_token
            provided_token = request.headers.get("X-DeepSearcher-Service-Token")
            authenticated_tenant = (
                re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tenant_key) is not None
                and expected_token
                and provided_token
                and hmac.compare_digest(provided_token, expected_token)
            )
            if not authenticated_tenant:
                client = request.client
                tenant_key = f"client:{client.host if client is not None else 'unknown'}"
            admitted, retry_after = await request.app.state.query_rate_limiter.admit(tenant_key)
            if not admitted:
                response = _error_response(
                    request,
                    status_code=429,
                    code="QUERY_RATE_LIMITED",
                    message="Too many query requests were submitted.",
                    retryable=True,
                    headers={"Retry-After": str(retry_after)},
                )
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    trusted_origins = _parse_cors_origins(cors_origins)
    if trusted_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=trusted_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "X-Request-ID",
                "X-DeepSearcher-Tenant",
                "X-DeepSearcher-Service-Token",
                "X-DeepSearcher-Admin-Token",
                "X-Confirm-Collection",
            ],
            expose_headers=["X-Request-ID", "Retry-After"],
        )

    application.add_exception_handler(APIError, api_error_handler)
    application.add_exception_handler(VectorDBError, vector_db_error_handler)
    application.add_exception_handler(RuntimeControlError, runtime_control_error_handler)
    application.add_exception_handler(
        RuntimeInitializationError, runtime_initialization_error_handler
    )
    application.add_exception_handler(RequestValidationError, request_validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(Exception, unhandled_error_handler)
    application.add_api_route("/health/live", liveness, methods=["GET"])
    application.add_api_route("/health/ready", runtime_health, methods=["GET"])
    application.add_api_route("/health", runtime_health, methods=["GET"])
    application.add_api_route(
        "/health/diagnostics",
        runtime_diagnostics,
        methods=["POST"],
    )
    application.add_api_route("/runtime/context", runtime_context, methods=["GET"])
    application.add_api_route(
        "/runtime/tenants/{tenant_id}/versions",
        publish_tenant_runtime,
        methods=["POST"],
    )
    application.add_api_route(
        "/runtime/tenants/{tenant_id}/rollback",
        rollback_tenant_runtime,
        methods=["POST"],
    )
    application.add_api_route("/set-provider-config/", set_provider_config, methods=["POST"])
    application.add_api_route("/load-files/", load_files, methods=["POST"])
    application.add_api_route(
        "/collections/{collection_name}/documents/{document_id}",
        delete_document_vectors,
        methods=["DELETE"],
    )
    application.add_api_route(
        "/collections/{collection_name}/manifest",
        get_vector_collection_manifest,
        methods=["GET"],
    )
    application.add_api_route(
        "/collections/{collection_name}/rebuild",
        rebuild_vector_collection,
        methods=["POST"],
    )
    application.add_api_route(
        "/collections/{collection_name}",
        delete_vector_collection,
        methods=["DELETE"],
    )
    application.add_api_route("/load-website/", load_website, methods=["POST"])
    application.add_api_route("/query", perform_query, methods=["POST"])
    application.add_api_route("/query/stream", perform_query_stream, methods=["POST"])
    return application


app = create_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FastAPI Server")
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        help="Trusted browser origin. Repeat for multiple origins.",
    )
    args = parser.parse_args()
    cli_app = create_app(cors_origins=args.cors_origin)
    uvicorn.run(cli_app, host="127.0.0.1", port=8000)
