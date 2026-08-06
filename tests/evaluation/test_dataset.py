import json
from pathlib import Path

import pytest

from evaluation.dataset import DatasetValidationError, load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "milvus_v1.json"


def test_load_versioned_dataset():
    dataset = load_dataset(DATASET)
    assert dataset.schema_version == 1
    assert dataset.version == "1.0.0"
    assert len(dataset.samples) == 30
    assert sum(sample.answerable for sample in dataset.samples) == 25
    assert len(dataset.sha256) == 64


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
