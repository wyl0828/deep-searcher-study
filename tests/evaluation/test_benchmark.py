import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deepsearcher.llm.base import ChatResponse
from deepsearcher.vector_db.base import RetrievalResult
from evaluation.benchmark import (
    append_checkpoint_row,
    apply_llm_timeout_override,
    checkpoint_signature,
    evaluate_agent,
    load_env_file,
    parse_args,
    parse_source_aliases,
    prepare_checkpoint,
    recommend_answer_agent,
    save_report,
    select_samples,
    summarize_sample_selection,
    verify_source,
)
from evaluation.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "milvus_v1.json"
WORKSPACE_DATASET = (
    Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "workspace_v2.json"
)


def test_checkpoint_is_durable_resumable_and_bound_to_run_signature(tmp_path):
    signature = {
        "dataset_sha256": "a" * 64,
        "agents": ["naive"],
        "sample_ids": ["sample-1"],
        "external_call_timeout_seconds": 30.0,
        "request_timeout_seconds": 300.0,
    }
    path, rows = prepare_checkpoint(tmp_path / "run", signature)
    assert rows == {}
    append_checkpoint_row(
        path,
        {"agent": "naive", "sample_id": "sample-1", "error": "temporary"},
    )
    append_checkpoint_row(
        path,
        {"agent": "naive", "sample_id": "sample-1", "error": None, "tokens": 12},
    )

    _, resumed = prepare_checkpoint(tmp_path / "run", signature)
    assert resumed[("naive", "sample-1")]["tokens"] == 12
    assert checkpoint_signature(signature)["sha256"]

    widened = {**signature, "external_call_timeout_seconds": 60.0}
    _, resumed_after_widening = prepare_checkpoint(tmp_path / "run", widened)
    assert resumed_after_widening[("naive", "sample-1")]["tokens"] == 12
    history = (tmp_path / "run" / "checkpoint.history.jsonl").read_text(encoding="utf-8")
    assert "timeout_widened_after_observed_failures" in history

    provider_widened = {**widened, "llm_timeout_seconds": 60.0}
    _, resumed_after_provider_widening = prepare_checkpoint(tmp_path / "run", provider_widened)
    assert resumed_after_provider_widening[("naive", "sample-1")]["tokens"] == 12

    legacy_null = tmp_path / "legacy-null"
    null_signature = {**signature, "llm_timeout_seconds": None}
    null_path, _ = prepare_checkpoint(legacy_null, null_signature)
    append_checkpoint_row(
        null_path,
        {"agent": "naive", "sample_id": "sample-1", "error": "APITimeoutError"},
    )
    _, resumed_after_null_widening = prepare_checkpoint(
        legacy_null,
        {**null_signature, "llm_timeout_seconds": 120.0},
    )
    assert resumed_after_null_widening[("naive", "sample-1")]["error"] == "APITimeoutError"

    with pytest.raises(ValueError, match="signature"):
        prepare_checkpoint(tmp_path / "run", {**provider_widened, "llm_timeout_seconds": 30.0})

    with pytest.raises(ValueError, match="signature"):
        prepare_checkpoint(tmp_path / "run", {**provider_widened, "agents": ["deep_search"]})

    with pytest.raises(ValueError, match="signature"):
        prepare_checkpoint(
            tmp_path / "run",
            {**provider_widened, "freshness_classifier_version": "different"},
        )


def test_llm_timeout_override_replaces_the_openai_compatible_client():
    updated_client = object()
    client = SimpleNamespace(with_options=Mock(return_value=updated_client))
    runtime = SimpleNamespace(llm=SimpleNamespace(client=client))

    apply_llm_timeout_override(runtime, 60)

    client.with_options.assert_called_once_with(timeout=60.0)
    assert runtime.llm.client is updated_client


def test_llm_timeout_override_rejects_unsupported_clients():
    runtime = SimpleNamespace(llm=SimpleNamespace(client=object()))

    with pytest.raises(ValueError, match="does not support"):
        apply_llm_timeout_override(runtime, 60)


def test_checkpoint_ignores_only_a_truncated_final_line(tmp_path):
    output = tmp_path / "run"
    path, _ = prepare_checkpoint(output, {"run": "one"})
    append_checkpoint_row(path, {"agent": "naive", "sample_id": "sample-1", "error": None})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"agent":"naive"')

    _, resumed = prepare_checkpoint(output, {"run": "one"})
    assert set(resumed) == {("naive", "sample-1")}


