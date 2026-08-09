from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from evaluation.dataset import EvalSample, EvidenceTarget

METRIC_VERSION = "2.1.0"

REFUSAL_MARKERS = (
    "没有相关信息",
    "未找到相关",
    "资料中未提及",
    "文档中未提及",
    "无法从",
    "no relevant information",
    "not mentioned",
    "not provided",
    "cannot determine",
    "没有负载测试数据",
    "没有给出具体数值",
    "未提供具体数值",
    "未明确规定",
    "not specified",
    "no evidence",
)


@dataclass(frozen=True)
class ResultView:
    document: str | None
    page: int | None
    chunk: int | None
    text: str


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def result_view(result: Any) -> ResultView:
    metadata = result.metadata if isinstance(getattr(result, "metadata", None), dict) else {}
    document = metadata.get("display_name") or metadata.get("title") or result.reference
    if document:
        document = str(document).replace("\\", "/").rsplit("/", 1)[-1]
    page = metadata.get("page_number")
    chunk = metadata.get("chunk_index")
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    try:
        chunk = int(chunk) if chunk is not None else None
    except (TypeError, ValueError):
        chunk = None
    return ResultView(
        document=document,
        page=page,
        chunk=chunk,
        text=str(getattr(result, "text", "") or ""),
    )


def matches_evidence(
    view: ResultView,
    target: EvidenceTarget,
    source_aliases: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    accepted_documents = [target.document]
    if source_aliases:
        accepted_documents.extend(source_aliases.get(target.document, ()))
    return (
        view.document is not None
        and any(
            _normalize(view.document) == _normalize(document) for document in accepted_documents
        )
        and view.page == target.page
    )


def criteria_coverage(text: str, criteria: Sequence[Sequence[str]]) -> float | None:
    if not criteria:
        return None
    normalized = _normalize(text)
    matched = sum(
        any(_normalize(alternative) in normalized for alternative in alternatives)
        for alternatives in criteria
    )
    return matched / len(criteria)


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(percentile_value * len(ordered)) - 1, 0)
    return ordered[rank]


def evaluate_sample(
    sample: EvalSample,
    results: Iterable[Any],
    *,
    answer: str | None,
    top_k: int,
    latency_ms: float,
    tokens: int,
    llm_calls: int = 0,
    error: str | None = None,
    source_aliases: Mapping[str, Sequence[str]] | None = None,
    grounding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    views = [result_view(result) for result in results][:top_k]
    unique_keys = {(view.document, view.page, view.chunk, _normalize(view.text)) for view in views}
    duplicate_count = len(views) - len(unique_keys)
    relevant = [
        any(matches_evidence(view, target, source_aliases) for target in sample.evidence)
        for view in views
    ]
    matched_targets = {
        (target.document, target.page)
        for target in sample.evidence
        if any(matches_evidence(view, target, source_aliases) for view in views)
    }
    required_documents = {target.document for target in sample.evidence}
    matched_documents = {
        target.document
        for target in sample.evidence
        if any(matches_evidence(view, target, source_aliases) for view in views)
    }
    first_relevant = next((index + 1 for index, hit in enumerate(relevant) if hit), None)
    retrieval_recall = len(matched_targets) / len(sample.evidence) if sample.answerable else None
    retrieval_precision = sum(relevant) / top_k if sample.answerable else None
    retrieved_text = "\n".join(view.text for view in views)
    answer_text = answer or ""
    refused = any(marker in _normalize(answer_text) for marker in REFUSAL_MARKERS)
    grounding_claims = (
        grounding.get("claims", []) if isinstance(grounding, Mapping) else []
    )
    grounding_evidence = (
        grounding.get("evidence", []) if isinstance(grounding, Mapping) else []
    )
    claims = [item for item in grounding_claims if isinstance(item, Mapping)]
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in grounding_evidence
        if isinstance(item, Mapping) and item.get("evidence_id")
    }
    cited_evidence_ids = list(
        dict.fromkeys(
            str(evidence_id)
            for claim in claims
            for evidence_id in (
                claim.get("evidence_ids", [])
                if isinstance(claim.get("evidence_ids"), list)
                else []
            )
        )
    )
    correctly_cited_ids: list[str] = []
    cited_targets: set[tuple[str, int]] = set()
    for evidence_id in cited_evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        view = ResultView(
            document=str(item.get("display_name") or item.get("reference") or "") or None,
            page=(
                int(item["page_number"])
                if isinstance(item.get("page_number"), int)
                and not isinstance(item.get("page_number"), bool)
                else None
            ),
            chunk=(
                int(item["chunk_index"])
                if isinstance(item.get("chunk_index"), int)
                and not isinstance(item.get("chunk_index"), bool)
                else None
            ),
            text=str(item.get("text") or ""),
        )
        matching_targets = [
            target
            for target in sample.evidence
            if matches_evidence(view, target, source_aliases)
        ]
        if matching_targets:
            correctly_cited_ids.append(evidence_id)
            cited_targets.update((target.document, target.page) for target in matching_targets)
    supported_claim_count = sum(
        claim.get("status") in {"supported", "conflicting"} for claim in claims
    )
    invalid_claim_count = sum(claim.get("status") == "invalid_citation" for claim in claims)
    return {
        "sample_id": sample.id,
        "question": sample.question,
        "answerable": sample.answerable,
        "tags": list(sample.tags),
        "difficulty": sample.difficulty,
        "history_turn_count": len(sample.history),
        "multi_document": len(required_documents) > 1,
        "required_evidence_count": len(sample.evidence),
        "matched_evidence_count": len(matched_targets),
        "required_document_count": len(required_documents),
        "matched_document_count": len(matched_documents),
        "full_evidence_retrieved": (
            len(matched_targets) == len(sample.evidence) if sample.answerable else None
        ),
        "full_document_coverage": (
            len(matched_documents) == len(required_documents)
            if sample.answerable and len(required_documents) > 1
            else None
        ),
        "retrieved_count": len(views),
        "retrieval_hit": bool(matched_targets) if sample.answerable else None,
        "retrieval_recall": retrieval_recall,
        "retrieval_precision": retrieval_precision,
        "reciprocal_rank": 1 / first_relevant
        if first_relevant
        else (0.0 if sample.answerable else None),
        "empty_result": not views,
        "duplicate_count": duplicate_count,
        "duplicate_rate": duplicate_count / len(views) if views else 0.0,
        "no_answer_false_positive": bool(views) if not sample.answerable else None,
        "answer_criteria_coverage": (
            criteria_coverage(answer_text, sample.criteria)
            if sample.answerable and answer is not None
            else None
        ),
        "grounded_criteria_coverage": (
            criteria_coverage(retrieved_text, sample.criteria) if sample.answerable else None
        ),
        "evidence_source_correct": bool(matched_targets) if sample.answerable else None,
        "refusal_correct": refused if not sample.answerable and answer is not None else None,
        "grounding_state": (
            str(grounding.get("state")) if isinstance(grounding, Mapping) else None
        ),
        "claim_count": len(claims) if grounding is not None else None,
        "supported_claim_count": supported_claim_count if grounding is not None else None,
        "claim_support_rate": (
            supported_claim_count / len(claims) if claims else None
        ),
        "ungrounded_claim_rate": (
            (len(claims) - supported_claim_count) / len(claims) if claims else None
        ),
        "invalid_claim_citation_rate": (
            invalid_claim_count / len(claims) if claims else None
        ),
        "claim_citation_precision": (
            len(correctly_cited_ids) / len(cited_evidence_ids)
            if cited_evidence_ids and sample.answerable
            else None
        ),
        "claim_citation_recall": (
            len(cited_targets) / len(sample.evidence)
            if sample.answerable and grounding is not None
            else None
        ),
        "latency_ms": round(latency_ms, 3),
        "tokens": int(tokens),
        "llm_calls": int(llm_calls),
        "error": error,
        "answer": answer,
        "reference_answer": sample.reference_answer,
        "retrieved": [
            {
                "document": view.document,
                "page": view.page,
                "chunk": view.chunk,
                "text": view.text,
            }
            for view in views
        ],
    }


def aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def average(field: str) -> float | None:
        values = [row[field] for row in rows if row.get(field) is not None]
        return round(mean(values), 4) if values else None

    latencies = [float(row["latency_ms"]) for row in rows]
    tokens = [int(row["tokens"]) for row in rows]
    llm_calls = [int(row["llm_calls"]) for row in rows]
    return {
        "sample_count": len(rows),
        "successful_count": sum(row.get("error") is None for row in rows),
        "error_rate": round(mean(row.get("error") is not None for row in rows), 4) if rows else 0.0,
        "retrieval_hit_rate": average("retrieval_hit"),
        "retrieval_recall_at_k": average("retrieval_recall"),
        "retrieval_precision_at_k": average("retrieval_precision"),
        "mrr": average("reciprocal_rank"),
        "empty_result_rate": average("empty_result"),
        "duplicate_rate": average("duplicate_rate"),
        "no_answer_false_positive_rate": average("no_answer_false_positive"),
        "answer_criteria_coverage": average("answer_criteria_coverage"),
        "grounded_criteria_coverage": average("grounded_criteria_coverage"),
        "evidence_source_accuracy": average("evidence_source_correct"),
        "full_evidence_retrieval_rate": average("full_evidence_retrieved"),
        "multi_document_sample_count": sum(
            bool(row.get("multi_document")) and bool(row.get("answerable"))
            for row in rows
        ),
        "full_document_coverage_rate": average("full_document_coverage"),
        "refusal_accuracy": average("refusal_correct"),
        "claim_support_rate": average("claim_support_rate"),
        "ungrounded_claim_rate": average("ungrounded_claim_rate"),
        "invalid_claim_citation_rate": average("invalid_claim_citation_rate"),
        "claim_citation_precision": average("claim_citation_precision"),
        "claim_citation_recall": average("claim_citation_recall"),
        "context_dependency_accuracy": average("context_dependency_correct"),
        "context_query_match_rate": average("context_query_match"),
        "context_fallback_rate": average("context_fallback_used"),
        "latency_ms": {
            "average": round(mean(latencies), 3) if latencies else None,
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "tokens": {
            "total": sum(tokens),
            "average": round(mean(tokens), 2) if tokens else None,
        },
        "llm_calls": {
            "total": sum(llm_calls),
            "average": round(mean(llm_calls), 2) if llm_calls else None,
        },
    }
