from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.online_query import query, query_with_trace


def make_result(text="Aurora fact", reference="aurora.pdf", score=0.91):
    return RetrievalResult(
        embedding=[0.1, 0.2],
        text=text,
        reference=reference,
        metadata={"secret": "must-not-leak", "wider_text": "private wider text"},
        score=score,
    )


def test_trace_collector_builds_versioned_safe_trace():
    result = make_result(text="x" * 700)
    collector = TraceCollector("Who owns Aurora?")

    collector.select_agent("ChainOfRAG", 11)
    collector.start_iteration(1)
    collector.record_subquery("Who owns Project Aurora?", 12)
    collector.record_collections(["deepsearcher"], 13)
    collector.record_documents_retrieved([result])
    collector.record_intermediate_answer("Lin Qiao owns Aurora.", 14)
    collector.record_documents_supported([result], 15)
    collector.record_reflection(True, 16)

    trace = collector.build(total_tokens=97, final_results=[result], final_answer_tokens=16)
    iteration = trace["iterations"][0]
    document = iteration["retrieved_documents"][0]

    assert trace["version"] == 1
    assert trace["agent"] == "ChainOfRAG"
    assert trace["original_query"] == "Who owns Aurora?"
    assert trace["summary"] == {
        "iteration_count": 1,
        "supported_document_count": 1,
        "final_answer_tokens": 16,
        "routing_tokens": 11,
        "total_tokens": 97,
    }
    assert iteration["collections"] == ["deepsearcher"]
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
    assert document["score"] == 0.91
    assert document["supported"] is True
    assert "embedding" not in document
    assert "metadata" not in document
    assert "secret" not in str(trace)


def test_trace_collector_limits_visible_documents_but_keeps_real_count():
    results = [make_result(text=f"Document {index}") for index in range(7)]
    collector = TraceCollector("question")

    collector.start_iteration(1)
    collector.record_documents_retrieved(results)
    trace = collector.build(total_tokens=0, final_results=[])
    iteration = trace["iterations"][0]

    assert iteration["retrieved_count"] == 7
    assert len(iteration["retrieved_documents"]) == 5


def test_trace_collector_hides_local_directories_from_references():
    result = make_result(reference=r"C:\Users\demo\AppData\Local\Temp\aurora-facts.pdf")
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[])

    assert trace["iterations"][0]["retrieved_documents"][0]["reference"] == "aurora-facts.pdf"


def test_trace_collector_removes_query_secrets_from_url_references():
    result = make_result(
        reference="https://example.com/paper.pdf?access_token=secret#page=2"
    )
    collector = TraceCollector("question")
    collector.start_iteration(1)
    collector.record_documents_retrieved([result])

    trace = collector.build(total_tokens=0, final_results=[])
    reference = trace["iterations"][0]["retrieved_documents"][0]["reference"]

    assert reference == "https://example.com/paper.pdf#page=2"
    assert "secret" not in str(trace)


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
