"""Connector protocol (aligned with ragent DocumentFetcher + RemoteFileFetcher).

- ConnectorItem: stable source identity (id = root-relative path) + fast metadata
  (mtime/size). content identity (sha256) is only resolved by fetch_item.
- FetchResult: carries the authoritative content_hash; the sync layer compares it
  against the persisted Document.content_hash to decide changed / skipped
  (metadata hint filters candidates, content identity is the final judge).
- SourceType: local_directory for v0.6; URL/Feishu are out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ConnectorError(RuntimeError):
    """Base error for connector failures."""


class ConnectorPathEscapeError(ConnectorError):
    """Raised when an item id escapes the connector root."""


@dataclass(frozen=True)
class ConnectorItem:
    """One source file's stable identity plus fast-change metadata."""

    id: str
    path: str
    size: int
    last_modified: float


@dataclass(frozen=True)
class FetchResult:
    """Authoritative fetch outcome (aligned with RemoteFetchResult.changed)."""

    content_hash: str | None = None
    last_modified: float | None = None
    size: int = 0
    message: str | None = None


@dataclass
class ChangeSet:
    """Candidate changes from a cursor diff; modified needs sha256 confirmation."""

    added: list[ConnectorItem] = field(default_factory=list)
    modified_candidates: list[ConnectorItem] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    cursor: dict[str, dict[str, Any]] = field(default_factory=dict)


class Connector:
    """Base class for source connectors."""

    source_type: str = ""

    def list_items(self) -> list[ConnectorItem]:
        raise NotImplementedError

    def fetch_item(self, item_id: str) -> FetchResult:
        raise NotImplementedError

    def detect_changes(self, cursor: dict[str, dict[str, Any]] | None) -> ChangeSet:
        raise NotImplementedError

    def fetch_permissions(self, path: str) -> list[dict[str, str]]:
        """Declared permission grants for a source path (additive-only sync).

        v0.6 permission sync is explicitly ADDITIVE: grants are applied with
        add_kb_member / set_kb_member_role and revocation is not performed.
        """
        return []
