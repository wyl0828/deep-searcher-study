import asyncio

import pytest
from fastapi.testclient import TestClient

from frontend.product.services.documents import StagedUpload
from frontend.server import (
    app,
    map_query_response,
    probe_backend,
    validate_collection_name,
)


def test_legacy_ingest_accepts_streamed_multipart_pdf(tmp_path, monkeypatch):
    staged_path = tmp_path / "staged.part"
    captured = {}

    async def stage(file):
        captured["filename"] = file.filename
        staged_path.write_bytes(b"%PDF-1.7\nstreamed")
        return StagedUpload(
            path=staged_path,
            display_name="paper.pdf",
            size_bytes=staged_path.stat().st_size,
            sha256="a" * 64,
        )

    async def inspect(path):
        assert path == staged_path
        return 1

    class Response:
        is_success = True

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, json):
            captured["url"] = url
            captured["json"] = json
            return Response()

    monkeypatch.setattr("frontend.server.stage_pdf_upload", stage)
    monkeypatch.setattr("frontend.server.inspect_pdf_pages", inspect)
    monkeypatch.setattr("frontend.server.httpx.AsyncClient", Client)

    response = TestClient(app).post(
        "/api/ingest",
        data={"collection_name": "deepsearcher"},
        files={"file": ("paper.pdf", b"streamed", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["collection_name"] == "deepsearcher"
    assert captured["filename"] == "paper.pdf"
    assert captured["json"] == {
        "paths": str(staged_path),
        "collection_name": "deepsearcher",
    }
    assert not staged_path.exists()


@pytest.mark.parametrize("name", ["deepsearcher", "project_docs_2026", "_scratch"])
def test_collection_name_accepts_safe_names(name):
    assert validate_collection_name(name) == name


@pytest.mark.parametrize("name", ["", "bad/name", "has space", "中文集合"])
def test_collection_name_rejects_unsafe_names(name):
    with pytest.raises(ValueError, match="Collection"):
        validate_collection_name(name)


def test_probe_backend_bypasses_windows_system_proxy(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "version": 1,
                "service": "deepsearcher-api",
                "mode": "readiness",
                "status": "ready",
                "request_id": "workspace-health-1",
                "checks": {
                    "runtime": {"status": "ready"},
                    "vector_db": {"status": "ready"},
                    "llm": {"status": "unknown"},
                    "embedding": {"status": "unknown"},
                },
            }

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return Response()

    monkeypatch.setattr("frontend.server.httpx.AsyncClient", Client)

    result = asyncio.run(probe_backend("workspace-health-1"))

    assert result["status"] == "ready"
    assert captured["trust_env"] is False
    assert captured["url"].endswith("/health/ready")
    assert captured["headers"]["X-Request-ID"] == "workspace-health-1"


@pytest.mark.parametrize(
    ("status_code", "request_id", "expected_code"),
    [
        (500, "workspace-health-invalid", "BACKEND_INVALID_HEALTH_RESPONSE"),
        (200, "different-request", "BACKEND_INVALID_HEALTH_RESPONSE"),
    ],
)
def test_probe_backend_rejects_http_and_request_id_contract_mismatches(
    monkeypatch,
    status_code,
    request_id,
    expected_code,
):
    class Response:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return {
                "version": 1,
                "service": "deepsearcher-api",
                "mode": "readiness",
                "status": "ready",
                "request_id": request_id,
                "checks": {
                    "runtime": {"status": "ready"},
                    "vector_db": {"status": "ready"},
                    "llm": {"status": "unknown"},
                    "embedding": {"status": "unknown"},
                },
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, **_kwargs):
            return Response()

    monkeypatch.setattr("frontend.server.httpx.AsyncClient", Client)

    result = asyncio.run(probe_backend("workspace-health-invalid"))

    assert result["status"] == "not_ready"
    assert result["checks"]["runtime"]["code"] == expected_code


def test_workspace_health_maps_core_readiness_checks(monkeypatch):
    async def backend_health(_request_id=None, *, deep=False):
        assert deep is False
        return {
            "status": "ready",
            "checks": {
                "runtime": {"status": "ready"},
                "vector_db": {"status": "ready"},
                "llm": {
                    "status": "unknown",
                    "code": "DEEP_PROBE_NOT_RUN",
                    "retryable": False,
                },
                "embedding": {
                    "status": "unknown",
                    "code": "DEEP_PROBE_NOT_RUN",
                    "retryable": False,
                },
            },
        }

    monkeypatch.setattr("frontend.server.probe_backend", backend_health)
    monkeypatch.setattr("frontend.server.worker_is_ready", lambda *_args, **_kwargs: True)

    response = TestClient(app).get(
        "/api/health",
        headers={"X-Request-ID": "workspace-health-2"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["services"]["milvus"] == {"state": "ready"}
    assert response.json()["services"]["ingest_worker"] == {"state": "ready"}
    assert response.json()["services"]["llm"] == {
        "state": "unknown",
        "code": "DEEP_PROBE_NOT_RUN",
        "retryable": False,
    }
    assert response.json()["request_id"] == "workspace-health-2"


def test_workspace_health_is_degraded_when_document_worker_is_missing(monkeypatch):
    async def backend_health(_request_id=None, *, deep=False):
        assert deep is False
        return {
            "status": "ready",
            "checks": {
                "runtime": {"status": "ready"},
                "vector_db": {"status": "ready"},
                "llm": {"status": "unknown"},
                "embedding": {"status": "unknown"},
            },
        }

    monkeypatch.setattr("frontend.server.probe_backend", backend_health)
    monkeypatch.setattr("frontend.server.worker_is_ready", lambda *_args, **_kwargs: False)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["services"]["ingest_worker"] == {
        "state": "not_ready",
        "code": "INGEST_WORKER_UNAVAILABLE",
        "retryable": True,
    }


def test_workspace_health_distinguishes_backend_offline(monkeypatch):
    async def backend_health(_request_id=None, *, deep=False):
        assert deep is False
        return {
            "status": "not_ready",
            "checks": {
                "runtime": {
                    "status": "not_ready",
                    "code": "BACKEND_OFFLINE",
                    "retryable": True,
                },
                "vector_db": {"status": "unknown"},
                "llm": {"status": "unknown"},
                "embedding": {"status": "unknown"},
            },
        }

    monkeypatch.setattr("frontend.server.probe_backend", backend_health)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["services"]["fastapi"] == {
        "state": "not_ready",
        "code": "BACKEND_OFFLINE",
        "retryable": True,
    }
    assert response.json()["services"]["milvus"] == {"state": "unknown"}


def test_workspace_liveness_does_not_depend_on_core_api():
    response = TestClient(app).get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "camera=()" in response.headers["permissions-policy"]


def test_workspace_rejects_untrusted_host_before_routing():
    response = TestClient(app).get(
        "/api/health/live",
        headers={"Host": "attacker.example", "X-Request-ID": "invalid-host-1"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "INVALID_HOST",
            "message": "请求目标主机不受信任。",
            "request_id": "invalid-host-1",
            "retryable": False,
        }
    }
    assert response.headers["x-request-id"] == "invalid-host-1"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "cross-site",
        },
    ],
)
def test_workspace_rejects_cross_site_write_requests(headers):
    response = TestClient(app).post(
        "/api/query",
        headers={**headers, "X-Request-ID": "cross-site-1"},
        json={"question": "test", "max_iter": 0},
    )

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "CROSS_SITE_REQUEST_BLOCKED",
        "message": "已拒绝跨站写请求。",
        "request_id": "cross-site-1",
        "retryable": False,
    }
    assert response.headers["cache-control"] == "no-store"


def test_workspace_allows_same_origin_and_non_browser_write_requests():
    client = TestClient(app)

    same_origin = client.post(
        "/api/query",
        headers={"Origin": "http://testserver"},
        json={"question": "test", "max_iter": 0},
    )
    non_browser = client.post(
        "/api/query",
        json={"question": "test", "max_iter": 0},
    )

    assert same_origin.status_code == 422
    assert same_origin.json()["error"]["code"] == "INVALID_REQUEST"
    assert non_browser.status_code == 422
    assert non_browser.json()["error"]["code"] == "INVALID_REQUEST"


def test_workspace_accepts_explicit_trusted_proxy_host_and_public_origin(monkeypatch):
    monkeypatch.setattr(
        "frontend.server.WORKSPACE_ALLOWED_HOSTS",
        frozenset({"workspace.example"}),
    )
    monkeypatch.setattr(
        "frontend.server.WORKSPACE_PUBLIC_ORIGIN",
        ("https", "workspace.example", 443),
    )

    response = TestClient(app).post(
        "/api/query",
        headers={
            "Host": "workspace.example",
            "Origin": "https://workspace.example",
            "Sec-Fetch-Site": "same-origin",
        },
        json={"question": "test", "max_iter": 0},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_workspace_diagnostics_surfaces_model_auth_degradation(monkeypatch):
    async def backend_health(_request_id=None, *, deep=False):
        assert deep is True
        return {
            "status": "degraded",
            "checks": {
                "runtime": {"status": "ready"},
                "vector_db": {"status": "ready"},
                "llm": {
                    "status": "not_ready",
                    "code": "PROVIDER_AUTH_FAILED",
                    "retryable": False,
                },
                "embedding": {"status": "ready"},
            },
        }

    monkeypatch.setattr("frontend.server.probe_backend", backend_health)

    response = TestClient(app).post("/api/health/diagnostics")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["services"]["fastapi"] == {"state": "ready"}
    assert response.json()["services"]["llm"] == {
        "state": "not_ready",
        "code": "PROVIDER_AUTH_FAILED",
        "retryable": False,
    }


def test_map_query_response_preserves_structured_trace():
    trace = {"version": 1, "iterations": [{"number": 1}]}

    assert map_query_response(
        {"result": "答案", "consume_token": 42, "trace": trace},
        latency_ms=1234,
    ) == {
        "result": "答案",
        "consume_token": 42,
        "trace": trace,
        "latency_ms": 1234,
    }


def test_workspace_query_forwards_explicit_collection_scope(monkeypatch):
    captured = {}

    class Response:
        is_success = True

        def json(self):
            return {"result": "答案", "consume_token": 2, "trace": {"version": 3}}

    class Client:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return Response()

    monkeypatch.setattr("frontend.server.httpx.AsyncClient", Client)

    response = TestClient(app).post(
        "/api/query",
        json={
            "question": "Milvus 是什么？",
            "max_iter": 1,
            "collection_name": "kb_verified",
            "use_web_search": True,
        },
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/query")
    assert captured["json"]["collection_names"] == ["kb_verified"]
    assert captured["json"]["use_web_search"] is True


def test_spa_deep_links_return_frontend_shell():
    response = TestClient(app).get("/chat/example-conversation")

    assert response.status_code == 200
    assert "DeepSearcher 用户工作台" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"


def test_workspace_api_validation_uses_safe_error_contract_and_request_id():
    response = TestClient(app).post(
        "/api/query",
        headers={"X-Request-ID": "workspace-request-1"},
        json={"question": "test", "max_iter": 0},
    )

    assert response.status_code == 422
    assert response.headers["x-request-id"] == "workspace-request-1"
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "请求内容格式不正确，请检查后重试。",
            "request_id": "workspace-request-1",
            "retryable": False,
        }
    }


def test_unknown_workspace_api_path_does_not_fall_back_to_spa():
    response = TestClient(app).get("/api/not-a-real-endpoint")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
