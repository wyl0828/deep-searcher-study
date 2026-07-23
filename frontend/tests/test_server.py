import asyncio
import base64

import pytest

from frontend.server import (
    decode_pdf,
    map_query_response,
    probe_backend,
    validate_collection_name,
)


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_decode_pdf_rejects_non_pdf_extension():
    with pytest.raises(ValueError, match="仅支持 PDF 文件"):
        decode_pdf("note.txt", encode(b"plain text"))


def test_decode_pdf_rejects_invalid_signature():
    with pytest.raises(ValueError, match="有效的 PDF"):
        decode_pdf("paper.pdf", encode(b"plain text"))


def test_decode_pdf_accepts_pdf_signature():
    assert decode_pdf("paper.pdf", encode(b"%PDF-1.7\ncontent")).startswith(b"%PDF")


def test_decode_pdf_rejects_oversized_payload(monkeypatch):
    monkeypatch.setattr("frontend.server.MAX_PDF_BYTES", 4)
    with pytest.raises(ValueError, match="20 MiB"):
        decode_pdf("paper.pdf", encode(b"%PDF-1.7"))


@pytest.mark.parametrize("name", ["deepsearcher", "project_docs_2026", "_scratch"])
def test_collection_name_accepts_safe_names(name):
    assert validate_collection_name(name) == name


@pytest.mark.parametrize("name", ["", "bad/name", "has space", "中文集合"])
def test_collection_name_rejects_unsafe_names(name):
    with pytest.raises(ValueError, match="Collection"):
        validate_collection_name(name)


def test_probe_backend_bypasses_windows_system_proxy(monkeypatch):
    captured = {}

    class Response:
        is_success = True

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

    monkeypatch.setattr("frontend.server.httpx.AsyncClient", Client)

    assert asyncio.run(probe_backend()) is True
    assert captured["trust_env"] is False


def test_map_query_response_preserves_structured_trace():
    trace = {"version": 1, "iterations": [{"number": 1}]}

    assert map_query_response(
        {"result": "答案", "consume_token": 42, "trace": trace},
        latency_ms=1234,
    ) == {
        "result": "答案",
        "consume_token": 42,
        "trace": trace,
        "latency_ms": 1234,
    }
