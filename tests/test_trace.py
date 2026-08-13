import threading

import pytest

from deepsearcher.online_query import query, query_with_trace
from deepsearcher.trace import QueryCancelled, TraceCollector
from deepsearcher.vector_db.base import RetrievalResult


def make_result(text="Aurora fact", reference="aurora.pdf", distance=0.91):
    return RetrievalResult(
        embedding=[0.1, 0.2],
        text=text,
        reference=reference,
        metadata={"secret": "must-not-leak", "wider_text": "private wider text"},
        metric_type="L2",
        distance=distance,
    )


def test_trace_collector_builds_versioned_safe_trace():
    result = make_result(text="x" * 700)
    collector = TraceCollector("Who owns Aurora?")

    collector.select_agent(
        "ChainOfRAG",
        11,
        decision={
            "selected": [1],
            "fallback_used": True,
            "reason": "invalid_index_format",
        },
    )
    collector.start_iteration(1)
    collector.record_subquery("Who owns Project Aurora?", 12)
    collector.record_collections(
        ["deepsearcher"],
        13,
        decision={
            "source": "model",
            "requested": ["deepsearcher", "invented"],
            "selected": ["deepsearcher"],
            "rejected": ["invented"],
            "fallback_used": False,
            "reason": "hallucinated_collections_filtered",
        },
    )
    collector.record_documents_retrieved([result])
    collector.record_intermediate_answer("Lin Qiao owns Aurora.", 14)
    collector.record_documents_supported(
        [result],
        15,
        decision={
            "selected": [0],
            "rejected": ["-1"],
            "fallback_used": False,
            "reason": "invalid_items_filtered",
            "trusted": True,
            "evidence_count": 1,
        },
    )
    collector.record_selection_event(
        "deep_search.sub_queries",
        {
            "selected": ["safe question"],
            "rejected": ["<duplicate>"],
            "fallback_used": False,
            "reason": "invalid_items_filtered",
        },
    )
    collector.record_reflection(True, 16)

    trace = collector.build(total_tokens=97, final_results=[result], final_answer_tokens=16)
    iteration = trace["iterations"][0]
    document = iteration["retrieved_documents"][0]

    assert trace["version"] == 7
    assert trace["agent"] == "ChainOfRAG"
    assert trace["routing"]["fallback_used"] is True
    assert trace["routing"]["reason"] == "invalid_index_format"
    assert trace["selection_events"][0]["stage"] == "deep_search.sub_queries"
    assert trace["selection_events"][0]["decision"]["rejected_count"] == 1
    assert "original_query" not in trace
    assert "Who owns" not in str(trace)
    assert trace["summary"] == {
        "iteration_count": 1,
        "supported_document_count": 1,
        "final_answer_tokens": 16,
        "routing_tokens": 11,
        "total_tokens": 97,
    }
    assert "collections" not in iteration
    assert "subquery" not in iteration
    assert "intermediate_answer" not in iteration
    assert iteration["collection_routing"]["rejected_count"] == 1
    assert iteration["support_selection"]["rejected_count"] == 1
    assert iteration["support_selection"]["trusted"] is True
    assert iteration["support_selection"]["evidence_count"] == 1
    assert iteration["has_enough_information"] is True
    assert iteration["token_usage"] == {
        "subquery": 12,
        "collection_routing": 13,
        "retrieval_answer": 14,
        "support_filter": 15,
        "reflection": 16,
        "total": 70,
    }
    assert len(document["text"]) == 600
    assert document["reference"] == "aurora.pdf"
    assert document["metric_type"] == "L2"
    assert document["score_kind"] == "distance"
    assert document["distance"] == 0.91
    assert document["similarity"] is None
    assert document["rank_score"] is None
    assert document["higher_is_better"] is False
    assert document["source_type"] == "knowledge_base"
    assert document["source_url"] is None
    assert document["trusted"] is True
    assert "score" not in document
    assert document["supported"] is True
    assert "embedding" not in document
    assert "metadata" not in document
    assert "secret" not in str(trace)


def test_trace_collector_limits_visible_documents_but_keeps_real_count():
    results = [make_result(text=f"Document {index}") for index in range(7)]
    collector = TraceCollector("question")

    collector.start_iteration(1)
    collector.record_documents_retrieved(results)
    collector.record_documents_supported(results)
    trace = collector.build(total_tokens=0, final_results=[])
    iteration = trace["iterations"][0]

    assert iteration["retrieved_count"] == 7
    assert len(iteration["retrieved_documents"]) == 5


