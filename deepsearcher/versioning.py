"""Validated document-series identity used for bounded version comparison."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from deepsearcher.temporal import (
    DOCUMENT_TEMPORAL_FIELDS,
    TRUSTED_TEMPORAL_METADATA_SOURCES,
    sanitize_document_temporal_metadata,
)

VERSION_FAMILY_FIELD = "version_family"
VERSION_FAMILY_SOURCE_FIELD = "version_family_source"
MAX_VERSION_FAMILY_LENGTH = 128
_SEPARATOR = re.compile(r"[\s-]+")


def normalize_version_family(value: Any) -> str | None:
    """Normalize a user/connector supplied series identifier to one stable key."""

    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return None
    raw = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = _SEPARATOR.sub("-", raw)
    if not normalized or len(normalized) > MAX_VERSION_FAMILY_LENGTH:
        return None
    if normalized[0] in "._:" or normalized[-1] in "._:":
        return None
    if any(not (character.isalnum() or character in "-_.:") for character in normalized):
        return None
    return normalized


def sanitize_document_version_metadata(value: Any) -> dict[str, str] | None:
    """Return a canonical version-family identity, or None when invalid."""

    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        return None
    if set(value) - {VERSION_FAMILY_FIELD, VERSION_FAMILY_SOURCE_FIELD}:
        return None
    raw_family = value.get(VERSION_FAMILY_FIELD)
    source = str(value.get(VERSION_FAMILY_SOURCE_FIELD) or "")
    if raw_family in (None, ""):
        return {} if not source else None
    family = normalize_version_family(raw_family)
    if family is None or source not in TRUSTED_TEMPORAL_METADATA_SOURCES:
        return None
    return {
        VERSION_FAMILY_FIELD: family,
        VERSION_FAMILY_SOURCE_FIELD: source,
    }


def extract_document_version_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    subset = {
        field: value.get(field)
        for field in (VERSION_FAMILY_FIELD, VERSION_FAMILY_SOURCE_FIELD)
        if value.get(field) not in (None, "")
    }
    return sanitize_document_version_metadata(subset) or {}


def sanitize_document_governance_metadata(value: Any) -> dict[str, str] | None:
    """Validate the complete metadata accepted by ingestion and index manifests."""

    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        return None
    allowed = {
        *DOCUMENT_TEMPORAL_FIELDS,
        "temporal_metadata_source",
        VERSION_FAMILY_FIELD,
        VERSION_FAMILY_SOURCE_FIELD,
    }
    if set(value) - allowed:
        return None
    temporal = sanitize_document_temporal_metadata(
        {
            field: value.get(field)
            for field in (*DOCUMENT_TEMPORAL_FIELDS, "temporal_metadata_source")
            if value.get(field) not in (None, "")
        }
    )
    version = sanitize_document_version_metadata(
        {
            field: value.get(field)
            for field in (VERSION_FAMILY_FIELD, VERSION_FAMILY_SOURCE_FIELD)
            if value.get(field) not in (None, "")
        }
    )
    if temporal is None or version is None:
        return None
    return {**temporal, **version}


def extract_document_governance_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    subset = {
        field: value.get(field)
        for field in (
            *DOCUMENT_TEMPORAL_FIELDS,
            "temporal_metadata_source",
            VERSION_FAMILY_FIELD,
            VERSION_FAMILY_SOURCE_FIELD,
        )
        if value.get(field) not in (None, "")
    }
    return sanitize_document_governance_metadata(subset) or {}
