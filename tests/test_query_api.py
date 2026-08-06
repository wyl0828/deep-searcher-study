import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

import main
from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.configuration import RuntimeInitializationError
from deepsearcher.runtime_registry import RuntimeControlStore
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.vector_db.exceptions import (
    CollectionNotFound,
    EmbeddingProfileMismatch,
    UnsafeCollectionReplacement,
    VectorDBUnavailable,
    VectorDimensionMismatch,
    VectorInsertFailed,
)


class FakeConfig:
    def __init__(self):
        self.updates = []

    def set_provider_config(self, feature, provider, config):
        if feature not in {"llm", "embedding", "vector_db"}:
            raise ValueError(feature)
        self.updates.append((feature, provider, config))


class FakeVectorDB:
    client = None

    def list_collections(self):
        return []

    def delete_by_document_id(self, *, collection, document_id):
        return {"delete_count": 0}

    def delete_collection(self, collection):
        return {"deleted": False}


def make_runtime(*, vector_db=None, searcher=None, config=None, llm=None, embedding=None):
    return SimpleNamespace(
        config=config or FakeConfig(),
        vector_db=vector_db or FakeVectorDB(),
        default_searcher=searcher or object(),
        llm=llm or object(),
        embedding_model=embedding or object(),
        file_loader=object(),
        web_crawler=object(),
    )


def make_collection_manifest(model="embedding-a"):
    return CollectionManifest.create(
        logical_collection="kb_selected",
        embedding=EmbeddingProfile(
            provider="TestEmbedding",
            model=model,
            version=f"{model}-v1",
            dimension=8,
            normalization="none",
        ),
        metric_type="L2",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=[],
    )


@contextmanager
def runtime_client(runtime=None, **app_options):
    current_runtime = runtime or make_runtime()
    service_token = app_options.pop("service_token", "test-service-token")
    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=lambda _config: current_runtime,
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token=service_token,
            **app_options,
        )
        with TestClient(
            app,
            headers={"X-DeepSearcher-Service-Token": service_token},
        ) as client:
            yield client


