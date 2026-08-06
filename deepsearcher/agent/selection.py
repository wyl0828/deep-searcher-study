from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Generic, Iterable, List, Optional, Sequence, TypeVar

T = TypeVar("T")
SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


@dataclass(frozen=True)
class SelectionResult(Generic[T]):
    """Validated values plus a safe explanation of filtering or fallback."""

    values: List[T]
    rejected: List[str] = field(default_factory=list)
    fallback_used: bool = False
    reason: Optional[str] = None

    def as_trace(self) -> dict:
        return {
            "selected": list(self.values),
            "rejected": list(self.rejected),
            "fallback_used": self.fallback_used,
            "reason": self.reason,
        }


def fallback_selection(values: Iterable[T], reason: str) -> SelectionResult[T]:
    return SelectionResult(
        values=list(values),
        fallback_used=True,
        reason=reason,
    )


def parse_one_based_index(
    response: str,
    *,
    upper_bound: int,
    fallback_index: int = 0,
) -> SelectionResult[int]:
    """Accept exactly one one-based integer and convert it to a zero-based index."""
    if upper_bound <= 0:
        raise ValueError("upper_bound must be positive")
    if not 0 <= fallback_index < upper_bound:
        raise ValueError("fallback_index is outside the available choices")

    normalized = str(response or "").strip()
    if re.fullmatch(r"[0-9]+", normalized) is None:
        return fallback_selection([fallback_index], "invalid_index_format")

    one_based_index = int(normalized)
    selected_index = one_based_index - 1
    if not 0 <= selected_index < upper_bound:
        return fallback_selection([fallback_index], "index_out_of_range")
    return SelectionResult(values=[selected_index])


def validate_string_list(
    value,
    *,
    max_items: int,
    fallback: Sequence[str] = (),
    fallback_on_empty: bool = False,
    max_length: int = 512,
    excluded: Iterable[str] = (),
) -> SelectionResult[str]:
    """Validate a model-produced list of non-empty, unique strings."""
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    if not isinstance(value, list):
        return fallback_selection(fallback, "invalid_list_type")

    excluded_values = {item.strip() for item in excluded if isinstance(item, str)}
    selected: List[str] = []
    seen = set()
    rejected: List[str] = []
    for item in value:
        if not isinstance(item, str):
            rejected.append(f"<{type(item).__name__}>")
            continue
        normalized = item.strip()
        if not normalized:
            rejected.append("<empty>")
            continue
        if len(normalized) > max_length:
            rejected.append("<too_long>")
            continue
        if normalized in excluded_values:
            rejected.append("<previous_query>")
            continue
        if normalized in seen:
            rejected.append("<duplicate>")
            continue
        if len(selected) >= max_items:
            rejected.append("<over_limit>")
            continue
        selected.append(normalized)
        seen.add(normalized)

    if not selected and fallback_on_empty:
        return SelectionResult(
            values=list(fallback),
            rejected=rejected,
            fallback_used=True,
            reason="empty_or_invalid_selection",
        )
    return SelectionResult(
        values=selected,
        rejected=rejected,
        reason="invalid_items_filtered" if rejected else None,
    )


def validate_zero_based_indices(value, *, upper_bound: int) -> SelectionResult[int]:
    """Validate unique zero-based integer indices without Python negative indexing."""
    if not isinstance(value, list):
        return fallback_selection([], "invalid_list_type")

    selected: List[int] = []
    seen = set()
    rejected: List[str] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            rejected.append(f"<{type(item).__name__}>")
            continue
        if not 0 <= item < upper_bound:
            rejected.append(str(item))
            continue
        if item in seen:
            rejected.append("<duplicate>")
            continue
        selected.append(item)
        seen.add(item)

    fallback_used = bool(value) and not selected
    reason = "invalid_items_filtered" if rejected else None
    if fallback_used:
        reason = "no_valid_indices"
    return SelectionResult(
        values=selected,
        rejected=rejected,
        fallback_used=fallback_used,
        reason=reason,
    )


def safe_collection_name(value: str) -> str:
    """Return a trace-safe collection identifier."""
    return value if SAFE_IDENTIFIER.fullmatch(value) else "<invalid>"
