import json
from pathlib import Path

import pytest

from evaluation.risk_profile import (
    DEFAULT_DATASET,
    load_risk_profile_dataset,
    main,
    run_risk_profile_evaluation,
)


def test_risk_profile_gold_dataset_passes_all_cases():
    report = run_risk_profile_evaluation(load_risk_profile_dataset())

    assert report["metrics"]["failed"] == 0
    assert report["metrics"]["accuracy"] == 1.0
    assert report["dataset"]["case_count"] >= 14


def test_risk_profile_dataset_rejects_duplicate_ids(tmp_path: Path):
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case id"):
        load_risk_profile_dataset(path)


def test_risk_profile_cli_writes_machine_readable_report(tmp_path: Path):
    output = tmp_path / "risk-profile.json"

    assert main(["--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["metrics"]["failed"] == 0
