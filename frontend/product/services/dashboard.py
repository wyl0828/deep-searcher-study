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

from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from frontend.product.models import (
    AnswerRun,
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
from sqlalchemy.orm import selectinload
from frontend.product.services.knowledge_health import get_health_level

_DASHBOARD_TIMEZONE = timezone.utc
_ANSWERS_TIMEZONE = ZoneInfo("Asia/Shanghai")


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


def _metric(
    *,
    value: float | None,
    sample_count: int,
    numerator: float | None,
    denominator: float | None,
) -> dict[str, Any]:
    return {
        "value": round(value, 2) if value is not None else None,
        "sample_count": sample_count,
        "numerator": numerator,
        "denominator": denominator,
    }


def _answer_quality(session: Session) -> dict[str, Any]:
    runs = session.scalars(
        select(AnswerRun).options(
            selectinload(AnswerRun.answer_message).selectinload(Message.citations),
        )
    ).all()
    succeeded = [run for run in runs if run.status == "succeeded"]
    failed = [run for run in runs if run.status == "failed"]
    cancelled = [run for run in runs if run.status == "cancelled"]
    success_denominator = len(succeeded) + len(failed)

    valid_feedback = session.scalars(
        select(MessageFeedback).where(
            MessageFeedback.cancelled.is_(False),
            MessageFeedback.vote.in_([-1, 1]),
        )
    ).all()
    negative_feedback = sum(item.vote == -1 for item in valid_feedback)
    terminal_count = len(succeeded) + len(failed) + len(cancelled)
    latency_values = [run.total_latency_ms for run in runs if run.total_latency_ms is not None]
    uncited_count = sum(
        run.answer_message is None or len(run.answer_message.citations) == 0
        for run in succeeded
    )

    return {
        "success_rate": _metric(
            value=(len(succeeded) / success_denominator * 100) if success_denominator else None,
            sample_count=success_denominator,
            numerator=len(succeeded) if success_denominator else None,
            denominator=success_denominator if success_denominator else None,
        ),
        "negative_feedback_rate": _metric(
            value=(negative_feedback / len(valid_feedback) * 100) if valid_feedback else None,
            sample_count=len(valid_feedback),
            numerator=negative_feedback if valid_feedback else None,
            denominator=len(valid_feedback) if valid_feedback else None,
        ),
        "feedback_coverage_rate": _metric(
            value=(len(valid_feedback) / terminal_count * 100) if terminal_count else None,
            sample_count=terminal_count,
            numerator=len(valid_feedback) if terminal_count else None,
            denominator=terminal_count if terminal_count else None,
        ),
        "uncited_answer_count": _metric(
            value=float(uncited_count) if succeeded else None,
            sample_count=len(succeeded),
            numerator=uncited_count if succeeded else None,
            denominator=len(succeeded) if succeeded else None,
        ),
        "average_latency_ms": _metric(
            value=(sum(latency_values) / len(latency_values)) if latency_values else None,
            sample_count=len(latency_values),
            numerator=sum(latency_values) if latency_values else None,
            denominator=len(latency_values) if latency_values else None,
        ),
        "cancelled_count": len(cancelled),
    }


def _count_24h(session: Session, model, column, now: datetime) -> int:
    threshold = now - timedelta(hours=24)
    return int(session.scalar(select(func.count(model.id)).where(column >= threshold)) or 0)


def _local_day_utc_bounds(
    now: datetime,
    *,
    timezone_zone: ZoneInfo = _ANSWERS_TIMEZONE,
) -> tuple[datetime, datetime]:
    """Return the half-open UTC bounds for the local calendar day."""

    local_now = now.astimezone(timezone_zone)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone_zone)
    local_next_start = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc),
        local_next_start.astimezone(timezone.utc),
    )


def _answers_today(session: Session, now: datetime) -> int:
    """Count AnswerRun creation attempts in today's Asia/Shanghai window."""

    utc_start, utc_end = _local_day_utc_bounds(now)
    return int(
        session.scalar(
            select(func.count(AnswerRun.id)).where(
                AnswerRun.created_at >= utc_start,
                AnswerRun.created_at < utc_end,
            )
        )
        or 0
    )


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
    answers_today = _answers_today(session, now)

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
        "answers_today": _kpi(answers_today),
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
        "quality": _answer_quality(session),
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
