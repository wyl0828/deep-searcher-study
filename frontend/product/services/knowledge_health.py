"""Knowledge Health scoring for the product workspace (roadmap v0.4).

The health score is not a black-box number. Every snapshot persists the formula
version, the per-dimension scores, the raw metrics that produced them, the
deduction reasons and the suggested actions. The pure compute functions take
plain rows so they can be unit tested without a database.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from frontend.product.errors import ProductError
from frontend.product.models import (
    AnswerClaim,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgeHealthSnapshot,
    Message,
)
from frontend.product.services.documents import retry_document
from frontend.product.services.knowledge_bases import reindex_knowledge_base

HEALTH_FORMULA_VERSION = "1.1"

# Weights are part of the formula contract; changing them must bump the version.
DATA_WEIGHT = 0.4
RETRIEVAL_WEIGHT = 0.3
TRUST_WEIGHT = 0.3

MIN_RETRIEVAL_SAMPLE = 3
MIN_TRUST_SAMPLE = 3
RETRIEVAL_SAMPLE_LIMIT = 50
SERIES_GAP_DAYS = 90
SERIES_PENALTY_CAP = 40
# Formula 1.1 contract: penalty per anomaly unit, cumulative up to the cap.
SERIES_PENALTIES = {
    "DUPLICATE_DOCUMENTS": 10,
    "TEMPORAL_OVERLAP": 10,
    "SERIES_GAP": 5,
    "SERIES_SUPERSEDE_ANOMALY": 10,
    "SERIES_ORPHANED": 5,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, ndigits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, ndigits)


def _deduction(
    code: str,
    reason: str,
    impact: str,
    *,
    document_ids: Sequence[str] | None = None,
    version_family: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "reason": reason, "impact": impact}
    if document_ids is not None:
        item["document_ids"] = list(document_ids)
    if version_family is not None:
        item["version_family"] = version_family
    return item


def _action(code: str, action: str, priority: str) -> dict[str, str]:
    return {"code": code, "action": action, "priority": priority}


def _series_bounds(document: Document) -> tuple[date | None, date | None]:
    """Normalize a document's version interval: [start, end)."""
    return document.effective_at or document.published_at, document.superseded_at


