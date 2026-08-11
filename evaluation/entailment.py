"""Validate or run the hand-authored semantic-entailment gold dataset."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepsearcher.configuration import Configuration, build_runtime
from deepsearcher.entailment import (
    BaseEntailmentChecker,
    EntailmentInput,
    LLMEntailmentChecker,
)
from deepsearcher.runtime_registry import close_runtime

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "entailment_v1.json"
ENTAILMENT_EVAL_VERSION = "1.0.0"


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
        if expected_status not in {"entailed", "contradicted", "unknown"}:
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
    return {
        "report_schema_version": 1,
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
            "expected_statuses": dict(
                sorted(Counter(case.expected_status for case in dataset.cases).items())
            ),
        },
        "semantic_accuracy": None,
        "note": "Dataset validation does not measure model accuracy; run --mode live.",
    }


def run_entailment_evaluation(
    dataset: EntailmentGoldDataset,
    checker: BaseEntailmentChecker,
) -> dict[str, Any]:
    inputs = [
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
    result = checker.check(inputs)
    by_index = {finding.claim_index: finding for finding in result.findings}
    details = []
    for index, case in enumerate(dataset.cases, start=1):
        finding = by_index.get(index)
        actual = finding.status if finding is not None else "unknown"
        details.append(
            {
                "id": case.id,
                "category": case.category,
                "expected_status": case.expected_status,
                "actual_status": actual,
                "confidence": finding.confidence if finding is not None else None,
                "passed": actual == case.expected_status,
            }
        )
    passed = sum(detail["passed"] for detail in details)
    confusion: dict[str, dict[str, int]] = {}
    for detail in details:
        expected = detail["expected_status"]
        actual = detail["actual_status"]
        confusion.setdefault(expected, {})[actual] = (
            confusion.setdefault(expected, {}).get(actual, 0) + 1
        )
    return {
        "report_schema_version": 1,
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
            "name": result.checker,
            "version": result.checker_version,
            "status": result.status,
            "token_usage": result.token_usage,
            "error_code": result.error_code,
        },
        "metrics": {
            "passed": passed,
            "failed": len(details) - passed,
            "accuracy": round(passed / len(details), 4),
            "confusion": confusion,
        },
        "details": details,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--mode", choices=("validate", "live"), default="validate")
    parser.add_argument("--min-confidence", type=float, default=0.8)
    parser.add_argument("--minimum-accuracy", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    dataset = load_entailment_dataset(args.dataset)
    runtime = None
    if args.mode == "validate":
        report = dataset_validation_report(dataset)
    else:
        runtime = build_runtime(Configuration())
        checker = LLMEntailmentChecker(
            runtime.llm,
            min_confidence=args.min_confidence,
        )
        report = run_entailment_evaluation(dataset, checker)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.mode == "validate":
        print(json.dumps(report["distribution"], ensure_ascii=False))
        return_code = 0
    else:
        print(json.dumps(report["metrics"], ensure_ascii=False))
        minimum = args.minimum_accuracy
        return_code = 1 if minimum is not None and report["metrics"]["accuracy"] < minimum else 0
    if runtime is not None:
        asyncio.run(close_runtime(runtime))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