def test_import_main_does_not_initialize_external_runtime():
    code = """
import deepsearcher.configuration as configuration
def forbidden(_self):
    raise AssertionError("runtime creation happened during import")
configuration.ModuleFactory.create_vector_db = forbidden
import main
print(type(main.app).__name__)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(main.__file__.rsplit("\\", 1)[0]),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "FastAPI" in result.stdout


def test_runtime_startup_failure_keeps_health_and_api_available():
    def fail_runtime(_config):
        raise RuntimeInitializationError("vector_db")

    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=fail_runtime,
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="test-service-token",
        )
        with TestClient(
            app,
            headers={"X-DeepSearcher-Service-Token": "test-service-token"},
        ) as client:
            health = client.get("/health")
            query_response = client.post("/query", json={"original_query": "问题"})

    assert health.status_code == 503
    health_payload = health.json()
    assert health_payload["status"] == "not_ready"
    assert health_payload["mode"] == "readiness"
    assert health_payload["checks"]["runtime"] == {
        "status": "not_ready",
        "code": "RUNTIME_INITIALIZATION_FAILED",
        "retryable": True,
        "component": "vector_db",
    }
    assert query_response.status_code == 503
    assert query_response.json()["error"]["component"] == "vector_db"


def test_runtime_lifespan_reports_ready_and_closes_vector_client():
    closed = []

    class VectorClient:
        def close(self):
            closed.append(True)

    vector_db = FakeVectorDB()
    vector_db.client = VectorClient()

    with runtime_client(make_runtime(vector_db=vector_db)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ready"
        assert payload["mode"] == "readiness"
        assert payload["checks"] == {
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
        }
        assert closed == []

    assert closed == [True]


def test_liveness_stays_alive_when_runtime_initialization_failed():
    def fail_runtime(_config):
        raise RuntimeInitializationError("vector_db")

    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=fail_runtime,
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="test-service-token",
        )
        with TestClient(app) as client:
            response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_reports_vector_database_unavailable():
    class UnavailableVectorDB(FakeVectorDB):
        def list_collections(self):
            raise OSError("connection refused")

    with runtime_client(make_runtime(vector_db=UnavailableVectorDB())) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["vector_db"] == {
        "status": "not_ready",
        "code": "DEPENDENCY_UNAVAILABLE",
        "retryable": True,
    }


def test_diagnostics_reports_provider_auth_failure_as_degraded_and_caches_probe():
    calls = {"llm": 0, "embedding": 0}

    class AuthenticationFailure(RuntimeError):
        status_code = 401

    class FailingLLM:
        def chat(self, _messages):
            calls["llm"] += 1
            raise AuthenticationFailure("invalid secret")

    class HealthyEmbedding:
        def embed_query(self, _text):
            calls["embedding"] += 1
            return [0.0]

    runtime = make_runtime(llm=FailingLLM(), embedding=HealthyEmbedding())
    with runtime_client(runtime) as client:
        first = client.post("/health/diagnostics")
        second = client.post("/health/diagnostics")

    assert first.status_code == 200
    assert first.json()["status"] == "degraded"
    assert first.json()["checks"]["llm"] == {
        "status": "not_ready",
        "code": "PROVIDER_AUTH_FAILED",
        "retryable": False,
    }
    assert first.json()["checks"]["embedding"] == {"status": "ready"}
    assert second.json()["checks"] == first.json()["checks"]
    assert calls == {"llm": 1, "embedding": 1}


def test_diagnostics_rejects_empty_or_invalid_provider_results():
    class EmptyLLM:
        def chat(self, _messages):
            return SimpleNamespace(content="   ")

    class WrongDimensionEmbedding:
        dimension = 2

        def embed_query(self, _text):
            return [0.0]

    runtime = make_runtime(llm=EmptyLLM(), embedding=WrongDimensionEmbedding())
    with runtime_client(runtime) as client:
        response = client.post("/health/diagnostics")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["llm"] == {
        "status": "not_ready",
        "code": "DEPENDENCY_INVALID_RESPONSE",
        "retryable": True,
    }
    assert response.json()["checks"]["embedding"] == {
        "status": "not_ready",
        "code": "DEPENDENCY_INVALID_RESPONSE",
        "retryable": True,
    }


def test_diagnostics_accepts_nonempty_llm_and_finite_dimension_matched_embedding():
    class HealthyLLM:
        def chat(self, _messages):
            return SimpleNamespace(content="OK")

    class HealthyEmbedding:
        dimension = 2

        def embed_query(self, _text):
            return [0.0, 1.0]

    runtime = make_runtime(llm=HealthyLLM(), embedding=HealthyEmbedding())
    with runtime_client(runtime) as client:
        response = client.post("/health/diagnostics")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["checks"]["llm"] == {"status": "ready"}
    assert response.json()["checks"]["embedding"] == {"status": "ready"}


def test_diagnostics_requires_service_token():
    with runtime_client() as client:
        response = client.post(
            "/health/diagnostics",
            headers={"X-DeepSearcher-Service-Token": ""},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "SERVICE_UNAUTHORIZED"


def test_query_api_returns_response_without_trace(monkeypatch):
    monkeypatch.setattr(main, "query", lambda _question, _max_iter, **_kwargs: ("答案", [], 12))

    with runtime_client() as client:
        response = client.post("/query", json={"original_query": "问题"})

    assert response.status_code == 200
    assert response.json() == {"result": "答案", "consume_token": 12}


def test_query_api_returns_trace_when_requested(monkeypatch):
    expected_trace = {"version": 1, "agent": {"name": "ChainOfRAG"}}
    monkeypatch.setattr(
        main,
        "query_with_trace",
        lambda _question, _max_iter, **_kwargs: ("答案", [], 12, expected_trace),
    )

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={"original_query": "问题", "include_trace": True},
        )

    assert response.status_code == 200
    assert response.json() == {
        "result": "答案",
        "consume_token": 12,
        "trace": expected_trace,
    }


def test_query_api_forwards_explicit_collection_scope(monkeypatch):
    captured = {}

    def traced_query(question, max_iter, **kwargs):
        captured["question"] = question
        captured["max_iter"] = max_iter
        captured.update(kwargs)
        return "答案", [], 12, {"version": 1}

    monkeypatch.setattr(main, "query_with_trace", traced_query)

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={
                "original_query": "问题",
                "max_iter": 3,
                "include_trace": True,
                "collection_names": ["kb_first", "kb_second"],
                "use_web_search": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["result"] == "答案"
    assert captured["question"] == "问题"
    assert captured["max_iter"] == 3
    assert captured["collection_names"] == ["kb_first", "kb_second"]
    assert captured["use_web_search"] is True
    assert captured["searcher"] is not None


def parse_sse_events(body: str) -> list[dict]:
    events = []
    for frame in body.split("\n\n"):
        data_line = next(
            (line[6:] for line in frame.splitlines() if line.startswith("data: ")),
            None,
        )
        if data_line:
            events.append(json.loads(data_line))
    return events


def test_query_stream_emits_safe_incremental_stage_events():
    captured = {}
    result = RetrievalResult(
        embedding=[0.1],
        text=(
            "Supported fact owner@example.com api_key=top-secret "
            r"C:\Users\private\source.txt"
        ),
        reference=r"C:\Users\private\source.pdf",
        metadata={
            "document_id": "doc-safe",
            "display_name": r"C:\Users\private\source.pdf",
            "page_number": 2,
        },
        metric_type="L2",
        distance=0.9,
    )

    class Searcher:
        def query(self, original_query, **kwargs):
            captured["original_query"] = original_query
            captured["collection_names"] = kwargs.get("collection_names")
            captured["use_web_search"] = kwargs.get("use_web_search")
            collector = kwargs["trace_collector"]
            collector.select_agent("ChainOfRAG")
            collector.start_iteration(1)
            collector.record_web_search(
                {
                    "status": "completed",
                    "provider": "tavily",
                    "query_count": 1,
                    "result_count": 1,
                    "error_code": None,
                }
            )
            collector.record_subquery("private generated subquery")
            collector.record_documents_retrieved([result])
            collector.record_documents_supported([result])
            collector.record_reflection(True)
            collector.record_final_answer(4)
            return "Safe final answer.", [result], 12

    with runtime_client(make_runtime(searcher=Searcher())) as client:
        response = client.post(
            "/query/stream",
            headers={"X-Request-ID": "request-sse-safe-1"},
            json={
                "original_query": "private original question",
                "max_iter": 2,
                "collection_names": ["kb_selected"],
                "use_web_search": True,
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store, no-transform"
    assert response.headers["x-trace-retention"] == "transient"
    assert response.headers["x-request-id"] == "request-sse-safe-1"
    events = parse_sse_events(response.text)
    assert [event["event"] for event in events] == [
        "started",
        "routing",
        "iteration",
        "web_search",
        "retrieval",
        "support",
        "reflection",
        "completed",
    ]
    assert [event["sequence"] for event in events] == list(range(1, 9))
    assert {event["request_id"] for event in events} == {"request-sse-safe-1"}
    completed = events[-1]["data"]
    assert completed["result"] == "Safe final answer."
    assert completed["consume_token"] == 12
    assert completed["trace"]["version"] == 3
    document = completed["trace"]["iterations"][0]["retrieved_documents"][0]
    assert document["metric_type"] == "L2"
    assert document["distance"] == 0.9
    assert "score" not in document
    assert "original_query" not in completed["trace"]
    assert captured["original_query"] == "private original question"
    assert captured["collection_names"] == ["kb_selected"]
    assert captured["use_web_search"] is True
    assert "private original question" not in response.text
    assert "private generated subquery" not in response.text
    assert "owner@example.com" not in response.text
    assert "top-secret" not in response.text
    assert r"C:\Users\private" not in response.text


def test_query_stream_returns_safe_error_event_without_exception_details():
    class Searcher:
        def query(self, _original_query, **_kwargs):
            raise RuntimeError("api_key=do-not-leak C:\\private\\service")

    with runtime_client(make_runtime(searcher=Searcher())) as client:
        response = client.post(
            "/query/stream",
            json={"original_query": "question"},
        )

    events = parse_sse_events(response.text)
    assert response.status_code == 200
    assert [event["event"] for event in events] == ["started", "error"]
    assert events[-1]["data"] == {
        "code": "QUERY_FAILED",
        "message": "The query could not be completed.",
        "retryable": True,
    }
    assert "do-not-leak" not in response.text
    assert "C:\\private" not in response.text


def test_delete_document_vectors_forwards_collection_and_document_id():
    captured = {}

    class VectorDB(FakeVectorDB):
        def delete_by_document_id(self, *, collection, document_id):
            captured["collection"] = collection
            captured["document_id"] = document_id
            return {"delete_count": 3}

    document_id = "a" * 64
    with runtime_client(make_runtime(vector_db=VectorDB())) as client:
        response = client.delete(f"/collections/kb_selected/documents/{document_id}")

    assert response.status_code == 200
    assert response.json()["delete_count"] == 3
    assert captured == {
        "collection": "kb_selected",
        "document_id": document_id,
    }


def test_delete_document_vectors_rejects_unsafe_identifiers():
    with runtime_client() as client:
        response = client.delete(f"/collections/invalid-name/documents/{'a' * 64}")

    assert response.status_code == 400


def test_collection_manifest_endpoint_reports_runtime_compatibility():
    manifest = make_collection_manifest()

    class VectorDB(FakeVectorDB):
        def get_collection_manifest(self, collection):
            assert collection == "kb_selected"
            return manifest

    embedding_model = SimpleNamespace(
        dimension=8,
        _deepsearcher_embedding_provider="TestEmbedding",
        _deepsearcher_embedding_model="embedding-a",
        _deepsearcher_embedding_version="embedding-a-v1",
        _deepsearcher_embedding_normalization="none",
    )
    runtime = make_runtime(vector_db=VectorDB())
    runtime.embedding_model = embedding_model

    with runtime_client(runtime) as client:
        response = client.get("/collections/kb_selected/manifest")

    assert response.status_code == 200
    assert response.json()["compatible"] is True
    assert response.json()["status"] == "compatible"
    assert response.json()["mismatches"] == []
    assert response.json()["manifest"]["embedding_model"] == "embedding-a"


def test_collection_manifest_endpoint_reports_same_dimension_model_mismatch():
    manifest = make_collection_manifest(model="embedding-a")

    class VectorDB(FakeVectorDB):
        def get_collection_manifest(self, _collection):
            return manifest

    embedding_model = SimpleNamespace(
        dimension=8,
        _deepsearcher_embedding_provider="TestEmbedding",
        _deepsearcher_embedding_model="embedding-b",
        _deepsearcher_embedding_version="embedding-b-v1",
        _deepsearcher_embedding_normalization="none",
    )
    runtime = make_runtime(vector_db=VectorDB())
    runtime.embedding_model = embedding_model

    with runtime_client(runtime) as client:
        response = client.get("/collections/kb_selected/manifest")

    assert response.status_code == 200
    assert response.json()["compatible"] is False
    assert set(response.json()["mismatches"]) == {"model", "version"}


def test_collection_rebuild_requires_product_scope_and_exact_confirmation(monkeypatch):
    rebuild = Mock()
    monkeypatch.setattr(main, "load_from_local_files", rebuild)
    collection_name = f"kb_{'d' * 32}"

    with runtime_client() as client:
        missing_confirmation = client.post(
            f"/collections/{collection_name}/rebuild",
            json={"paths": ["guide.pdf"]},
        )
        non_product = client.post(
            "/collections/deepsearcher/rebuild",
            headers={"X-Confirm-Collection": "deepsearcher"},
            json={"paths": ["guide.pdf"]},
        )

    assert missing_confirmation.status_code == 409
    assert non_product.status_code == 403
    rebuild.assert_not_called()


def test_collection_rebuild_forwards_complete_safe_version_request(monkeypatch):
    collection_name = f"kb_{'e' * 32}"
    manifest = make_collection_manifest().to_dict()
    captured = {}

    def rebuild(**kwargs):
        captured.update(kwargs)
        return {
            "active_collection": collection_name,
            "backing_collection": f"{collection_name}__v_candidate",
            "previous_collection": f"{collection_name}__previous_old",
            "manifest": manifest,
        }

    monkeypatch.setattr(main, "load_from_local_files", rebuild)
    runtime = make_runtime()

    with runtime_client(runtime) as client:
        response = client.post(
            f"/collections/{collection_name}/rebuild",
            headers={"X-Confirm-Collection": collection_name},
            json={
                "paths": ["first.pdf", "second.pdf"],
                "collection_description": "完整资料",
                "chunk_size": 800,
                "chunk_overlap": 80,
                "batch_size": 16,
            },
        )

    assert response.status_code == 200
    assert response.json()["collection"]["manifest"] == manifest
    assert captured == {
        "paths_or_directory": ["first.pdf", "second.pdf"],
        "collection_name": collection_name,
        "collection_description": "完整资料",
        "force_new_collection": True,
        "chunk_size": 800,
        "chunk_overlap": 80,
        "batch_size": 16,
        "vector_db_instance": runtime.vector_db,
        "embedding_model_instance": runtime.embedding_model,
        "file_loader_instance": runtime.file_loader,
    }


def test_collection_rebuild_rejects_invalid_processing_limits(monkeypatch):
    rebuild = Mock()
    monkeypatch.setattr(main, "load_from_local_files", rebuild)
    collection_name = f"kb_{'f' * 32}"

    with runtime_client() as client:
        empty_paths = client.post(
            f"/collections/{collection_name}/rebuild",
            headers={"X-Confirm-Collection": collection_name},
            json={"paths": []},
        )
        zero_chunk_size = client.post(
            f"/collections/{collection_name}/rebuild",
            headers={"X-Confirm-Collection": collection_name},
            json={"paths": ["guide.pdf"], "chunk_size": 0},
        )
        invalid_overlap = client.post(
            f"/collections/{collection_name}/rebuild",
            headers={"X-Confirm-Collection": collection_name},
            json={"paths": ["guide.pdf"], "chunk_size": 100, "chunk_overlap": 100},
        )
        zero_batch_size = client.post(
            f"/collections/{collection_name}/rebuild",
            headers={"X-Confirm-Collection": collection_name},
            json={"paths": ["guide.pdf"], "batch_size": 0},
        )

    assert empty_paths.status_code == 422
    assert zero_chunk_size.status_code == 422
    assert invalid_overlap.status_code == 400
    assert zero_batch_size.status_code == 422
    rebuild.assert_not_called()


def test_delete_vector_collection_requires_exact_confirmation_and_forwards_name():
    captured = {}
    collection_name = f"kb_{'a' * 32}"

    class VectorDB(FakeVectorDB):
        def delete_collection(self, collection):
            captured["collection"] = collection
            return {"deleted": True}

    with runtime_client(make_runtime(vector_db=VectorDB())) as client:
        response = client.delete(
            f"/collections/{collection_name}",
            headers={"X-Confirm-Collection": collection_name},
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Collection deleted successfully.",
        "deleted": True,
    }
    assert captured == {"collection": collection_name}


def test_delete_vector_collection_does_not_mutate_without_confirmation():
    captured = []
    collection_name = f"kb_{'b' * 32}"

    class VectorDB(FakeVectorDB):
        def delete_collection(self, collection):
            captured.append(collection)
            return {"deleted": True}

    with runtime_client(make_runtime(vector_db=VectorDB())) as client:
        missing = client.delete(f"/collections/{collection_name}")
        mismatched = client.delete(
            f"/collections/{collection_name}",
            headers={"X-Confirm-Collection": f"kb_{'c' * 32}"},
        )

    assert missing.status_code == 409
    assert mismatched.status_code == 409
    assert captured == []


def test_delete_vector_collection_rejects_non_product_collection():
    with runtime_client() as client:
        response = client.delete(
            "/collections/deepsearcher",
            headers={"X-Confirm-Collection": "deepsearcher"},
        )

    assert response.status_code == 403


def test_delete_vector_collection_rejects_unsafe_identifier():
    with runtime_client() as client:
        response = client.delete("/collections/invalid-name")

    assert response.status_code == 400


def test_query_http_reports_vector_database_unavailable_without_leaking_details(
    monkeypatch,
):
    def unavailable(*_args, **_kwargs):
        raise VectorDBUnavailable(operation="search", collection="kb_private")

    monkeypatch.setattr(main, "query_with_trace", unavailable)

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={
                "original_query": "问题",
                "include_trace": True,
                "collection_names": ["kb_private"],
            },
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"].pop("request_id")
    assert payload == {
        "error": {
            "code": "VECTOR_DB_UNAVAILABLE",
            "message": "The vector database is temporarily unavailable.",
            "retryable": True,
        }
    }
    assert "kb_private" not in response.text


def test_query_http_distinguishes_missing_collection(monkeypatch):
    def missing(*_args, **_kwargs):
        raise CollectionNotFound(operation="search", collection="kb_missing")

    monkeypatch.setattr(main, "query_with_trace", missing)

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={
                "original_query": "问题",
                "include_trace": True,
                "collection_names": ["kb_missing"],
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VECTOR_COLLECTION_NOT_FOUND"
    assert response.json()["error"]["retryable"] is False


def test_query_http_distinguishes_dimension_mismatch(monkeypatch):
    def mismatch(*_args, **_kwargs):
        raise VectorDimensionMismatch(operation="search", collection="kb_selected")

    monkeypatch.setattr(main, "query_with_trace", mismatch)

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={
                "original_query": "问题",
                "include_trace": True,
                "collection_names": ["kb_selected"],
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VECTOR_DIMENSION_MISMATCH"


def test_unsafe_collection_replacement_is_a_conflict(monkeypatch):
    def unsafe_replacement(**_kwargs):
        raise UnsafeCollectionReplacement(
            operation="initialize",
            collection="kb_private",
        )

    monkeypatch.setattr(main, "load_from_local_files", unsafe_replacement)

    with runtime_client() as client:
        response = client.post(
            "/load-files/",
            json={
                "paths": "document.pdf",
                "collection_name": "kb_private",
            },
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VECTOR_UNSAFE_COLLECTION_REPLACEMENT"


def test_embedding_profile_mismatch_is_a_safe_conflict(monkeypatch):
    def mismatch(*_args, **_kwargs):
        raise EmbeddingProfileMismatch(
            operation="validate_embedding",
            collection="kb_private",
            mismatches=("model",),
        )

    monkeypatch.setattr(main, "query_with_trace", mismatch)

    with runtime_client() as client:
        response = client.post(
            "/query",
            json={
                "original_query": "问题",
                "include_trace": True,
                "collection_names": ["kb_private"],
            },
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["error"].pop("request_id")
    assert payload == {
        "error": {
            "code": "VECTOR_EMBEDDING_PROFILE_MISMATCH",
            "message": ("The active embedding model is incompatible with the vector collection."),
            "retryable": False,
        }
    }
    assert "kb_private" not in response.text


def test_ingest_http_reports_vector_insert_failure_with_safe_contract(monkeypatch):
    def insert_failed(**_kwargs):
        raise VectorInsertFailed(operation="insert", collection="kb_private")

    monkeypatch.setattr(main, "load_from_local_files", insert_failed)

    with runtime_client() as client:
        response = client.post(
            "/load-files/",
            json={
                "paths": "document.pdf",
                "collection_name": "kb_private",
            },
        )

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"].pop("request_id")
    assert payload == {
        "error": {
            "code": "VECTOR_INSERT_FAILED",
            "message": "The vector data could not be stored.",
            "retryable": True,
        }
    }
    assert "kb_private" not in response.text


def test_ingest_retry_replaces_existing_document_chunks_before_loading(monkeypatch):
    events = []

    class VectorDB(FakeVectorDB):
        def delete_by_document_id(self, *, collection, document_id):
            events.append(("delete", collection, document_id))
            return {"delete_count": 2}

    def load(**kwargs):
        events.append(("load", kwargs["collection_name"], kwargs["paths_or_directory"]))
        return {"manifest": {"schema_version": 1}}

    monkeypatch.setattr(main, "load_from_local_files", load)
    document_id = "a" * 64
    with runtime_client(make_runtime(vector_db=VectorDB())) as client:
        response = client.post(
            "/load-files/",
            json={
                "paths": "document.pdf",
                "collection_name": "kb_private",
                "replace_document_id": document_id,
            },
        )

    assert response.status_code == 200
    assert events == [
        ("delete", "kb_private", document_id),
        ("load", "kb_private", "document.pdf"),
    ]


def test_ingest_retry_rejects_invalid_document_identifier_before_mutation(monkeypatch):
    load_files_mock = Mock()
    vector_db = FakeVectorDB()
    vector_db.delete_by_document_id = Mock()
    monkeypatch.setattr(main, "load_from_local_files", load_files_mock)

    with runtime_client(make_runtime(vector_db=vector_db)) as client:
        response = client.post(
            "/load-files/",
            json={
                "paths": "document.pdf",
                "collection_name": "kb_private",
                "replace_document_id": "../../unsafe",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_DOCUMENT_ID"
    vector_db.delete_by_document_id.assert_not_called()
    load_files_mock.assert_not_called()


def test_provider_update_does_not_echo_secret_config(monkeypatch):
    closed = []

    class VectorClient:
        def close(self):
            closed.append(True)

    initial_vector_db = FakeVectorDB()
    initial_vector_db.client = VectorClient()
    initial_runtime = make_runtime(vector_db=initial_vector_db)

    def rebuild(candidate_config):
        return make_runtime(config=candidate_config)

    calls = []

    def runtime_factory(candidate_config):
        if not calls:
            calls.append("initial")
            return initial_runtime
        return rebuild(candidate_config)

    monkeypatch.setenv("TEST_RUNTIME_API_KEY", "do-not-return")
    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=runtime_factory,
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="test-service-token",
        )
        with TestClient(
            app,
            headers={"X-DeepSearcher-Service-Token": "test-service-token"},
        ) as client:
            response = client.post(
                "/set-provider-config/",
                json={
                    "feature": "llm",
                    "provider": "OpenAI",
                    "config": {"api_key": {"$env": "TEST_RUNTIME_API_KEY"}},
                },
            )

    assert response.status_code == 200
    assert response.json()["message"] == "Provider config set successfully"
    assert response.json()["provider"] == "OpenAI"
    assert response.json()["tenant_id"] == "local"
    assert response.json()["runtime_version"] > 1
    assert "do-not-return" not in response.text
    assert closed == [True]


def test_failed_provider_update_keeps_the_previous_runtime_active(monkeypatch):
    initial_runtime = make_runtime()
    calls = []

    def runtime_factory(_candidate_config):
        if not calls:
            calls.append("initial")
            return initial_runtime
        raise RuntimeInitializationError("llm")

    monkeypatch.setenv("TEST_RUNTIME_API_KEY", "do-not-return")
    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=runtime_factory,
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="test-service-token",
        )
        with TestClient(
            app,
            headers={"X-DeepSearcher-Service-Token": "test-service-token"},
        ) as client:
            response = client.post(
                "/set-provider-config/",
                json={
                    "feature": "llm",
                    "provider": "BrokenProvider",
                    "config": {"api_key": {"$env": "TEST_RUNTIME_API_KEY"}},
                },
            )
            health = client.get("/health")
            active_runtime = app.state.runtime

    assert response.status_code == 503
    assert response.json()["error"]["component"] == "llm"
    assert "do-not-return" not in response.text
    assert health.status_code == 200
    assert active_runtime is initial_runtime


def test_concurrent_tenant_requests_use_isolated_models_and_collections(monkeypatch):
    class TenantConfig:
        def __init__(self):
            self.provide_settings = {
                "llm": {
                    "provider": "FakeLLM",
                    "config": {"model": "base-model"},
                }
            }
            self.query_settings = {"max_iter": 3}
            self.load_settings = {"chunk_size": 100, "chunk_overlap": 10}

        def set_provider_config(self, feature, provider, config):
            if feature != "llm":
                raise ValueError(feature)
            self.provide_settings[feature] = {
                "provider": provider,
                "config": dict(config),
            }

    def build_runtime(config):
        model = config.provide_settings["llm"]["config"]["model"]
        return make_runtime(searcher=SimpleNamespace(model=model), config=config)

    def tenant_query(_question, _max_iter, **kwargs):
        model = kwargs["searcher"].model
        collections = ",".join(kwargs.get("collection_names") or [])
        return f"{model}:{collections}", [], 1

    monkeypatch.setattr(main, "query", tenant_query)
    service_headers = {
        "X-DeepSearcher-Service-Token": "service-secret",
    }
    admin_headers = {
        "X-DeepSearcher-Admin-Token": "admin-secret",
    }

    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=build_runtime,
            config_factory=TenantConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="service-secret",
            admin_token="admin-secret",
        )
        with TestClient(app) as client:
            tenant_a = client.post(
                "/runtime/tenants/tenant-a/versions",
                headers=admin_headers,
                json={
                    "feature": "llm",
                    "provider": "FakeLLM",
                    "config": {"model": "model-a"},
                    "allowed_collections": ["kb_tenant_a"],
                    "model_policy": "policy-a",
                },
            )
            tenant_b = client.post(
                "/runtime/tenants/tenant-b/versions",
                headers=admin_headers,
                json={
                    "feature": "llm",
                    "provider": "FakeLLM",
                    "config": {"model": "model-b"},
                    "allowed_collections": ["kb_tenant_b"],
                    "model_policy": "policy-b",
                },
            )
            assert tenant_a.status_code == 200
            assert tenant_b.status_code == 200

            def ask(tenant_id, collection_name):
                return client.post(
                    "/query",
                    headers={
                        **service_headers,
                        "X-DeepSearcher-Tenant": tenant_id,
                    },
                    json={
                        "original_query": "问题",
                        "collection_names": [collection_name],
                    },
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_a = executor.submit(ask, "tenant-a", "kb_tenant_a")
                future_b = executor.submit(ask, "tenant-b", "kb_tenant_b")
                response_a = future_a.result()
                response_b = future_b.result()

            cross_tenant = ask("tenant-a", "kb_tenant_b")
            missing_service_token = client.get(
                "/runtime/context",
                headers={"X-DeepSearcher-Tenant": "tenant-a"},
            )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json()["result"] == "model-a:kb_tenant_a"
    assert response_b.json()["result"] == "model-b:kb_tenant_b"
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["error"]["code"] == "RUNTIME_COLLECTION_ACCESS_DENIED"
    assert missing_service_token.status_code == 401
    assert missing_service_token.json()["error"]["code"] == "SERVICE_UNAUTHORIZED"


def test_runtime_api_rollback_restores_previous_model_across_worker_registries(monkeypatch):
    class TenantConfig:
        def __init__(self):
            self.provide_settings = {
                "llm": {
                    "provider": "FakeLLM",
                    "config": {"model": "base-model"},
                }
            }
            self.query_settings = {}
            self.load_settings = {}

        def set_provider_config(self, feature, provider, config):
            self.provide_settings[feature] = {
                "provider": provider,
                "config": dict(config),
            }

    def build_runtime(config):
        model = config.provide_settings["llm"]["config"]["model"]
        return make_runtime(searcher=SimpleNamespace(model=model), config=config)

    monkeypatch.setattr(
        main,
        "query",
        lambda _question, _max_iter, **kwargs: (
            kwargs["searcher"].model,
            [],
            1,
        ),
    )
    headers = {
        "X-DeepSearcher-Tenant": "tenant-a",
        "X-DeepSearcher-Service-Token": "service-secret",
    }

    with TemporaryDirectory() as directory:
        store_path = Path(directory) / "runtime.db"
        app_a = main.create_app(
            runtime_factory=build_runtime,
            config_factory=TenantConfig,
            runtime_store_factory=lambda: RuntimeControlStore(store_path),
            service_token="service-secret",
            admin_token="admin-secret",
        )
        app_b = main.create_app(
            runtime_factory=build_runtime,
            config_factory=TenantConfig,
            runtime_store_factory=lambda: RuntimeControlStore(store_path),
            service_token="service-secret",
            admin_token="admin-secret",
        )
        admin_headers = {"X-DeepSearcher-Admin-Token": "admin-secret"}
        with TestClient(app_a) as publisher, TestClient(app_b) as observer:
            first = publisher.post(
                "/runtime/tenants/tenant-a/versions",
                headers=admin_headers,
                json={
                    "feature": "llm",
                    "provider": "FakeLLM",
                    "config": {"model": "model-a"},
                    "allowed_collections": ["kb_tenant_a"],
                    "model_policy": "policy-v1",
                },
            )
            first_version = first.json()["runtime_version"]
            second = publisher.post(
                "/runtime/tenants/tenant-a/versions",
                headers=admin_headers,
                json={
                    "feature": "llm",
                    "provider": "FakeLLM",
                    "config": {"model": "model-a-v2"},
                    "allowed_collections": ["kb_tenant_a", "kb_extra"],
                    "model_policy": "policy-v2",
                    "expected_version": first_version,
                },
            )
            second_version = second.json()["runtime_version"]
            before = observer.post(
                "/query",
                headers=headers,
                json={
                    "original_query": "问题",
                    "collection_names": ["kb_tenant_a"],
                },
            )
            rollback = publisher.post(
                "/runtime/tenants/tenant-a/rollback",
                headers=admin_headers,
                json={"expected_version": second_version},
            )
            after = observer.post(
                "/query",
                headers=headers,
                json={
                    "original_query": "问题",
                    "collection_names": ["kb_tenant_a"],
                },
            )
            context_after = observer.get("/runtime/context", headers=headers)

    assert before.status_code == 200
    assert before.json()["result"] == "model-a-v2"
    assert rollback.status_code == 200
    assert rollback.json()["runtime_version"] == first_version
    assert after.status_code == 200
    assert after.json()["result"] == "model-a"
    assert context_after.status_code == 200
    assert context_after.json()["model_policy"] == "policy-v1"
    assert context_after.json()["allowed_collections"] == ["kb_tenant_a"]


def test_api_errors_have_stable_codes_and_request_ids():
    with runtime_client() as client:
        invalid = client.post(
            "/query",
            headers={"X-Request-ID": "request-validation-1"},
            json={"original_query": "question", "max_iter": 0},
        )
        empty = client.post("/query", json={"original_query": "   "})
        missing = client.get(
            "/missing-resource",
            headers={"X-Request-ID": r"C:\private\invalid"},
        )

    assert invalid.status_code == 422
    assert invalid.headers["x-request-id"] == "request-validation-1"
    assert invalid.json()["error"] == {
        "code": "INVALID_REQUEST",
        "message": "The request payload is invalid.",
        "request_id": "request-validation-1",
        "retryable": False,
    }
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "QUERY_EMPTY"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert main.REQUEST_ID_PATTERN.fullmatch(missing.json()["error"]["request_id"])
    assert "private" not in missing.text


def test_query_uses_post_body_and_legacy_get_is_not_exposed(monkeypatch):
    monkeypatch.setattr(main, "query", lambda *_args, **_kwargs: ("answer", [], 1))

    with runtime_client() as client:
        schema = client.get("/openapi.json").json()
        wrong_method = client.get(
            "/query",
            params={"original_query": "private question"},
        )
        legacy = client.get(
            "/query/",
            params={"original_query": "private question"},
        )
        current = client.post("/query", json={"original_query": "private question"})

    assert set(schema["paths"]["/query"]) == {"post"}
    assert wrong_method.status_code == 405
    assert legacy.status_code == 404
    assert "private question" not in legacy.text
    assert current.status_code == 200


def test_query_timeout_and_internal_failure_do_not_leak_details(monkeypatch, caplog):
    secret = "sk-private-credential"
    private_path = r"C:\Users\private\service.py"

    def timeout(*_args, **_kwargs):
        raise TimeoutError(f"{secret} {private_path}")

    monkeypatch.setattr(main, "query", timeout)
    with runtime_client() as client:
        timed_out = client.post("/query", json={"original_query": "question"})

    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "QUERY_TIMEOUT"
    assert secret not in timed_out.text
    assert private_path not in timed_out.text

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{secret} {private_path}")

    monkeypatch.setattr(main, "query", fail)
    with caplog.at_level(logging.ERROR, logger="main"):
        with runtime_client() as client:
            failed = client.post("/query", json={"original_query": "question"})

    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "QUERY_FAILED"
    assert "RuntimeError" in caplog.text
    assert secret not in failed.text
    assert secret not in caplog.text
    assert private_path not in failed.text
    assert private_path not in caplog.text


def test_query_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(main, "query", lambda *_args, **_kwargs: ("answer", [], 1))

    with runtime_client(query_rate_limit=1, query_rate_window_seconds=60) as client:
        first = client.post("/query", json={"original_query": "first"})
        limited = client.post("/query", json={"original_query": "second"})

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "QUERY_RATE_LIMITED"
    assert limited.json()["error"]["retryable"] is True
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["x-request-id"] == limited.json()["error"]["request_id"]


def test_unauthorized_query_does_not_consume_authenticated_tenant_budget(monkeypatch):
    monkeypatch.setattr(main, "query", lambda *_args, **_kwargs: ("answer", [], 1))

    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=lambda _config: make_runtime(),
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="required-service-token",
            query_rate_limit=1,
            query_rate_window_seconds=60,
        )
        with TestClient(app) as client:
            unauthorized = client.post(
                "/query",
                headers={"X-DeepSearcher-Tenant": "local"},
                json={"original_query": "unauthorized"},
            )
            authenticated_headers = {
                "X-DeepSearcher-Tenant": "local",
                "X-DeepSearcher-Service-Token": "required-service-token",
            }
            accepted = client.post(
                "/query",
                headers=authenticated_headers,
                json={"original_query": "accepted"},
            )
            limited = client.post(
                "/query",
                headers=authenticated_headers,
                json={"original_query": "limited"},
            )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert limited.status_code == 429


def test_cors_allows_only_explicit_trusted_origins():
    preflight_headers = {
        "Origin": "https://workspace.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-request-id",
    }
    with runtime_client(cors_origins=["https://workspace.example", "*"]) as client:
        trusted = client.options("/query", headers=preflight_headers)
        untrusted = client.options(
            "/query",
            headers={**preflight_headers, "Origin": "https://attacker.example"},
        )

    assert trusted.status_code == 200
    assert trusted.headers["access-control-allow-origin"] == "https://workspace.example"
    assert "access-control-allow-origin" not in untrusted.headers


def test_unauthorized_caller_cannot_switch_provider_or_load_server_files(
    monkeypatch,
):
    load_files_mock = Mock()
    monkeypatch.setattr(main, "load_from_local_files", load_files_mock)

    with TemporaryDirectory() as directory:
        app = main.create_app(
            runtime_factory=lambda _config: make_runtime(),
            config_factory=FakeConfig,
            runtime_store_factory=lambda: RuntimeControlStore(Path(directory) / "runtime.db"),
            service_token="required-service-token",
        )
        with TestClient(app) as client:
            load_response = client.post(
                "/load-files/",
                json={
                    "paths": r"C:\Users\private\secret.txt",
                    "collection_name": "deepsearcher",
                },
            )
            config_response = client.post(
                "/set-provider-config/",
                json={
                    "feature": "llm",
                    "provider": "OpenAI",
                    "config": {"api_key": "should-never-be-accepted"},
                },
            )

    assert load_response.status_code == 401
    assert config_response.status_code == 401
    assert load_response.json()["error"]["code"] == "SERVICE_UNAUTHORIZED"
    assert config_response.json()["error"]["code"] == "SERVICE_UNAUTHORIZED"
    assert "secret.txt" not in load_response.text
    assert "should-never-be-accepted" not in config_response.text
    load_files_mock.assert_not_called()
