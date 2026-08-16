"""Knowledge Health scoring for the product workspace (roadmap v0.4).

The health score is not a black-box number. Every snapshot persists the formula
version, the per-dimension scores, the raw metrics that produced them, the
deduction reasons and the suggested actions. The pure compute functions take
plain rows so they can be unit tested without a database.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from frontend.product.models import (
    AnswerClaim,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgeHealthSnapshot,
    Message,
)

HEALTH_FORMULA_VERSION = "1.0"

# Weights are part of the formula contract; changing them must bump the version.
DATA_WEIGHT = 0.4
RETRIEVAL_WEIGHT = 0.3
TRUST_WEIGHT = 0.3

MIN_RETRIEVAL_SAMPLE = 3
MIN_TRUST_SAMPLE = 3
RETRIEVAL_SAMPLE_LIMIT = 50


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, ndigits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, ndigits)


def _deduction(code: str, reason: str, impact: str) -> dict[str, str]:
    return {"code": code, "reason": reason, "impact": impact}


def _action(code: str, action: str, priority: str) -> dict[str, str]:
    return {"code": code, "action": action, "priority": priority}


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

    metrics = {
        "total_documents": total,
        "ready_documents": ready,
        "failed_documents": failed,
        "empty_documents": empty,
        "total_pages": total_pages,
        "index_verified": index_verified,
        "temporal_metadata_ratio": round(temporal_ratio, 4),
    }
    return {
        "score": _round(score),
        "metrics": metrics,
        "deductions": deductions,
        "actions": actions,
    }


def compute_retrieval_health(
    message_rows: Sequence[dict[str, Any]],
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
    message_rows = [
        {
            "citation_count": len(message.citations),
            "web_citation_count": sum(
                1
                for citation in message.citations
                if citation.source_type == "web"
            ),
            "policy_action": message.policy_action,
            "answer_state": message.answer_state,
        }
        for message in messages
    ]
    retrieval = compute_retrieval_health(message_rows)

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

    scores = [data["score"], retrieval["score"], trust["score"]]
    if all(score is not None for score in scores):
        overall = (
            DATA_WEIGHT * float(data["score"])
            + RETRIEVAL_WEIGHT * float(retrieval["score"])
            + TRUST_WEIGHT * float(trust["score"])
        )
        status = "complete"
    else:
        overall = None
        status = "partial"

    return {
        "formula_version": HEALTH_FORMULA_VERSION,
        "status": status,
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
        **payload,
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
        "overall_score": snapshot.overall_score,
        "data_score": snapshot.data_score,
        "retrieval_score": snapshot.retrieval_score,
        "trust_score": snapshot.trust_score,
        "metrics": snapshot.metrics,
        "deductions": snapshot.deductions or [],
        "actions": snapshot.actions or [],
        "created_at": snapshot.created_at,
    }
