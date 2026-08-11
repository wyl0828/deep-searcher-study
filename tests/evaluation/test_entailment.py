import json
from pathlib import Path

import pytest

from deepsearcher.entailment import (
    BaseEntailmentChecker,
    EntailmentBatchResult,
    EntailmentFinding,
)
from evaluation.entailment import (
    DEFAULT_DATASET,
    dataset_validation_report,
    load_entailment_dataset,
    main,
    run_entailment_evaluation,
)


class ExpectedStatusChecker(BaseEntailmentChecker):
    checker_name = "test_expected_status"
    checker_version = "1.0.0"

    def __init__(self, statuses):
        self.statuses = statuses

    def check(self, items):
        return EntailmentBatchResult(
            checker=self.checker_name,
            checker_version=self.checker_version,
            status="completed",
            token_usage=12,
            findings=tuple(
                EntailmentFinding(
                    claim_index=item.claim_index,
                    status=self.statuses[item.claim_index - 1],
                    confidence=0.95,
                    reason_codes=("TEST_ONLY",),
                )
                for item in items
            ),
        )


def test_entailment_dataset_is_balanced_and_validation_is_honest():
    dataset = load_entailment_dataset()
    report = dataset_validation_report(dataset)

    assert len(dataset.cases) >= 12
    assert set(report["distribution"]["expected_statuses"]) == {
        "entailed",
        "contradicted",
        "unknown",
    }
    assert report["semantic_accuracy"] is None
    assert "does not measure model accuracy" in report["note"]


def test_entailment_evaluator_measures_checker_predictions():
    dataset = load_entailment_dataset()
    checker = ExpectedStatusChecker([case.expected_status for case in dataset.cases])

    report = run_entailment_evaluation(dataset, checker)

    assert report["metrics"]["accuracy"] == 1.0
    assert report["checker"]["token_usage"] == 12
    assert report["details"][0]["passed"] is True


def test_entailment_dataset_rejects_duplicate_ids(tmp_path: Path):
    payload = json.loads(DEFAULT_DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate case id"):
        load_entailment_dataset(path)


def test_entailment_validation_cli_writes_non_accuracy_report(tmp_path: Path):
    output = tmp_path / "entailment.json"

    assert main(["--mode", "validate", "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["semantic_accuracy"] is None
    assert report["mode"] == "validate"
