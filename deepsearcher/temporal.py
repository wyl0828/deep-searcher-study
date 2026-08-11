"""Validated, secret-free document business-time metadata."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

DOCUMENT_TEMPORAL_FIELDS = ("published_at", "effective_at", "superseded_at")
TRUSTED_TEMPORAL_METADATA_SOURCES = frozenset({"user_declared", "connector", "admin_verified"})


def sanitize_document_temporal_metadata(value: Any) -> dict[str, str] | None:
    """Return a canonical bounded identity, or None when the payload is invalid."""

    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        return None
    if set(value) - {*DOCUMENT_TEMPORAL_FIELDS, "temporal_metadata_source"}:
        return None
    parsed: dict[str, date] = {}
    for field in DOCUMENT_TEMPORAL_FIELDS:
        raw = value.get(field)
        if raw in (None, ""):
            continue
        if isinstance(raw, datetime):
            return None
        try:
            candidate = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
        if str(raw) != candidate.isoformat() and not isinstance(raw, date):
            return None
        parsed[field] = candidate
    source = str(value.get("temporal_metadata_source") or "")
    if parsed and source not in TRUSTED_TEMPORAL_METADATA_SOURCES:
        return None
    if not parsed and source:
        return None
    superseded_at = parsed.get("superseded_at")
    if superseded_at is not None and any(
        boundary is not None and superseded_at < boundary
        for boundary in (parsed.get("published_at"), parsed.get("effective_at"))
    ):
        return None
    result = {
        field: parsed[field].isoformat() for field in DOCUMENT_TEMPORAL_FIELDS if field in parsed
    }
    if result:
        result["temporal_metadata_source"] = source
    return result


def extract_document_temporal_metadata(value: Any) -> dict[str, str]:
    """Extract only temporal fields from a larger RetrievalResult metadata map."""

    if not isinstance(value, Mapping):
        return {}
    subset = {
        field: value.get(field)
        for field in (*DOCUMENT_TEMPORAL_FIELDS, "temporal_metadata_source")
        if value.get(field) not in (None, "")
    }
    return sanitize_document_temporal_metadata(subset) or {}


def published_anchor(value: Mapping[str, Any] | None) -> date | None:
    """Return the explicit publication date; never infer it from mtime or ingestion time."""

    sanitized = sanitize_document_temporal_metadata(value)
    if not sanitized or "published_at" not in sanitized:
        return None
    return date.fromisoformat(sanitized["published_at"])
