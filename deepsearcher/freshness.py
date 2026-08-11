"""Bounded, server-owned freshness intent classification for Trust checks."""

from __future__ import annotations

import re
from typing import Any, Mapping

FRESHNESS_CONTRACT_VERSION = 1
FRESHNESS_CLASSIFIER_VERSION = "1.0.0"

_LATEST = re.compile(
    r"最新(?:版|版本)?|最近(?:的|发布|更新)?|最新版|"
    r"(?i:\b(?:latest|newest|most\s+recent)\b)"
)
_CURRENT = re.compile(
    r"当前|现行|目前(?:有效|执行|使用)?|现有效|正在(?:执行|生效|使用)|"
    r"(?i:\b(?:current|currently|in\s+force|effective\s+now)\b)"
)
_RECENT = re.compile(
    r"近期|近来|近\s*\d+\s*(?:天|周|月|年)|"
    r"(?i:\b(?:recently|in\s+the\s+(?:past|last)\s+\d+\s+(?:days?|weeks?|months?|years?))\b)"
)
_PUBLICATION = re.compile(
    r"发布|公布|公告|发版|出版|上传|"
    r"(?i:\b(?:publish(?:ed|ing)?|release(?:d)?|announcement|document)\b)"
)

_MODES = {"none", "current", "latest_effective", "latest_published", "recent"}
_ORDERING_BASES = {"none", "effective_at", "published_at", "undefined_window"}


def classify_query_freshness(query: str) -> dict[str, Any]:
    """Classify freshness intent without persisting the original query text."""

    text = str(query or "")
    latest = _LATEST.search(text) is not None
    current = _CURRENT.search(text) is not None
    recent = _RECENT.search(text) is not None
    publication = _PUBLICATION.search(text) is not None
    if recent and not latest:
        mode = "recent"
        ordering_basis = "undefined_window"
        reasons = ["FRESHNESS_RECENCY_WINDOW_REQUIRED"]
    elif latest:
        mode = "latest_published" if publication else "latest_effective"
        ordering_basis = "published_at" if publication else "effective_at"
        reasons = [
            "FRESHNESS_LATEST_PUBLICATION_REQUESTED"
            if publication
            else "FRESHNESS_LATEST_EFFECTIVE_REQUESTED"
        ]
    elif current:
        mode = "current"
        ordering_basis = "effective_at"
        reasons = ["FRESHNESS_CURRENT_VERSION_REQUESTED"]
    else:
        mode = "none"
        ordering_basis = "none"
        reasons = []
    return {
        "version": FRESHNESS_CONTRACT_VERSION,
        "classifier": "deterministic_freshness_intent",
        "classifier_version": FRESHNESS_CLASSIFIER_VERSION,
        "required": mode != "none",
        "mode": mode,
        "ordering_basis": ordering_basis,
        "reason_codes": reasons,
    }


def sanitize_freshness_intent(value: Any) -> dict[str, Any] | None:
    """Return the fixed freshness profile shape, or None for invalid input."""

    if not isinstance(value, Mapping) or value.get("version") != FRESHNESS_CONTRACT_VERSION:
        return None
    mode = str(value.get("mode") or "")
    ordering_basis = str(value.get("ordering_basis") or "")
    required = value.get("required")
    if (
        mode not in _MODES
        or ordering_basis not in _ORDERING_BASES
        or not isinstance(required, bool)
        or required != (mode != "none")
    ):
        return None
    expected_basis = {
        "none": "none",
        "current": "effective_at",
        "latest_effective": "effective_at",
        "latest_published": "published_at",
        "recent": "undefined_window",
    }[mode]
    if ordering_basis != expected_basis:
        return None
    classifier = str(value.get("classifier") or "")
    classifier_version = str(value.get("classifier_version") or "")
    if (
        classifier != "deterministic_freshness_intent"
        or classifier_version != FRESHNESS_CLASSIFIER_VERSION
    ):
        return None
    raw_reasons = value.get("reason_codes")
    if not isinstance(raw_reasons, list) or any(not isinstance(item, str) for item in raw_reasons):
        return None
    expected_reasons = {
        "none": [],
        "current": ["FRESHNESS_CURRENT_VERSION_REQUESTED"],
        "latest_effective": ["FRESHNESS_LATEST_EFFECTIVE_REQUESTED"],
        "latest_published": ["FRESHNESS_LATEST_PUBLICATION_REQUESTED"],
        "recent": ["FRESHNESS_RECENCY_WINDOW_REQUIRED"],
    }[mode]
    if raw_reasons != expected_reasons:
        return None
    return {
        "version": FRESHNESS_CONTRACT_VERSION,
        "classifier": classifier,
        "classifier_version": classifier_version,
        "required": required,
        "mode": mode,
        "ordering_basis": ordering_basis,
        "reason_codes": list(raw_reasons),
    }
