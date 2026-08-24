"""Effective Core retrieval-mode compatibility helpers."""

from __future__ import annotations

from typing import Literal

RetrievalMode = Literal["knowledge", "web", "hybrid"]
RETRIEVAL_MODES = frozenset({"knowledge", "web", "hybrid"})


def resolve_retrieval_mode(
    retrieval_mode: str | None = None,
    *,
    use_web_search: bool = False,
) -> RetrievalMode:
    """Resolve the new mode while preserving the legacy boolean contract."""

    if retrieval_mode is None or str(retrieval_mode).strip() == "":
        return "hybrid" if bool(use_web_search) else "knowledge"
    value = str(retrieval_mode).strip().lower()
    if value not in RETRIEVAL_MODES:
        raise ValueError("retrieval_mode must be one of: knowledge, web, hybrid")
    return value  # type: ignore[return-value]


def uses_vector_db(retrieval_mode: str | None = None, *, use_web_search: bool = False) -> bool:
    return resolve_retrieval_mode(retrieval_mode, use_web_search=use_web_search) in {
        "knowledge",
        "hybrid",
    }


def uses_web_search(retrieval_mode: str | None = None, *, use_web_search: bool = False) -> bool:
    return resolve_retrieval_mode(retrieval_mode, use_web_search=use_web_search) in {
        "web",
        "hybrid",
    }


__all__ = [
    "RETRIEVAL_MODES",
    "RetrievalMode",
    "resolve_retrieval_mode",
    "uses_vector_db",
    "uses_web_search",
]
