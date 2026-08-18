from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from deepsearcher.loader.file_loader.mime_type import normalize_extension
from frontend.product.auth import require_admin
from frontend.product.backend import backend_request_headers
from frontend.product.db import SessionLocal, init_database, worker_is_ready
from frontend.product.errors import ProductError
from frontend.product.routes import router as product_router
from frontend.product.services.documents import inspect_document_pages, stage_upload

FRONTEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_ROOT.parent
DIST_DIR = FRONTEND_ROOT / "dist"
CONFIG_PATH = PROJECT_ROOT / "deepsearcher" / "config.yaml"
load_dotenv(PROJECT_ROOT / ".env")

BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")
COLLECTION_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HEALTH_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
HEALTH_COMPONENTS = ("runtime", "vector_db", "llm", "embedding")
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
DEFAULT_WORKSPACE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
BROWSER_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
HTML_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
    )
)
logger = logging.getLogger(__name__)


def _normalize_host(value: str) -> str | None:
    candidate = value.strip().lower().rstrip(".")
    if not candidate or "/" in candidate or "@" in candidate:
        return None
    try:
        hostname = urlsplit(f"//{candidate}").hostname
    except ValueError:
        return None
    return hostname.lower().rstrip(".") if hostname else None


def _configured_workspace_hosts() -> frozenset[str]:
    configured = os.environ.get("DEEPSEARCHER_WORKSPACE_ALLOWED_HOSTS", "")
    hosts = set(DEFAULT_WORKSPACE_HOSTS)
    for value in configured.split(","):
        if not value.strip():
            continue
        host = _normalize_host(value)
        if host is None:
            raise RuntimeError("DEEPSEARCHER_WORKSPACE_ALLOWED_HOSTS 包含无效主机名")
        hosts.add(host)
    return frozenset(hosts)


def _canonical_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return scheme, hostname, port or (443 if scheme == "https" else 80)


def _configured_public_origin() -> tuple[str, str, int] | None:
    value = os.environ.get("DEEPSEARCHER_WORKSPACE_PUBLIC_ORIGIN", "").strip()
    if not value:
        return None
    origin = _canonical_origin(value)
    if origin is None:
        raise RuntimeError("DEEPSEARCHER_WORKSPACE_PUBLIC_ORIGIN 必须是完整的 HTTP(S) origin")
    return origin


WORKSPACE_ALLOWED_HOSTS = _configured_workspace_hosts()
WORKSPACE_PUBLIC_ORIGIN = _configured_public_origin()


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        if str(scope.get("path") or "").startswith("/api/"):
            raise StarletteHTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or Path(path).suffix or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


app = FastAPI(title="DeepSearcher 用户工作台")
init_database()


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
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": _request_id(request),
                "retryable": retryable,
            }
        },
    )


def _request_host_is_allowed(request: Request) -> bool:
    try:
        hostname = request.url.hostname
    except ValueError:
        return False
    normalized = hostname.lower().rstrip(".") if hostname else ""
    if normalized in WORKSPACE_ALLOWED_HOSTS:
        return True
    client = request.client
    return normalized == "testserver" and client is not None and client.host == "testclient"


def _unsafe_request_is_same_origin(request: Request) -> bool:
    if request.method.upper() in SAFE_HTTP_METHODS:
        return True
    if request.headers.get("Sec-Fetch-Site", "").strip().lower() == "cross-site":
        return False
    supplied_origin = request.headers.get("Origin", "").strip()
    if not supplied_origin:
        return True
    origin = _canonical_origin(supplied_origin)
    target = _canonical_origin(f"{request.url.scheme}://{request.headers.get('Host', '')}")
    return origin is not None and (origin == target or origin == WORKSPACE_PUBLIC_ORIGIN)


def _apply_browser_security_headers(request: Request, response):
    response.headers["X-Request-ID"] = request.state.request_id
    for header, value in BROWSER_SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    content_type = response.headers.get("Content-Type", "").lower()
    if content_type.startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", HTML_CONTENT_SECURITY_POLICY)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "").strip()
    request.state.request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
    if not _request_host_is_allowed(request):
        response = _error_response(
            request,
            status_code=400,
            code="INVALID_HOST",
            message="请求目标主机不受信任。",
        )
    elif not _unsafe_request_is_same_origin(request):
        response = _error_response(
            request,
            status_code=403,
            code="CROSS_SITE_REQUEST_BLOCKED",
            message="已拒绝跨站写请求。",
        )
    else:
        response = await call_next(request)
    return _apply_browser_security_headers(request, response)


