import json

import pytest

from evaluation.trust_consistency import (
    DEFAULT_DATASET,
    load_trust_consistency_dataset,
    main,
    run_trust_consistency_evaluation,
)


def test_hand_authored_trust_consistency_gold_set_passes():
    dataset = load_trust_consistency_dataset()

    report = run_trust_consistency_evaluation(dataset)

    assert len(dataset.cases) >= 58
    assert report["dataset"]["sha256"] == dataset.sha256
    assert report["metrics"]["failed"] == 0
    assert report["metrics"]["accuracy"] == 1
    assert set(report["metrics"]["by_category"]) >= {
        "quantity",
        "date",
        "version",
        "range",
        "condition",
        "condition-relation",
        "prerequisite",
        "exception",
        "permission",
        "entity-quantity",
        "relative-time",
        "freshness",
        "negation",
        "negative-control",
        "known-boundary",
    }


def test_dataset_rejects_duplicate_case_ids(tmp_path):
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case id"):
        load_trust_consistency_dataset(path)


def test_cli_writes_machine_readable_report(tmp_path):
    output = tmp_path / "trust-report.json"

    exit_code = main(["--output", str(output)])

    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["trust_eval_version"] == "1.6.0"
    assert report["metrics"]["failed"] == 0
