"""Evaluate claim-to-evidence span localization against a hand-authored gold set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepsearcher.grounding import build_grounding
from deepsearcher.vector_db.base import RetrievalResult

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "citation_span_v1.json"
CITATION_SPAN_EVAL_VERSION = "1.0.0"


@dataclass(frozen=True)
class ExpectedSpan:
    evidence_id: str
    match_type: str
    text: str


@dataclass(frozen=True)
class CitationSpanCase:
    id: str
    category: str
    claim: str
    evidence: tuple[str, ...]
    expected: tuple[ExpectedSpan, ...]


@dataclass(frozen=True)
class CitationSpanDataset:
    dataset_id: str
    version: str
    sha256: str
    cases: tuple[CitationSpanCase, ...]


def _required_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = str(value or "")
    if not allow_empty:
        text = text.strip()
    if not text and not allow_empty:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def load_citation_span_dataset(path: Path = DEFAULT_DATASET) -> CitationSpanDataset:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported citation span dataset schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    cases: list[CitationSpanCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, Mapping):
            raise ValueError(f"cases[{index}] must be an object")
        case_id = _required_text(raw_case.get("id"), f"cases[{index}].id")
        if case_id in seen_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        raw_evidence = raw_case.get("evidence")
        raw_expected = raw_case.get("expected")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ValueError(f"cases[{index}].evidence must be a non-empty array")
        if not isinstance(raw_expected, list) or len(raw_expected) != len(raw_evidence):
            raise ValueError(f"cases[{index}].expected must match evidence length")
        expected: list[ExpectedSpan] = []
        for span_index, raw_span in enumerate(raw_expected):
            if not isinstance(raw_span, Mapping):
                raise ValueError(f"cases[{index}].expected[{span_index}] must be an object")
            match_type = _required_text(
                raw_span.get("match_type"),
                f"cases[{index}].expected[{span_index}].match_type",
            )
            if match_type not in {"normalized_exact", "sentence_overlap", "not_found"}:
                raise ValueError(f"unsupported match type: {match_type}")
            expected.append(
                ExpectedSpan(
                    evidence_id=_required_text(
                        raw_span.get("evidence_id"),
                        f"cases[{index}].expected[{span_index}].evidence_id",
                    ),
                    match_type=match_type,
                    text=_required_text(
                        raw_span.get("text"),
                        f"cases[{index}].expected[{span_index}].text",
                        allow_empty=True,
                    ),
                )
            )
        cases.append(
            CitationSpanCase(
                id=case_id,
                category=_required_text(raw_case.get("category"), f"cases[{index}].category"),
                claim=_required_text(raw_case.get("claim"), f"cases[{index}].claim"),
                evidence=tuple(
                    _required_text(item, f"cases[{index}].evidence") for item in raw_evidence
                ),
                expected=tuple(expected),
            )
        )
    return CitationSpanDataset(
        dataset_id=_required_text(payload.get("id"), "id"),
        version=_required_text(payload.get("version"), "version"),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def _result(text: str, index: int) -> RetrievalResult:
    return RetrievalResult(
        embedding=[],
        text=text,
        reference=f"citation-span-gold-{index}.txt",
        metadata={"location_id": f"citation-span-gold-{index}"},
    )


def _serialize(result: RetrievalResult, supported: bool) -> dict[str, Any]:
    return {"text": result.text, "supported": supported}


def evaluate_citation_span_case(case: CitationSpanCase) -> dict[str, Any]:
    results = [_result(text, index) for index, text in enumerate(case.evidence, start=1)]
    markers = "".join(f"[E{index}]" for index in range(1, len(results) + 1))
    grounding = build_grounding(
        f"{case.claim}{markers}",
        results,
        serialize_evidence=_serialize,
    )
    actual = grounding["claims"][0]["citation_spans"]
    expected = [
        {
            "evidence_id": item.evidence_id,
            "match_type": item.match_type,
            "text": item.text,
        }
        for item in case.expected
    ]
    comparable = [
        {
            "evidence_id": item.get("evidence_id"),
            "match_type": item.get("match_type"),
            "text": item.get("text"),
        }
        for item in actual
    ]
    offsets_valid = all(
        (
            item.get("start") is None
            and item.get("end") is None
            and item.get("match_type") == "not_found"
        )
        or (
            isinstance(item.get("start"), int)
            and isinstance(item.get("end"), int)
            and case.evidence[index][item["start"] : item["end"]] == item.get("text")
        )
        for index, item in enumerate(actual)
    )
    return {
        "id": case.id,
        "category": case.category,
        "passed": comparable == expected and offsets_valid,
        "expected": expected,
        "actual": actual,
        "offsets_valid": offsets_valid,
    }


def run_citation_span_evaluation(dataset: CitationSpanDataset) -> dict[str, Any]:
    details = [evaluate_citation_span_case(case) for case in dataset.cases]
    categories = Counter(detail["category"] for detail in details)
    by_category = {
        category: {
            "total": total,
            "passed": (
                passed := sum(
                    detail["passed"] for detail in details if detail["category"] == category
                )
            ),
            "accuracy": round(passed / total, 4),
        }
        for category, total in sorted(categories.items())
    }
    passed_count = sum(detail["passed"] for detail in details)
    return {
        "report_schema_version": 1,
        "citation_span_eval_version": CITATION_SPAN_EVAL_VERSION,
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
    report = run_citation_span_evaluation(load_citation_span_dataset(args.dataset))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    return 0 if report["metrics"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
