import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deepsearcher.runtime_registry import (
    RuntimeCollectionAccessDenied,
    RuntimeConfigRejected,
    RuntimeControlStore,
    RuntimePatch,
    RuntimeRegistry,
    close_runtime,
)


class FakeConfig:
    def __init__(self, model: str = "base-model"):
        self.provide_settings = {
            "llm": {
                "provider": "FakeLLM",
                "config": {"model": model},
            }
        }
        self.query_settings = {"max_iter": 3}
        self.load_settings = {"chunk_size": 100, "chunk_overlap": 10}

    def set_provider_config(self, feature, provider, provider_configs):
        if feature != "llm":
            raise ValueError(feature)
        self.provide_settings[feature] = {
            "provider": provider,
            "config": dict(provider_configs),
        }


class FakeVectorClient:
    def __init__(self, model: str, closed: list[str]):
        self.model = model
        self.closed = closed

    def close(self):
        self.closed.append(self.model)


def runtime_factory(closed: list[str]):
    def build(config):
        model = config.provide_settings["llm"]["config"]["model"]
        vector_db = SimpleNamespace(client=FakeVectorClient(model, closed))
        return SimpleNamespace(
            config=config,
            vector_db=vector_db,
            default_searcher=SimpleNamespace(model=model),
            embedding_model=object(),
            file_loader=object(),
            web_crawler=object(),
        )

    return build


def make_registry(path: Path, closed: list[str]) -> RuntimeRegistry:
    return RuntimeRegistry(
        base_config=FakeConfig(),
        runtime_factory=runtime_factory(closed),
        store=RuntimeControlStore(path),
    )


def test_version_publish_keeps_leased_runtime_alive_until_request_finishes(tmp_path):
    async def scenario():
        closed: list[str] = []
        registry = make_registry(tmp_path / "runtime.db", closed)
        await registry.start()
        old_lease = await registry.acquire("local")

        binding = await registry.publish(
            tenant_id="local",
            patch=RuntimePatch("llm", "FakeLLM", {"model": "new-model"}),
        )
        new_lease = await registry.acquire("local")

        assert old_lease.runtime.default_searcher.model == "base-model"
        assert new_lease.runtime.default_searcher.model == "new-model"
        assert new_lease.context.runtime_version == binding.active_version
        assert closed == []

        await old_lease.release()
        assert closed == ["base-model"]
        await new_lease.release()
        assert closed == ["base-model"]
        await registry.shutdown()
        assert closed == ["base-model", "new-model"]

    asyncio.run(scenario())


def test_two_workers_observe_isolated_tenant_models_and_collection_permissions(tmp_path):
    async def scenario():
        closed: list[str] = []
        store_path = tmp_path / "runtime.db"
        worker_a = make_registry(store_path, closed)
        worker_b = make_registry(store_path, closed)
        await worker_a.start()
        await worker_b.start()

        tenant_a = await worker_a.publish(
            tenant_id="tenant-a",
            patch=RuntimePatch("llm", "FakeLLM", {"model": "model-a"}),
            allowed_collections=["kb_tenant_a"],
        )
        tenant_b = await worker_a.publish(
            tenant_id="tenant-b",
            patch=RuntimePatch("llm", "FakeLLM", {"model": "model-b"}),
            allowed_collections=["kb_tenant_b"],
        )

        lease_a, lease_b = await asyncio.gather(
            worker_b.acquire("tenant-a"),
            worker_b.acquire("tenant-b"),
        )
        assert lease_a.runtime.default_searcher.model == "model-a"
        assert lease_b.runtime.default_searcher.model == "model-b"
        assert lease_a.context.runtime_version == tenant_a.active_version
        assert lease_b.context.runtime_version == tenant_b.active_version
        assert lease_a.context.collections_for_query(None) == ["kb_tenant_a"]
        assert lease_b.context.collections_for_query(None) == ["kb_tenant_b"]
        with pytest.raises(RuntimeCollectionAccessDenied):
            lease_a.context.require_collection("kb_tenant_b")
        with pytest.raises(RuntimeCollectionAccessDenied):
            lease_b.context.require_collection("kb_tenant_a")

        await lease_a.release()
        await lease_b.release()
        await worker_a.shutdown()
        await worker_b.shutdown()

    asyncio.run(scenario())


