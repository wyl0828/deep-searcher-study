import json
from pathlib import Path

import pytest

from deepsearcher.llm.base import ChatResponse
from deepsearcher.vector_db.base import RetrievalResult
from evaluation.dataset import load_dataset
from evaluation.retrieval_compare import (
    DecisionThresholds,
    evaluate_modes,
    parse_args,
    parse_modes,
    recommend_default,
    save_report,
    validate_evaluation_collection,
)

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "milvus_v1.json"
WORKSPACE_DATASET = (
    Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "workspace_v2.json"
)


class FakeEmbedding:
    def __init__(self):
        self.calls = 0

    def embed_query(self, query):
        self.calls += 1
        return [float(len(query))]


class FakeVectorDB:
    def __init__(self):
        self.calls = []

    def search_data(self, **kwargs):
        self.calls.append(kwargs)
        return [
            RetrievalResult(
                embedding=[1.0],
                text="Milvus 是管理非结构化数据 embedding 的向量数据库。",
                reference="WhatisMilvus.pdf",
                metadata={
                    "display_name": "WhatisMilvus.pdf",
                    "page_number": 1,
                    "chunk_index": 0,
                },
                metric_type="RRF" if kwargs["retrieval_mode"] == "hybrid" else "L2",
                rank_score=0.5 if kwargs["retrieval_mode"] == "hybrid" else None,
                distance=0.1 if kwargs["retrieval_mode"] != "hybrid" else None,
            )
        ]


def test_evaluate_modes_replans_each_repetition_and_shares_plan_across_modes():
    dataset = load_dataset(DATASET)
    embedding = FakeEmbedding()
    vector_db = FakeVectorDB()

    rows, summaries = evaluate_modes(
        dataset,
        embedding_model=embedding,
        vector_db=vector_db,
        collection="eval_test",
        modes=("dense", "bm25", "hybrid"),
        top_k=5,
        repetitions=2,
        limit=1,
        source_aliases={"WhatisMilvus.pdf": ("WhatisMilvus.pdf",)},
    )

    assert embedding.calls == 2
    assert len(vector_db.calls) == 6
    assert set(rows) == {"dense", "bm25", "hybrid"}
    assert set(summaries) == set(rows)
    assert all(mode_rows[0]["retrieval_hit"] == 1.0 for mode_rows in rows.values())
    assert all(mode_rows[0]["search_repetitions"] == 2 for mode_rows in rows.values())
    assert all(mode_rows[0]["ranking_stability_rate"] == 1 for mode_rows in rows.values())
    assert all(mode_rows[0]["query_plan_stability_rate"] == 1 for mode_rows in rows.values())
    assert all(len(mode_rows[0]["repetitions"]) == 2 for mode_rows in rows.values())


def test_evaluate_modes_uses_contextualized_query_for_every_mode():
    class ContextLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, _messages):
            self.calls += 1
            return ChatResponse(
                content=(
                    '{"depends_on_history": true, '
                    '"standalone_query": "Milvus Lite、Standalone 和 Distributed 分别适合什么部署场景？"}'
                ),
                total_tokens=9,
            )

        @staticmethod
        def remove_think(content):
            return content

    dataset = load_dataset(WORKSPACE_DATASET)
    embedding = FakeEmbedding()
    vector_db = FakeVectorDB()
    rows, summaries = evaluate_modes(
        dataset,
        embedding_model=embedding,
        vector_db=vector_db,
        contextualizer_llm=ContextLLM(),
        collection="eval_test",
        modes=("dense", "hybrid"),
        top_k=5,
        repetitions=1,
        sample_ids=("workspace-061",),
        source_aliases={"WhatisMilvus.pdf": ("WhatisMilvus.pdf",)},
    )

    queries = {call["query_text"] for call in vector_db.calls}
    assert "Milvus 是什么，它有哪些部署形态？ 那三种分别适合什么场景？" in queries
    assert any("部署形态包括 Lite" in query for query in queries)
    assert rows["dense"][0]["context_dependency_correct"] == 1.0
    assert rows["dense"][0]["context_query_match"] == 1.0
    assert len(rows["dense"][0]["context_retrieval_queries"]) == 2
    assert rows["dense"][0]["tokens"] == 9
    assert rows["dense"][0]["llm_calls"] == 1
    assert summaries["hybrid"]["context_dependency_accuracy"] == 1.0


def _summary(quality, search_p95=5.0, error_rate=0.0):
    return {
        "retrieval_recall_at_k": quality,
        "mrr": quality,
        "grounded_criteria_coverage": quality,
        "error_rate": error_rate,
        "search_latency_ms": {"average": search_p95, "p50": search_p95, "p95": search_p95},
    }


def test_recommendation_promotes_only_material_safe_hybrid_gain():
    decision = recommend_default(
        {"dense": _summary(0.8), "hybrid": _summary(0.84, search_p95=8)},
        answerable_samples=25,
        thresholds=DecisionThresholds(),
    )

    assert decision["promote_hybrid"] is True
    assert decision["recommended_default"] == "hybrid"
    assert decision["quality"]["gain"] == pytest.approx(0.04)


@pytest.mark.parametrize(
    ("hybrid", "expected_reason"),
    [
        (_summary(0.8), "QUALITY_GAIN_BELOW_THRESHOLD"),
        (_summary(0.84, search_p95=20), "SEARCH_P95_OVERHEAD_TOO_HIGH"),
        (_summary(0.84, error_rate=0.1), "HYBRID_ERRORS_PRESENT"),
    ],
)
def test_recommendation_keeps_dense_when_gate_fails(hybrid, expected_reason):
    decision = recommend_default(
        {"dense": _summary(0.8), "hybrid": hybrid},
        answerable_samples=25,
        thresholds=DecisionThresholds(),
    )

    assert decision["promote_hybrid"] is False
    assert decision["recommended_default"] == "dense"
    assert expected_reason in decision["reasons"]


def test_collection_and_modes_are_restricted_to_safe_evaluation_scope():
    assert validate_evaluation_collection("eval_o06") == "eval_o06"
    assert parse_modes("dense,bm25,dense,hybrid") == ("dense", "bm25", "hybrid")
    with pytest.raises(ValueError):
        validate_evaluation_collection("kb_product")
    with pytest.raises(ValueError):
        parse_modes("dense,invalid")


def test_save_report_is_atomic_and_csv_has_retrieval_mode(tmp_path):
    report = {
        "details": [
            {
                "retrieval_mode": "dense",
                "sample_id": "q1",
                "tags": ["事实"],
            }
        ]
    }

    report_path, details_path = save_report(report, tmp_path)

    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert "retrieval_mode" in details_path.read_text(encoding="utf-8-sig")


def test_parse_args_exposes_rrf_and_repetition_contract():
    args = parse_args(
        [
            "--rrf-k",
            "37",
            "--hybrid-ranker",
            "weighted_rrf",
            "--dense-weight",
            "1.5",
            "--sparse-weight",
            "0.75",
            "--candidate-multiplier",
            "2",
            "--dense-anchors",
            "2",
            "--diversity-tolerance",
            "0.1",
            "--repetitions",
            "5",
            "--batch-size",
            "8",
            "--prepare",
            "--cleanup",
        ]
    )

    assert args.rrf_k == 37
    assert args.hybrid_ranker == "weighted_rrf"
    assert args.dense_weight == 1.5
    assert args.sparse_weight == 0.75
    assert args.candidate_multiplier == 2
    assert args.dense_anchors == 2
    assert args.diversity_tolerance == 0.1
    assert args.repetitions == 5
    assert args.batch_size == 8
    assert args.prepare is True
    assert args.cleanup is True
