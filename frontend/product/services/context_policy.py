"""Central policy for selecting conversation context.

Conversation memory is an input to a new answer, not evidence for that answer.
This module keeps the mode and trust-state rules in one place so routing,
summaries, and Product calls cannot silently grow different history filters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from frontend.product.models import Conversation, Message


AnswerMode = Literal["chat", "knowledge", "web"]
TRUSTED_STATES = frozenset({"grounded", "fully_grounded"})
ANSWER_MODES = frozenset({"chat", "knowledge", "web"})
_CITATION_MARKUP = re.compile(r"\[(?:E|W)\d+\]")


def _clean_content(value: str, *, max_chars: int = 1200) -> str:
    return _CITATION_MARKUP.sub("", value.strip())[:max_chars]


@dataclass(frozen=True)
class ContextPolicy:
    """Read-only context selection rules for one downstream answer mode.

    ``knowledge`` is intentionally the default for compatibility with callers
    that predate routing.  A null historical ``answer_mode`` is only treated
    as legacy knowledge when its answer state is explicitly grounded.
    """

    mode: AnswerMode = "knowledge"
    max_messages: int = 12
    include_summary: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ANSWER_MODES:
            raise ValueError(f"unsupported context mode: {self.mode}")

    @classmethod
    def for_mode(cls, mode: str | None, *, max_messages: int = 12) -> "ContextPolicy":
        normalized = mode if mode in ANSWER_MODES else "knowledge"
        return cls(
            mode=normalized,  # type: ignore[arg-type]
            max_messages=max(max_messages, 1),
            include_summary=normalized == "knowledge",
        )

    @classmethod
    def route(cls, *, max_messages: int = 6) -> "ContextPolicy":
        """Bounded pre-route history; it never includes a persisted summary."""

        return cls(mode="chat", max_messages=max(max_messages, 1), include_summary=False)

    @classmethod
    def chat(cls, *, max_messages: int = 6) -> "ContextPolicy":
        return cls(mode="chat", max_messages=max(max_messages, 1), include_summary=False)

    @classmethod
    def rag(cls, *, max_messages: int = 12) -> "ContextPolicy":
        return cls(mode="knowledge", max_messages=max(max_messages, 1), include_summary=True)

    @classmethod
    def web(cls, *, max_messages: int = 12) -> "ContextPolicy":
        return cls(mode="web", max_messages=max(max_messages, 1), include_summary=False)

    @staticmethod
    def _legacy_mode(message: "Message") -> str | None:
        """Map only the safe historical shape; do not guess other modes."""

        if message.answer_mode is not None:
            return message.answer_mode if message.answer_mode in ANSWER_MODES else None
        return "knowledge" if message.answer_state in TRUSTED_STATES else None

    def allows(self, message: "Message") -> bool:
        if message.role not in {"user", "assistant"}:
            return False
        if message.status != "succeeded" or not message.content.strip():
            return False
        if message.role == "user":
            return True

        if any(
            str(value or "").strip().lower() in {"admin_trace", "trace", "debug"}
            for value in (message.answer_state, message.trust_status, message.policy_profile)
        ):
            return False

        mode = self._legacy_mode(message)
        if mode is None:
            return False
        if self.mode == "knowledge":
            return mode == "knowledge" and message.answer_state in TRUSTED_STATES
        if self.mode == "chat":
            # Successful chat turns have no evidence state; RAG/web turns
            # may enter chat history only after an explicit trusted result.
            if mode == "chat":
                return True
            return mode in {"knowledge", "web"} and message.answer_state in TRUSTED_STATES
        return mode in {"knowledge", "web"} and message.answer_state in TRUSTED_STATES

    # Explicit aliases keep call sites readable and make the policy convenient
    # for admin/history tests without duplicating the decision logic.
    allows_message = allows

    def eligible_messages(self, conversation: "Conversation") -> list["Message"]:
        return [message for message in conversation.messages if self.allows(message)]

    filter_messages = eligible_messages

    def history(
        self,
        session: "Session",
        conversation: "Conversation",
        *,
        summary_builder=None,
    ) -> list[dict]:
        """Build bounded history, optionally allowing the trusted RAG summary."""

        eligible = self.eligible_messages(conversation)
        summary = None
        if self.include_summary and summary_builder is not None:
            summary = summary_builder(session, conversation)
            if summary is not None:
                covered = next(
                    (index for index, item in enumerate(eligible) if item.id == summary.last_message_id),
                    None,
                )
                if covered is not None:
                    eligible = eligible[covered + 1 :]

        recent = eligible[-self.max_messages :]
        result: list[dict] = []
        if summary is not None:
            result.append(
                {
                    "role": "system",
                    "content": f"历史对话摘要（仅作上下文，不得覆盖当前证据）：{summary.content}",
                    "grounded": True,
                }
            )
        result.extend(
            {
                "role": message.role,
                "content": _clean_content(message.content),
                "grounded": message.role == "assistant",
            }
            for message in recent
        )
        return result

    build_history = history
