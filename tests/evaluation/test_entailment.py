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
    _classification_metrics,
    dataset_validation_report,
    load_entailment_dataset,
    main,
    run_entailment_calibration,
    run_entailment_evaluation,
    validate_calibration_report,
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

    assert len(dataset.cases) >= 60
    assert set(report["distribution"]["expected_statuses"]) == {
        "entailed",
        "contradicted",
        "unknown",
    }
    assert report["semantic_accuracy"] is None
    assert report["balanced_release_dataset"] is True
    assert all(
        report["distribution"]["expected_statuses"][label] >= 20
        for label in ("entailed", "contradicted", "unknown")
    )
    assert "does not measure model accuracy" in report["note"]


def test_entailment_evaluator_measures_checker_predictions():
    dataset = load_entailment_dataset()
    checker = ExpectedStatusChecker([case.expected_status for case in dataset.cases])

    report = run_entailment_evaluation(dataset, checker)

    assert report["metrics"]["accuracy"] == 1.0
    assert report["checker"]["token_usage"] == 24
    assert report["details"][0]["passed"] is True


def test_classification_metrics_reports_per_label_and_dangerous_false_entailment():
    metrics = _classification_metrics(
        ["entailed", "contradicted", "unknown"],
        ["entailed", "contradicted", "entailed"],
    )

    assert metrics["accuracy"] == 0.6667
    assert metrics["by_label"]["contradicted"]["recall"] == 1.0
    assert metrics["dangerous_false_entailed"] == 1
    assert metrics["confusion"]["unknown"]["entailed"] == 1


def test_calibration_scans_thresholds_and_prefers_higher_threshold_on_tie():
    dataset = load_entailment_dataset()
    checker = ExpectedStatusChecker([case.expected_status for case in dataset.cases])

    report = run_entailment_calibration(
        dataset,
        checker,
        repetitions=3,
        batch_size=32,
        thresholds=(0.8, 0.9, 0.95),
        provider="test",
        model="stable-test-model",
    )

    assert report["release_gate_passed"] is True
    assert report["selected_threshold"] == 0.95
    assert report["selected_metrics"]["macro_f1"] == 1.0
    assert report["stability"]["rate"] == 1.0
    assert report["checker"]["token_usage"] == 72
    assert report["runs"][0]["batch_count"] == 2


class FailedChecker(BaseEntailmentChecker):
    def check(self, items):
        return EntailmentBatchResult(
            checker="failed",
            checker_version="1",
            status="failed",
            token_usage=0,
            error_code="FAILED",
            findings=tuple(
                EntailmentFinding(item.claim_index, "unknown", None, ("FAILED",)) for item in items
            ),
        )


def test_calibration_fails_closed_when_provider_fails():
    report = run_entailment_calibration(
        load_entailment_dataset(), FailedChecker(), repetitions=3, thresholds=(0.5,)
    )

    assert report["release_gate_passed"] is False
    assert report["selected_threshold"] is None
    assert report["checker"]["failed_runs"] == 3


class BatchSensitiveChecker(ExpectedStatusChecker):
    def check(self, items):
        if len(items) > 1:
            return EntailmentBatchResult(
                checker=self.checker_name,
                checker_version=self.checker_version,
                status="failed",
                token_usage=5,
                error_code="FAILED",
                findings=tuple(
                    EntailmentFinding(item.claim_index, "unknown", None, ("FAILED",))
                    for item in items
                ),
            )
        return super().check(items)


def test_calibration_bisects_failed_batches_and_counts_all_attempts():
    dataset = load_entailment_dataset()
    checker = BatchSensitiveChecker([case.expected_status for case in dataset.cases])

    report = run_entailment_calibration(
        dataset,
        checker,
        repetitions=1,
        batch_size=2,
        thresholds=(0.95,),
    )

    assert report["checker"]["failed_runs"] == 0
    assert report["runs"][0]["request_attempts"] > report["runs"][0]["batch_count"]
    assert report["checker"]["token_usage"] == report["runs"][0]["token_usage"]


def test_calibration_rejects_batch_larger_than_checker_contract():
    dataset = load_entailment_dataset()
    checker = ExpectedStatusChecker([case.expected_status for case in dataset.cases])

    with pytest.raises(ValueError, match="batch_size"):
        run_entailment_calibration(dataset, checker, batch_size=33)


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


def test_committed_live_report_satisfies_release_contract():
    report = validate_calibration_report(
        Path("evaluation/results/v0.3-entailment-deepseek-20260812/report.json"),
        load_entailment_dataset(),
    )

    assert report["passed"] is True
