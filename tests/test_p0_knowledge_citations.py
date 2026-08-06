from pathlib import Path
from unittest.mock import MagicMock

from deepsearcher.loader.file_loader import PDFLoader
from deepsearcher.loader.splitter import split_docs_to_chunks
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db import Milvus

EXAMPLE_PDF = Path(__file__).parents[1] / "examples" / "data" / "WhatisMilvus.pdf"


def test_real_multipage_pdf_metadata_reaches_safe_trace():
    documents = PDFLoader().load_file(str(EXAMPLE_PDF))

    assert len(documents) == 4
    assert [document.metadata["page_number"] for document in documents] == [1, 2, 3, 4]
    assert {document.metadata["display_name"] for document in documents} == {"WhatisMilvus.pdf"}
    document_ids = {document.metadata["document_id"] for document in documents}
    assert len(document_ids) == 1

    chunks = split_docs_to_chunks(documents, chunk_size=800, chunk_overlap=80)

    assert chunks
    assert all("page_number" in chunk.metadata for chunk in chunks)
    assert all("chunk_index" in chunk.metadata for chunk in chunks)
    assert len({chunk.metadata["chunk_index"] for chunk in chunks}) == len(chunks)

    selected_chunk = chunks[0]
    selected_chunk.embedding = [0.1, 0.2]

    milvus = object.__new__(Milvus)
    milvus.default_collection = "kb_selected"
    milvus.hybrid = False
    milvus.client = MagicMock()
    milvus.insert_data("kb_selected", [selected_chunk])

    inserted = milvus.client.insert.call_args.kwargs["data"][0]
    milvus.client.search.return_value = [[{"entity": inserted, "distance": 0.91}]]
    result = milvus.search_data(
        collection="kb_selected",
        vector=[0.1, 0.2],
        top_k=1,
        query_text="What is Milvus?",
    )
    assert len(result) == 1
    result = result[0]

    collector = TraceCollector("What is Milvus?")
    collector.start_iteration(1)
    collector.record_collections(["kb_selected"])
    collector.record_documents_retrieved([result])
    collector.record_documents_supported([result])

    trace = collector.build(total_tokens=0, final_results=[result])
    citation = trace["iterations"][0]["retrieved_documents"][0]

    assert citation["reference"] == "WhatisMilvus.pdf"
    assert citation["display_name"] == "WhatisMilvus.pdf"
    assert citation["document_id"] == next(iter(document_ids))
    assert citation["page_number"] == selected_chunk.metadata["page_number"]
    assert citation["chunk_index"] == selected_chunk.metadata["chunk_index"]
    assert citation["section_title"] == selected_chunk.metadata["section_title"]
    assert citation["char_start"] == selected_chunk.metadata["char_start"]
    assert citation["char_end"] == selected_chunk.metadata["char_end"]
    assert citation["bbox"] == selected_chunk.metadata["bbox"]
    assert citation["location_id"] == selected_chunk.metadata["location_id"]
    assert citation["source_locator"] == selected_chunk.metadata["source_locator"]
    assert citation["parser_version"] == "pdfplumber-layout-v2+rapidocr-v3"
    assert citation["extraction_method"] == "text"
    assert citation["metric_type"] == "L2"
    assert citation["score_kind"] == "distance"
    assert citation["distance"] == 0.91
    assert citation["higher_is_better"] is False
    assert "score" not in citation
    assert citation["supported"] is True
