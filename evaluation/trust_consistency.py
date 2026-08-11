"""Deterministic evaluation for the hand-authored Trust Consistency gold set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from deepsearcher.freshness import classify_query_freshness
from deepsearcher.grounding import build_grounding
from deepsearcher.trust import (
    apply_answer_policy,
    assess_grounding_consistency,
    build_temporal_context,
)
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.versioning import sanitize_document_governance_metadata

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "trust_consistency_v1.json"
TRUST_EVAL_VERSION = "1.6.0"


@dataclass(frozen=True)
class TrustConsistencyEvidence:
    text: str
    temporal_metadata: Mapping[str, str]


@dataclass(frozen=True)
class TrustConsistencyCase:
    id: str
    category: str
    claim: str
    evidence: tuple[TrustConsistencyEvidence, ...]
    expected_status: str
    expected_reason_codes: tuple[str, ...]
    expected_policy_action: str
    reference_time: str | None
    timezone: str | None
    query: str
    citation_indices: tuple[int, ...]


@dataclass(frozen=True)
class TrustConsistencyDataset:
    dataset_id: str
    version: str
    sha256: str
    cases: tuple[TrustConsistencyCase, ...]


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def load_trust_consistency_dataset(path: Path = DEFAULT_DATASET) -> TrustConsistencyDataset:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes)
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise ValueError("unsupported trust consistency dataset schema")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty array")
    cases: list[TrustConsistencyCase] = []
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
        raw_reasons = raw_case.get("expected_reason_codes")
        if not isinstance(raw_reasons, list):
            raise ValueError(f"cases[{index}].expected_reason_codes must be an array")
        evidence_items = []
        for evidence_index, item in enumerate(raw_evidence):
            if isinstance(item, str):
                evidence_items.append(
                    TrustConsistencyEvidence(
                        text=_required_text(item, f"cases[{index}].evidence[{evidence_index}]"),
                        temporal_metadata={},
                    )
                )
                continue
            if not isinstance(item, Mapping) or set(item) - {
                "text",
                "published_at",
                "effective_at",
                "superseded_at",
                "temporal_metadata_source",
                "version_family",
                "version_family_source",
            }:
                raise ValueError(f"cases[{index}].evidence[{evidence_index}] is invalid")
            metadata = sanitize_document_governance_metadata(
                {key: value for key, value in item.items() if key != "text"}
            )
            if metadata is None:
                raise ValueError(
                    f"cases[{index}].evidence[{evidence_index}] temporal metadata is invalid"
                )
            evidence_items.append(
                TrustConsistencyEvidence(
                    text=_required_text(
                        item.get("text"),
                        f"cases[{index}].evidence[{evidence_index}].text",
                    ),
                    temporal_metadata=metadata,
                )
            )
        raw_citation_indices = raw_case.get("citation_indices")
        if raw_citation_indices is None:
            citation_indices = tuple(range(1, len(evidence_items) + 1))
        elif (
            not isinstance(raw_citation_indices, list)
            or not raw_citation_indices
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or item < 1
                or item > len(evidence_items)
                for item in raw_citation_indices
            )
            or len(set(raw_citation_indices)) != len(raw_citation_indices)
        ):
            raise ValueError(f"cases[{index}].citation_indices is invalid")
        else:
            citation_indices = tuple(raw_citation_indices)
        cases.append(
            TrustConsistencyCase(
                id=case_id,
                category=_required_text(raw_case.get("category"), f"cases[{index}].category"),
                claim=_required_text(raw_case.get("claim"), f"cases[{index}].claim"),
                evidence=tuple(evidence_items),
                expected_status=_required_text(
                    raw_case.get("expected_status"),
                    f"cases[{index}].expected_status",
                ),
                expected_reason_codes=tuple(
                    _required_text(item, f"cases[{index}].expected_reason_codes")
                    for item in raw_reasons
                ),
                expected_policy_action=_required_text(
                    raw_case.get("expected_policy_action"),
                    f"cases[{index}].expected_policy_action",
                ),
                reference_time=(
                    _required_text(raw_case.get("reference_time"), f"cases[{index}].reference_time")
                    if raw_case.get("reference_time") is not None
                    else None
                ),
                timezone=(
                    _required_text(raw_case.get("timezone"), f"cases[{index}].timezone")
                    if raw_case.get("timezone") is not None
                    else None
                ),
                query=str(raw_case.get("query") or ""),
                citation_indices=citation_indices,
            )
        )
    return TrustConsistencyDataset(
        dataset_id=_required_text(payload.get("id"), "id"),
        version=_required_text(payload.get("version"), "version"),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def _result(evidence: TrustConsistencyEvidence, index: int) -> RetrievalResult:
    return RetrievalResult(
        embedding=[],
        text=evidence.text,
        reference=f"trust-gold-{index}.txt",
        metadata={
            "location_id": f"trust-gold-{index}",
            **dict(evidence.temporal_metadata),
        },
    )


def _serialize_evidence(result: RetrievalResult, supported: bool) -> dict[str, Any]:
    return {
        "text": result.text,
        "location_id": result.metadata.get("location_id"),
        "supported": supported,
        **{
            field: result.metadata[field]
            for field in (
                "published_at",
                "effective_at",
                "superseded_at",
                "temporal_metadata_source",
                "version_family",
                "version_family_source",
            )
            if field in result.metadata
        },
    }


def evaluate_trust_case(case: TrustConsistencyCase) -> dict[str, Any]:
    results = [_result(item, index) for index, item in enumerate(case.evidence, start=1)]
    markers = "".join(f"[E{index}]" for index in case.citation_indices)
    answer = f"{case.claim}{markers}"
    structural = build_grounding(
        answer,
        results,
        serialize_evidence=_serialize_evidence,
    )
    temporal_context = (
        build_temporal_context(
            reference_time=case.reference_time,
            timezone_name=case.timezone or "UTC",
        )
        if case.reference_time is not None
        else None
    )
    assessed = assess_grounding_consistency(
        structural,
        temporal_context=temporal_context,
        freshness_intent=classify_query_freshness(case.query),
    )
    claim = assessed["claims"][0]
    _, policy = apply_answer_policy(
        answer,
        assessed,
        evidence_snapshot_available=True,
        enforce=True,
    )
    actual_reasons = tuple(claim.get("consistency_reason_codes") or ())
    missing_reasons = sorted(set(case.expected_reason_codes) - set(actual_reasons))
    passed = (
        claim.get("consistency_status") == case.expected_status
        and not missing_reasons
        and policy.get("action") == case.expected_policy_action
    )
    return {
        "id": case.id,
        "category": case.category,
        "passed": passed,
        "expected_status": case.expected_status,
        "actual_status": claim.get("consistency_status"),
        "expected_reason_codes": list(case.expected_reason_codes),
        "actual_reason_codes": list(actual_reasons),
        "missing_reason_codes": missing_reasons,
        "expected_policy_action": case.expected_policy_action,
        "actual_policy_action": policy.get("action"),
        "checks": claim.get("consistency_checks") or [],
    }


def run_trust_consistency_evaluation(
    dataset: TrustConsistencyDataset,
) -> dict[str, Any]:
    details = [evaluate_trust_case(case) for case in dataset.cases]
    by_category: dict[str, dict[str, int | float]] = {}
    categories = Counter(detail["category"] for detail in details)
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
        "trust_eval_version": TRUST_EVAL_VERSION,
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
    report = run_trust_consistency_evaluation(load_trust_consistency_dataset(args.dataset))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    return 0 if report["metrics"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