def test_cross_worker_rollback_is_visible_on_the_next_request(tmp_path):
    async def scenario():
        closed: list[str] = []
        store_path = tmp_path / "runtime.db"
        publisher = make_registry(store_path, closed)
        observer = make_registry(store_path, closed)
        base_binding = await publisher.start()
        await observer.start()

        published = await publisher.publish(
            tenant_id="local",
            patch=RuntimePatch("llm", "FakeLLM", {"model": "candidate-model"}),
        )
        candidate_lease = await observer.acquire("local")
        assert candidate_lease.context.runtime_version == published.active_version
        assert candidate_lease.runtime.default_searcher.model == "candidate-model"
        await candidate_lease.release()

        rolled_back = await publisher.rollback(
            tenant_id="local",
            expected_version=published.active_version,
        )
        restored_lease = await observer.acquire("local")
        assert rolled_back.active_version == base_binding.active_version
        assert restored_lease.context.runtime_version == base_binding.active_version
        assert restored_lease.runtime.default_searcher.model == "base-model"

        await restored_lease.release()
        await publisher.shutdown()
        await observer.shutdown()

    asyncio.run(scenario())


def test_published_secrets_must_reference_environment_variables(tmp_path, monkeypatch):
    async def scenario():
        closed: list[str] = []
        registry = make_registry(tmp_path / "runtime.db", closed)
        await registry.start()

        with pytest.raises(RuntimeConfigRejected):
            await registry.publish(
                tenant_id="local",
                patch=RuntimePatch(
                    "llm",
                    "FakeLLM",
                    {"model": "secret-model", "api_key": "plain-text-secret"},
                ),
            )

        monkeypatch.setenv("TENANT_A_API_KEY", "resolved-secret")
        binding = await registry.publish(
            tenant_id="local",
            patch=RuntimePatch(
                "llm",
                "FakeLLM",
                {
                    "model": "env-model",
                    "api_key": {"$env": "TENANT_A_API_KEY"},
                },
            ),
        )
        lease = await registry.acquire("local")
        assert lease.context.runtime_version == binding.active_version
        assert lease.runtime.config.provide_settings["llm"]["config"]["api_key"] == "resolved-secret"
        await lease.release()
        await registry.shutdown()

    asyncio.run(scenario())


def test_runtime_cleanup_closes_all_unique_component_clients():
    async def scenario():
        closed: list[str] = []

        class SyncClient:
            def __init__(self, name):
                self.name = name

            def close(self):
                closed.append(self.name)

        class AsyncClient:
            async def aclose(self):
                closed.append("embedding")

        shared_llm_client = SyncClient("llm")
        runtime = SimpleNamespace(
            llm=SimpleNamespace(client=shared_llm_client),
            embedding_model=SimpleNamespace(client=AsyncClient()),
            file_loader=object(),
            vector_db=SimpleNamespace(client=SyncClient("vector")),
            web_crawler=SimpleNamespace(client=shared_llm_client),
        )

        await close_runtime(runtime)

        assert closed == ["llm", "embedding", "vector"]

    asyncio.run(scenario())


def test_runtime_cleanup_log_does_not_serialize_exception_details():
    async def scenario():
        class FailingClient:
            def close(self):
                raise RuntimeError("sk-private C:\\Users\\alice\\runtime-secret.txt")

        runtime = SimpleNamespace(
            llm=SimpleNamespace(client=FailingClient()),
            embedding_model=object(),
            file_loader=object(),
            vector_db=object(),
            web_crawler=object(),
            web_search=object(),
        )

        await close_runtime(runtime)

    with patch("deepsearcher.runtime_registry.logger.warning") as warning:
        asyncio.run(scenario())

    template, *values = warning.call_args.args
    rendered = template % tuple(values)
    assert "runtime_component_close_failed" in rendered
    assert "exception_type=RuntimeError" in rendered
    assert "sk-private" not in rendered
    assert "alice" not in rendered
    assert "runtime-secret.txt" not in rendered
