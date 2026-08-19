from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from deepsearcher.loader.file_loader.mime_type import detect_upload_extension
from frontend.product.storage import LocalObjectStorage


def test_markdown_content_named_pdf_not_forced_to_pdf(tmp_path):
    md = tmp_path / "content"
    md.write_bytes(b"# Title\n\nDeepSearcher is a RAG based document QA project.\n")
    ext = detect_upload_extension(md, "notes.pdf")
    assert ext != "pdf"
    assert ext in ("md", "txt"), ext


def test_real_pdf_content_routes_to_pdf_regardless_of_name(tmp_path):
    pdf = tmp_path / "real"
    c = canvas.Canvas(str(pdf)); c.drawString(40, 800, "hello"); c.save()
    ext = detect_upload_extension(pdf, "strange.name")
    assert ext == "pdf"


def test_local_storage_uses_real_extension_in_object_key(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"hello md")
    storage = LocalObjectStorage(root=tmp_path / "root")
    key = storage.put_staged(src, knowledge_base_id="kb1", extension="md")
    assert key.endswith(".md"), key
    assert Path(key).exists()
    with storage.materialize(key) as path:
        assert path.name.endswith(".md")
