"""Ingestion pipeline engine tests (aligned with ragent IngestionEngine)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from frontend.product.errors import ProductError
from frontend.product.services.ingestion_pipeline import (
    CHUNK_SIZE_WHOLE_DOCUMENT,
    NODE_CHUNK,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_TERMINATED,
    IngestionContext,
    NodeResult,
    default_pipeline_steps,
    execute_chain,
    validate_pipeline,
)


def _context(*, steps=None, display_name="a.txt", collection="coll"):
    document = SimpleNamespace(display_name=display_name, sha256="a" * 64)
    knowledge_base = SimpleNamespace(collection_name=collection)
    return IngestionContext(
        job=None,
        document=document,
        knowledge_base=knowledge_base,
        steps=steps or default_pipeline_steps(),
    )


def _run(coro):
    return asyncio.run(coro)


# ---- validate_pipeline ----


def test_default_steps_validate():
    nodes = validate_pipeline(default_pipeline_steps())
    assert [node.node_id for node in nodes] == ["parse", "chunk", "embed", "index"]


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [
            {"node_id": "a", "node_type": "parse", "settings": {}},
            {"node_id": "a", "node_type": "index", "settings": {}},
        ],
        [
            {"node_id": "p", "node_type": "not_a_type", "settings": {}},
            {"node_id": "i", "node_type": "index", "settings": {}},
        ],
        [
            {"node_id": "p", "node_type": "parse", "settings": "nope"},
            {"node_id": "i", "node_type": "index", "settings": {}},
        ],
    ],
)
def test_validate_pipeline_invalid_basics(steps):
    with pytest.raises(ProductError):
        validate_pipeline(steps)


def test_validate_requires_exactly_one_enabled_terminal_index():
    # no index
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}},
            ]
        )
    # two indexes
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}},
                {"node_id": "i1", "node_type": "index", "settings": {}},
                {"node_id": "i2", "node_type": "index", "settings": {}},
            ]
        )
    # disabled index
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}},
                {
                    "node_id": "i",
                    "node_type": "index",
                    "settings": {},
                    "enabled": False,
                },
            ]
        )
    # index with a next node
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}},
                {
                    "node_id": "i",
                    "node_type": "index",
                    "settings": {},
                    "next_node_id": "x",
                },
                {"node_id": "x", "node_type": "parse", "settings": {}},
            ]
        )


def test_validate_single_start_and_reachability_and_cycle():
    # multiple starts
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "a", "node_type": "parse", "settings": {}},
                {"node_id": "b", "node_type": "chunk", "settings": {}},
                {"node_id": "i", "node_type": "index", "settings": {}},
            ]
        )
    # unreachable node
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}, "next_node_id": "i"},
                {"node_id": "i", "node_type": "index", "settings": {}},
                {"node_id": "orphan", "node_type": "embed", "settings": {}},
            ]
        )
    # cycle
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}, "next_node_id": "c"},
                {"node_id": "c", "node_type": "chunk", "settings": {}, "next_node_id": "p"},
                {"node_id": "i", "node_type": "index", "settings": {}},
            ]
        )
    # missing next reference
    with pytest.raises(ProductError):
        validate_pipeline(
            [
                {"node_id": "p", "node_type": "parse", "settings": {}, "next_node_id": "ghost"},
                {"node_id": "i", "node_type": "index", "settings": {}},
            ]
        )


# ---- execute_chain ----


def test_execute_chain_completes(monkeypatch):
    async def fake_load(**kwargs):
        return {"collection": {"manifest": {"version": 1}}}

    monkeypatch.setattr(
        "frontend.product.services.documents._load_document_into_backend",
        fake_load,
    )
    context = _context()
    result = _run(execute_chain(context))
    assert result.status == STATUS_COMPLETED
    assert result.manifest == {"collection": {"manifest": {"version": 1}}}
    assert [log["node_id"] for log in result.node_logs] == ["parse", "chunk", "embed", "index"]
    assert result.node_outputs["parse"]["loader"] == "TextLoader"


def test_execute_chain_skips_disabled_node(monkeypatch):
    async def fake_load(**kwargs):
        return {"manifest": {}}

    monkeypatch.setattr(
        "frontend.product.services.documents._load_document_into_backend",
        fake_load,
    )
    steps = default_pipeline_steps()
    for step in steps:
        if step["node_type"] == NODE_CHUNK:
            step["enabled"] = False
    context = _context(steps=steps)
    result = _run(execute_chain(context))
    assert result.status == STATUS_COMPLETED
    assert NODE_CHUNK not in result.node_outputs
    assert any("Skipped" in log["message"] for log in result.node_logs)


def test_execute_chain_failure_stops_chain():
    context = _context(display_name="unknown.xyz")
    result = _run(execute_chain(context))
    assert result.status == STATUS_FAILED
    assert result.failure is not None
    assert result.failure.code == "NODE_PARSE_UNSUPPORTED_TYPE"
    assert result.failure.node_id == "parse"
    # chain stopped before chunk/embed/index
    assert [log["node_id"] for log in result.node_logs] == ["parse"]
    assert result.manifest is None


def test_execute_node_normalizes_exception(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(
        "frontend.product.services.documents._load_document_into_backend",
        boom,
    )
    context = _context()
    result = _run(execute_chain(context))
    assert result.status == STATUS_FAILED
    assert result.failure is not None
    assert result.failure.node_id == "index"
    assert result.failure.code == "NODE_INDEX_FAILED"
    assert "backend exploded" in result.failure.message
    assert result.manifest is None


def test_chunk_settings_validation():
    steps = default_pipeline_steps()
    for step in steps:
        if step["node_type"] == NODE_CHUNK:
            step["settings"] = {"chunk_size": CHUNK_SIZE_WHOLE_DOCUMENT}
    assert validate_pipeline(steps)  # -1 sentinel is legal

    bad = default_pipeline_steps()
    for step in bad:
        if step["node_type"] == NODE_CHUNK:
            step["settings"] = {"chunk_size": 0}
    context = _context(steps=bad)
    result = _run(execute_chain(context))
    assert result.status == STATUS_FAILED
    assert result.failure.code == "NODE_CHUNK_INVALID_SETTINGS"


def test_terminate_stops_chain_without_manifest(monkeypatch):
    async def fake_load(**kwargs):
        raise AssertionError("IndexNode must not run after terminate")

    monkeypatch.setattr(
        "frontend.product.services.documents._load_document_into_backend",
        fake_load,
    )
    steps = default_pipeline_steps()
    # Replace embed with a terminating node type via settings is not possible;
    # instead run a chain where embed node terminates by monkeypatching its execute.
    from frontend.product.services import ingestion_pipeline as engine

    original_execute = engine.EmbedNode.execute

    async def terminate_execute(self, context, config):
        return NodeResult.terminate("stop here")

    engine.EmbedNode.execute = terminate_execute
    try:
        context = _context(steps=steps)
        result = _run(execute_chain(context))
    finally:
        engine.EmbedNode.execute = original_execute
    assert result.status == STATUS_TERMINATED
    assert result.manifest is None
    assert result.failure is None
