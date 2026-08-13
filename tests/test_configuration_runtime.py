from types import SimpleNamespace

import pytest

from deepsearcher import configuration
from deepsearcher.llm.routing import RoutingLLM


class FakeConfig:
    query_settings = {"max_iter": 3}


def test_module_factory_keeps_single_llm_configuration(monkeypatch):
    sentinel = object()
    config = SimpleNamespace(
        provide_settings={"llm": {"provider": "Fake", "config": {"model": "one"}}}
    )
    factory = configuration.ModuleFactory(config)
    monkeypatch.setattr(factory, "_create_module_instance", lambda *_args: sentinel)

    assert factory.create_llm() is sentinel


def test_module_factory_builds_ordered_chat_candidates(monkeypatch):
    import deepsearcher.llm as llm_module

    class FakeProvider:
        def __init__(self, model):
            self.model = model

    monkeypatch.setattr(llm_module, "FakeProvider", FakeProvider, raising=False)
    config = SimpleNamespace(
        provide_settings={
            "llm": {
                "provider": "FakeProvider",
                "config": {"model": "legacy"},
                "candidates": [
                    {"provider": "FakeProvider", "config": {"model": "first"}},
                    {"provider": "FakeProvider", "config": {"model": "second"}},
                ],
                "routing": {"failure_threshold": 2},
            }
        }
    )

    result = configuration.ModuleFactory(config).create_llm()

    assert isinstance(result, RoutingLLM)
    assert [candidate.model for candidate in result.candidates] == ["first", "second"]


def test_configuration_reads_utf8_yaml_on_windows(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """# 中文配置说明
provide_settings: {}
query_settings: {}
load_settings: {}
""",
        encoding="utf-8",
    )

    loaded = configuration.Configuration(str(config_path))

    assert loaded.provide_settings == {}


def install_fake_components(monkeypatch):
    components = {
        "llm": object(),
        "embedding": object(),
        "file_loader": object(),
        "web_crawler": object(),
        "web_search": object(),
        "vector_db": object(),
    }
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_llm",
        lambda _self: components["llm"],
    )
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_embedding",
        lambda _self: components["embedding"],
    )
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_file_loader",
        lambda _self: components["file_loader"],
    )
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_web_crawler",
        lambda _self: components["web_crawler"],
    )
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_web_search",
        lambda _self: components["web_search"],
    )
    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_vector_db",
        lambda _self: components["vector_db"],
    )
    monkeypatch.setattr(
        configuration,
        "_load_agent_classes",
        lambda: (
            lambda **kwargs: SimpleNamespace(kind="deep", kwargs=kwargs),
            lambda **kwargs: SimpleNamespace(kind="chain", kwargs=kwargs),
            lambda **kwargs: SimpleNamespace(kind="router", kwargs=kwargs),
            lambda **kwargs: SimpleNamespace(kind="naive", kwargs=kwargs),
        ),
    )
    return components


def test_build_runtime_returns_isolated_components_without_publishing_globals(
    monkeypatch,
):
    components = install_fake_components(monkeypatch)
    previous_vector_db = configuration.vector_db

    runtime = configuration.build_runtime(FakeConfig())

    assert runtime.llm is components["llm"]
    assert runtime.embedding_model is components["embedding"]
    assert runtime.file_loader is components["file_loader"]
    assert runtime.web_crawler is components["web_crawler"]
    assert runtime.web_search is components["web_search"]
    assert runtime.vector_db is components["vector_db"]
    assert runtime.default_searcher.kind == "router"
    routed_agents = runtime.default_searcher.kwargs["rag_agents"]
    assert [agent.kind for agent in routed_agents] == ["deep", "chain", "naive"]
    assert runtime.default_searcher.kwargs["fallback_agent_index"] == 2
    chain = runtime.default_searcher.kwargs["rag_agents"][1]
    assert chain.kwargs["early_stopping"] is True
    assert chain.kwargs["min_evidence_for_stop"] == 2
    assert runtime.naive_rag.kind == "naive"
    assert runtime.naive_rag is routed_agents[2]
    assert runtime.entailment_checker is None
    assert configuration.vector_db is previous_vector_db


def test_build_runtime_allows_an_explicit_default_answer_agent(monkeypatch):
    install_fake_components(monkeypatch)
    config = FakeConfig()
    config.query_settings = {"max_iter": 3, "default_agent": "chain_of_rag"}

    runtime = configuration.build_runtime(config)

    assert runtime.default_searcher.kwargs["fallback_agent_index"] == 1


def test_build_runtime_can_enable_the_shared_llm_entailment_checker(monkeypatch):
    components = install_fake_components(monkeypatch)
    config = FakeConfig()
    config.query_settings = {
        "max_iter": 3,
        "trust": {"entailment": {"enabled": True, "min_confidence": 0.9}},
    }

    runtime = configuration.build_runtime(config)

    assert runtime.entailment_checker is not None
    assert runtime.entailment_checker.llm is components["llm"]
    assert runtime.entailment_checker.min_confidence == 0.9


def test_build_runtime_rejects_invalid_trust_temporal_timezone_before_provider_startup(
    monkeypatch,
):
    install_fake_components(monkeypatch)
    config = FakeConfig()
    config.query_settings = {
        "max_iter": 3,
        "trust": {"temporal": {"timezone": "Mars/Olympus"}},
    }

    with pytest.raises(configuration.RuntimeInitializationError) as exc_info:
        configuration.build_runtime(config)

    assert exc_info.value.component == "trust_temporal"
    assert exc_info.value.code == "RUNTIME_INITIALIZATION_FAILED"
    assert "Mars/Olympus" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_init_config_keeps_legacy_global_api_compatible(monkeypatch):
    components = install_fake_components(monkeypatch)
    for name in (
        "module_factory",
        "llm",
        "embedding_model",
        "file_loader",
        "web_crawler",
        "web_search",
        "vector_db",
        "default_searcher",
        "naive_rag",
        "entailment_checker",
    ):
        monkeypatch.setattr(configuration, name, getattr(configuration, name))

    runtime = configuration.init_config(FakeConfig())

    assert configuration.llm is runtime.llm
    assert configuration.embedding_model is runtime.embedding_model
    assert configuration.file_loader is runtime.file_loader
    assert configuration.web_crawler is runtime.web_crawler
    assert configuration.web_search is runtime.web_search
    assert configuration.vector_db is components["vector_db"]
    assert configuration.default_searcher is runtime.default_searcher
    assert configuration.naive_rag is runtime.naive_rag
    assert configuration.entailment_checker is runtime.entailment_checker


def test_build_runtime_reports_the_failing_component_without_leaking_details(
    monkeypatch,
):
    install_fake_components(monkeypatch)

    def fail_vector_db(_self):
        raise RuntimeError("http://private-milvus:19530?token=secret")

    monkeypatch.setattr(
        configuration.ModuleFactory,
        "create_vector_db",
        fail_vector_db,
    )

    with pytest.raises(configuration.RuntimeInitializationError) as exc_info:
        configuration.build_runtime(FakeConfig())

    assert exc_info.value.component == "vector_db"
    assert exc_info.value.code == "RUNTIME_INITIALIZATION_FAILED"
    assert "private-milvus" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