def compute_series_detections(
    documents: Sequence[Document],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Detect version-family anomalies (formula 1.1).

    Scope: ready documents with a non-empty version_family and a resolvable
    start date. Returns (deductions, actions, penalty) where penalty is the
    total amount subtracted from the base data health score, capped at
    SERIES_PENALTY_CAP. Anomalies are suppressed locally (per affected
    document / relation / duplicate group), never as a whole-series short
    circuit.
    """

    deductions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    penalty = 0

    scoped = [
        document
        for document in documents
        if document.status == "ready" and document.version_family
    ]
    if not scoped:
        return deductions, actions, 0

    families: dict[str, list[Document]] = {}
    for document in scoped:
        families.setdefault(document.version_family, []).append(document)

    def record(
        code: str,
        reason: str,
        impact: str,
        action: str,
        priority: str,
        *,
        document_ids: Sequence[str],
        family: str,
        units: int = 1,
    ) -> None:
        nonlocal penalty
        deductions.append(
            _deduction(
                code,
                reason,
                impact,
                document_ids=document_ids,
                version_family=family,
            )
        )
        actions.append(_action(action, action_reason(action), priority))
        penalty += SERIES_PENALTIES[code] * units

    def action_reason(action: str) -> str:
        return {
            "REVIEW_SUPERSEDE": "核对并修正文档的生效/失效日期",
            "REVIEW_DUPLICATES": "核对并保留正确版本，删除多余副本",
            "REVIEW_VERSION_INTERVALS": "核对各版本生效/失效日期",
            "REVIEW_ORPHANED": "补充后继版本或修正失效日期",
            "REVIEW_SERIES_GAP": "核对是否缺少中间版本或补充生效日期",
        }[action]

    now = date.today()
    for family, members in families.items():
        # 1) Invalid interval: end < start -> excluded from downstream checks.
        valid: list[Document] = []
        for document in members:
            start, end = _series_bounds(document)
            if start is None:
                continue
            if end is not None and end < start:
                record(
                    "SERIES_SUPERSEDE_ANOMALY",
                    f"系列“{family}”存在失效日期早于生效日期的文档（{document.display_name}）",
                    "版本时间区间非法，时效判断不可靠。",
                    "REVIEW_SUPERSEDE",
                    "high",
                    document_ids=[document.id],
                    family=family,
                )
                continue
            valid.append(document)

        if not valid:
            continue

        # 2) Duplicates: one deduction per (version_family, sha256) group; the
        #    earliest representative stays in the temporal chain.
        dup_groups: dict[str, list[Document]] = {}
        for document in valid:
            dup_groups.setdefault(document.sha256, []).append(document)
        representatives: list[Document] = []
        for sha, group in dup_groups.items():
            if len(group) > 1:
                record(
                    "DUPLICATE_DOCUMENTS",
                    f"系列“{family}”存在 {len(group)} 份内容完全相同的文档",
                    "重复内容占用索引并可能造成引用歧义。",
                    "REVIEW_DUPLICATES",
                    "medium",
                    document_ids=[document.id for document in group],
                    family=family,
                )
            representative = sorted(
                group,
                key=lambda document: (_series_bounds(document)[0], document.id),
            )[0]
            representatives.append(representative)

        # 3) Multiple current documents: current = start <= now and end is None.
        current_docs = [
            document
            for document in representatives
            if _series_bounds(document)[1] is None
            and _series_bounds(document)[0] <= now
        ]
        if len(current_docs) > 1:
            record(
                "SERIES_SUPERSEDE_ANOMALY",
                f"系列“{family}”存在 {len(current_docs)} 份同时有效的文档",
                "同一时间可能引用两个版本。",
                "REVIEW_SUPERSEDE",
                "high",
                document_ids=[document.id for document in current_docs],
                family=family,
                units=len(current_docs) - 1,
            )

        # 4) Temporal adjacent scan over the temporal chain.
        chain = sorted(
            representatives,
            key=lambda document: (_series_bounds(document)[0], document.id),
        )
        max_start = max(_series_bounds(document)[0] for document in chain)
        for index, document in enumerate(chain):
            start, end = _series_bounds(document)
            if end is None:
                continue
            # Orphan: end exists but no successor with start >= end.
            if max_start < end:
                record(
                    "SERIES_ORPHANED",
                    f"系列“{family}”中“{document.display_name}”被取代后没有后继版本",
                    "失效后缺少接续版本，断档期可能无可用资料。",
                    "REVIEW_ORPHANED",
                    "medium",
                    document_ids=[document.id],
                    family=family,
                )
            if index + 1 >= len(chain):
                continue
            next_start, _ = _series_bounds(chain[index + 1])
            if next_start < end:
                record(
                    "TEMPORAL_OVERLAP",
                    f"系列“{family}”相邻版本有效区间重叠（{document.display_name} 与 {chain[index + 1].display_name}）",
                    "同一时间可能引用两个版本。",
                    "REVIEW_VERSION_INTERVALS",
                    "medium",
                    document_ids=[document.id, chain[index + 1].id],
                    family=family,
                )
            elif next_start == end:
                continue
            elif (next_start - end).days <= SERIES_GAP_DAYS:
                continue
            else:
                days = (next_start - end).days
                record(
                    "SERIES_GAP",
                    f"系列“{family}”相邻版本之间存在 {days} 天空档",
                    "断档期的问题可能缺少可用版本。",
                    "REVIEW_SERIES_GAP",
                    "medium",
                    document_ids=[document.id, chain[index + 1].id],
                    family=family,
                )

    return deductions, actions, min(penalty, SERIES_PENALTY_CAP)


def compute_data_health(
    documents: Sequence[Document],
    index_verified: bool,
) -> dict[str, Any]:
    """Score data readiness from document rows and index state (0-100)."""

    total = len(documents)
    if total == 0:
        return {
            "score": 0.0,
            "metrics": {
                "total_documents": 0,
                "ready_documents": 0,
                "failed_documents": 0,
                "empty_documents": 0,
                "total_pages": 0,
                "index_verified": False,
                "temporal_metadata_ratio": 0.0,
            },
            "deductions": [
                _deduction(
                    "KB_EMPTY",
                    "知识库还没有文档",
                    "没有可检索内容，无法回答任何问题。",
                )
            ],
            "actions": [
                _action(
                    "UPLOAD_DOCUMENTS",
                    "上传 PDF 文档并等待处理完成",
                    "high",
                )
            ],
        }

    ready = sum(1 for d in documents if d.status == "ready")
    failed = sum(1 for d in documents if d.status == "failed")
    empty = sum(1 for d in documents if d.status == "ready" and (d.page_count or 0) == 0)
    total_pages = sum(d.page_count or 0 for d in documents)
    dated = sum(1 for d in documents if d.published_at is not None)
    temporal_ratio = dated / total

    readiness = ready / total
    failed_ratio = failed / total
    index_score = 100.0 if index_verified else 0.0
    score = _clamp(
        100.0
        * (
            0.70 * readiness
            + 0.15 * (1.0 - failed_ratio)
            + 0.15 * (index_score / 100.0)
        )
    )

    deductions: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    if failed:
        deductions.append(
            _deduction(
                "FAILED_DOCUMENTS",
                f"{failed} 个文档处理失败",
                "这些文档无法参与检索和回答。",
            )
        )
        actions.append(
            _action(
                "RETRY_FAILED_DOCUMENTS",
                "重试失败文档或重新上传",
                "high",
            )
        )
    if empty:
        deductions.append(
            _deduction(
                "EMPTY_DOCUMENTS",
                f"{empty} 个文档没有解析出页面",
                "空文档占用索引但没有可检索内容。",
            )
        )
        actions.append(
            _action(
                "REVIEW_EMPTY_DOCUMENTS",
                "检查空文档的 PDF 结构",
                "medium",
            )
        )
    if not index_verified:
        deductions.append(
            _deduction(
                "INDEX_MISSING",
                "检索索引尚未建立或已验证",
                "问答会阻止检索，无法使用该知识库。",
            )
        )
        actions.append(
            _action(
                "REBUILD_INDEX",
                "重建索引以绑定嵌入模型与分块版本",
                "high",
            )
        )
    if temporal_ratio < 0.5:
        deductions.append(
            _deduction(
                "TEMPORAL_METADATA_SPARSE",
                "超过一半文档缺少业务日期",
                "时效类问题的回答会因缺少发布锚点而保守拒答。",
            )
        )
        actions.append(
            _action(
                "DECLARE_TEMPORAL_METADATA",
                "为文档声明发布时间",
                "medium",
            )
        )

    series_deductions, series_actions, series_penalty = compute_series_detections(
        documents
    )
    score = _clamp(score - series_penalty)
    deductions = deductions + series_deductions
    actions = actions + series_actions

    metrics = {
        "total_documents": total,
        "ready_documents": ready,
        "failed_documents": failed,
        "empty_documents": empty,
        "total_pages": total_pages,
        "index_verified": index_verified,
        "temporal_metadata_ratio": round(temporal_ratio, 4),
        "series_penalty": series_penalty,
    }
    return {
        "score": _round(score),
        "metrics": metrics,
        "deductions": deductions,
        "actions": actions,
    }


def compute_retrieval_attribution(
    message_rows: Sequence[dict[str, Any]],
    ready_documents: Sequence[Document] | None = None,
    referenced_document_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Build retrieval attribution from observed rows (formula 1.1).

    Pure statistics, no LLM. query_type is bucketed with None -> "unknown" so
    the total rates always reconcile with the per-type counts. Each type
    carries sample_count/count/rate so 1/1 is never shown as a confident 100%.
    Unreferenced documents use all ready documents as the denominator and
    describe the recent observation window, not a permanent quality verdict.
    """

    def cluster(flag: str) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, int]] = {}
        for row in message_rows:
            qtype = row.get("query_type") or "unknown"
            bucket = buckets.setdefault(qtype, {"sample_count": 0, "count": 0})
            bucket["sample_count"] += 1
            if flag == "refusal" and row.get("policy_action") == "refuse":
                bucket["count"] += 1
            if (
                flag == "insufficient"
                and row.get("answer_state") in {"insufficient_evidence", "failed"}
            ):
                bucket["count"] += 1
        return [
            {
                "query_type": qtype,
                "sample_count": bucket["sample_count"],
                "count": bucket["count"],
                "rate": round(
                    bucket["count"] / bucket["sample_count"],
                    4,
                )
                if bucket["sample_count"]
                else 0.0,
            }
            for qtype, bucket in sorted(buckets.items())
        ]

    if ready_documents is None or referenced_document_ids is None:
        unreferenced: list[dict[str, Any]] = []
        referenced_ready_coverage: float | None = None
    else:
        ready_ids = {
            document.id
            for document in ready_documents
            if document.status == "ready"
        }
        referenced_ready = ready_ids & referenced_document_ids
        total_ready = len(ready_ids)
        referenced_ready_coverage = (
            round(len(referenced_ready) / total_ready, 4) if total_ready else None
        )
        unreferenced = [
            {"id": document.id, "display_name": document.display_name}
            for document in ready_documents
            if document.status == "ready"
            and document.id not in referenced_document_ids
        ]

    return {
        "unreferenced_documents": unreferenced,
        "referenced_ready_coverage": referenced_ready_coverage,
        "refusal_by_query_type": cluster("refusal"),
        "insufficient_by_query_type": cluster("insufficient"),
    }


