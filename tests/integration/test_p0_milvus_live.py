import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from deepsearcher.agent import ChainOfRAG
from deepsearcher.loader.file_loader import PDFLoader
from deepsearcher.loader.splitter import Chunk, split_docs_to_chunks
from deepsearcher.online_query import query_with_trace
from deepsearcher.vector_db import Milvus
from tests.agent.test_base import MockEmbedding, MockLLM

pytestmark = pytest.mark.skipif(
    os.environ.get("DEEPSEARCHER_RUN_LIVE_MILVUS") != "1",
    reason="set DEEPSEARCHER_RUN_LIVE_MILVUS=1 to run against local Milvus",
)

EXAMPLE_PDF = Path(__file__).parents[2] / "examples" / "data" / "WhatisMilvus.pdf"


def drop_collection_with_retry(vector_db: Milvus, collection: str) -> None:
    for attempt in range(3):
        try:
            if vector_db.client.has_collection(collection):
                vector_db.client.drop_collection(collection)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def test_live_l2_results_are_ascending_distances():
    collection = f"o02_metric_{uuid4().hex[:10]}"
    vector_db = Milvus(uri="http://127.0.0.1:19530", token="root:Milvus")
    try:
        vector_db.init_collection(dim=2, collection=collection, force_new_collection=True)
        chunks = [
            Chunk(text="nearest", reference="vectors.txt", metadata={}, embedding=[0.0, 0.0]),
            Chunk(text="middle", reference="vectors.txt", metadata={}, embedding=[1.0, 0.0]),
            Chunk(text="furthest", reference="vectors.txt", metadata={}, embedding=[2.0, 0.0]),
        ]
        vector_db.insert_data(collection, chunks)
        vector_db.client.flush(collection_name=collection)
        vector_db.client.load_collection(collection_name=collection)

        results = vector_db.search_data(collection, [0.0, 0.0], top_k=3)

        assert [result.text for result in results] == ["nearest", "middle", "furthest"]
        assert [result.distance for result in results] == sorted(
            result.distance for result in results
        )
        assert all(result.metric_type == "L2" for result in results)
        assert all(result.score_kind == "distance" for result in results)
        assert all(result.higher_is_better is False for result in results)
    finally:
        drop_collection_with_retry(vector_db, collection)
        vector_db.client.close()


def test_explicit_scope_and_real_pdf_citations_round_trip_through_live_milvus():
    suffix = uuid4().hex[:10]
    selected_collection = f"p0_scope_{suffix}_selected"
    other_collection = f"p0_scope_{suffix}_other"
    vector_db = Milvus(uri="http://127.0.0.1:19530", token="root:Milvus")

    try:
        vector_db.init_collection(
            dim=8,
            collection=selected_collection,
            force_new_collection=True,
        )
        vector_db.init_collection(
            dim=8,
            collection=other_collection,
            force_new_collection=True,
        )
        assert vector_db.client.has_collection(selected_collection)
        assert vector_db.client.has_collection(other_collection)

        documents = PDFLoader().load_file(str(EXAMPLE_PDF))
        chunks = split_docs_to_chunks(documents, chunk_size=800, chunk_overlap=80)
        for chunk in chunks:
            chunk.embedding = [0.1] * 8
        vector_db.insert_data(selected_collection, chunks)

        decoy = Chunk(
            text="This content must never be retrieved from the selected knowledge base.",
            reference="decoy.pdf",
            metadata={
                "document_id": "decoy-document",
                "display_name": "decoy.pdf",
                "page_number": 99,
                "chunk_index": 0,
            },
            embedding=[0.9] * 8,
        )
        vector_db.insert_data(other_collection, [decoy])
        vector_db.client.flush(collection_name=selected_collection)
        vector_db.client.flush(collection_name=other_collection)
        vector_db.client.load_collection(collection_name=selected_collection)
        vector_db.client.load_collection(collection_name=other_collection)
        embedding = MockEmbedding(dimension=8)
        vector_db.allow_unmanaged_collections = True

        llm = MockLLM(
            predefined_responses={
                "generate a new simple follow-up question": "What is Milvus?",
                "generate an appropriate answer": "Milvus is a vector database.",
                "Select documents only when": "[0]",
                "generate a final answer": "Milvus is a vector database.",
            }
        )
        agent = ChainOfRAG(
            llm=llm,
            embedding_model=embedding,
            vector_db=vector_db,
            max_iter=1,
        )
        agent.collection_router.invoke = MagicMock(
            side_effect=AssertionError("explicit scope must bypass collection routing")
        )

        with (
            patch.object(vector_db, "search_data", wraps=vector_db.search_data) as search_spy,
            patch("deepsearcher.configuration.default_searcher", agent),
        ):
            answer, results, _, trace = query_with_trace(
                "What is Milvus?",
                max_iter=1,
                collection_names=[selected_collection],
            )

        assert answer
        assert results
        assert all(result.metric_type == "L2" for result in results)
        assert all(result.distance is not None for result in results)
        assert all(result.higher_is_better is False for result in results)
        assert search_spy.call_count == 1
        assert search_spy.call_args.kwargs["collection"] == selected_collection
        assert all(result.reference != "decoy.pdf" for result in results)
        agent.collection_router.invoke.assert_not_called()

        iteration = trace["iterations"][0]
        citation = iteration["retrieved_documents"][0]
        assert citation["reference"] == "WhatisMilvus.pdf"
        assert citation["display_name"] == "WhatisMilvus.pdf"
        assert citation["document_id"]
        assert citation["page_number"] in {1, 2, 3, 4}
        assert citation["chunk_index"] >= 0
        assert citation["section_title"]
        assert citation["char_start"] >= 0
        assert citation["char_end"] > citation["char_start"]
        assert len(citation["bbox"]) == 4
        assert citation["location_id"]
        assert citation["source_locator"].startswith(f"page={citation['page_number']}&char=")
        assert citation["parser_version"] == "pdfplumber-layout-v2+rapidocr-v3"
        assert citation["extraction_method"] == "text"
        assert citation["metric_type"] == "L2"
        assert citation["score_kind"] == "distance"
        assert citation["distance"] is not None
        assert citation["higher_is_better"] is False
        assert "score" not in citation
        assert citation["supported"] is True
    finally:
        for collection in (selected_collection, other_collection):
            drop_collection_with_retry(vector_db, collection)
        vector_db.client.close()
