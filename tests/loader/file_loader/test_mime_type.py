"""Container-family detection tests (aligned with ragent MimeTypeDetector)."""

from __future__ import annotations

import io
import zipfile

import pytest

from deepsearcher.loader.file_loader.mime_type import (
    detect_container_family,
    normalize_extension,
    validate_upload,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\xff\xff\xff\xff"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _write(tmp_path, name: str, payload: bytes):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_normalize_extension():
    assert normalize_extension("Report.PDF") == "pdf"
    assert normalize_extension("archive.tar.gz") == "gz"
    assert normalize_extension("noext") == ""


def test_signature_families(tmp_path):
    cases = {
        "a.pdf": (b"%PDF-1.7\n", "pdf"),
        "a.png": (PNG_BYTES, "png"),
        "a.jpg": (b"\xff\xd8\xff\xe0", "jpeg"),
        "a.rtf": (b"{\\rtf1\\ansi", "rtf"),
        "a.doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16, "cfb"),
        "a.xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16, "cfb"),
        "a.ppt": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16, "cfb"),
    }
    for name, (payload, expected) in cases.items():
        path = _write(tmp_path, name, payload)
        assert detect_container_family(path) == expected, name


def test_ooxml_families(tmp_path):
    xlsx = _zip_with(
        {
            "[Content_Types].xml": "<Types/>",
            "xl/workbook.xml": "<workbook/>",
        }
    )
    docx = _zip_with(
        {
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:document/>",
        }
    )
    pptx = _zip_with(
        {
            "[Content_Types].xml": "<Types/>",
            "ppt/presentation.xml": "<p:presentation/>",
        }
    )
    assert detect_container_family(_write(tmp_path, "a.xlsx", xlsx)) == "ooxml_spreadsheet"
    assert detect_container_family(_write(tmp_path, "a.docx", docx)) == "ooxml_word"
    assert detect_container_family(_write(tmp_path, "a.pptx", pptx)) == "ooxml_presentation"


def test_odt_and_epub(tmp_path):
    odt = _zip_with({"mimetype": b"application/vnd.oasis.opendocument.text"})
    epub = _zip_with({"mimetype": b"application/epub+zip"})
    assert detect_container_family(_write(tmp_path, "a.odt", odt)) == "odt"
    assert detect_container_family(_write(tmp_path, "a.epub", epub)) == "epub"


def test_text_like_families(tmp_path):
    cases = {
        "a.svg": (b'<svg xmlns="http://www.w3.org/2000/svg">', "svg"),
        "a.html": (b"<!DOCTYPE html><html>", "html"),
        "a.txt": (b"hello world", "text"),
        "a.md": (b"# title\nbody", "text"),
        "a.json": (b'{"a": 1}', "text"),
        "a.csv": (b"a,b\n1,2\n", "text"),
    }
    for name, (payload, expected) in cases.items():
        path = _write(tmp_path, name, payload)
        assert detect_container_family(path) == expected, name


def test_validate_upload_accepts_matching_container(tmp_path):
    path = _write(
        tmp_path,
        "data.xlsx",
        _zip_with(
            {
                "[Content_Types].xml": "<Types/>",
                "xl/workbook.xml": "<w/>",
            }
        ),
    )
    assert validate_upload(path, "data.xlsx") == "xlsx"


def test_validate_upload_rejects_mismatch(tmp_path):
    path = _write(tmp_path, "notes.md", b"\x00\x01\x02\x03not-text")
    with pytest.raises(ValueError):
        validate_upload(path, "notes.md")


def test_validate_upload_rejects_unknown_extension(tmp_path):
    path = _write(tmp_path, "notes.txt", b"hello")
    with pytest.raises(ValueError):
        validate_upload(path, "notes.xyz")