def compute_retrieval_health(
    message_rows: Sequence[dict[str, Any]],
    ready_documents: Sequence[Document] | None = None,
    referenced_document_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Score retrieval health from observed answer/citation rows.

    The metrics are online observations from real conversations, not a gold
    benchmark. When fewer than MIN_RETRIEVAL_SAMPLE answers exist the score is
    None (insufficient data) while the metrics are still returned.
    """

    sample_count = len(message_rows)
    if sample_count == 0:
        return {
            "score": None,
            "metrics": {
                "message_sample_count": 0,
                "citation_coverage_rate": 0.0,
                "avg_citations_per_message": 0.0,
                "refusal_rate": 0.0,
                "insufficient_evidence_rate": 0.0,
                "web_source_rate": 0.0,
                "attribution": compute_retrieval_attribution(
                    message_rows,
                    ready_documents,
                    referenced_document_ids,
                ),
            },
            "deductions": [
                _deduction(
                    "NO_ANSWER_SAMPLES",
                    "还没有可用的问答记录",
                    "检索健康需要真实问答样本来评估。",
                )
            ],
            "actions": [
                _action(
                    "ADD_ANSWER_SAMPLES",
                    "在知识库上发起几次真实问答后再评分",
                    "medium",
                )
            ],
        }

    cited = sum(1 for row in message_rows if row.get("citation_count", 0) > 0)
    citation_coverage = cited / sample_count
    avg_citations = (
        sum(row.get("citation_count", 0) for row in message_rows) / sample_count
    )
    refusal_rate = (
        sum(1 for row in message_rows if row.get("policy_action") == "refuse")
        / sample_count
    )
    insufficient_rate = (
        sum(
            1
            for row in message_rows
            if row.get("answer_state") in {"insufficient_evidence", "failed"}
        )
        / sample_count
    )
    total_citations = sum(row.get("citation_count", 0) for row in message_rows)
    web_count = sum(row.get("web_citation_count", 0) for row in message_rows)
    web_source_rate = (web_count / total_citations) if total_citations else 0.0

    score = _clamp(
        100.0
        * (
            0.60 * citation_coverage
            + 0.20 * (1.0 - insufficient_rate)
            + 0.20 * (1.0 - refusal_rate)
        )
    )
    if sample_count < MIN_RETRIEVAL_SAMPLE:
        score = None

    deductions: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    if score is not None:
        if citation_coverage < 0.5:
            deductions.append(
                _deduction(
                    "LOW_CITATION_COVERAGE",
                    "引用覆盖不足",
                    "相当一部分回答没有可追溯的引用来源。",
                )
            )
            actions.append(
                _action(
                    "REVIEW_RETRIEVAL",
                    "检查检索阈值与分块配置，必要时重建索引",
                    "high",
                )
            )
        if refusal_rate > 0.3:
            deductions.append(
                _deduction(
                    "HIGH_REFUSAL_RATE",
                    "无依据拒答比例偏高",
                    "知识库可能缺少用户常问内容。",
                )
            )
            actions.append(
                _action(
                    "EXPAND_KNOWLEDGE_BASE",
                    "补充缺失主题的文档",
                    "medium",
                )
            )
        if insufficient_rate > 0.3:
            deductions.append(
                _deduction(
                    "HIGH_INSUFFICIENT_EVIDENCE",
                    "证据不足回答比例偏高",
                    "检索没有为多数问题带回足够证据。",
                )
            )
            actions.append(
                _action(
                    "EXPAND_KNOWLEDGE_BASE",
                    "补充覆盖不足主题的文档",
                    "medium",
                )
            )

    metrics = {
        "message_sample_count": sample_count,
        "citation_coverage_rate": round(citation_coverage, 4),
        "avg_citations_per_message": round(avg_citations, 4),
        "refusal_rate": round(refusal_rate, 4),
        "insufficient_evidence_rate": round(insufficient_rate, 4),
        "web_source_rate": round(web_source_rate, 4),
        "attribution": compute_retrieval_attribution(
            message_rows,
            ready_documents,
            referenced_document_ids,
        ),
    }
    return {
        "score": _round(score),
        "metrics": metrics,
        "deductions": deductions,
        "actions": actions,
    }


def compute_trust_health(
    claim_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Score answer trustworthiness from persisted AnswerClaim rows (0-100)."""

    claim_count = len(claim_rows)
    if claim_count == 0:
        return {
            "score": None,
            "metrics": {
                "claim_count": 0,
                "supported_claim_rate": 0.0,
                "conflicting_claim_rate": 0.0,
                "invalid_citation_rate": 0.0,
                "consistency_issue_rate": 0.0,
                "entailment_contradiction_rate": 0.0,
            },
            "deductions": [
                _deduction(
                    "NO_CLAIMS",
                    "还没有可评估的声明",
                    "回答可信度需要持久化的声明与引用数据。",
                )
            ],
            "actions": [
                _action(
                    "ADD_ANSWER_SAMPLES",
                    "在知识库上发起几次真实问答后再评分",
                    "medium",
                )
            ],
        }

    supported = sum(
        1 for row in claim_rows if row.get("support_status") == "supported"
    )
    conflicting = sum(
        1 for row in claim_rows if row.get("support_status") == "conflicting"
    )
    invalid_citations = sum(
        1
        for row in claim_rows
        if row.get("citation_status") in {"invalid", "missing"}
    )
    consistency_issues = sum(
        1
        for row in claim_rows
        if row.get("consistency_status") == "inconsistent"
    )
    entailment_contradictions = sum(
        1
        for row in claim_rows
        if row.get("entailment_status") == "contradicted"
    )

    supported_rate = supported / claim_count
    conflicting_rate = conflicting / claim_count
    invalid_citation_rate = invalid_citations / claim_count
    consistency_issue_rate = consistency_issues / claim_count
    entailment_rate = entailment_contradictions / claim_count

    score = _clamp(
        100.0
        * (
            0.55 * supported_rate
            + 0.20 * (1.0 - invalid_citation_rate)
            + 0.15 * (1.0 - consistency_issue_rate)
            + 0.10 * (1.0 - entailment_rate)
        )
    )
    if claim_count < MIN_TRUST_SAMPLE:
        score = None

    deductions: list[dict[str, str]] = []
    actions: list[dict[str, str]] = []
    if score is not None:
        if supported_rate < 0.8:
            deductions.append(
                _deduction(
                    "LOW_CLAIM_SUPPORT",
                    "声明支持率偏低",
                    "部分声明缺少有效证据会被策略改写或拒答。",
                )
            )
            actions.append(
                _action(
                    "REVIEW_RETRIEVAL",
                    "检查检索质量与证据是否充分",
                    "high",
                )
            )
        if invalid_citation_rate > 0.1:
            deductions.append(
                _deduction(
                    "INVALID_CITATIONS",
                    "存在无效或缺失引用",
                    "引用无法定位原文时核验会失败。",
                )
            )
            actions.append(
                _action(
                    "REVIEW_CITATIONS",
                    "检查文档解析与引用定位",
                    "medium",
                )
            )
        if consistency_issue_rate > 0.05:
            deductions.append(
                _deduction(
                    "CONSISTENCY_ISSUES",
                    "存在一致性冲突声明",
                    "冲突声明即使有引用也会被降级处理。",
                )
            )
            actions.append(
                _action(
                    "REVIEW_SOURCE_CONFLICTS",
                    "核对冲突资料的准确性",
                    "medium",
                )
            )
        if entailment_rate > 0.05:
            deductions.append(
                _deduction(
                    "ENTAILMENT_CONTRADICTIONS",
                    "存在语义矛盾声明",
                    "与证据语义矛盾的声明会被策略拒绝。",
                )
            )
            actions.append(
                _action(
                    "REVIEW_GROUNDING",
                    "检查证据与答案的语义一致性",
                    "medium",
                )
            )

    metrics = {
        "claim_count": claim_count,
        "supported_claim_rate": round(supported_rate, 4),
        "conflicting_claim_rate": round(conflicting_rate, 4),
        "invalid_citation_rate": round(invalid_citation_rate, 4),
        "consistency_issue_rate": round(consistency_issue_rate, 4),
        "entailment_contradiction_rate": round(entailment_rate, 4),
    }
    return {
        "score": _round(score),
        "metrics": metrics,
        "deductions": deductions,
        "actions": actions,
    }


def assemble_health_payload(
    session: Session,
    knowledge_base: KnowledgeBase,
) -> dict[str, Any]:
    """Compute the full snapshot payload for a knowledge base."""

    documents = session.scalars(
        select(Document).where(Document.knowledge_base_id == knowledge_base.id)
    ).all()
    index_verified = knowledge_base.index_manifest is not None
    data = compute_data_health(documents, index_verified)

    messages = session.scalars(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .options(selectinload(Message.citations))
        .where(
            Conversation.knowledge_base_id == knowledge_base.id,
            Message.role == "assistant",
            Message.status == "completed",
        )
        .order_by(Message.created_at.desc())
        .limit(RETRIEVAL_SAMPLE_LIMIT)
    ).all()
    referenced_document_ids: set[str] = set()
    message_rows = []
    for message in messages:
        for citation in message.citations:
            if citation.document_id is not None:
                referenced_document_ids.add(citation.document_id)
        message_rows.append(
            {
                "citation_count": len(message.citations),
                "web_citation_count": sum(
                    1
                    for citation in message.citations
                    if citation.source_type == "web"
                ),
                "policy_action": message.policy_action,
                "answer_state": message.answer_state,
                "query_type": message.query_type,
            }
        )
    ready_documents = [document for document in documents if document.status == "ready"]
    retrieval = compute_retrieval_health(
        message_rows,
        ready_documents,
        referenced_document_ids,
    )

    claims = session.scalars(
        select(AnswerClaim)
        .join(Message, AnswerClaim.message_id == Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.knowledge_base_id == knowledge_base.id)
    ).all()
    claim_rows = [
        {
            "support_status": claim.support_status,
            "citation_status": claim.citation_status,
            "consistency_status": claim.consistency_status,
            "entailment_status": claim.entailment_status,
        }
        for claim in claims
    ]
    trust = compute_trust_health(claim_rows)

    overall = aggregate_overall_score(
        data["score"],
        retrieval["score"],
        trust["score"],
    )
    status = "complete" if overall is not None else "partial"

    return {
        "formula_version": HEALTH_FORMULA_VERSION,
        "status": status,
        "level": get_health_level(overall),
        "overall_score": _round(overall),
        "data_score": data["score"],
        "retrieval_score": retrieval["score"],
        "trust_score": trust["score"],
        "metrics": {
            "data": data["metrics"],
            "retrieval": retrieval["metrics"],
            "trust": trust["metrics"],
        },
        "deductions": data["deductions"] + retrieval["deductions"] + trust["deductions"],
        "actions": data["actions"] + retrieval["actions"] + trust["actions"],
    }


def latest_health_snapshot(
    session: Session,
    knowledge_base_id: str,
) -> KnowledgeHealthSnapshot | None:
    return session.scalar(
        select(KnowledgeHealthSnapshot)
        .where(KnowledgeHealthSnapshot.knowledge_base_id == knowledge_base_id)
        .order_by(
            KnowledgeHealthSnapshot.created_at.desc(),
            KnowledgeHealthSnapshot.id.desc(),
        )
        .limit(1)
    )


def list_health_snapshots(
    session: Session,
    knowledge_base_id: str,
    limit: int = 30,
) -> list[KnowledgeHealthSnapshot]:
    return list(
        session.scalars(
            select(KnowledgeHealthSnapshot)
            .where(KnowledgeHealthSnapshot.knowledge_base_id == knowledge_base_id)
            .order_by(
                KnowledgeHealthSnapshot.created_at.desc(),
                KnowledgeHealthSnapshot.id.desc(),
            )
            .limit(limit)
        ).all()
    )


def snapshot_change(
    snapshot: KnowledgeHealthSnapshot,
    previous: KnowledgeHealthSnapshot | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "direction": "new",
            "overall_delta": None,
            "data_delta": None,
            "retrieval_delta": None,
            "trust_delta": None,
        }

    def delta(current: float | None, base: float | None) -> float | None:
        if current is None or base is None:
            return None
        return _round(current - base)

    return {
        "direction": "changed",
        "overall_delta": delta(snapshot.overall_score, previous.overall_score),
        "data_delta": delta(snapshot.data_score, previous.data_score),
        "retrieval_delta": delta(snapshot.retrieval_score, previous.retrieval_score),
        "trust_delta": delta(snapshot.trust_score, previous.trust_score),
    }


def create_health_snapshot(
    session: Session,
    knowledge_base: KnowledgeBase,
    owner_id: str,
) -> tuple[KnowledgeHealthSnapshot, KnowledgeHealthSnapshot | None, dict[str, Any]]:
    """Compute, persist and return a new health snapshot with its change."""

    payload = assemble_health_payload(session, knowledge_base)
    previous = latest_health_snapshot(session, knowledge_base.id)
    snapshot = KnowledgeHealthSnapshot(
        knowledge_base_id=knowledge_base.id,
        owner_id=owner_id,
        **{key: value for key, value in payload.items() if key != "level"},
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot, previous, snapshot_change(snapshot, previous)


def health_snapshot_response(snapshot: KnowledgeHealthSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "knowledge_base_id": snapshot.knowledge_base_id,
        "formula_version": snapshot.formula_version,
        "status": snapshot.status,
        "level": get_health_level(snapshot.overall_score),
        "overall_score": snapshot.overall_score,
        "data_score": snapshot.data_score,
        "retrieval_score": snapshot.retrieval_score,
        "trust_score": snapshot.trust_score,
        "metrics": snapshot.metrics,
        "deductions": snapshot.deductions or [],
        "actions": snapshot.actions or [],
        "created_at": snapshot.created_at,
    }


def get_health_level(overall: float | None) -> str | None:
    """Machine-code health level shared by health/snapshot/trend responses.

    Thresholds are part of the formula 1.1 contract: >= 60 healthy,
    40 <= score < 60 warning, < 40 critical.
    """
    if overall is None:
        return None
    if overall < 40:
        return "critical"
    if overall < 60:
        return "warning"
    return "healthy"


def aggregate_overall_score(
    data_score: float | None,
    retrieval_score: float | None,
    trust_score: float | None,
) -> float | None:
    """Formula 1.1 aggregation shared by production and gold tests.

    Returns None when any dimension is not computable (partial); otherwise
    data 40% + retrieval 30% + trust 30%.
    """
    if data_score is None or retrieval_score is None or trust_score is None:
        return None
    return (
        DATA_WEIGHT * float(data_score)
        + RETRIEVAL_WEIGHT * float(retrieval_score)
        + TRUST_WEIGHT * float(trust_score)
    )


def health_trend_payload(
    snapshots: Sequence[KnowledgeHealthSnapshot],
) -> dict[str, Any]:
    """Build the ascending trend series from snapshots by created_at."""
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.created_at)
    items = [
        {
            "created_at": snapshot.created_at,
            "overall": snapshot.overall_score,
            "data": snapshot.data_score,
            "retrieval": snapshot.retrieval_score,
            "trust": snapshot.trust_score,
            "level": get_health_level(snapshot.overall_score),
        }
        for snapshot in ordered
    ]
    return {"items": items}


async def run_health_actions(
    session: Session,
    knowledge_base: KnowledgeBase,
    owner_id: str,
    actions: Sequence[str],
) -> dict[str, Any]:
    """Execute health suggestion actions and immediately re-evaluate.

    Knowledge Health only orchestrates existing services; it does not re-
    implement document/index workflows. The returned delta is the immediate
    re-evaluation value and never claims that async retry/reindex completed.
    """

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code in actions:
        if code in seen:
            continue
        seen.add(code)
        if code == "RETRY_FAILED_DOCUMENTS":
            failed = session.scalars(
                select(Document).where(
                    Document.knowledge_base_id == knowledge_base.id,
                    Document.status == "failed",
                )
            ).all()
            retried = 0
            try:
                for document in failed:
                    retry_document(session, document)
                    retried += 1
                results.append(
                    {
                        "code": code,
                        "status": "succeeded",
                        "affected_count": retried,
                        "message": f"已重试 {retried} 个失败文档",
                    }
                )
            except ProductError as exc:
                results.append(
                    {
                        "code": code,
                        "status": "failed",
                        "affected_count": retried,
                        "message": exc.message,
                    }
                )
        elif code == "REBUILD_INDEX":
            try:
                await reindex_knowledge_base(session, knowledge_base)
                results.append(
                    {
                        "code": code,
                        "status": "succeeded",
                        "affected_count": 1,
                        "message": "已提交索引重建",
                    }
                )
            except ProductError as exc:
                results.append(
                    {
                        "code": code,
                        "status": "failed",
                        "affected_count": 0,
                        "message": exc.message,
                    }
                )
        elif code == "UPLOAD_DOCUMENTS":
            results.append(
                {
                    "code": code,
                    "status": "requires_user_action",
                    "affected_count": 0,
                    "message": "上传更多文档以提升覆盖率。",
                }
            )
        else:
            raise ProductError(
                "UNKNOWN_HEALTH_ACTION",
                f"未知健康动作：{code}",
                status_code=400,
            )

    snapshot, previous, change = create_health_snapshot(
        session,
        knowledge_base,
        owner_id,
    )
    return {
        "results": results,
        "snapshot": health_snapshot_response(snapshot),
        "delta": change,
    }