def multi_source_dataset(tmp_path):
    raw = {
        "schema_version": 2,
        "dataset_id": "multi-v2",
        "version": "2.0.0",
        "description": "multi source fixture",
        "sources": [
            {
                "document": "one.pdf",
                "path": "one.pdf",
                "sha256": "1" * 64,
            },
            {
                "document": "two.pdf",
                "path": "two.pdf",
                "sha256": "2" * 64,
            },
        ],
        "samples": [
            {
                "id": "multi-001",
                "question": "两份资料分别说了什么？",
                "answerable": True,
                "reference_answer": "one and two",
                "evidence": [
                    {"document": "one.pdf", "page": 1},
                    {"document": "two.pdf", "page": 2},
                ],
                "criteria": [["one"], ["two"]],
                "tags": ["跨文档"],
                "difficulty": "hard",
                "history": [
                    {"role": "user", "content": "先说说第一份资料。"},
                    {"role": "assistant", "content": "第一份资料介绍 one。"},
                ],
                "context_dependent": True,
                "standalone_question": "第一份和第二份资料分别说了什么？",
            }
        ],
    }
    path = tmp_path / "multi.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return load_dataset(path)


class FakeLLM:
    calls = 0


class FakeAgent:
    llm = FakeLLM()

    def retrieve(self, query, **kwargs):
        assert kwargs["collection_names"] == ["kb_test"]
        assert kwargs["top_k"] == 3
        return (
            [
                RetrievalResult(
                    [],
                    "Milvus 是向量数据库，管理非结构化数据的 embedding。",
                    "WhatisMilvus.pdf",
                    {
                        "display_name": "WhatisMilvus.pdf",
                        "page_number": 1,
                        "chunk_index": 0,
                    },
                )
            ],
            0,
            {},
        )


def test_evaluate_agent_uses_exact_collection_scope():
    dataset = load_dataset(DATASET)
    rows, summary = evaluate_agent(
        "naive",
        FakeAgent(),
        dataset,
        collection="kb_test",
        mode="retrieval",
        top_k=3,
        limit=1,
    )
    assert len(rows) == 1
    assert rows[0]["retrieval_hit"] is True
    assert summary["sample_count"] == 1


def test_evaluate_agent_skips_successful_checkpoint_rows():
    dataset = load_dataset(DATASET)
    initial_rows, _ = evaluate_agent(
        "naive",
        FakeAgent(),
        dataset,
        collection="kb_test",
        mode="retrieval",
        top_k=3,
        limit=1,
    )

    class MustNotRunAgent:
        llm = FakeLLM()

        def retrieve(self, *_args, **_kwargs):
            raise AssertionError("successful checkpoint row must be resumed")

    resumed_rows, summary = evaluate_agent(
        "naive",
        MustNotRunAgent(),
        dataset,
        collection="kb_test",
        mode="retrieval",
        top_k=3,
        limit=1,
        existing_rows={initial_rows[0]["sample_id"]: initial_rows[0]},
    )
    assert resumed_rows[0]["sample_id"] == initial_rows[0]["sample_id"]
    assert resumed_rows[0]["retrieval_recall"] == initial_rows[0]["retrieval_recall"]
    assert resumed_rows[0]["checkpoint_status"] == "reused"
    assert summary["error_rate"] == 0
    assert summary["checkpoint"]["reused"] == 1


def test_evaluate_agent_retries_failed_checkpoint_and_reports_recovery():
    dataset = load_dataset(DATASET)
    rows, summary = evaluate_agent(
        "naive",
        FakeAgent(),
        dataset,
        collection="kb_test",
        mode="retrieval",
        top_k=3,
        limit=1,
        existing_rows={
            "milvus-001": {
                "agent": "naive",
                "sample_id": "milvus-001",
                "error": "APITimeoutError",
                "evaluation_attempts": 1,
            }
        },
    )

    assert rows[0]["checkpoint_status"] == "recovered"
    assert rows[0]["evaluation_attempts"] == 2
    assert summary["checkpoint"]["recovered"] == 1


