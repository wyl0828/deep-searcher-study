import json

import pytest

from evaluation.evaluator_manifest import (
    EVALUATOR_COLLECTION,
    assert_evaluator_collection,
    build_evaluator_manifest,
)


def test_manifest_records_dataset_config_and_corpus_identity(tmp_path):
    root = tmp_path
    dataset = root / "evaluation" / "datasets" / "workspace_v2.json"
    quality_gate = root / "evaluation" / "quality_gate.json"
    runtime_config = root / "deepsearcher" / "config.yaml"
    source = root / "output" / "pdf" / "source.pdf"
    for path in (dataset, quality_gate, runtime_config, source):
        path.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"pdf")
    dataset.write_text(
        json.dumps({"source": {"document": "source.pdf", "path": "output/pdf/source.pdf"}}),
        encoding="utf-8",
    )
    quality_gate.write_text("{}", encoding="utf-8")
    runtime_config.write_text("provider: test\n", encoding="utf-8")

    manifest = build_evaluator_manifest(
        root=root,
        dataset_path=dataset,
        quality_gate_path=quality_gate,
        runtime_config_path=runtime_config,
        application_commit="a" * 40,
        corpus_commit="b" * 40,
        execution_role="local_canonical",
        execution_node="test-node",
    )

    assert manifest["collection"] == EVALUATOR_COLLECTION
    assert manifest["application_commit"] == "a" * 40
    assert manifest["corpus_commit"] == "b" * 40
    assert manifest["dataset"][0]["path"] == "evaluation/datasets/workspace_v2.json"
    assert manifest["configs"][0]["path"] == "evaluation/quality_gate.json"
    assert manifest["files"][0]["path"] == "output/pdf/source.pdf"
    assert manifest["execution"]["prepare"] == "reset_owned_collection"


def test_manifest_rejects_non_owned_collection():
    with pytest.raises(ValueError, match="eval_workspace_v2"):
        assert_evaluator_collection("eval_other")