def test_trace_collector_hides_local_directories_from_references():
    result = make_result(reference=r"C:\Users\demo\AppData\Local\Temp\aurora-facts.pdf")
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[result])

    assert trace["iterations"][0]["retrieved_documents"][0]["reference"] == "aurora-facts.pdf"


def test_trace_collector_removes_query_secrets_from_url_references():
    result = make_result(reference="https://example.com/paper.pdf?access_token=secret#page=2")
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[result])
    reference = trace["iterations"][0]["retrieved_documents"][0]["reference"]

    assert reference == "https://example.com/paper.pdf"
    assert "secret" not in str(trace)


def test_trace_collector_rejects_internal_and_credentialed_url_references():
    results = [
        make_result(reference="http://127.0.0.1/private?token=secret"),
        make_result(reference="https://user:password@example.com/private"),
        make_result(reference="http://metadata.local/credentials"),
    ]
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved(results)

    trace = collector.build(total_tokens=0, final_results=results)

    documents = trace["iterations"][0]["retrieved_documents"]
    assert [document["reference"] for document in documents] == [None, None, None]
    assert "password" not in str(trace)
    assert "127.0.0.1" not in str(trace)


def test_trace_collector_serializes_safe_web_source_and_stage():
    result = RetrievalResult(
        [],
        "Public evidence",
        "https://docs.example.com/guide?token=secret",
        {
            "display_name": "Official guide",
            "source_type": "web",
            "source_url": "https://docs.example.com/guide?token=secret",
            "source_domain": "docs.example.com",
            "trusted": True,
            "ignored": "must-not-leak",
        },
        metric_type="TAVILY_RELEVANCE",
        rank_score=0.88,
    )
    events = []
    collector = TraceCollector("question", event_callback=events.append)
    collector.start_iteration(1)
    collector.record_web_search(
        {
            "status": "completed",
            "provider": "tavily",
            "query_count": 2,
            "result_count": 1,
            "error_code": None,
            "secret": "must-not-leak",
        }
    )
    collector.record_documents_retrieved([result])
    trace = collector.build(total_tokens=0, final_results=[result])

    iteration = trace["iterations"][0]
    assert iteration["web_search"] == {
        "iteration": 1,
        "status": "completed",
        "provider": "tavily",
        "query_count": 2,
        "result_count": 1,
        "error_code": None,
    }
    document = iteration["retrieved_documents"][0]
    assert document["reference"] == "https://docs.example.com/guide"
    assert document["source_type"] == "web"
    assert document["source_url"] == "https://docs.example.com/guide"
    assert document["source_domain"] == "docs.example.com"
    assert document["trusted"] is True
    assert document["rank_score"] == 0.88
    assert events[1]["event"] == "web_search"
    assert "secret" not in str(trace)
    assert "must-not-leak" not in str(trace)


def test_trace_collector_rejects_unsafe_reference_schemes():
    result = make_result(reference="javascript://alert/document")
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[result])

    assert trace["iterations"][0]["retrieved_documents"][0]["reference"] is None


def test_trace_collector_whitelists_citation_metadata():
    result = make_result()
    result.metadata.update(
        {
            "document_id": "doc-123",
            "display_name": r"C:\private\Aurora facts.pdf",
            "page_number": 7,
            "chunk_index": 11,
            "section_title": "Architecture",
            "section_path": ["Guide", "Architecture"],
            "char_start": 120,
            "char_end": 180,
            "bbox": [0.1, 0.2, 0.8, 0.3],
            "location_id": "location-123",
            "source_locator": "page=7&char=120-180",
            "parser_version": "pdfplumber-layout-v2+rapidocr-v3",
            "extraction_method": "text+tables",
            "api_key": "must-not-leak",
        }
    )
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[result])
    document = trace["iterations"][0]["retrieved_documents"][0]

    assert document["document_id"] == "doc-123"
    assert document["display_name"] == "Aurora facts.pdf"
    assert document["page_number"] == 7
    assert document["chunk_index"] == 11
    assert document["section_title"] == "Architecture"
    assert document["section_path"] == ["Guide", "Architecture"]
    assert document["char_start"] == 120
    assert document["char_end"] == 180
    assert document["bbox"] == [0.1, 0.2, 0.8, 0.3]
    assert document["location_id"] == "location-123"
    assert document["source_locator"] == "page=7&char=120-180"
    assert document["parser_version"] == "pdfplumber-layout-v2+rapidocr-v3"
    assert document["extraction_method"] == "text+tables"
    assert "api_key" not in str(trace)
    assert "must-not-leak" not in str(trace)


