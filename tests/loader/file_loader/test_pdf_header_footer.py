from __future__ import annotations

from reportlab.pdfgen import canvas

from deepsearcher.loader.file_loader.pdf_loader import PDFLoader

HEADER = "DeepSearcher Study Notes"
FOOTER = "Confidential Draft"
REPEAT = "A repeated middle sentence appears on two pages."


def _make_fixture(path):
    w, h = 595, 842
    c = canvas.Canvas(str(path))
    bodies = [
        "COVER PAGE: DeepSearcher Study Notes",
        "Page two body: DeepSearcher is a RAG based document QA project.",
        "Page three body: " + REPEAT,
        "Page four body: " + REPEAT,
        "Page five body: short retrieval wide context generation.",
    ]
    for page_no, body in enumerate(bodies, start=1):
        if page_no > 1:
            c.drawString(40, h - 30, HEADER)
        c.drawString(40, h - 120, body)
        c.drawString(40, 30, FOOTER)
        c.showPage()
    c.save()


def test_header_footer_suppression(tmp_path):
    pdf = tmp_path / "fixture.pdf"
    _make_fixture(pdf)
    docs = PDFLoader(ocr_enabled=False).load_file(str(pdf))
    assert len(docs) == 5
    contents = [d.page_content for d in docs]
    for c in contents:
        assert FOOTER not in c, c
    for c in contents[1:]:
        assert HEADER not in c, c
    assert "Study Notes" in contents[0]
    assert "RAG based document QA" in contents[1]
    assert contents[2].count(REPEAT) == 1 and contents[3].count(REPEAT) == 1
    assert "wide context" in contents[4]
    assert docs[1].metadata.get("edge_noise_suppressed") is not None


def test_edge_repeat_below_min_pages_not_removed(tmp_path):
    pdf = tmp_path / "edge.pdf"
    w, h = 595, 842
    c = canvas.Canvas(str(pdf))
    edge_sentence = "Top edge sentence X"
    for i in (1, 2):
        c.drawString(40, h - 30, edge_sentence)
        c.drawString(40, h - 120, "Body page {}".format(i))
        c.showPage()
    c.save()
    docs = PDFLoader(ocr_enabled=False, edge_min_pages=3).load_file(str(pdf))
    for d in docs:
        assert edge_sentence in d.page_content
