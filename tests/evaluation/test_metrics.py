import pytest

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
    assert row["full_evidence_retrieved"] is True
    assert row["multi_document"] is False
    assert row["full_document_coverage"] is None


def test_multi_document_sample_requires_every_source_for_full_coverage():
    sample = EvalSample(
        id="multi",
        question="对比两份资料",
        answerable=True,
        reference_answer="A 和 B",
        evidence=(EvidenceTarget("a.pdf", 1), EvidenceTarget("b.pdf", 2)),
        criteria=(("A",), ("B",)),
        tags=("跨文档",),
        difficulty="hard",
    )
    row = evaluate_sample(
        sample,
        [result(document="a.pdf", page=1, text="A")],
        answer="A",
        top_k=5,
        latency_ms=1,
        tokens=1,
    )
    assert row["multi_document"] is True
    assert row["matched_document_count"] == 1
    assert row["full_evidence_retrieved"] is False
    assert row["full_document_coverage"] is False


def test_full_document_coverage_aggregate_uses_only_multi_document_samples():
    multi_sample = EvalSample(
        id="multi",
        question="对比两份资料",
        answerable=True,
        reference_answer="A 和 B",
        evidence=(EvidenceTarget("a.pdf", 1), EvidenceTarget("b.pdf", 2)),
        criteria=(("A",), ("B",)),
        tags=("跨文档",),
    )
    single_row = evaluate_sample(
        answerable_sample(),
        [result()],
        answer=None,
        top_k=5,
        latency_ms=1,
        tokens=0,
    )
    incomplete_multi_row = evaluate_sample(
        multi_sample,
        [result(document="a.pdf", page=1, text="A")],
        answer=None,
        top_k=5,
        latency_ms=1,
        tokens=0,
    )

    summary = aggregate([single_row, incomplete_multi_row])

    assert summary["multi_document_sample_count"] == 1
    assert summary["full_document_coverage_rate"] == 0


def test_claim_level_metrics_reject_wrong_source_and_measure_support():
    grounding = {
        "state": "partially_grounded",
        "claims": [
            {"status": "supported", "evidence_ids": ["E1"]},
            {"status": "invalid_citation", "evidence_ids": []},
        ],
        "evidence": [
            {
                "evidence_id": "E1",
                "display_name": "WhatisMilvus.pdf",
                "page_number": 1,
                "chunk_index": 0,
                "text": "Milvus 是向量数据库",
            }
        ],
    }
    row = evaluate_sample(
        answerable_sample(),
        [result()],
        answer="Milvus 是向量数据库。[E1] 另一个说法。[E9]",
        top_k=5,
        latency_ms=1,
        tokens=1,
        grounding=grounding,
    )
    assert row["claim_support_rate"] == 0.5
    assert row["ungrounded_claim_rate"] == 0.5
    assert row["invalid_claim_citation_rate"] == 0.5
    assert row["claim_citation_precision"] == 1
    assert row["claim_citation_recall"] == 1


def test_trust_metrics_measure_pre_policy_consistency_and_answer_changes():
    trust = {
        "input": {
            "claims": [
                {
                    "support_status": "supported",
                    "consistency_status": "consistent",
                    "entailment_status": "entailed",
                    "risk_status": "passed",
                },
                {
                    "support_status": "unsupported",
                    "consistency_status": "inconsistent",
                    "entailment_status": "contradicted",
                    "risk_status": "rejected",
                },
                {
                    "support_status": "supported",
                    "consistency_status": "not_applicable",
                    "entailment_status": "unknown",
                    "risk_status": "rejected",
                },
            ]
        },
        "entailment": {"token_usage": 23},
        "risk": {"risk_level": "high", "query_type": "financial_policy"},
        "policy": {"action": "downgrade", "answer_changed": True},
        "provenance": {
            "version": 2,
            "digest": "sha256:" + "a" * 64,
            "evidence": {
                "snapshot_status": "complete",
                "evidence_count": 2,
                "knowledge_base_count": 1,
                "web_count": 1,
                "items": [
                    {
                        "source_type": "knowledge_base",
                        "publication_anchor_bound": True,
                        "version_family_bound": True,
                    },
                    {
                        "source_type": "web",
                        "publication_anchor_bound": False,
                        "version_family_bound": False,
                    },
                ],
            },
            "temporal": {
                "version": 1,
                "source": "request_clock",
                "reference_date": "2026-08-11",
                "timezone": "Asia/Shanghai",
                "fingerprint": "sha256:" + "b" * 64,
            },
        },
    }
    row = evaluate_sample(
        answerable_sample(),
        [result()],
        answer="Milvus 是向量数据库。[E1]",
        top_k=5,
        latency_ms=1,
        tokens=1,
        trust=trust,
    )

    assert row["trust_input_claim_count"] == 3
    assert row["trust_input_claim_support_rate"] == pytest.approx(2 / 3)
    assert row["consistency_checked_claim_count"] == 2
    assert row["consistency_inconsistency_rate"] == 0.5
    assert row["entailment_checked_claim_count"] == 3
    assert row["entailment_coverage_rate"] == 1
    assert row["entailment_contradiction_rate"] == pytest.approx(1 / 3)
    assert row["entailment_unknown_rate"] == pytest.approx(1 / 3)
    assert row["entailment_token_usage"] == 23
    assert row["risk_level"] == "high"
    assert row["query_type"] == "financial_policy"
    assert row["risk_rejected_claim_count"] == 2
    assert row["risk_claim_rejection_rate"] == pytest.approx(2 / 3)
    assert row["policy_action"] == "downgrade"
    assert row["policy_answer_changed"] is True
    assert row["provenance_available"] is True
    assert row["provenance_contract_version"] == 2
    assert row["temporal_provenance_bound"] is True
    assert row["evidence_provenance_status"] == "complete"
    assert row["evidence_provenance_bound"] is True
    assert row["evidence_provenance_complete"] is True
    assert row["web_evidence_provenance_count"] == 1
    assert row["evidence_publication_anchor_count"] == 1
    assert row["evidence_publication_anchor_eligible_count"] == 1
    assert row["evidence_publication_anchor_coverage_rate"] == 1
    assert row["evidence_version_family_count"] == 1
    assert row["evidence_version_family_eligible_count"] == 1
    assert row["evidence_version_family_coverage_rate"] == 1
    summary = aggregate([row])
    assert summary["trust_input_claim_support_rate"] == 0.6667
    assert summary["consistency_inconsistency_rate"] == 0.5
    assert summary["entailment_coverage_rate"] == 1
    assert summary["entailment_contradiction_rate"] == 0.3333
    assert summary["entailment_unknown_rate"] == 0.3333
    assert summary["entailment_tokens"] == {"total": 23, "average": 23.0}
    assert summary["high_risk_sample_count"] == 1
    assert summary["high_risk_rate"] == 1
    assert summary["risk_claim_rejection_rate"] == 0.6667
    assert summary["policy_change_rate"] == 1
    assert summary["provenance_coverage_rate"] == 1
    assert summary["temporal_provenance_bound_rate"] == 1
    assert summary["evidence_provenance_bound_rate"] == 1
    assert summary["evidence_provenance_complete_rate"] == 1
    assert summary["web_evidence_provenance_count"] == 1
    assert summary["evidence_publication_anchor_count"] == 1
    assert summary["evidence_publication_anchor_eligible_count"] == 1
    assert summary["evidence_publication_anchor_coverage_rate"] == 1
    assert summary["evidence_version_family_count"] == 1
    assert summary["evidence_version_family_eligible_count"] == 1
    assert summary["evidence_version_family_coverage_rate"] == 1


