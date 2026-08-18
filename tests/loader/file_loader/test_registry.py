"""LoaderRegistry ownership matrix tests (aligned with ragent ParserRegistry)."""

from __future__ import annotations

import pytest

from deepsearcher.loader.file_loader.base import BaseLoader
from deepsearcher.loader.file_loader.pdf_loader import PDFLoader
from deepsearcher.loader.file_loader.registry import (
    REQUIRED_EXTENSIONS,
    LoaderRegistrationConflict,
    LoaderRegistry,
    LoaderUnclaimedError,
)
from deepsearcher.loader.file_loader.text_loader import TextLoader


def test_default_matrix_routes_every_required_extension():
    registry = LoaderRegistry()
    assert set(registry.supported_file_types) == set(REQUIRED_EXTENSIONS)
    assert registry.owned_extensions == REQUIRED_EXTENSIONS
    # Lightweight loaders (no optional deps) resolve on first routing.
    for extension in ("pdf", "xlsx", "pptx", "png", "jpg", "jpeg", "txt", "md", "json"):
        assert registry.loader_for(f"file.{extension}") is not None


def test_default_matrix_specific_routes():
    registry = LoaderRegistry()
    assert type(registry.loader_for("a.xlsx")).__name__ == "ExcelLoader"
    assert type(registry.loader_for("a.pptx")).__name__ == "PptxLoader"
    assert type(registry.loader_for("a.png")).__name__ == "ImageLoader"
    assert type(registry.loader_for("a.json")).__name__ == "JsonFileLoader"
    assert type(registry.loader_for("a.xls")).__name__ == "UnstructuredLoader"


def test_unknown_extension_load_file_raises():
    registry = LoaderRegistry()
    with pytest.raises(LoaderUnclaimedError):
        registry.load_file("file.xyz")


def test_duplicate_claim_fails_at_registration():
    # Two loaders claiming the same extension must fail during registration
    # (a Python dict literal would silently overwrite the second claim).
    text_loader = TextLoader()
    with pytest.raises(LoaderRegistrationConflict):
        LoaderRegistry(
            [
                (("txt",), text_loader),
                (("txt",), PDFLoader()),
            ]
        )


def test_factory_resolved_lazily_and_cached():
    registry = LoaderRegistry()
    first = registry.loader_for("a.xlsx")
    second = registry.loader_for("b.xlsx")
    assert first is second
    assert first is not None


def test_load_directory_only_routes_owned_extensions(tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "ignored.xyz").write_bytes(b"nope")
    registry = LoaderRegistry()
    documents = registry.load_directory(str(tmp_path))
    assert len(documents) == 1
    assert documents[0].page_content == "hello"


class _ProbeLoader(BaseLoader):
    def __init__(self, name: str):
        self.name = name

    def load_file(self, file_path):
        return []

    @property
    def supported_file_types(self):
        return []
