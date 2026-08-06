from deepsearcher.vector_db.base import RetrievalResult
from evaluation.dataset import EvalSample, EvidenceTarget
from evaluation.metrics import aggregate, criteria_coverage, evaluate_sample


def result(document="WhatisMilvus.pdf", page=1, chunk=0, text="Milvus 是向量数据库"):
    return RetrievalResult(
        embedding=[],
        text=text,
        reference=document,
        metadata={
            "display_name": document,
            "page_number": page,
            "chunk_index": chunk,
        },
        metric_type="L2",
        distance=0.1,
    )


def answerable_sample():
    return EvalSample(
        id="q1",
        question="Milvus 是什么？",
        answerable=True,
        reference_answer="向量数据库",
        evidence=(EvidenceTarget("WhatisMilvus.pdf", 1),),
        criteria=(("向量数据库", "vector database"),),
        tags=("事实",),
    )


def test_evaluate_sample_calculates_retrieval_and_answer_metrics():
    row = evaluate_sample(
        answerable_sample(),
        [result(), result()],
        answer="Milvus 是向量数据库。",
        top_k=5,
        latency_ms=12.5,
        tokens=30,
        llm_calls=2,
    )
    assert row["retrieval_recall"] == 1
    assert row["retrieval_precision"] == 0.4
    assert row["reciprocal_rank"] == 1
    assert row["duplicate_rate"] == 0.5
    assert row["answer_criteria_coverage"] == 1
    assert row["grounded_criteria_coverage"] == 1
    assert row["evidence_source_correct"] is True


def test_empty_answerable_result_is_counted_as_miss():
    row = evaluate_sample(
        answerable_sample(),
        [],
        answer=None,
        top_k=5,
        latency_ms=1,
        tokens=0,
    )
    assert row["retrieval_recall"] == 0
    assert row["retrieval_precision"] == 0
    assert row["evidence_source_correct"] is False


def test_explicit_source_alias_matches_internal_ingestion_name():
    row = evaluate_sample(
        answerable_sample(),
        [result(document="doc_internal.pdf")],
        answer=None,
        top_k=5,
        latency_ms=1,
        tokens=0,
        source_aliases={"WhatisMilvus.pdf": ("doc_internal.pdf",)},
    )
    assert row["retrieval_hit"] is True


def test_unanswerable_answer_mode_measures_refusal_and_false_positive():
    sample = EvalSample(
        id="q2",
        question="价格？",
        answerable=False,
        reference_answer="未提及",
        evidence=(),
        criteria=(),
        tags=("无答案",),
    )
    row = evaluate_sample(
        sample,
        [result()],
        answer="资料中未提及精确价格。",
        top_k=5,
        latency_ms=2,
        tokens=10,
    )
    assert row["no_answer_false_positive"] is True
    assert row["refusal_correct"] is True
    assert row["retrieval_recall"] is None


def test_criteria_coverage_accepts_alternatives_and_aggregate_has_cost():
    assert criteria_coverage("A vector database", (("向量数据库", "vector database"),)) == 1
    rows = [
        evaluate_sample(
            answerable_sample(),
            [result()],
            answer=None,
            top_k=5,
            latency_ms=10,
            tokens=4,
            llm_calls=1,
        ),
        evaluate_sample(
            answerable_sample(),
            [],
            answer=None,
            top_k=5,
            latency_ms=20,
            tokens=6,
            llm_calls=2,
            error="Failure",
        ),
    ]
    summary = aggregate(rows)
    assert summary["retrieval_recall_at_k"] == 0.5
    assert summary["error_rate"] == 0.5
    assert summary["latency_ms"]["p95"] == 20
    assert summary["tokens"]["total"] == 10
    assert summary["llm_calls"]["total"] == 3
