"""Container/signature family detection (aligned with ragent MimeTypeDetector).

The detector validates that an uploaded file's container/signature family is
compatible with its normalized extension. It deliberately does NOT identify the
exact subtype for legacy Office files (OLE CFB): a valid Compound File container
is enough for .doc/.xls/.ppt, and the real parsing is delegated to the loader.

Routing authority stays with LoaderRegistry (by normalized extension); this
module is only an upload authenticity gate (extension <-> container conflict is
rejected before any loader sees the file).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# Byte signatures for binary families. OOXML/ODT/EPUB share the ZIP signature
# and are distinguished by their ZIP entries (see _classify_zip).
_CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SIGNATURES = {
    "pdf": (b"%PDF",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpeg": (b"\xff\xd8\xff",),
    "cfb": (_CFB_SIGNATURE,),
    "rtf": (b"{\\rtf",),
    "zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}

_OOXML_MARKERS = {
    "xl/": "ooxml_spreadsheet",
    "word/": "ooxml_word",
    "ppt/": "ooxml_presentation",
}

# Extension -> allowed container family. Legacy Office (doc/xls/ppt) accepts any
# OLE CFB container; text-family extensions accept the text family.
EXTENSION_FAMILY = {
    "pdf": {"pdf"},
    "xlsx": {"ooxml_spreadsheet"},
    "docx": {"ooxml_word"},
    "pptx": {"ooxml_presentation"},
    "xls": {"cfb"},
    "doc": {"cfb"},
    "ppt": {"cfb"},
    "rtf": {"rtf"},
    "odt": {"odt"},
    "epub": {"epub"},
    "svg": {"svg"},
    "html": {"html"},
    "htm": {"html"},
    "png": {"png"},
    "jpg": {"jpeg"},
    "jpeg": {"jpeg"},
    "txt": {"text"},
    "md": {"text"},
    "markdown": {"text"},
    "text": {"text"},
    "csv": {"text"},
    "json": {"text"},
    "adoc": {"text"},
    "asciidoc": {"text"},
}

TEXT_FAMILY_EXTENSIONS = frozenset(
    ext for ext, families in EXTENSION_FAMILY.items() if families == {"text"}
)
HTML_FAMILY_EXTENSIONS = frozenset(
    ext for ext, families in EXTENSION_FAMILY.items() if families == {"html"}
)


def normalize_extension(filename: str) -> str:
    """Return the lowercase extension without the leading dot."""
    name = (filename or "").strip()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def detect_container_family(source_path: Path) -> str:
    """Detect the container/signature family of a file on disk.

    Returns one of: pdf|png|jpeg|cfb|rtf|zip|ooxml_spreadsheet|ooxml_word|
    ooxml_presentation|odt|epub|svg|html|text|unknown.
    """
    try:
        with open(source_path, "rb") as handle:
            prefix = handle.read(16)
    except OSError:
        return "unknown"
    if not prefix:
        return "unknown"

    family = _match_signature(prefix)
    if family == "zip":
        family = _classify_zip(source_path)
    elif family is None:
        family = _classify_text_like(source_path, prefix)
    return family or "unknown"


def validate_upload(source_path: Path, filename: str) -> str:
    """Validate extension <-> container compatibility; return normalized extension.

    Raises ValueError when the extension is not routable or the container family
    conflicts with it. The registry (by extension) remains the routing authority;
    this is only the authenticity gate.
    """
    extension = normalize_extension(filename)
    if not extension or extension not in EXTENSION_FAMILY:
        raise ValueError(f"不支持的文件类型: {extension or 'unknown'}")
    family = detect_container_family(source_path)
    allowed = EXTENSION_FAMILY[extension]
    if family == "unknown" or family not in allowed:
        raise ValueError(f"文件内容与扩展名不匹配: .{extension} (detected {family})")
    return extension


def _match_signature(prefix: bytes) -> str | None:
    for family, signatures in _SIGNATURES.items():
        for signature in signatures:
            if prefix.startswith(signature):
                return family
    return None


def _classify_zip(source_path: Path) -> str:
    try:
        with zipfile.ZipFile(source_path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return "zip"
    lowered = {name.lower() for name in names}
    if "mimetype" in lowered:
        try:
            with zipfile.ZipFile(source_path) as archive:
                mimetype = archive.read("mimetype")[:64].decode("ascii", "ignore")
        except (OSError, zipfile.BadZipFile, KeyError):
            mimetype = ""
        if mimetype.startswith("application/epub+zip"):
            return "epub"
        if mimetype.startswith("application/vnd.oasis.opendocument"):
            return "odt"
    if "[content_types].xml" in lowered:
        for marker, family in _OOXML_MARKERS.items():
            if any(name.startswith(marker) for name in lowered):
                return family
    return "zip"


def _classify_text_like(source_path: Path, prefix: bytes) -> str | None:
    try:
        text = prefix.decode("utf-8", "ignore")
    except Exception:
        return None
    stripped = text.lstrip("\ufeff \t\r\n")
    lower = stripped.lower()
    if lower.startswith("<svg") or lower.startswith("<?xml") and "<svg" in lower[:4096]:
        return "svg"
    if lower.startswith("<!doctype html") or lower.startswith("<html"):
        return "html"
    if not text:
        return "unknown"
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t\f")
    if printable / len(text) >= 0.8:
        return "text"
    return "unknown"


def _looks_like_json(sample: str) -> bool:
    head = sample.lstrip()
    return head.startswith(("{", "[")) and head.rstrip().endswith(("}", "]"))


def _looks_like_csv(source_path: Path) -> bool:
    try:
        with open(source_path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = [line for line in handle.readlines(4096) if line.strip()][:8]
    except OSError:
        return False
    if len(lines) < 2:
        return False
    scored = 0
    for line in lines[:4]:
        if line.count(",") >= 1:
            scored += 1
    return scored >= 2
