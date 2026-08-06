from pathlib import Path

import pdfplumber
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from deepsearcher.loader.file_loader.pdf_loader import PDF_PARSER_VERSION, PDFLoader
from deepsearcher.loader.splitter import split_docs_to_chunks


def _draw_text_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle("Section-aware PDF")
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(72, 742, "Introduction")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(
        72,
        710,
        "DeepSearcher turns uploaded documents into grounded answers with source locations.",
    )
    pdf.drawString(
        72,
        690,
        "Every retrieved passage keeps its page, character range, and layout coordinates.",
    )
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, 640, "Architecture")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(
        72,
        610,
        "The loader detects headings before the section-aware splitter creates chunks.",
    )
    pdf.drawString(
        72,
        590,
        "Stable location identifiers let citations survive persistence and API serialization.",
    )
    pdf.save()


def _draw_table_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(72, 742, "Component Matrix")
    table = Table(
        [
            ["Layer", "Responsibility"],
            ["Loader", "Extract page layout"],
            ["Splitter", "Preserve source location"],
        ],
        colWidths=[150, 280],
        rowHeights=[28, 28, 28],
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    table.wrapOn(pdf, 430, 100)
    table.drawOn(pdf, 72, 600)
    pdf.save()


def _draw_multi_column_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(72, 742, "Two Column Reading Order")
    pdf.setFont("Helvetica", 11)
    left_lines = [
        "Left topic one explains ingestion.",
        "Left topic two explains parsing.",
        "Left topic three explains chunking.",
        "Left topic four explains indexing.",
    ]
    right_lines = [
        "Right topic one explains retrieval.",
        "Right topic two explains grounding.",
        "Right topic three explains citations.",
        "Right topic four explains verification.",
    ]
    for index, text in enumerate(left_lines):
        pdf.drawString(54, 690 - index * 28, text)
    for index, text in enumerate(right_lines):
        pdf.drawString(330, 690 - index * 28, text)
    pdf.save()


def _draw_scanned_pdf(path: Path, tmp_path: Path) -> None:
    source_pdf = tmp_path / "ocr-source.pdf"
    source_image = tmp_path / "ocr-source.png"
    pdf = canvas.Canvas(str(source_pdf), pagesize=letter)
    pdf.setFont("Helvetica-Bold", 26)
    pdf.drawString(72, 680, "SCANNED SOURCE LOCATOR")
    pdf.setFont("Helvetica", 18)
    pdf.drawString(72, 630, "OCR FALLBACK VERIFICATION 2026")
    pdf.drawString(72, 590, "Image-only pages still preserve page coordinates.")
    pdf.save()

    with pdfplumber.open(source_pdf) as source:
        source.pages[0].to_image(resolution=180, antialias=True).original.save(source_image)

    scanned = canvas.Canvas(str(path), pagesize=letter)
    scanned.drawImage(
        str(source_image),
        0,
        0,
        width=letter[0],
        height=letter[1],
        preserveAspectRatio=False,
    )
    scanned.save()


def test_text_pdf_preserves_sections_offsets_and_layout_boxes(tmp_path):
    pdf_path = tmp_path / "section-aware.pdf"
    _draw_text_pdf(pdf_path)

    document = PDFLoader(ocr_enabled=False).load_file(str(pdf_path))[0]

    assert document.metadata["parser_version"] == PDF_PARSER_VERSION
    assert document.metadata["extraction_method"] == "text"
    assert document.metadata["layout_type"] == "single_column"
    assert [section["section_title"] for section in document.metadata["_section_spans"]] == [
        "Introduction",
        "Architecture",
    ]

    chunks = split_docs_to_chunks([document], chunk_size=95, chunk_overlap=15)
    assert chunks
    assert {chunk.metadata["section_title"] for chunk in chunks} == {
        "Introduction",
        "Architecture",
    }
    assert len({chunk.metadata["location_id"] for chunk in chunks}) == len(chunks)
    for chunk in chunks:
        start = chunk.metadata["char_start"]
        end = chunk.metadata["char_end"]
        assert document.page_content[start:end] == chunk.text
        assert chunk.metadata["source_locator"] == f"page=1&char={start}-{end}"
        assert len(chunk.metadata["location_id"]) == 24
        assert len(chunk.metadata["bbox"]) == 4
        assert all(0 <= coordinate <= 1 for coordinate in chunk.metadata["bbox"])


def test_table_pdf_is_serialized_as_searchable_markdown(tmp_path):
    pdf_path = tmp_path / "table.pdf"
    _draw_table_pdf(pdf_path)

    document = PDFLoader(ocr_enabled=False).load_file(str(pdf_path))[0]

    assert document.metadata["extraction_method"] == "text+tables"
    assert "[Table 1]" in document.page_content
    assert "| Layer | Responsibility |" in document.page_content
    assert "| Loader | Extract page layout |" in document.page_content
    table_spans = [span for span in document.metadata["_line_spans"] if span["kind"] == "table"]
    assert len(table_spans) == 1
    assert len(table_spans[0]["bbox"]) == 4


def test_multi_column_pdf_uses_column_first_reading_order(tmp_path):
    pdf_path = tmp_path / "multi-column.pdf"
    _draw_multi_column_pdf(pdf_path)

    document = PDFLoader(ocr_enabled=False).load_file(str(pdf_path))[0]

    assert document.metadata["layout_type"] == "multi_column"
    text = document.page_content
    assert text.index("Left topic one") < text.index("Left topic four")
    assert text.index("Left topic four") < text.index("Right topic one")
    assert text.index("Right topic one") < text.index("Right topic four")


def test_image_only_pdf_uses_real_ocr_and_preserves_boxes(tmp_path):
    pdf_path = tmp_path / "scanned.pdf"
    _draw_scanned_pdf(pdf_path, tmp_path)

    document = PDFLoader(ocr_resolution=144).load_file(str(pdf_path))[0]

    normalized_text = document.page_content.upper()
    assert document.metadata["extraction_method"] == "ocr"
    assert (document.metadata["extraction_confidence"] or 0) > 0.7
    assert "SCANNED SOURCE LOCATOR" in normalized_text
    assert "OCR FALLBACK" in normalized_text
    assert document.metadata["_line_spans"]
    assert all(len(span["bbox"]) == 4 for span in document.metadata["_line_spans"])


def test_repository_reference_pdf_keeps_real_page_and_heading_locations():
    repository_root = Path(__file__).resolve().parents[3]
    pdf_path = repository_root / "examples" / "data" / "WhatisMilvus.pdf"

    documents = PDFLoader(ocr_enabled=False).load_file(str(pdf_path))

    assert len(documents) >= 4
    assert [document.metadata["page_number"] for document in documents[:4]] == [1, 2, 3, 4]
    assert all(document.metadata["parser_version"] == PDF_PARSER_VERSION for document in documents)
    assert any(
        span["heading_level"] is not None
        for document in documents
        for span in document.metadata["_line_spans"]
    )
    chunks = split_docs_to_chunks(documents[:2], chunk_size=500, chunk_overlap=50)
    assert chunks
    assert all(chunk.metadata["page_number"] in {1, 2} for chunk in chunks)
    assert all(chunk.metadata["location_id"] for chunk in chunks)
