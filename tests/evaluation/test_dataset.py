import json
from pathlib import Path

import pytest

from evaluation.dataset import DatasetValidationError, load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "milvus_v1.json"
WORKSPACE_DATASET = (
    Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "workspace_v2.json"
)


def write_v2(tmp_path, *, mutate=None):
    sources = [
        {
            "document": "one.pdf",
            "path": "examples/data/one.pdf",
            "sha256": "1" * 64,
        },
        {
            "document": "two.pdf",
            "path": "examples/data/two.pdf",
            "sha256": "2" * 64,
        },
    ]
    raw = {
        "schema_version": 2,
        "dataset_id": "multi-v2",
        "version": "2.0.0",
        "description": "multi source fixture",
        "sources": sources,
        "samples": [
            {
                "id": "multi-001",
                "question": "两份资料分别说了什么？",
                "answerable": True,
                "reference_answer": "one and two",
                "evidence": [
                    {"document": "one.pdf", "page": 1},
                    {"document": "two.pdf", "page": 2},
                ],
                "criteria": [["one"], ["two"]],
                "tags": ["跨文档"],
                "difficulty": "hard",
                "history": [{"role": "user", "content": "先介绍第一份资料"}],
                "context_dependent": True,
                "standalone_question": "结合第一份资料，两份资料分别说了什么？",
            }
        ],
    }
    if mutate:
        mutate(raw)
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_versioned_dataset():
    dataset = load_dataset(DATASET)
    assert dataset.schema_version == 1
    assert dataset.version == "1.0.0"
    assert len(dataset.samples) == 30
    assert sum(sample.answerable for sample in dataset.samples) == 25
    assert len(dataset.sha256) == 64
    assert len(dataset.sources) == 1


def test_load_multi_source_v2_dataset(tmp_path):
    dataset = load_dataset(write_v2(tmp_path))
    assert dataset.schema_version == 2
    assert [source.document for source in dataset.sources] == ["one.pdf", "two.pdf"]
    assert dataset.samples[0].difficulty == "hard"
    assert dataset.samples[0].history == (("user", "先介绍第一份资料"),)
    assert dataset.samples[0].context_dependent is True
    assert dataset.samples[0].standalone_question == "结合第一份资料，两份资料分别说了什么？"


def test_history_requires_contextualization_labels(tmp_path):
    path = write_v2(
        tmp_path,
        mutate=lambda raw: raw["samples"][0].pop("standalone_question"),
    )
    with pytest.raises(DatasetValidationError, match="standalone_question"):
        load_dataset(path)


def test_workspace_v2_has_pinned_multi_document_business_coverage():
    dataset = load_dataset(WORKSPACE_DATASET)
    assert dataset.schema_version == 2
    assert len(dataset.sources) == 3
    assert len(dataset.samples) == 72
    assert sum(not sample.answerable for sample in dataset.samples) == 7
    assert sum(bool(sample.history) for sample in dataset.samples) == 12
    assert sum(
        bool(sample.history) and not sample.context_dependent for sample in dataset.samples
    ) == 2
    assert sum(len({target.document for target in sample.evidence}) > 1 for sample in dataset.samples) >= 7
    assert {"跨文档", "无答案", "引用", "并发"}.issubset(
        {tag for sample in dataset.samples for tag in sample.tags}
    )


def test_v2_rejects_unknown_evidence_document(tmp_path):
    path = write_v2(
        tmp_path,
        mutate=lambda raw: raw["samples"][0]["evidence"][0].update(
            {"document": "missing.pdf"}
        ),
    )
    with pytest.raises(DatasetValidationError, match="unknown documents"):
        load_dataset(path)


def test_rejects_answerable_sample_without_evidence(tmp_path):
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["samples"][0]["evidence"] = []
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="require evidence"):
        load_dataset(path)


def test_rejects_duplicate_sample_ids(tmp_path):
    raw = json.loads(DATASET.read_text(encoding="utf-8"))
    raw["samples"][1]["id"] = raw["samples"][0]["id"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="duplicate sample id"):
        load_dataset(path)
