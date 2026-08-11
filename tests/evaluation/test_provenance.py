import json

import pytest

from evaluation.provenance import DEFAULT_DATASET, evaluate_dataset


def test_provenance_dataset_gate_passes_all_invariants():
    report = evaluate_dataset(DEFAULT_DATASET)

    assert report["passed"] is True
    assert report["case_count"] == 23
    assert report["passed_count"] == 23


def test_provenance_dataset_rejects_duplicate_case_ids(tmp_path):
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        evaluate_dataset(path)
