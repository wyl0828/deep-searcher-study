"""Local directory connector (v0.6).

- root is resolved once; every item id is a root-relative posix path and every
  fetch re-resolves under root (path escape is rejected).
- Symlinks are NOT followed by default.
- detect_changes uses (mtime, size) as a fast candidate filter; the authoritative
  changed decision is made by the sync layer via content_hash from fetch_item.
- fetch_permissions returns declared grants from the connector config (additive).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from frontend.product.connectors.base import (
    ChangeSet,
    Connector,
    ConnectorItem,
    ConnectorPathEscapeError,
    FetchResult,
)

SOURCE_TYPE_LOCAL_DIRECTORY = "local_directory"


def _extension_allowed(path: Path) -> bool:
    from deepsearcher.loader.file_loader.mime_type import EXTENSION_FAMILY

    return path.suffix.lower().lstrip(".") in EXTENSION_FAMILY


class LocalDirectoryConnector(Connector):
    source_type = SOURCE_TYPE_LOCAL_DIRECTORY

    def __init__(self, *, root: str, permissions: list[dict[str, str]] | None = None):
        self.root = Path(root).resolve()
        self._permissions = list(permissions or [])

    def _safe_path(self, item_id: str) -> Path:
        candidate = (self.root / item_id).resolve()
        root_prefix = str(self.root) + os.sep
        if str(candidate) != str(self.root) and not str(candidate).startswith(root_prefix):
            raise ConnectorPathEscapeError(f"item id escapes connector root: {item_id}")
        return candidate

    def list_items(self) -> list[ConnectorItem]:
        items: list[ConnectorItem] = []
        for path in self.root.rglob("*"):
            if path.is_symlink():
                continue
            if not path.is_file():
                continue
            if not _extension_allowed(path):
                continue
            rel = path.relative_to(self.root).as_posix()
            stat = path.stat()
            items.append(
                ConnectorItem(
                    id=rel,
                    path=str(path),
                    size=stat.st_size,
                    last_modified=stat.st_mtime,
                )
            )
        return items

    def fetch_item(self, item_id: str) -> FetchResult:
        path = self._safe_path(item_id)
        if not path.is_file():
            return FetchResult(message=f"source file missing: {item_id}")
        stat = path.stat()
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return FetchResult(
            content_hash=digest.hexdigest(),
            last_modified=stat.st_mtime,
            size=stat.st_size,
        )

    def detect_changes(self, cursor: dict[str, dict[str, Any]] | None) -> ChangeSet:
        current = self.list_items()
        current_map = {
            item.id: {"mtime": item.last_modified, "size": item.size} for item in current
        }
        previous = cursor or {}

        added = [item for item in current if item.id not in previous]
        removed = [item_id for item_id in previous if item_id not in current_map]
        modified_candidates = [
            item
            for item in current
            if item.id in previous
            and (
                previous[item.id].get("mtime") != item.last_modified
                or previous[item.id].get("size") != item.size
            )
        ]
        return ChangeSet(
            added=added,
            modified_candidates=modified_candidates,
            removed=removed,
            cursor=current_map,
        )

    def fetch_permissions(self, path: str) -> list[dict[str, str]]:
        """Additive permission grants declared for the whole root (v0.6)."""
        return list(self._permissions)