def test_evaluate_agent_contextualizes_conversation_and_counts_cost(tmp_path):
    captured = {}

    class ContextLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, _messages):
            self.calls += 1
            return ChatResponse(
                content=(
                    '{"depends_on_history": true, '
                    '"standalone_query": "第一份和第二份资料分别说了什么？"}'
                ),
                total_tokens=6,
            )

        @staticmethod
        def remove_think(content):
            return content

    class ContextAgent:
        def __init__(self):
            self.llm = ContextLLM()

        def retrieve(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return (
                [
                    RetrievalResult(
                        [],
                        "one",
                        "one.pdf",
                        {"display_name": "one.pdf", "page_number": 1},
                    )
                ],
                2,
                {},
            )

    rows, summary = evaluate_agent(
        "naive",
        ContextAgent(),
        multi_source_dataset(tmp_path),
        collection="kb_test",
        mode="retrieval",
        top_k=3,
    )

    assert captured["query"] == "先说说第一份资料。 两份资料分别说了什么？"
    assert captured["kwargs"]["collection_names"] == ["kb_test"]
    assert rows[0]["context_dependency_correct"] is True
    assert rows[0]["context_query_match"] is True
    assert rows[0]["context_fallback_used"] is False
    assert rows[0]["context_tokens"] == 6
    assert rows[0]["tokens"] == 8
    assert rows[0]["llm_calls"] == 1
    assert summary["context_dependency_accuracy"] == 1.0


def test_save_report_writes_json_and_csv_atomically(tmp_path):
    report = {"details": [{"agent": "naive", "sample_id": "q1"}]}
    report_path, details_path = save_report(report, tmp_path)
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert "sample_id" in details_path.read_text(encoding="utf-8-sig")


def test_verify_source_checks_pinned_sha():
    dataset = load_dataset(DATASET)
    source = verify_source(dataset)
    assert source.name == "WhatisMilvus.pdf"


def test_load_env_file_does_not_override_existing_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EVAL_NEW='new value'\nEVAL_EXISTING=changed\n", encoding="utf-8")
    monkeypatch.setenv("EVAL_EXISTING", "original")
    monkeypatch.delenv("EVAL_NEW", raising=False)

    load_env_file(env_file)

    assert __import__("os").environ["EVAL_NEW"] == "new value"
    assert __import__("os").environ["EVAL_EXISTING"] == "original"


def test_select_samples_preserves_requested_order():
    dataset = load_dataset(DATASET)
    selected = select_samples(dataset, sample_ids=("milvus-026", "milvus-001"))
    assert [sample.id for sample in selected] == ["milvus-026", "milvus-001"]


def test_stratified_answer_profile_has_pinned_coverage():
    manifest = json.loads(
        (Path(__file__).resolve().parents[2] / "evaluation" / "quality_gate.json").read_text(
            encoding="utf-8"
        )
    )
    dataset = load_dataset(WORKSPACE_DATASET)
    selected = select_samples(
        dataset,
        sample_ids=tuple(manifest["full"]["answer_sample_ids"]),
    )

    assert summarize_sample_selection(selected) == {
        "sample_count": 24,
        "answerable": 20,
        "unanswerable": 4,
        "history": 6,
        "multi_document": 4,
        "difficulty": {"easy": 7, "hard": 9, "medium": 8},
    }


def test_answer_recommendation_uses_quality_gate_then_efficiency_tiebreakers():
    def summary(quality, *, tokens, refusal=1.0, support=None):
        return {
            "error_rate": 0.0,
            "answer_criteria_coverage": quality,
            "grounded_criteria_coverage": quality,
            "claim_support_rate": quality if support is None else support,
            "claim_citation_precision": quality,
            "claim_citation_recall": quality,
            "invalid_claim_citation_rate": 0.0,
            "refusal_accuracy": refusal,
            "tokens": {"average": tokens},
            "latency_ms": {"p95": 1000.0},
        }

    decision = recommend_answer_agent(
        {
            "naive": summary(0.85, tokens=100),
            "deep_search": summary(0.85, tokens=300),
            "chain_of_rag": summary(0.9, tokens=200, refusal=0.5),
        }
    )

    assert decision["recommended_default"] == "naive"
    assert decision["ranking"] == ["naive", "deep_search"]
    assert decision["scorecards"]["chain_of_rag"]["eligible"] is False
    assert (
        "REFUSAL_ACCURACY_BELOW_THRESHOLD"
        in decision["scorecards"]["chain_of_rag"]["rejection_reasons"]
    )


def test_parse_args_accepts_deep_search_concurrency_limits():
    args = parse_args(
        [
            "--collection",
            "kb_test",
            "--deep-search-concurrency",
            "8",
            "--llm-timeout-seconds",
            "30",
            "--external-call-timeout-seconds",
            "12",
            "--request-timeout-seconds",
            "60",
            "--chain-min-evidence-for-stop",
            "3",
            "--sample-profile",
            "answer_stratified_v1",
        ]
    )
    assert args.deep_search_concurrency == 8
    assert args.llm_timeout_seconds == 30
    assert args.external_call_timeout_seconds == 12
    assert args.request_timeout_seconds == 60
    assert args.chain_min_evidence_for_stop == 3
    assert args.sample_profile == "answer_stratified_v1"


def test_parse_source_aliases_supports_multi_source_mapping(tmp_path):
    dataset = multi_source_dataset(tmp_path)
    aliases = parse_source_aliases(
        dataset,
        ("one.pdf=doc_one.pdf", "two.pdf=doc_two.pdf"),
    )
    assert aliases == {
        "one.pdf": ("doc_one.pdf",),
        "two.pdf": ("doc_two.pdf",),
    }


def test_parse_source_aliases_rejects_ambiguous_multi_source_name(tmp_path):
    dataset = multi_source_dataset(tmp_path)
    with pytest.raises(ValueError, match="DOCUMENT=INGESTED_NAME"):
        parse_source_aliases(dataset, ("doc_one.pdf",))
