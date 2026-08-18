"""Loader registry (aligned with ragent ParserRegistry).

Explicit extension -> loader ownership is built from a SEQUENCE of registrations
so duplicate claims raise at startup instead of being silently overwritten (a
Python dict literal would hide the second claim). The registry is a drop-in
BaseLoader: load_file / load_directory route by normalized extension and REQUIRE
the extension explicitly instead of falling back to a generic loader.
"""

from __future__ import annotations

import os
from typing import Any, List, Sequence

from langchain_core.documents import Document

from deepsearcher.loader.file_loader.base import BaseLoader

# Every extension the product advertises must be claimed exactly once. json is
# included because JsonFileLoader routes through the registry too.
REQUIRED_EXTENSIONS = frozenset(
    {
        "pdf",
        "xlsx",
        "pptx",
        "png",
        "jpg",
        "jpeg",
        "txt",
        "md",
        "json",
        "docx",
        "html",
        "htm",
        "adoc",
        "asciidoc",
        "doc",
        "xls",
        "ppt",
        "rtf",
        "csv",
        "svg",
        "odt",
        "epub",
    }
)


class LoaderRegistrationConflict(RuntimeError):
    """Raised when an extension is unclaimed, double-claimed, or empty."""


class LoaderUnclaimedError(ValueError):
    """Raised when load_file receives an extension no loader owns."""


def _default_registrations() -> List[tuple[Sequence[str], Any]]:
    """Default ownership matrix. Loaders are registered as FACTORIES (classes or
    lambdas) and instantiated lazily on first routing, so optional dependencies
    (docling, unstructured) do not block startup until a matching file arrives."""
    from deepsearcher.loader.file_loader.docling_loader import DoclingLoader
    from deepsearcher.loader.file_loader.excel_loader import ExcelLoader
    from deepsearcher.loader.file_loader.image_loader import ImageLoader
    from deepsearcher.loader.file_loader.json_loader import JsonFileLoader
    from deepsearcher.loader.file_loader.pdf_loader import PDFLoader
    from deepsearcher.loader.file_loader.pptx_loader import PptxLoader
    from deepsearcher.loader.file_loader.text_loader import TextLoader
    from deepsearcher.loader.file_loader.unstructured_loader import UnstructuredLoader

    return [
        (("pdf",), PDFLoader),
        (("xlsx",), ExcelLoader),
        (("pptx",), PptxLoader),
        (("png", "jpg", "jpeg"), ImageLoader),
        (("txt", "md"), TextLoader),
        (("json",), lambda: JsonFileLoader(text_key="")),
        (("docx", "html", "htm", "adoc", "asciidoc"), DoclingLoader),
        (("doc", "xls", "ppt", "rtf", "csv", "svg", "odt", "epub"), UnstructuredLoader),
    ]


class LoaderRegistry(BaseLoader):
    """BaseLoader-compatible extension router over explicit ownership claims.

    Registrations may be loader instances or factories (callables returning a
    BaseLoader). Factories are resolved lazily on first routing and cached, so
    optional dependencies do not block startup.
    """

    def __init__(
        self,
        registrations: Sequence[tuple[Sequence[str], Any]] | None = None,
    ) -> None:
        if registrations is None:
            registrations = _default_registrations()
        self._ownership: dict[str, Any] = {}
        self._resolved: dict[int, BaseLoader] = {}
        for extensions, loader in registrations:
            self._register(extensions, loader)
        self._self_check()

    def _register(self, extensions: Sequence[str], loader: Any) -> None:
        if loader is None:
            raise LoaderRegistrationConflict("loader 不能为 None")
        label = loader.__name__ if isinstance(loader, type) else loader.__class__.__name__
        for raw in extensions:
            extension = str(raw).strip().lower()
            if not extension:
                raise LoaderRegistrationConflict("认领了空扩展名")
            if extension in self._ownership:
                previous = self._ownership[extension]
                previous_label = (
                    previous.__name__ if isinstance(previous, type) else previous.__class__.__name__
                )
                raise LoaderRegistrationConflict(
                    f"扩展名冲突: .{extension} 同时被 {previous_label} 与 {label} 认领"
                )
            self._ownership[extension] = loader

    def _self_check(self) -> None:
        missing = sorted(REQUIRED_EXTENSIONS - set(self._ownership))
        if missing:
            raise LoaderRegistrationConflict(
                "注册表自检失败，以下扩展名无人认领: " + ", ".join("." + ext for ext in missing)
            )

    def _resolve(self, entry: Any) -> BaseLoader:
        if isinstance(entry, BaseLoader):
            return entry
        resolved = self._resolved.get(id(entry))
        if resolved is None:
            try:
                resolved = entry()
            except Exception as exc:
                label = entry.__name__ if isinstance(entry, type) else entry.__class__.__name__
                raise LoaderUnclaimedError(
                    f"该格式的解析器 {label} 无法加载，可能缺少可选依赖: {exc}"
                ) from exc
            self._resolved[id(entry)] = resolved
        return resolved

    def loader_for(self, file_path: str) -> BaseLoader | None:
        extension = os.path.splitext(file_path)[1].lower().lstrip(".")
        entry = self._ownership.get(extension)
        if entry is None:
            return None
        return self._resolve(entry)

    def load_file(self, file_path: str) -> List[Document]:
        loader = self.loader_for(file_path)
        if loader is None:
            raise LoaderUnclaimedError(f"不支持的文件类型: {file_path}")
        return loader.load_file(file_path)

    def load_directory(self, directory: str) -> List[Document]:
        if not os.path.isdir(directory):
            raise NotADirectoryError(f"Error: '{directory}' is not a directory.")
        documents: List[Document] = []
        for root, _, files in os.walk(directory):
            for file in files:
                loader = self.loader_for(file)
                if loader is not None:
                    documents.extend(loader.load_file(os.path.join(root, file)))
        return documents

    @property
    def supported_file_types(self) -> List[str]:
        return sorted(self._ownership)

    @property
    def owned_extensions(self) -> set[str]:
        return set(self._ownership)