def test_trace_collector_without_iterations_still_returns_summary():
    result = make_result()
    collector = TraceCollector("question")
    collector.select_agent("NaiveRAG", 4)
    collector.record_final_answer(8)

    trace = collector.build(total_tokens=20, final_results=[result])

    assert trace["agent"] == "NaiveRAG"
    assert trace["iterations"] == []
    assert trace["summary"]["supported_document_count"] == 1
    assert trace["summary"]["final_answer_tokens"] == 8


def test_query_with_trace_keeps_legacy_query_signature(monkeypatch):
    result = make_result()

    class Searcher:
        def query(self, original_query, max_iter, trace_collector=None):
            if trace_collector is not None:
                trace_collector.select_agent("ChainOfRAG", 3)
                trace_collector.start_iteration(1)
                trace_collector.record_subquery("subquery", 4)
                trace_collector.record_documents_retrieved([result])
                trace_collector.record_documents_supported([result], 5)
            return "answer", [result], 20

    monkeypatch.setattr("deepsearcher.configuration.default_searcher", Searcher())

    legacy = query("question", max_iter=1)
    traced = query_with_trace("question", max_iter=1)

    assert len(legacy) == 3
    assert len(traced) == 4
    assert traced[3]["agent"] == "ChainOfRAG"
    assert traced[3]["summary"]["total_tokens"] == traced[2]


def test_query_with_trace_forwards_explicit_collection_scope(monkeypatch):
    captured = {}

    class Searcher:
        def query(self, original_query, **kwargs):
            captured["original_query"] = original_query
            captured.update(kwargs)
            return "answer", [], 0

    monkeypatch.setattr("deepsearcher.configuration.default_searcher", Searcher())

    query_with_trace(
        "question",
        max_iter=1,
        collection_names=["kb_selected"],
    )

    assert captured["original_query"] == "question"
    assert captured["max_iter"] == 1
    assert captured["collection_names"] == ["kb_selected"]


def test_legacy_query_can_opt_in_to_trust_policy(monkeypatch):
    evidence = make_result(text="Milvus 是向量数据库。")

    class Searcher:
        def query(self, _original_query, **kwargs):
            collector = kwargs["trace_collector"]
            from deepsearcher.grounding import format_grounding_evidence

            format_grounding_evidence(
                [evidence],
                use_wider_text=False,
                trace_collector=collector,
            )
            return "Milvus 是向量数据库。[E1] 它支持任意 SQL。", [evidence], 1

    monkeypatch.setattr("deepsearcher.configuration.default_searcher", Searcher())

    answer, _, _ = query("question", enforce_trust=True)

    assert answer == "Milvus 是向量数据库。[E1]"


def test_trace_events_only_expose_bounded_stage_facts():
    events = []
    result = make_result(
        text=(
            "Owner email owner@example.com api_key=super-secret "
            r"from C:\Users\private\facts.txt"
        )
    )
    collector = TraceCollector(
        "private original question",
        event_callback=events.append,
        request_id="request-safe-1",
    )

    collector.emit_started()
    collector.select_agent("ChainOfRAG", decision={"selected": [1]})
    collector.start_iteration(1)
    collector.record_subquery("private generated subquery")
    collector.record_documents_retrieved([result])
    collector.record_documents_supported([result])
    collector.record_reflection(True)
    trace = collector.build(total_tokens=3, final_results=[result])

    assert [event["event"] for event in events] == [
        "started",
        "routing",
        "iteration",
        "retrieval",
        "support",
        "reflection",
    ]
    serialized_events = str(events)
    assert "private original question" not in serialized_events
    assert "private generated subquery" not in serialized_events
    serialized_trace = str(trace)
    assert "owner@example.com" not in serialized_trace
    assert "super-secret" not in serialized_trace
    assert r"C:\Users\private" not in serialized_trace
    assert "[REDACTED_EMAIL]" in serialized_trace
    assert "[LOCAL_PATH]" in serialized_trace


def test_trace_cancellation_stops_at_the_next_safe_stage_boundary():
    cancellation_event = threading.Event()
    collector = TraceCollector("question", cancellation_event=cancellation_event)
    collector.emit_started()
    cancellation_event.set()

    with pytest.raises(QueryCancelled):
        collector.start_iteration(1)