@app.exception_handler(ProductError)
async def product_error_handler(request: Request, exc: ProductError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, _exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        request,
        status_code=422,
        code="INVALID_REQUEST",
        message="请求内容格式不正确，请检查后重试。",
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mapping = {
        400: ("INVALID_REQUEST", "请求内容不正确。"),
        404: ("NOT_FOUND", "没有找到请求的资源。"),
        405: ("METHOD_NOT_ALLOWED", "当前请求方式不受支持。"),
        502: ("UPSTREAM_ERROR", "问答服务返回异常，请稍后重试。"),
        503: ("SERVICE_UNAVAILABLE", "问答服务暂时不可用，请稍后重试。"),
    }
    code, message = mapping.get(
        exc.status_code,
        ("HTTP_ERROR", "请求暂时无法完成。"),
    )
    return _error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        retryable=exc.status_code in {502, 503, 504},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    route = request.scope.get("route")
    route_path = getattr(route, "path", "unmatched")
    logger.error(
        "workspace_api_unhandled_error request_id=%s method=%s route=%s exception_type=%s",
        _request_id(request),
        request.method,
        route_path,
        type(exc).__name__,
    )
    return _error_response(
        request,
        status_code=500,
        code="INTERNAL_ERROR",
        message="服务暂时无法完成请求。",
    )


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    max_iter: int = Field(default=3, ge=1, le=10)
    collection_name: str = Field(default="deepsearcher", min_length=1, max_length=64)
    use_web_search: bool = False


def map_query_response(payload: dict, latency_ms: int) -> dict:
    response = {
        "result": payload.get("result", ""),
        "consume_token": payload.get("consume_token"),
        "latency_ms": latency_ms,
    }
    if "trace" in payload:
        response["trace"] = payload["trace"]
    return response


def validate_collection_name(value: str) -> str:
    normalized = value.strip()
    if not COLLECTION_PATTERN.fullmatch(normalized):
        raise ValueError("Collection 名称只能包含字母、数字和下划线，且不能以数字开头")
    return normalized


def load_config_summary() -> dict[str, str]:
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        providers = config.get("provide_settings", {})
        llm = providers.get("llm", {})
        embedding = providers.get("embedding", {})
        vector_db = providers.get("vector_db", {})
        return {
            "llm_model": str(llm.get("config", {}).get("model") or "未配置"),
            "embedding_model": str(embedding.get("config", {}).get("model") or "未配置"),
            "collection": str(
                vector_db.get("config", {}).get("default_collection") or "deepsearcher"
            ),
        }
    except (OSError, yaml.YAMLError, AttributeError):
        return {
            "llm_model": "读取失败",
            "embedding_model": "读取失败",
            "collection": "deepsearcher",
        }


def _offline_backend_health(code: str) -> dict:
    unknown = {"status": "unknown", "code": "BACKEND_UNREACHABLE", "retryable": True}
    return {
        "version": 1,
        "service": "deepsearcher-api",
        "mode": "readiness",
        "status": "not_ready",
        "checks": {
            "runtime": {
                "status": "not_ready",
                "code": code,
                "retryable": True,
            },
            "vector_db": dict(unknown),
            "llm": dict(unknown),
            "embedding": dict(unknown),
        },
    }


def _valid_backend_health(
    response: httpx.Response,
    payload: object,
    *,
    request_id: str | None,
    deep: bool,
) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    expected_http_status = 503 if status == "not_ready" else 200
    if (
        payload.get("version") != 1
        or payload.get("service") != "deepsearcher-api"
        or payload.get("mode") != ("diagnostic" if deep else "readiness")
        or status not in {"ready", "degraded", "not_ready"}
        or response.status_code != expected_http_status
        or not isinstance(payload.get("checks"), dict)
        or (request_id and payload.get("request_id") != request_id)
    ):
        return False
    checks = payload["checks"]
    for component in HEALTH_COMPONENTS:
        check = checks.get(component)
        if not isinstance(check, dict) or check.get("status") not in {
            "ready",
            "not_ready",
            "unknown",
        }:
            return False
        code = check.get("code")
        if code is not None and (
            not isinstance(code, str) or HEALTH_CODE_PATTERN.fullmatch(code) is None
        ):
            return False
        if "retryable" in check and not isinstance(check["retryable"], bool):
            return False
    return True


async def probe_backend(request_id: str | None = None, *, deep: bool = False) -> dict:
    try:
        timeout = 12.0 if deep else 10.0
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            headers = backend_request_headers(request_id)
            if deep:
                response = await client.post(
                    f"{BACKEND_URL}/health/diagnostics",
                    headers=headers,
                )
            else:
                response = await client.get(
                    f"{BACKEND_URL}/health/ready",
                    headers=headers,
                )
            payload = response.json()
            if _valid_backend_health(
                response,
                payload,
                request_id=request_id,
                deep=deep,
            ):
                return payload
            return _offline_backend_health("BACKEND_INVALID_HEALTH_RESPONSE")
    except (httpx.HTTPError, ValueError):
        return _offline_backend_health("BACKEND_OFFLINE")


@app.get("/api/health/live")
async def workspace_liveness(request: Request) -> dict:
    return {
        "version": 1,
        "service": "deepsearcher-workspace",
        "status": "alive",
        "request_id": _request_id(request),
    }


def _service_check(checks: dict, component: str) -> dict:
    check = checks.get(component)
    if not isinstance(check, dict):
        return {
            "state": "unknown",
            "code": "HEALTH_CHECK_MISSING",
            "retryable": True,
        }
    result = {"state": str(check.get("status") or "unknown")}
    if check.get("code"):
        result["code"] = str(check["code"])
    if "retryable" in check:
        result["retryable"] = bool(check["retryable"])
    return result


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    return await _workspace_health(request, deep=False)


@app.post("/api/health/diagnostics")
async def health_diagnostics(
    request: Request,
    _admin=Depends(require_admin),
) -> JSONResponse:
    return await _workspace_health(request, deep=True)


async def _workspace_health(request: Request, *, deep: bool) -> JSONResponse:
    config = load_config_summary()
    backend = await probe_backend(_request_id(request), deep=deep)
    checks = backend["checks"]
    status = backend["status"]
    services = {
        "fastapi": _service_check(checks, "runtime"),
        "milvus": _service_check(checks, "vector_db"),
        "llm": _service_check(checks, "llm"),
        "embedding": _service_check(checks, "embedding"),
    }
    try:
        with SessionLocal() as session:
            ingest_worker_ready = worker_is_ready(
                session,
                worker_name="document-ingest",
            )
    except Exception:
        ingest_worker_ready = False
    services["ingest_worker"] = (
        {"state": "ready"}
        if ingest_worker_ready
        else {
            "state": "not_ready",
            "code": "INGEST_WORKER_UNAVAILABLE",
            "retryable": True,
        }
    )
    if status == "ready" and not ingest_worker_ready:
        status = "degraded"
    return JSONResponse(
        status_code=503 if status == "not_ready" else 200,
        content={
            "version": 1,
            "service": "deepsearcher-workspace",
            "status": status,
            "request_id": _request_id(request),
            "services": services,
            "config": config,
        },
    )


@app.post("/api/ingest")
async def ingest(
    request: Request,
    file: UploadFile = File(...),
    collection_name: str = Form(default="deepsearcher"),
    _admin=Depends(require_admin),
) -> dict:
    try:
        normalized_collection = validate_collection_name(collection_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    staged = None
    try:
        staged = await stage_upload(file)
        await inspect_document_pages(
            staged.path,
            normalize_extension(staged.display_name),
        )
        async with httpx.AsyncClient(
            timeout=180.0,
            trust_env=False,
            headers=backend_request_headers(_request_id(request)),
        ) as client:
            response = await client.post(
                f"{BACKEND_URL}/load-files/",
                json={"paths": str(staged.path), "collection_name": normalized_collection},
            )
        if not response.is_success:
            raise HTTPException(status_code=502, detail="DeepSearcher 入库失败，请查看本地服务日志")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="FastAPI 当前不可用，请先启动后端服务") from exc
    finally:
        await file.close()
        if staged is not None:
            try:
                staged.path.unlink(missing_ok=True)
            except OSError:
                pass

    return {"message": "入库请求成功", "collection_name": normalized_collection}


@app.post("/api/query")
async def query(
    payload: QueryRequest,
    request: Request,
    _admin=Depends(require_admin),
) -> dict:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题")
    try:
        collection_name = validate_collection_name(payload.collection_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    started = perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=180.0,
            trust_env=False,
            headers=backend_request_headers(_request_id(request)),
        ) as client:
            response = await client.post(
                f"{BACKEND_URL}/query",
                json={
                    "original_query": question,
                    "max_iter": payload.max_iter,
                    "include_trace": True,
                    "collection_names": [collection_name],
                    "use_web_search": payload.use_web_search,
                },
            )
        if not response.is_success:
            raise HTTPException(status_code=502, detail="DeepSearcher 查询失败，请查看本地服务日志")
        payload = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="FastAPI 当前不可用，请先启动后端服务") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="FastAPI 返回了无法解析的响应") from exc

    return map_query_response(
        payload,
        latency_ms=round((perf_counter() - started) * 1000),
    )


app.include_router(product_router)


if DIST_DIR.exists():
    app.mount("/", SPAStaticFiles(directory=DIST_DIR, html=True), name="frontend")
else:

    @app.get("/")
    async def frontend_not_built() -> dict[str, str]:
        return {"detail": "前端尚未构建，请先运行 npm run build"}
