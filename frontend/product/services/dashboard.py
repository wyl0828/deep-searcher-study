"""Dashboard service (aligned with ragent DashboardService + VOs).

- KpiVO uniform schema: {value, delta, delta_pct}; delta = 24h increment;
  delta_pct = delta / base * 100, base = value - delta; base == 0 -> null.
- Trends: fixed calendar window (today + the previous N-1 days), fixed N points,
  zero-fill, same-day merge, UTC bucketing. series are documents / messages /
  feedback (feedback = currently-valid negative feedback by first-created day).
- Health distribution: batch latest snapshot per KB + an "unknown" bucket for KBs
  with no snapshot; healthy+warning+critical+partial+unknown == knowledge base count.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from frontend.product.models import (
    ConnectorSync,
    Conversation,
    Document,
    KnowledgeBase,
    KnowledgeHealthSnapshot,
    Message,
    MessageFeedback,
    OperationAuditLog,
    User,
    utcnow,
)
from frontend.product.services.knowledge_health import get_health_level

_DASHBOARD_TIMEZONE = timezone.utc


def _kpi(
    value: int,
    *,
    delta: int | None = None,
    base: int | None = None,
) -> dict[str, Any]:
    """Uniform KpiVO: delta/delta_pct are null when not comparable."""
    if delta is None or base is None or base <= 0:
        return {"value": value, "delta": None, "delta_pct": None}
    return {
        "value": value,
        "delta": delta,
        "delta_pct": round(delta / base * 100, 2),
    }


def _count_24h(session: Session, model, column, now: datetime) -> int:
    threshold = now - timedelta(hours=24)
    return int(session.scalar(select(func.count(model.id)).where(column >= threshold)) or 0)


def _daily_series(
    timestamps: list[datetime],
    *,
    days: Literal[7, 30],
    now: datetime,
    timezone_zone: timezone = _DASHBOARD_TIMEZONE,
) -> list[dict[str, Any]]:
    """Fixed calendar window (today + previous N-1 days), zero-filled, UTC-bucketed."""
    today = now.astimezone(timezone_zone).date()
    start = today - timedelta(days=days - 1)
    buckets: dict[Any, int] = {start + timedelta(days=i): 0 for i in range(days)}
    for timestamp in timestamps:
        day = timestamp.astimezone(timezone_zone).date()
        if day in buckets:
            buckets[day] += 1
    return [{"ts": day.isoformat(), "value": buckets[day]} for day in sorted(buckets)]


def _health_distribution(session: Session) -> dict[str, int]:
    """Batch latest snapshot level per KB (no per-KB N+1) + unknown bucket."""
    knowledge_bases = session.scalars(select(KnowledgeBase)).all()
    snapshots = session.scalars(
        select(KnowledgeHealthSnapshot).order_by(
            KnowledgeHealthSnapshot.created_at.desc(),
            KnowledgeHealthSnapshot.id.desc(),
        )
    ).all()
    latest_by_kb: dict[str, KnowledgeHealthSnapshot] = {}
    for snapshot in snapshots:
        latest_by_kb.setdefault(snapshot.knowledge_base_id, snapshot)

    distribution = {"healthy": 0, "warning": 0, "critical": 0, "partial": 0, "unknown": 0}
    for knowledge_base in knowledge_bases:
        snapshot = latest_by_kb.get(knowledge_base.id)
        if snapshot is None:
            distribution["unknown"] += 1
        elif snapshot.status == "partial":
            distribution["partial"] += 1
        else:
            level = get_health_level(snapshot.overall_score) or "unknown"
            distribution[level] += 1
    return distribution


def overview(session: Session) -> dict[str, Any]:
    """Aggregate the global KPI cards (DB service)."""
    now = utcnow()
    total_users = int(session.scalar(select(func.count(User.id))) or 0)
    users_24h = _count_24h(session, User, User.created_at, now)

    knowledge_base_count = int(session.scalar(select(func.count(KnowledgeBase.id))) or 0)

    document_total = int(session.scalar(select(func.count(Document.id))) or 0)
    document_ready = int(
        session.scalar(select(func.count(Document.id)).where(Document.status == "ready")) or 0
    )
    document_failed = int(
        session.scalar(select(func.count(Document.id)).where(Document.status == "failed")) or 0
    )

    total_conversations = int(session.scalar(select(func.count(Conversation.id))) or 0)
    conversations_24h = _count_24h(session, Conversation, Conversation.created_at, now)

    total_messages = int(session.scalar(select(func.count(Message.id))) or 0)
    messages_24h = _count_24h(session, Message, Message.created_at, now)

    current_negative_feedback = int(
        session.scalar(
            select(func.count(MessageFeedback.id)).where(
                MessageFeedback.cancelled.is_(False),
                MessageFeedback.vote == -1,
            )
        )
        or 0
    )

    active_connector_syncs = int(
        session.scalar(select(func.count(ConnectorSync.id)).where(ConnectorSync.status == "active"))
        or 0
    )

    audit_count = int(session.scalar(select(func.count(OperationAuditLog.id))) or 0)

    kpis = {
        "users": _kpi(total_users, delta=users_24h, base=total_users - users_24h),
        "knowledge_bases": _kpi(knowledge_base_count),
        "documents": {
            "value": document_total,
            "ready": document_ready,
            "failed": document_failed,
            "delta": None,
            "delta_pct": None,
        },
        "conversations": _kpi(
            total_conversations,
            delta=conversations_24h,
            base=total_conversations - conversations_24h,
        ),
        "messages": _kpi(total_messages, delta=messages_24h, base=total_messages - messages_24h),
        "feedback": _kpi(current_negative_feedback),
        "connector_syncs": _kpi(active_connector_syncs),
        "audit_logs": _kpi(audit_count),
    }
    return {
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "kpis": kpis,
        "health_distribution": _health_distribution(session),
    }


def trends(session: Session, *, days: int) -> dict[str, Any]:
    """Daily trends for documents / messages / feedback (bounded window rows)."""
    if days not in (7, 30):
        raise ValueError("days 必须是 7 或 30")
    now = utcnow()
    zone = _DASHBOARD_TIMEZONE
    local_now = now.astimezone(zone)
    start_local = (local_now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_utc = start_local.astimezone(zone).astimezone(timezone.utc)

    # Bounded selects: only rows inside the window, then bucket in Python (UTC).
    document_rows = list(
        session.scalars(select(Document.created_at).where(Document.created_at >= start_utc)).all()
    )
    message_rows = list(
        session.scalars(select(Message.created_at).where(Message.created_at >= start_utc)).all()
    )
    feedback_rows = list(
        session.scalars(
            select(MessageFeedback.created_at).where(
                MessageFeedback.cancelled.is_(False),
                MessageFeedback.vote == -1,
                MessageFeedback.created_at >= start_utc,
            )
        ).all()
    )

    series = [
        {
            "name": "documents",
            "data": _daily_series(document_rows, days=days, now=now, timezone_zone=zone),
        },
        {
            "name": "messages",
            "data": _daily_series(message_rows, days=days, now=now, timezone_zone=zone),
        },
        {
            "name": "feedback",
            "data": _daily_series(feedback_rows, days=days, now=now, timezone_zone=zone),
        },
    ]
    return {
        "metric": "activity",
        "window": f"{days}d",
        "granularity": "daily",
        "series": series,
    }
