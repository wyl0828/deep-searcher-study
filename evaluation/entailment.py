"""Validate and calibrate the hand-authored semantic-entailment gold dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from deepsearcher.configuration import Configuration, ModuleFactory
from deepsearcher.entailment import (
    MAX_ENTAILMENT_CLAIMS,
    BaseEntailmentChecker,
    EntailmentInput,
    LLMEntailmentChecker,
)
from evaluation.benchmark import load_env_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "entailment_v1.json"
DEFAULT_CONFIG = ROOT / "deepsearcher" / "config.yaml"
DEFAULT_LIVE_REPORT = (
    ROOT / "evaluation" / "results" / "v0.3-entailment-deepseek-20260812" / "report.json"
)
ENTAILMENT_EVAL_VERSION = "1.2.0"
LABELS = ("entailed", "contradicted", "unknown")
DEFAULT_THRESHOLDS = tuple(round(value / 100, 2) for value in range(50, 96, 5))


@dataclass(frozen=True)
class EntailmentGoldCase:
    id: str
    category: str
    claim: str
    evidence: tuple[str, ...]
    expected_status: str


@dataclass(frozen=True)
class EntailmentGoldDataset:
    dataset_id: str
    version: str
    sha256: str
    cases: tuple[EntailmentGoldCase, ...]


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def load_entailment_dataset(path: Path = DEFAULT_DATASET) -> EntailmentGoldDataset:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported entailment dataset schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    cases: list[EntailmentGoldCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = _required_text(raw_case.get("id"), f"cases[{index}].id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        raw_evidence = raw_case.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ValueError(f"cases[{index}].evidence must be a non-empty array")
        expected_status = _required_text(
            raw_case.get("expected_status"), f"cases[{index}].expected_status"
        )
        if expected_status not in LABELS:
            raise ValueError(f"unsupported expected status: {expected_status}")
        cases.append(
            EntailmentGoldCase(
                id=case_id,
                category=_required_text(raw_case.get("category"), f"cases[{index}].category"),
                claim=_required_text(raw_case.get("claim"), f"cases[{index}].claim"),
                evidence=tuple(
                    _required_text(item, f"cases[{index}].evidence") for item in raw_evidence
                ),
                expected_status=expected_status,
            )
        )
    return EntailmentGoldDataset(
        dataset_id=_required_text(payload.get("id"), "id"),
        version=_required_text(payload.get("version"), "version"),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def dataset_validation_report(dataset: EntailmentGoldDataset) -> dict[str, Any]:
    expected = Counter(case.expected_status for case in dataset.cases)
    balanced = len(dataset.cases) >= 60 and all(expected[label] >= 20 for label in LABELS)
    return {
        "report_schema_version": 2,
        "entailment_eval_version": ENTAILMENT_EVAL_VERSION,
        "mode": "validate",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "case_count": len(dataset.cases),
        },
        "distribution": {
            "categories": dict(sorted(Counter(case.category for case in dataset.cases).items())),
            "expected_statuses": dict(sorted(expected.items())),
        },
        "balanced_release_dataset": balanced,
        "semantic_accuracy": None,
        "note": "Dataset validation does not measure model accuracy; run --mode live.",
    }


def _inputs(dataset: EntailmentGoldDataset) -> list[EntailmentInput]:
    return [
        EntailmentInput(
            claim_index=index,
            claim_text=case.claim,
            evidence=tuple(
                (f"E{evidence_index}", text)
                for evidence_index, text in enumerate(case.evidence, start=1)
            ),
        )
        for index, case in enumerate(dataset.cases, start=1)
    ]


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _threshold_status(status: str, confidence: float | None, threshold: float) -> str:
    if status == "unknown" or confidence is None or confidence < threshold:
        return "unknown"
    return status


def _classification_metrics(expected: Sequence[str], actual: Sequence[str]) -> dict[str, Any]:
    confusion = {label: {predicted: 0 for predicted in LABELS} for label in LABELS}
    for wanted, predicted in zip(expected, actual, strict=True):
        confusion[wanted][predicted] += 1
    by_label: dict[str, dict[str, float | int]] = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in LABELS if other != label)
        fn = sum(confusion[label][other] for other in LABELS if other != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        by_label[label] = {
            "support": sum(confusion[label].values()),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    correct = sum(wanted == predicted for wanted, predicted in zip(expected, actual, strict=True))
    dangerous = sum(
        wanted in {"contradicted", "unknown"} and predicted == "entailed"
        for wanted, predicted in zip(expected, actual, strict=True)
    )
    return {
        "accuracy": round(correct / len(expected), 4),
        "macro_precision": round(mean(float(by_label[label]["precision"]) for label in LABELS), 4),
        "macro_recall": round(mean(float(by_label[label]["recall"]) for label in LABELS), 4),
        "macro_f1": round(mean(float(by_label[label]["f1"]) for label in LABELS), 4),
        "dangerous_false_entailed": dangerous,
        "by_label": by_label,
        "confusion": confusion,
    }


def _passes_release_gate(metrics: Mapping[str, Any], stability_rate: float, failures: int) -> bool:
    return all(
        (
            float(metrics["accuracy"]) >= 0.85,
            float(metrics["macro_f1"]) >= 0.85,
            float(metrics["by_label"]["contradicted"]["recall"]) >= 0.90,
            int(metrics["dangerous_false_entailed"]) == 0,
            stability_rate >= 0.90,
            failures == 0,
        )
    )


def validate_calibration_report(
    report_path: Path,
    dataset: EntailmentGoldDataset,
) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = {
        "schema": report.get("report_schema_version") == 2,
        "mode": report.get("mode") == "live",
        "dataset_id": report.get("dataset", {}).get("id") == dataset.dataset_id,
        "dataset_version": report.get("dataset", {}).get("version") == dataset.version,
        "dataset_sha256": report.get("dataset", {}).get("sha256") == dataset.sha256,
        "case_count": report.get("dataset", {}).get("case_count") == len(dataset.cases),
        "checker_version": report.get("checker", {}).get("version") == "1.2.0",
        "prompt_version": report.get("checker", {}).get("prompt_version") == "1.2.0",
        "provider": report.get("checker", {}).get("provider") == "DeepSeek",
        "model": report.get("checker", {}).get("model") == "deepseek-v4-flash",
        "three_repetitions": report.get("checker", {}).get("repetitions") == 3,
        "no_failed_runs": report.get("checker", {}).get("failed_runs") == 0,
        "stability": float(report.get("stability", {}).get("rate") or 0) >= 0.90,
        "selected_threshold": report.get("selected_threshold") == 0.90,
        "release_gate": report.get("release_gate_passed") is True,
        "git_identity": bool(
            re.fullmatch(
                r"[0-9a-f]{40}",
                str(report.get("environment", {}).get("git_commit") or ""),
            )
        ),
    }
    metrics = report.get("selected_metrics") or {}
    checks["strict_metrics"] = _passes_release_gate(
        metrics,
        float(report.get("stability", {}).get("rate") or 0),
        int(report.get("checker", {}).get("failed_runs") or 0),
    )
    return {"passed": all(checks.values()), "checks": checks}


def run_entailment_calibration(
    dataset: EntailmentGoldDataset,
    checker: BaseEntailmentChecker,
    *,
    repetitions: int = 3,
    batch_size: int = 8,
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    if not 1 <= batch_size <= MAX_ENTAILMENT_CLAIMS:
        raise ValueError(f"batch_size must be between 1 and {MAX_ENTAILMENT_CLAIMS}")
    inputs = _inputs(dataset)
    runs = []
    failures = 0
    total_tokens = 0
    for run_index in range(repetitions):
        batch_results = []
        request_attempts = 0
        run_attempt_tokens = 0
        pending = [
            inputs[start : start + batch_size] for start in range(0, len(inputs), batch_size)
        ]
        while pending:
            batch = pending.pop(0)
            result = checker.check(batch)
            request_attempts += 1
            run_attempt_tokens += result.token_usage
            total_tokens += result.token_usage
            if result.status == "failed" and len(batch) > 1:
                midpoint = len(batch) // 2
                pending[0:0] = [batch[:midpoint], batch[midpoint:]]
                continue
            batch_results.append(result)
        run_failed = any(result.status == "failed" for result in batch_results)
        failures += run_failed
        run_tokens = run_attempt_tokens
        by_index = {
            finding.claim_index: finding for result in batch_results for finding in result.findings
        }
        predictions = []
        for index, case in enumerate(dataset.cases, start=1):
            finding = by_index.get(index)
            predictions.append(
                {
                    "id": case.id,
                    "expected_status": case.expected_status,
                    "raw_status": finding.status if finding is not None else "unknown",
                    "confidence": finding.confidence if finding is not None else None,
                }
            )
        runs.append(
            {
                "run": run_index + 1,
                "status": "failed"
                if run_failed
                else (
                    "partial"
                    if any(result.status == "partial" for result in batch_results)
                    else "completed"
                ),
                "batch_count": len(batch_results),
                "request_attempts": request_attempts,
                "token_usage": run_tokens,
                "error_code": ("ENTAILMENT_CHECKER_FAILED" if run_failed else None),
                "predictions": predictions,
            }
        )

    stable = 0
    for case_index in range(len(dataset.cases)):
        labels = {run["predictions"][case_index]["raw_status"] for run in runs}
        stable += len(labels) == 1
    stability_rate = round(stable / len(dataset.cases), 4)
    expected = [case.expected_status for _ in runs for case in dataset.cases]
    scans = []
    for threshold in thresholds:
        actual = [
            _threshold_status(prediction["raw_status"], prediction["confidence"], threshold)
            for run in runs
            for prediction in run["predictions"]
        ]
        metrics = _classification_metrics(expected, actual)
        metrics["threshold"] = round(float(threshold), 2)
        metrics["release_gate_passed"] = _passes_release_gate(metrics, stability_rate, failures)
        scans.append(metrics)
    eligible = [item for item in scans if item["release_gate_passed"]]
    selected = max(
        eligible,
        key=lambda item: (float(item["macro_f1"]), float(item["threshold"])),
        default=None,
    )
    return {
        "report_schema_version": 2,
        "entailment_eval_version": ENTAILMENT_EVAL_VERSION,
        "mode": "live",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "case_count": len(dataset.cases),
        },
        "checker": {
            "name": checker.checker_name,
            "version": checker.checker_version,
            "prompt_version": checker.checker_version,
            "provider": provider,
            "model": model,
            "base_confidence": 0.5,
            "repetitions": repetitions,
            "batch_size": batch_size,
            "token_usage": total_tokens,
            "failed_runs": failures,
        },
        "environment": {"git_commit": _git_commit()},
        "stability": {"stable_samples": stable, "rate": stability_rate},
        "threshold_scan": scans,
        "selected_threshold": selected["threshold"] if selected else None,
        "release_gate_passed": selected is not None,
        "selected_metrics": selected,
        "runs": runs,
    }


def run_entailment_evaluation(
    dataset: EntailmentGoldDataset,
    checker: BaseEntailmentChecker,
) -> dict[str, Any]:
    """Backward-compatible one-run evaluation at the checker's configured threshold."""
    threshold = float(getattr(checker, "min_confidence", 0.5))
    report = run_entailment_calibration(
        dataset,
        checker,
        repetitions=1,
        batch_size=MAX_ENTAILMENT_CLAIMS,
        thresholds=(threshold,),
    )
    selected = report["threshold_scan"][0]
    details = []
    for case, prediction in zip(dataset.cases, report["runs"][0]["predictions"], strict=True):
        actual = _threshold_status(prediction["raw_status"], prediction["confidence"], threshold)
        details.append(
            {
                "id": case.id,
                "category": case.category,
                "expected_status": case.expected_status,
                "actual_status": actual,
                "confidence": prediction["confidence"],
                "passed": actual == case.expected_status,
            }
        )
    return {
        **report,
        "checker": {
            **report["checker"],
            "status": report["runs"][0]["status"],
            "token_usage": report["runs"][0]["token_usage"],
            "error_code": report["runs"][0]["error_code"],
        },
        "metrics": {
            **selected,
            "passed": sum(item["passed"] for item in details),
            "failed": sum(not item["passed"] for item in details),
        },
        "details": details,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--mode", choices=("validate", "live", "report"), default="validate")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--minimum-accuracy", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path, default=DEFAULT_LIVE_REPORT)
    args = parser.parse_args(argv)
    dataset = load_entailment_dataset(args.dataset)
    if args.mode == "validate":
        report = dataset_validation_report(dataset)
        return_code = 0 if report["balanced_release_dataset"] else 1
        print(json.dumps(report["distribution"], ensure_ascii=False))
    elif args.mode == "report":
        report = validate_calibration_report(args.report, dataset)
        print(json.dumps(report, ensure_ascii=False))
        return_code = 0 if report["passed"] else 1
    else:
        if args.repetitions < 1:
            raise SystemExit("repetitions must be positive")
        load_env_file(args.env_file)
        config = Configuration(str(args.config))
        llm_config = config.get_provider_config("llm")
        llm = ModuleFactory(config).create_llm()
        checker = LLMEntailmentChecker(llm, min_confidence=0.5)
        report = run_entailment_calibration(
            dataset,
            checker,
            repetitions=args.repetitions,
            batch_size=args.batch_size,
            provider=str(llm_config.get("provider") or "unknown"),
            model=str((llm_config.get("config") or {}).get("model") or "unknown"),
        )
        print(
            json.dumps(
                {
                    "release_gate_passed": report["release_gate_passed"],
                    "selected_threshold": report["selected_threshold"],
                    "selected_metrics": report["selected_metrics"],
                },
                ensure_ascii=False,
            )
        )
        return_code = 0 if report["release_gate_passed"] else 1
        if args.minimum_accuracy is not None:
            selected = report["selected_metrics"] or {}
            if float(selected.get("accuracy") or 0) < args.minimum_accuracy:
                return_code = 1
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
