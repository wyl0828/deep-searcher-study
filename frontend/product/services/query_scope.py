"""Resolve the knowledge scope for one user request.

The resolver is deliberately independent from the UI.  A conversation stores
only a scope policy; this module evaluates the current ACL and index state on
every message request, then returns an immutable execution snapshot for the
AnswerRun record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from frontend.product.errors import ProductError
from frontend.product.models import (
    Conversation,
    Document,
    KnowledgeBase,
    User,
)
from frontend.product.services.authorization import (
    list_accessible_knowledge_bases,
    require_knowledge_base_access,
)

ScopeMode = Literal["auto", "fixed"]
ScopeState = Literal[
    "ready",
    "no_access",
    "no_documents",
    "processing",
    "failed",
    "needs_rebuild",
    "partial",
    "unavailable",
    "unknown",
]

STATUS_KEYS = (
    "usable",
    "no_documents",
    "processing",
    "failed",
    "needs_rebuild",
    "unknown",
)


@dataclass(frozen=True)
class QueryScopeResolution:
    mode: ScopeMode
    knowledge_bases: tuple[KnowledgeBase, ...]
    collection_names: tuple[str, ...]
    payload: dict
    snapshot: dict


def _empty_status_counts() -> dict[str, int]:
    return {key: 0 for key in STATUS_KEYS}


def _index_verified(knowledge_base: KnowledgeBase) -> bool:
    if not knowledge_base.index_manifest:
        return False
    try:
        return isinstance(json.loads(knowledge_base.index_manifest), dict)
    except (TypeError, ValueError):
        return False


def _knowledge_base_status(
    knowledge_base: KnowledgeBase,
    document_stats: dict[str, dict[str, int]],
) -> str:
    stats = document_stats.get(
        knowledge_base.id,
        {"total": 0, "ready": 0, "processing": 0, "failed": 0},
    )
    if stats["total"] == 0:
        return "no_documents"
    if stats["ready"] > 0 and _index_verified(knowledge_base):
        return "usable"
    if stats["failed"] > 0:
        return "failed"
    if stats["processing"] > 0:
        return "processing"
    if stats["ready"] > 0:
        return "needs_rebuild"
    return "unknown"


def _document_stats(
    session: Session,
    knowledge_base_ids: list[str],
) -> dict[str, dict[str, int]]:
    stats = {
        knowledge_base_id: {"total": 0, "ready": 0, "processing": 0, "failed": 0}
        for knowledge_base_id in knowledge_base_ids
    }
    if not knowledge_base_ids:
        return stats
    documents = session.scalars(
        select(Document).where(Document.knowledge_base_id.in_(knowledge_base_ids))
    ).all()
    for document in documents:
        item = stats[document.knowledge_base_id]
        item["total"] += 1
        if document.status == "ready":
            item["ready"] += 1
        elif document.status in {"queued", "processing"}:
            item["processing"] += 1
        elif document.status == "failed":
            item["failed"] += 1
    return stats


def _aggregate_state(statuses: list[str]) -> tuple[ScopeState, dict[str, int]]:
    counts = _empty_status_counts()
    for status in statuses:
        counts[status if status in counts else "unknown"] += 1

    accessible = len(statuses)
    usable = counts["usable"]
    if accessible == 0:
        return "no_access", counts
    if usable == accessible:
        return "ready", counts
    if usable > 0:
        return "partial", counts

    unavailable_states = [key for key in STATUS_KEYS[1:] if counts[key] > 0]
    if len(unavailable_states) == 1:
        return unavailable_states[0], counts  # type: ignore[return-value]
    if unavailable_states == ["unknown"]:
        return "unknown", counts
    return "unavailable", counts


def _primary_action(state: ScopeState) -> str | None:
    if state == "processing":
        return "refresh"
    if state in {
        "no_access",
        "no_documents",
        "failed",
        "needs_rebuild",
        "unavailable",
        "unknown",
    }:
        return "contact_admin"
    return None


def _scope_payload(
    state: ScopeState,
    knowledge_base_count: int,
    counts: dict[str, int],
) -> dict:
    usable = counts["usable"]
    return {
        "state": state,
        "askable": usable > 0,
        "accessible_knowledge_base_count": knowledge_base_count,
        "usable_knowledge_base_count": usable,
        "status_counts": counts,
        "primary_action": _primary_action(state),
    }


def _build_resolution(
    *,
    mode: ScopeMode,
    knowledge_bases: list[KnowledgeBase],
    document_stats: dict[str, dict[str, int]],
) -> QueryScopeResolution:
    statuses = [
        _knowledge_base_status(knowledge_base, document_stats)
        for knowledge_base in knowledge_bases
    ]
    state, counts = _aggregate_state(statuses)
    payload = _scope_payload(state, len(knowledge_bases), counts)
    usable_knowledge_bases = tuple(
        knowledge_base
        for knowledge_base, status in zip(knowledge_bases, statuses, strict=True)
        if status == "usable"
    )
    collection_names = tuple(
        knowledge_base.collection_name for knowledge_base in usable_knowledge_bases
    )
    snapshot = {
        "schema_version": 1,
        "mode": mode,
        "knowledge_base_ids": [item.id for item in usable_knowledge_bases],
        "collection_names": list(collection_names),
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "status_counts": counts,
    }
    return QueryScopeResolution(
        mode=mode,
        knowledge_bases=usable_knowledge_bases,
        collection_names=collection_names,
        payload=payload,
        snapshot=snapshot,
    )


def resolve_user_query_scope(session: Session, user: User) -> QueryScopeResolution:
    """Return the live auto-scope view for a user, including unusable counts."""
    knowledge_bases = list_accessible_knowledge_bases(session, user)
    stats = _document_stats(session, [item.id for item in knowledge_bases])
    return _build_resolution(
        mode="auto",
        knowledge_bases=list(knowledge_bases),
        document_stats=stats,
    )


def resolve_conversation_scope(
    session: Session,
    user: User,
    conversation: Conversation,
) -> QueryScopeResolution:
    """Re-authorize and resolve a conversation immediately before querying."""
    mode = conversation.scope_mode or ("fixed" if conversation.knowledge_base_id else "auto")
    if mode == "fixed":
        if not conversation.knowledge_base_id:
            raise ProductError(
                "QUERY_SCOPE_INVALID",
                "这个对话没有有效的固定知识范围。",
                status_code=409,
            )
        knowledge_base = require_knowledge_base_access(
            session,
            user,
            conversation.knowledge_base_id,
            "read",
        )
        resolution = _build_resolution(
            mode="fixed",
            knowledge_bases=[knowledge_base],
            document_stats=_document_stats(session, [knowledge_base.id]),
        )
    else:
        resolution = resolve_user_query_scope(session, user)

    if not resolution.payload["askable"]:
        raise ProductError(
            "QUERY_SCOPE_UNAVAILABLE",
            "当前没有可用于问答的知识资料，请联系管理员。",
            status_code=409,
            retryable=resolution.payload["primary_action"] == "refresh",
        )
    return resolution
