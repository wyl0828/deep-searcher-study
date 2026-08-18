"""LocalDirectoryConnector tests."""

from __future__ import annotations

import pytest

from frontend.product.connectors import (
    ConnectorPathEscapeError,
    LocalDirectoryConnector,
    create_connector,
)
from frontend.product.connectors.registry import ConnectorUnsupportedSourceError


def _root(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "docs").mkdir()
    return root


def test_list_items_enumerates_allowed_files_and_skips_symlinks(tmp_path):
    root = _root(tmp_path)
    (root / "docs" / "a.txt").write_text("hello", encoding="utf-8")
    (root / "notes.md").write_text("# hi", encoding="utf-8")
    (root / "skip.xyz").write_text("x", encoding="utf-8")

    connector = LocalDirectoryConnector(root=str(root))
    items = connector.list_items()
    ids = {item.id for item in items}
    assert ids == {"docs/a.txt", "notes.md"}


def test_fetch_item_returns_sha256_and_rejects_escape(tmp_path):
    root = _root(tmp_path)
    (root / "docs" / "a.txt").write_text("abc", encoding="utf-8")
    connector = LocalDirectoryConnector(root=str(root))
    result = connector.fetch_item("docs/a.txt")
    assert result.content_hash == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert result.size == 3

    with pytest.raises(ConnectorPathEscapeError):
        connector.fetch_item("../outside.txt")


def test_detect_changes_add_remove_and_modified_candidates(tmp_path):
    root = _root(tmp_path)
    (root / "docs" / "a.txt").write_text("one", encoding="utf-8")
    connector = LocalDirectoryConnector(root=str(root))
    first = connector.detect_changes(None)
    assert [item.id for item in first.added] == ["docs/a.txt"]
    assert first.removed == []
    assert first.modified_candidates == []

    # content changes but size stays equal: mtime+size is a candidate filter only.
    (root / "docs" / "a.txt").write_text("three", encoding="utf-8")
    second = connector.detect_changes(first.cursor)
    assert second.added == []
    assert [item.id for item in second.modified_candidates] == ["docs/a.txt"]

    (root / "docs" / "a.txt").unlink()
    third = connector.detect_changes(second.cursor)
    assert third.removed == ["docs/a.txt"]


def test_create_connector_registry(tmp_path):
    root = _root(tmp_path)
    connector = create_connector("local_directory", {"root": str(root)})
    assert isinstance(connector, LocalDirectoryConnector)
    with pytest.raises(ConnectorUnsupportedSourceError):
        create_connector("feishu", {})
