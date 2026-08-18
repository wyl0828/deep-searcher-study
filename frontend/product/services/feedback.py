"""User message feedback (P1-4.2, aligned with ragent MessageFeedback).

Python equivalent of the ragent feedback stack:
- MessageFeedbackDO: the MessageFeedback model (models.py)
- MessageFeedbackServiceImpl.submitFeedback / cancelFeedbackAsync / getUserVotes:
  submit_message_feedback / cancel_message_feedback / get_feedback_map
- doUpsertFeedback + upsertActiveFeedback / upsertCancelledFeedback:
  _upsert_feedback() performs an idempotent upsert keyed by (user_id, message_id)

The conversation is never stored on the feedback row; it is reached through
message -> conversation. Both submit and cancel share the same concurrency-safe
upsert so every "missing -> INSERT" path is protected by a SAVEPOINT against
unique-key races (no POST-idempotent / DELETE-intermittent-500 split).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from frontend.product.errors import ProductError
from frontend.product.models import MessageFeedback

_MAX_REASON = 300
_MAX_COMMENT = 2000


def _limit(value: str | None, max_length: int) -> str | None:
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]


def _upsert_feedback(
    session: Session,
    *,
    user_id: str,
    message_id: str,
    vote: int | None,
    reason: str | None,
    comment: str | None,
    cancelled: bool,
) -> MessageFeedback:
    """Idempotent (user_id, message_id) upsert shared by submit and cancel.

    Concurrent writers may both miss the initial SELECT. The INSERT is wrapped
    in a nested transaction (SAVEPOINT) so a unique-key conflict only rolls back
    the savepoint; the row is then reloaded and updated instead of failing.
    """
    existing = session.scalar(
        select(MessageFeedback).where(
            MessageFeedback.user_id == user_id,
            MessageFeedback.message_id == message_id,
        )
    )
    if existing is not None:
        existing.vote = vote
        existing.reason = reason
        existing.comment = comment
        existing.cancelled = cancelled
        session.flush()
        return existing

    feedback = MessageFeedback(
        user_id=user_id,
        message_id=message_id,
        vote=vote,
        reason=reason,
        comment=comment,
        cancelled=cancelled,
    )
    try:
        with session.begin_nested():
            session.add(feedback)
            session.flush()
    except IntegrityError:
        reloaded = session.scalar(
            select(MessageFeedback).where(
                MessageFeedback.user_id == user_id,
                MessageFeedback.message_id == message_id,
            )
        )
        if reloaded is None:
            raise
        reloaded.vote = vote
        reloaded.reason = reason
        reloaded.comment = comment
        reloaded.cancelled = cancelled
        session.flush()
        return reloaded
    return feedback


def submit_message_feedback(
    session: Session,
    *,
    user_id: str,
    message_id: str,
    vote: int,
    reason: str | None = None,
    comment: str | None = None,
) -> MessageFeedback:
    """Submit or overwrite a vote (1 = helpful, -1 = unhelpful)."""
    if vote not in (1, -1):
        raise ProductError(
            "INVALID_FEEDBACK_VOTE",
            "反馈值必须为 1 或 -1。",
            status_code=400,
        )
    return _upsert_feedback(
        session,
        user_id=user_id,
        message_id=message_id,
        vote=vote,
        reason=_limit(reason, _MAX_REASON),
        comment=_limit(comment, _MAX_COMMENT),
        cancelled=False,
    )


def cancel_message_feedback(
    session: Session,
    *,
    user_id: str,
    message_id: str,
) -> MessageFeedback:
    """Cancel feedback for a message (creates a cancelled row when absent).

    Mirrors ragent upsertCancelledFeedback: the vote is cleared and the row is
    marked cancelled, so DELETE is naturally idempotent.
    """
    return _upsert_feedback(
        session,
        user_id=user_id,
        message_id=message_id,
        vote=None,
        reason=None,
        comment=None,
        cancelled=True,
    )


def get_feedback_map(
    session: Session,
    *,
    user_id: str,
    message_ids: Sequence[str],
) -> dict[str, dict[str, object]]:
    """Return {message_id: {"vote", "cancelled"}} for one user (getUserVotes)."""
    ids = list(message_ids)
    if not ids:
        return {}
    records = session.scalars(
        select(MessageFeedback).where(
            MessageFeedback.user_id == user_id,
            MessageFeedback.message_id.in_(ids),
        )
    ).all()
    return {
        record.message_id: {
            "vote": record.vote,
            "cancelled": record.cancelled,
        }
        for record in records
    }
