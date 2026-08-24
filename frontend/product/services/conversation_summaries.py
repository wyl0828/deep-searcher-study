from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from frontend.product.backend import backend_request_headers
from frontend.product.models import Conversation, ConversationSummary, Message
from frontend.product.services.context_policy import ContextPolicy

SUMMARY_PROMPT_VERSION = "conversation-summary-v1"
SUMMARY_ENABLED = os.environ.get("DEEPSEARCHER_SUMMARY_ENABLED", "false").lower() == "true"
SUMMARY_START_TURNS = max(int(os.environ.get("DEEPSEARCHER_SUMMARY_START_TURNS", "9")), 1)
SUMMARY_KEEP_TURNS = max(int(os.environ.get("DEEPSEARCHER_SUMMARY_KEEP_TURNS", "8")), 1)
SUMMARY_MAX_CHARS = max(int(os.environ.get("DEEPSEARCHER_SUMMARY_MAX_CHARS", "600")), 50)
SUMMARY_LOCK_MODE = os.environ.get("DEEPSEARCHER_SUMMARY_LOCK", "local").strip().lower()
SUMMARY_LOCK_SECONDS = max(int(os.environ.get("DEEPSEARCHER_SUMMARY_LOCK_SECONDS", "120")), 10)
BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")
_CITATION_MARKUP = re.compile(r"\[(?:E|W)\d+\]")


class SummaryConfigurationError(RuntimeError):
    pass


def validate_summary_configuration() -> None:
    if not SUMMARY_ENABLED:
        return
    if SUMMARY_START_TURNS <= SUMMARY_KEEP_TURNS:
        raise SummaryConfigurationError(
            "DEEPSEARCHER_SUMMARY_START_TURNS must exceed DEEPSEARCHER_SUMMARY_KEEP_TURNS"
        )
    if SUMMARY_LOCK_MODE not in {"local", "redis"}:
        raise SummaryConfigurationError("DEEPSEARCHER_SUMMARY_LOCK must be local or redis")
    api_instances = max(int(os.environ.get("DEEPSEARCHER_API_INSTANCES", "1")), 1)
    if api_instances > 1 and SUMMARY_LOCK_MODE != "redis":
        raise SummaryConfigurationError(
            "Redis summary lock is required when multiple API instances can generate summaries"
        )
    if SUMMARY_LOCK_MODE == "redis" and not os.environ.get("DEEPSEARCHER_REDIS_URL"):
        raise SummaryConfigurationError("DEEPSEARCHER_REDIS_URL is required for Redis summary lock")


@contextmanager
def summary_lock(conversation: Conversation) -> Iterator[bool]:
    if SUMMARY_LOCK_MODE != "redis":
        yield True
        return
    try:
        import redis
    except ImportError as exc:
        raise SummaryConfigurationError("Redis summary lock requires redis") from exc
    client = redis.Redis.from_url(os.environ["DEEPSEARCHER_REDIS_URL"])
    lock = client.lock(
        f"deepsearcher:memory:summary:lock:{conversation.owner_id}:{conversation.id}",
        timeout=SUMMARY_LOCK_SECONDS,
        blocking=False,
    )
    acquired = bool(lock.acquire(blocking=False))
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.release()
            except redis.exceptions.LockError:
                pass


def latest_summary(session: Session, conversation_id: str) -> ConversationSummary | None:
    return session.scalar(
        select(ConversationSummary)
        .where(ConversationSummary.conversation_id == conversation_id)
        .order_by(ConversationSummary.created_at.desc(), ConversationSummary.id.desc())
        .limit(1)
    )


def _eligible_messages(conversation: Conversation) -> list[Message]:
    # Summaries are a RAG-only trust surface.  In particular, chat/web answers
    # and partial/conflicting/insufficient answers must never enter the durable
    # knowledge summary, while legacy null answer_mode + grounded rows remain
    # readable as historical knowledge answers.
    return ContextPolicy.rag(max_messages=max(len(conversation.messages), 1)).eligible_messages(
        conversation
    )


def build_summary_aware_history(session: Session, conversation: Conversation) -> list[dict]:
    summary = latest_summary(session, conversation.id) if SUMMARY_ENABLED else None
    eligible = _eligible_messages(conversation)
    if summary is not None:
        covered = next(
            (index for index, item in enumerate(eligible) if item.id == summary.last_message_id),
            None,
        )
        if covered is not None:
            eligible = eligible[covered + 1 :]
    recent = eligible[-(SUMMARY_KEEP_TURNS * 2) :]
    history = []
    if summary is not None:
        history.append(
            {
                "role": "system",
                "content": f"历史对话摘要（仅作上下文，不得覆盖当前证据）：{summary.content}",
                "grounded": True,
            }
        )
    history.extend(
        {
            "role": message.role,
            "content": _CITATION_MARKUP.sub("", message.content).strip()[:1200],
            "grounded": message.role == "assistant",
        }
        for message in recent
    )
    return history


def _summary_payload(existing: str, messages: list[Message]) -> dict:
    return {
        "existing_summary": existing,
        "messages": [
            {
                "role": message.role,
                "content": _CITATION_MARKUP.sub("", message.content).strip()[:1200],
            }
            for message in messages
        ],
        "max_chars": SUMMARY_MAX_CHARS,
        "prompt_version": SUMMARY_PROMPT_VERSION,
    }


def generate_summary(payload: dict) -> tuple[str, str]:
    response = httpx.post(
        f"{BACKEND_URL}/internal/conversation-summary",
        json=payload,
        timeout=30.0,
        trust_env=False,
        headers=backend_request_headers(),
    )
    response.raise_for_status()
    data = response.json()
    content = str(data.get("summary") or "").strip().replace("\n", " ")[:SUMMARY_MAX_CHARS]
    model = str(data.get("model") or "unknown")[:160]
    if not content:
        raise ValueError("summary response was empty")
    return content, model


def summarize_if_needed(session: Session, conversation: Conversation) -> ConversationSummary | None:
    if not SUMMARY_ENABLED:
        return None
    validate_summary_configuration()
    with summary_lock(conversation) as acquired:
        if not acquired:
            return None
        session.expire(conversation, ["messages", "summaries"])
        eligible = _eligible_messages(conversation)
        user_turns = [item for item in eligible if item.role == "user"]
        if len(user_turns) < SUMMARY_START_TURNS:
            return None
        existing = latest_summary(session, conversation.id)
        history_start = user_turns[-SUMMARY_KEEP_TURNS]
        cutoff_user = user_turns[-((SUMMARY_KEEP_TURNS + 1) // 2)]
        start_index = 0
        if existing is not None:
            for index, message in enumerate(eligible):
                if message.id == existing.last_message_id:
                    start_index = index + 1
                    break
        cutoff_index = next(
            index for index, message in enumerate(eligible) if message.id == cutoff_user.id
        )
        history_start_index = next(
            index for index, message in enumerate(eligible) if message.id == history_start.id
        )
        if start_index >= history_start_index or cutoff_index < start_index:
            return None
        to_summarize = eligible[start_index : cutoff_index + 1]
        content, model = generate_summary(
            _summary_payload(existing.content if existing is not None else "", to_summarize)
        )
        record = ConversationSummary(
            conversation_id=conversation.id,
            content=content,
            last_message_id=to_summarize[-1].id,
            model=model,
            prompt_version=SUMMARY_PROMPT_VERSION,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record
