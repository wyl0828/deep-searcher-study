from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from evaluation.dataset import EvalSample, EvidenceTarget

METRIC_VERSION = "2.1.0"
TRUST_METRIC_VERSION = "1.6.0"

REFUSAL_MARKERS = (
    "没有相关信息",
    "未找到相关",
    "资料中未提及",
    "文档中未提及",
    "无法从",
    "无法给出有充分依据的回答",
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


def is_refusal(answer: str | None) -> bool:
    return any(marker in _normalize(answer or "") for marker in REFUSAL_MARKERS)


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


def matches_document(
    view: ResultView,
    target: EvidenceTarget,
    source_aliases: Mapping[str, Sequence[str]] | None = None,
) -> bool:
    accepted_documents = [target.document]
    if source_aliases:
        accepted_documents.extend(source_aliases.get(target.document, ()))
    return view.document is not None and any(
        _normalize(view.document) == _normalize(document) for document in accepted_documents
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
    trust: Mapping[str, Any] | None = None,
    policy_input_criteria_coverage: float | None = None,
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
        if any(matches_document(view, target, source_aliases) for view in views)
    }
    missing_documents = sorted(required_documents - matched_documents)
    first_relevant = next((index + 1 for index, hit in enumerate(relevant) if hit), None)
    retrieval_recall = len(matched_targets) / len(sample.evidence) if sample.answerable else None
    retrieval_precision = sum(relevant) / top_k if sample.answerable else None
    retrieved_text = "\n".join(view.text for view in views)
    answer_text = answer or ""
    refused = is_refusal(answer_text)
    grounding_claims = grounding.get("claims", []) if isinstance(grounding, Mapping) else []
    grounding_evidence = grounding.get("evidence", []) if isinstance(grounding, Mapping) else []
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
                claim.get("evidence_ids", []) if isinstance(claim.get("evidence_ids"), list) else []
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
            target for target in sample.evidence if matches_evidence(view, target, source_aliases)
        ]
        if matching_targets:
            correctly_cited_ids.append(evidence_id)
            cited_targets.update((target.document, target.page) for target in matching_targets)
    supported_claim_count = sum(
        claim.get("status") in {"supported", "conflicting"} for claim in claims
    )
    invalid_claim_count = sum(claim.get("status") == "invalid_citation" for claim in claims)
    trust_input = trust.get("input") if isinstance(trust, Mapping) else None
    trust_input_claims = trust_input.get("claims", []) if isinstance(trust_input, Mapping) else []
    input_claims = [item for item in trust_input_claims if isinstance(item, Mapping)]
    input_supported_count = sum(
        claim.get("support_status") in {"supported", "conflicting"} for claim in input_claims
    )
    consistency_checked_claims = [
        claim
        for claim in input_claims
        if claim.get("consistency_status") in {"consistent", "inconsistent", "unknown"}
    ]
    inconsistent_claim_count = sum(
        claim.get("consistency_status") == "inconsistent" for claim in consistency_checked_claims
    )
    consistency_unknown_count = sum(
        claim.get("consistency_status") == "unknown" for claim in consistency_checked_claims
    )
    relative_time_claims = [
        claim
        for claim in input_claims
        if any(
            isinstance(check, Mapping) and check.get("kind") == "relative_time"
            for check in claim.get("consistency_checks", [])
        )
    ]
    relative_time_unknown_count = sum(
        claim.get("consistency_status") == "unknown" for claim in relative_time_claims
    )
    relative_time_rejected_count = sum(
        claim.get("support_status") == "unsupported" for claim in relative_time_claims
    )
    freshness_claims = [
        claim
        for claim in input_claims
        if any(
            isinstance(check, Mapping) and check.get("kind") == "freshness"
            for check in claim.get("consistency_checks", [])
        )
    ]
    freshness_unknown_count = sum(
        claim.get("consistency_status") == "unknown" for claim in freshness_claims
    )
    freshness_rejected_count = sum(
        claim.get("support_status") == "unsupported" for claim in freshness_claims
    )
    entailment_checked_claims = [
        claim
        for claim in input_claims
        if claim.get("entailment_status") in {"entailed", "contradicted", "unknown"}
    ]
    entailment_contradicted_count = sum(
        claim.get("entailment_status") == "contradicted" for claim in entailment_checked_claims
    )
    entailment_unknown_count = sum(
        claim.get("entailment_status") == "unknown" for claim in entailment_checked_claims
    )
    entailment_details = trust.get("entailment") if isinstance(trust, Mapping) else None
    entailment_token_usage = (
        int(entailment_details.get("token_usage") or 0)
        if isinstance(entailment_details, Mapping)
        else None
    )
    risk_details = trust.get("risk") if isinstance(trust, Mapping) else None
    freshness_details = trust.get("freshness") if isinstance(trust, Mapping) else None
    freshness_mode = (
        str(freshness_details.get("mode"))
        if isinstance(freshness_details, Mapping) and freshness_details.get("mode")
        else None
    )
    freshness_required = (
        bool(freshness_details.get("required")) if isinstance(freshness_details, Mapping) else None
    )
    risk_level = (
        str(risk_details.get("risk_level"))
        if isinstance(risk_details, Mapping) and risk_details.get("risk_level")
        else None
    )
    query_type = (
        str(risk_details.get("query_type"))
        if isinstance(risk_details, Mapping) and risk_details.get("query_type")
        else None
    )
    risk_rejected_claim_count = sum(
        claim.get("risk_status") == "rejected" for claim in input_claims
    )
    policy = trust.get("policy") if isinstance(trust, Mapping) else None
    policy_action = str(policy.get("action")) if isinstance(policy, Mapping) else None
    policy_answer_changed = (
        bool(policy.get("answer_changed")) if isinstance(policy, Mapping) else None
    )
    policy_reason_codes = (
        [str(item) for item in policy.get("reason_codes", []) if str(item).strip()]
        if isinstance(policy, Mapping) and isinstance(policy.get("reason_codes"), list)
        else []
    )
    policy_input_coverage = (
        float(policy_input_criteria_coverage)
        if sample.answerable and policy_input_criteria_coverage is not None
        else None
    )
    final_answer_coverage = (
        criteria_coverage(answer_text, sample.criteria)
        if sample.answerable and answer is not None
        else None
    )
    grounded_coverage = (
        criteria_coverage(retrieved_text, sample.criteria) if sample.answerable else None
    )
    if error is not None or not sample.answerable:
        coverage_failure_type = None
    elif retrieval_recall is not None and retrieval_recall < 1.0:
        coverage_failure_type = "retrieval_gap"
    elif grounded_coverage is not None and grounded_coverage < 1.0:
        coverage_failure_type = "chunk_ranking_gap"
    elif (
        policy_input_coverage is not None
        and final_answer_coverage is not None
        and final_answer_coverage < policy_input_coverage
    ):
        coverage_failure_type = "policy_deletion"
    elif final_answer_coverage is not None and final_answer_coverage < 1.0:
        coverage_failure_type = "generation_gap"
    else:
        coverage_failure_type = "complete"
    provenance = trust.get("provenance") if isinstance(trust, Mapping) else None
    temporal_provenance = provenance.get("temporal") if isinstance(provenance, Mapping) else None
    evidence_provenance = provenance.get("evidence") if isinstance(provenance, Mapping) else None
    evidence_provenance_status = (
        str(evidence_provenance.get("snapshot_status"))
        if isinstance(evidence_provenance, Mapping) and evidence_provenance.get("snapshot_status")
        else None
    )
    web_evidence_provenance_count = (
        int(evidence_provenance.get("web_count") or 0)
        if isinstance(evidence_provenance, Mapping)
        else None
    )
    evidence_provenance_items = (
        [item for item in evidence_provenance.get("items", []) if isinstance(item, Mapping)]
        if isinstance(evidence_provenance, Mapping)
        and isinstance(evidence_provenance.get("items", []), list)
        else []
    )
    publication_anchor_eligible_items = [
        item for item in evidence_provenance_items if item.get("source_type") == "knowledge_base"
    ]
    evidence_publication_anchor_count = sum(
        item.get("publication_anchor_bound") is True for item in publication_anchor_eligible_items
    )
    evidence_version_family_count = sum(
        item.get("version_family_bound") is True for item in publication_anchor_eligible_items
    )
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
        "missing_documents": missing_documents,
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
        "answer_criteria_coverage": final_answer_coverage,
        "policy_input_criteria_coverage": policy_input_coverage,
        "grounded_criteria_coverage": grounded_coverage,
        "coverage_failure_type": coverage_failure_type,
        "evidence_source_correct": bool(matched_targets) if sample.answerable else None,
        "refusal_correct": refused if not sample.answerable and answer is not None else None,
        "grounding_state": (
            str(grounding.get("state")) if isinstance(grounding, Mapping) else None
        ),
        "claim_count": len(claims) if grounding is not None else None,
        "supported_claim_count": supported_claim_count if grounding is not None else None,
        "claim_support_rate": (supported_claim_count / len(claims) if claims else None),
        "ungrounded_claim_rate": (
            (len(claims) - supported_claim_count) / len(claims) if claims else None
        ),
        "invalid_claim_citation_rate": (invalid_claim_count / len(claims) if claims else None),
        "trust_input_claim_count": len(input_claims) if trust is not None else None,
        "trust_input_supported_claim_count": (input_supported_count if trust is not None else None),
        "trust_input_claim_support_rate": (
            input_supported_count / len(input_claims) if input_claims else None
        ),
        "consistency_checked_claim_count": (
            len(consistency_checked_claims) if trust is not None else None
        ),
        "consistency_inconsistent_claim_count": (
            inconsistent_claim_count if trust is not None else None
        ),
        "consistency_inconsistency_rate": (
            inconsistent_claim_count / len(consistency_checked_claims)
            if consistency_checked_claims
            else None
        ),
        "consistency_unknown_claim_count": (
            consistency_unknown_count if trust is not None else None
        ),
        "consistency_unknown_rate": (
            consistency_unknown_count / len(consistency_checked_claims)
            if consistency_checked_claims
            else None
        ),
        "relative_time_claim_count": len(relative_time_claims) if trust is not None else None,
        "relative_time_unknown_claim_count": (
            relative_time_unknown_count if trust is not None else None
        ),
        "relative_time_unknown_rate": (
            relative_time_unknown_count / len(relative_time_claims)
            if relative_time_claims
            else None
        ),
        "relative_time_rejected_claim_count": (
            relative_time_rejected_count if trust is not None else None
        ),
        "relative_time_rejection_rate": (
            relative_time_rejected_count / len(relative_time_claims)
            if relative_time_claims
            else None
        ),
        "freshness_mode": freshness_mode,
        "freshness_required": freshness_required,
        "freshness_claim_count": len(freshness_claims) if trust is not None else None,
        "freshness_unknown_claim_count": (freshness_unknown_count if trust is not None else None),
        "freshness_unknown_rate": (
            freshness_unknown_count / len(freshness_claims) if freshness_claims else None
        ),
        "freshness_rejected_claim_count": (freshness_rejected_count if trust is not None else None),
        "freshness_rejection_rate": (
            freshness_rejected_count / len(freshness_claims) if freshness_claims else None
        ),
        "entailment_checked_claim_count": (
            len(entailment_checked_claims) if trust is not None else None
        ),
        "entailment_coverage_rate": (
            len(entailment_checked_claims) / len(input_claims) if input_claims else None
        ),
        "entailment_contradicted_claim_count": (
            entailment_contradicted_count if trust is not None else None
        ),
        "entailment_contradiction_rate": (
            entailment_contradicted_count / len(entailment_checked_claims)
            if entailment_checked_claims
            else None
        ),
        "entailment_unknown_claim_count": (entailment_unknown_count if trust is not None else None),
        "entailment_unknown_rate": (
            entailment_unknown_count / len(entailment_checked_claims)
            if entailment_checked_claims
            else None
        ),
        "entailment_token_usage": entailment_token_usage,
        "risk_level": risk_level,
        "query_type": query_type,
        "risk_high": risk_level == "high" if risk_level is not None else None,
        "risk_rejected_claim_count": (risk_rejected_claim_count if trust is not None else None),
        "risk_claim_rejection_rate": (
            risk_rejected_claim_count / len(input_claims) if input_claims else None
        ),
        "policy_action": policy_action,
        "policy_reason_codes": policy_reason_codes if trust is not None else None,
        "policy_answer_changed": policy_answer_changed,
        "provenance_available": isinstance(provenance, Mapping) if trust is not None else None,
        "temporal_provenance_bound": (
            isinstance(temporal_provenance, Mapping) if trust is not None else None
        ),
        "provenance_contract_version": (
            int(provenance.get("version"))
            if isinstance(provenance, Mapping)
            and isinstance(provenance.get("version"), int)
            and not isinstance(provenance.get("version"), bool)
            else None
        ),
        "provenance_digest": (
            str(provenance.get("digest"))
            if isinstance(provenance, Mapping) and provenance.get("digest")
            else None
        ),
        "evidence_provenance_status": evidence_provenance_status,
        "evidence_provenance_bound": (
            evidence_provenance_status in {"complete", "partial"}
            if evidence_provenance_status is not None
            else None
        ),
        "evidence_provenance_complete": (
            evidence_provenance_status == "complete"
            if evidence_provenance_status is not None
            else None
        ),
        "web_evidence_provenance_count": web_evidence_provenance_count,
        "evidence_publication_anchor_count": (
            evidence_publication_anchor_count if isinstance(evidence_provenance, Mapping) else None
        ),
        "evidence_publication_anchor_eligible_count": (
            len(publication_anchor_eligible_items)
            if isinstance(evidence_provenance, Mapping)
            else None
        ),
        "evidence_publication_anchor_coverage_rate": (
            evidence_publication_anchor_count / len(publication_anchor_eligible_items)
            if publication_anchor_eligible_items
            else None
        ),
        "evidence_version_family_count": (
            evidence_version_family_count if isinstance(evidence_provenance, Mapping) else None
        ),
        "evidence_version_family_eligible_count": (
            len(publication_anchor_eligible_items)
            if isinstance(evidence_provenance, Mapping)
            else None
        ),
        "evidence_version_family_coverage_rate": (
            evidence_version_family_count / len(publication_anchor_eligible_items)
            if publication_anchor_eligible_items
            else None
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
    usage_fields = (
        "llm_input_tokens",
        "llm_cache_hit_tokens",
        "llm_cache_miss_tokens",
        "llm_output_tokens",
        "llm_reasoning_tokens",
        "llm_estimated_input_tokens",
    )
    summary = {
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
        "policy_input_criteria_coverage": average("policy_input_criteria_coverage"),
        "grounded_criteria_coverage": average("grounded_criteria_coverage"),
        "evidence_source_accuracy": average("evidence_source_correct"),
        "full_evidence_retrieval_rate": average("full_evidence_retrieved"),
        "multi_document_sample_count": sum(
            bool(row.get("multi_document")) and bool(row.get("answerable")) for row in rows
        ),
        "full_document_coverage_rate": average("full_document_coverage"),
        "refusal_accuracy": average("refusal_correct"),
        "claim_support_rate": average("claim_support_rate"),
        "ungrounded_claim_rate": average("ungrounded_claim_rate"),
        "invalid_claim_citation_rate": average("invalid_claim_citation_rate"),
        "trust_input_claim_support_rate": average("trust_input_claim_support_rate"),
        "consistency_inconsistency_rate": average("consistency_inconsistency_rate"),
        "consistency_unknown_rate": average("consistency_unknown_rate"),
        "relative_time_unknown_rate": average("relative_time_unknown_rate"),
        "relative_time_rejection_rate": average("relative_time_rejection_rate"),
        "policy_change_rate": average("policy_answer_changed"),
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
    if any(any(field in row for field in usage_fields) for row in rows):
        summary["llm_token_usage"] = {
            field.removeprefix("llm_"): sum(int(row.get(field) or 0) for row in rows)
            for field in usage_fields
        }
        stage_totals: dict[str, dict[str, int]] = {}
        for row in rows:
            stage_usage = row.get("llm_stage_usage")
            if not isinstance(stage_usage, Mapping):
                continue
            for stage, values in stage_usage.items():
                if not isinstance(values, Mapping):
                    continue
                bucket = stage_totals.setdefault(str(stage), {})
                for field, value in values.items():
                    if isinstance(value, int) and not isinstance(value, bool):
                        bucket[str(field)] = bucket.get(str(field), 0) + max(value, 0)
        summary["llm_token_usage"]["stages"] = stage_totals
    if any("coverage_failure_type" in row for row in rows):
        summary["coverage_failure_types"] = dict(
            sorted(
                Counter(
                    str(row["coverage_failure_type"])
                    for row in rows
                    if row.get("coverage_failure_type")
                ).items()
            )
        )
    # Committed reports predate Trust Metric 1.1. Do not synthesize new fields
    # while recomputing those immutable artifacts; new evaluator rows always
    # carry the keys, including explicit None when no checker ran.
    if any("entailment_token_usage" in row for row in rows):
        summary.update(
            {
                "entailment_coverage_rate": average("entailment_coverage_rate"),
                "entailment_contradiction_rate": average("entailment_contradiction_rate"),
                "entailment_unknown_rate": average("entailment_unknown_rate"),
                "entailment_tokens": {
                    "total": sum(
                        int(row.get("entailment_token_usage") or 0)
                        for row in rows
                        if row.get("entailment_token_usage") is not None
                    ),
                    "average": average("entailment_token_usage"),
                },
            }
        )
    if any("risk_level" in row for row in rows):
        summary.update(
            {
                "high_risk_sample_count": sum(row.get("risk_level") == "high" for row in rows),
                "high_risk_rate": average("risk_high"),
                "risk_claim_rejection_rate": average("risk_claim_rejection_rate"),
            }
        )
    if any("freshness_mode" in row for row in rows):
        summary.update(
            {
                "freshness_required_sample_count": sum(
                    row.get("freshness_required") is True for row in rows
                ),
                "freshness_required_rate": average("freshness_required"),
                "freshness_unknown_rate": average("freshness_unknown_rate"),
                "freshness_rejection_rate": average("freshness_rejection_rate"),
            }
        )
    if any("provenance_available" in row for row in rows):
        publication_anchor_count = sum(
            int(row.get("evidence_publication_anchor_count") or 0) for row in rows
        )
        publication_anchor_eligible_count = sum(
            int(row.get("evidence_publication_anchor_eligible_count") or 0) for row in rows
        )
        version_family_count = sum(
            int(row.get("evidence_version_family_count") or 0) for row in rows
        )
        version_family_eligible_count = sum(
            int(row.get("evidence_version_family_eligible_count") or 0) for row in rows
        )
        summary.update(
            {
                "provenance_coverage_rate": average("provenance_available"),
                "temporal_provenance_bound_rate": average("temporal_provenance_bound"),
                "evidence_provenance_bound_rate": average("evidence_provenance_bound"),
                "evidence_provenance_complete_rate": average("evidence_provenance_complete"),
                "web_evidence_provenance_count": sum(
                    int(row.get("web_evidence_provenance_count") or 0) for row in rows
                ),
                "evidence_publication_anchor_count": publication_anchor_count,
                "evidence_publication_anchor_eligible_count": (publication_anchor_eligible_count),
                "evidence_publication_anchor_coverage_rate": (
                    round(publication_anchor_count / publication_anchor_eligible_count, 4)
                    if publication_anchor_eligible_count
                    else None
                ),
                "evidence_version_family_count": version_family_count,
                "evidence_version_family_eligible_count": version_family_eligible_count,
                "evidence_version_family_coverage_rate": (
                    round(version_family_count / version_family_eligible_count, 4)
                    if version_family_eligible_count
                    else None
                ),
            }
        )
    return summary
