import json
from pathlib import Path

from deepsearcher.vector_db.base import RetrievalResult
from evaluation.benchmark import (
    evaluate_agent,
    load_env_file,
    parse_args,
    save_report,
    select_samples,
    verify_source,
)
from evaluation.dataset import load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "milvus_v1.json"


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


def test_parse_args_accepts_deep_search_concurrency_limits():
    args = parse_args(
        [
            "--collection",
            "kb_test",
            "--deep-search-concurrency",
            "8",
            "--external-call-timeout-seconds",
            "12",
            "--request-timeout-seconds",
            "60",
            "--chain-min-evidence-for-stop",
            "3",
        ]
    )
    assert args.deep_search_concurrency == 8
    assert args.external_call_timeout_seconds == 12
    assert args.request_timeout_seconds == 60
    assert args.chain_min_evidence_for_stop == 3