def test_relative_time_metrics_expose_unknown_and_fail_closed_rejection():
    trust = {
        "input": {
            "claims": [
                {
                    "support_status": "unsupported",
                    "consistency_status": "unknown",
                    "consistency_checks": [
                        {
                            "kind": "relative_time",
                            "status": "unknown",
                            "reason_code": "RELATIVE_TIME_EVIDENCE_ANCHOR_MISSING",
                        }
                    ],
                }
            ]
        },
        "policy": {"action": "refuse", "answer_changed": True},
    }

    row = evaluate_sample(
        answerable_sample(),
        [result()],
        answer="根据现有证据，无法给出有充分依据的回答。",
        top_k=5,
        latency_ms=1,
        tokens=1,
        trust=trust,
    )

    assert row["consistency_checked_claim_count"] == 1
    assert row["consistency_unknown_claim_count"] == 1
    assert row["consistency_unknown_rate"] == 1
    assert row["relative_time_claim_count"] == 1
    assert row["relative_time_unknown_rate"] == 1
    assert row["relative_time_rejection_rate"] == 1
    summary = aggregate([row])
    assert summary["consistency_unknown_rate"] == 1
    assert summary["relative_time_unknown_rate"] == 1
    assert summary["relative_time_rejection_rate"] == 1


def test_freshness_metrics_expose_intent_unknown_and_fail_closed_rejection():
    trust = {
        "input": {
            "claims": [
                {
                    "support_status": "unsupported",
                    "consistency_status": "unknown",
                    "consistency_checks": [
                        {
                            "kind": "freshness",
                            "status": "unknown",
                            "reason_code": "FRESHNESS_RECENCY_WINDOW_UNDEFINED",
                        }
                    ],
                }
            ]
        },
        "freshness": {
            "required": True,
            "mode": "recent",
        },
        "policy": {"action": "refuse", "answer_changed": True},
    }

    row = evaluate_sample(
        answerable_sample(),
        [result()],
        answer="根据现有证据，无法给出有充分依据的回答。",
        top_k=5,
        latency_ms=1,
        tokens=1,
        trust=trust,
    )

    assert row["freshness_mode"] == "recent"
    assert row["freshness_required"] is True
    assert row["freshness_claim_count"] == 1
    assert row["freshness_unknown_rate"] == 1
    assert row["freshness_rejection_rate"] == 1
    summary = aggregate([row])
    assert summary["freshness_required_sample_count"] == 1
    assert summary["freshness_required_rate"] == 1
    assert summary["freshness_unknown_rate"] == 1
    assert summary["freshness_rejection_rate"] == 1


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


def test_unanswerable_accepts_explicit_absence_of_load_test_data_as_refusal():
    sample = EvalSample(
        id="q3",
        question="生产 QPS 是多少？",
        answerable=False,
        reference_answer="没有规定",
        evidence=(),
        criteria=(),
        tags=("无答案",),
    )
    row = evaluate_sample(
        sample,
        [result()],
        answer="没有负载测试数据，不能声称当前接口达到某个生产 QPS。",
        top_k=5,
        latency_ms=2,
        tokens=10,
    )

    assert row["refusal_correct"] is True


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
    assert summary["full_evidence_retrieval_rate"] == 0.5
