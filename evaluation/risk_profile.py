"""Evaluate deterministic query risk profiles against a hand-authored gold set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepsearcher.risk import classify_query_risk

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "risk_profile_v1.json"
RISK_PROFILE_EVAL_VERSION = "1.0.0"


@dataclass(frozen=True)
class RiskProfileCase:
    id: str
    category: str
    query: str
    expected_risk_level: str
    expected_query_type: str
    expected_minimum_sources: int


@dataclass(frozen=True)
class RiskProfileDataset:
    dataset_id: str
    version: str
    sha256: str
    cases: tuple[RiskProfileCase, ...]


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def load_risk_profile_dataset(path: Path = DEFAULT_DATASET) -> RiskProfileDataset:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported risk profile dataset schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    cases: list[RiskProfileCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = _required_text(raw_case.get("id"), f"cases[{index}].id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        risk_level = _required_text(
            raw_case.get("expected_risk_level"),
            f"cases[{index}].expected_risk_level",
        )
        if risk_level not in {"low", "medium", "high"}:
            raise ValueError(f"unsupported expected risk level: {risk_level}")
        minimum_sources = raw_case.get("expected_minimum_sources")
        if (
            isinstance(minimum_sources, bool)
            or not isinstance(minimum_sources, int)
            or minimum_sources < 1
        ):
            raise ValueError(f"cases[{index}].expected_minimum_sources must be positive")
        cases.append(
            RiskProfileCase(
                id=case_id,
                category=_required_text(raw_case.get("category"), f"cases[{index}].category"),
                query=_required_text(raw_case.get("query"), f"cases[{index}].query"),
                expected_risk_level=risk_level,
                expected_query_type=_required_text(
                    raw_case.get("expected_query_type"),
                    f"cases[{index}].expected_query_type",
                ),
                expected_minimum_sources=minimum_sources,
            )
        )
    return RiskProfileDataset(
        dataset_id=_required_text(payload.get("id"), "id"),
        version=_required_text(payload.get("version"), "version"),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def evaluate_risk_profile_case(case: RiskProfileCase) -> dict[str, Any]:
    profile = classify_query_risk(case.query)
    requirements = profile["requirements"]
    actual = {
        "risk_level": profile["risk_level"],
        "query_type": profile["query_type"],
        "minimum_distinct_source_count": requirements["minimum_distinct_source_count"],
    }
    expected = {
        "risk_level": case.expected_risk_level,
        "query_type": case.expected_query_type,
        "minimum_distinct_source_count": case.expected_minimum_sources,
    }
    return {
        "id": case.id,
        "category": case.category,
        "passed": actual == expected,
        "expected": expected,
        "actual": actual,
        "risk_factors": profile["risk_factors"],
    }


def run_risk_profile_evaluation(dataset: RiskProfileDataset) -> dict[str, Any]:
    details = [evaluate_risk_profile_case(case) for case in dataset.cases]
    categories = Counter(detail["category"] for detail in details)
    by_category = {}
    for category, total in sorted(categories.items()):
        passed = sum(detail["passed"] for detail in details if detail["category"] == category)
        by_category[category] = {
            "total": total,
            "passed": passed,
            "accuracy": round(passed / total, 4),
        }
    passed_count = sum(detail["passed"] for detail in details)
    return {
        "report_schema_version": 1,
        "risk_profile_eval_version": RISK_PROFILE_EVAL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.version,
            "sha256": dataset.sha256,
            "case_count": len(dataset.cases),
        },
        "metrics": {
            "passed": passed_count,
            "failed": len(details) - passed_count,
            "accuracy": round(passed_count / len(details), 4),
            "by_category": by_category,
        },
        "details": details,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run_risk_profile_evaluation(load_risk_profile_dataset(args.dataset))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    return 0 if report["metrics"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
