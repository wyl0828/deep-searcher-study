import json
from pathlib import Path

from deepsearcher.answer_strategy import enforce_answer_order, plan_answer_strategy
from deepsearcher.grounding import format_grounding_evidence
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult

DEFINITION = (
    "DeepSearcher 是一个基于 RAG 的文档问答项目，解决的是通用大模型不了解用户私有文档、"
    "回答缺少可核验依据的问题。"
)
PROCESS = "文档清洗后会切分成片段并转为向量写入 Milvus。"
GOLD_PATH = Path(__file__).parents[1] / "evaluation" / "datasets" / "answer_order_v1.json"


def result(text: str) -> RetrievalResult:
    return RetrievalResult(
        embedding=[0.1] * 4,
        text=text,
        reference="notes.pdf",
        metadata={},
    )


def test_definition_queries_share_definition_first_plan():
    results = [result("DeepSearcher"), result(DEFINITION), result(PROCESS)]
    evidence_ids = {id(item): f"E{index}" for index, item in enumerate(results, start=1)}

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    assert gold["strategy_version"] == "definition-first.v1"
    assert len(gold["cases"]) == 4

    for case in gold["cases"]:
        query = case["query"]
        plan = plan_answer_strategy(query, results, evidence_ids=evidence_ids)

        assert plan.version == "definition-first.v1"
        assert plan.query_class == "concept_definition"
        assert plan.decision == "definition_first"
        assert plan.primary_evidence_ids == ("E2",)
        assert plan.supporting_evidence_ids == ("E1", "E3")


def test_definition_first_gate_replaces_process_first_answer_with_evidence_sentence():
    results = [result("DeepSearcher"), result(DEFINITION), result(PROCESS)]
    evidence_ids = {id(item): f"E{index}" for index, item in enumerate(results, start=1)}
    evidence_texts = {f"E{index}": item.text for index, item in enumerate(results, start=1)}
    plan = plan_answer_strategy("DeepSearcher 是什么", results, evidence_ids=evidence_ids)

    answer, gated_plan = enforce_answer_order(
        f"{PROCESS} [E3]\n{DEFINITION} [E2]",
        plan,
        results=results,
        evidence_texts=evidence_texts,
    )

    assert answer == f"{DEFINITION} [E2]"
    assert gated_plan.fallback_used is True
    assert gated_plan.output_gate == "canonical_definition"
    assert gated_plan.fallback_reason == "definition_sentence_fallback"


def test_definition_first_gate_keeps_a_valid_definition_first_answer():
    definition_result = result(DEFINITION)
    results = [definition_result]
    plan = plan_answer_strategy(
        "什么是DeepSearcher",
        results,
        evidence_ids={id(definition_result): "E1"},
    )

    answer, gated_plan = enforce_answer_order(
        f"{DEFINITION} [E1]",
        plan,
        results=results,
        evidence_texts={"E1": DEFINITION},
    )

    assert answer == f"{DEFINITION} [E1]"
    assert gated_plan.fallback_used is False
    assert gated_plan.output_gate == "canonical_definition"


def test_definition_first_does_not_invent_when_definition_evidence_is_missing():
    process_result = result(PROCESS)
    plan = plan_answer_strategy(
        "DeepSearcher 是什么",
        [process_result],
        evidence_ids={id(process_result): "E1"},
    )

    answer, gated_plan = enforce_answer_order(
        f"{PROCESS} [E1]",
        plan,
        results=[process_result],
        evidence_texts={"E1": PROCESS},
    )

    assert plan.decision == "fallback_default"
    assert answer == f"{PROCESS} [E1]"
    assert gated_plan.output_gate == "not_applicable"


def test_definition_strategy_trace_keeps_evidence_ids_and_roles_auditable():
    definition_result = result(DEFINITION)
    process_result = result(PROCESS)
    results = [definition_result, process_result]
    evidence_ids = {}
    evidence_texts = {}
    format_grounding_evidence(
        results,
        use_wider_text=False,
        evidence_ids=evidence_ids,
        evidence_texts=evidence_texts,
    )
    plan = plan_answer_strategy("DeepSearcher是什么", results, evidence_ids=evidence_ids)
    collector = TraceCollector("DeepSearcher是什么")
    collector.record_answer_strategy(plan.with_output_gate(used=False, reason=None).as_trace())

    trace = collector.build(total_tokens=0, final_results=results)

    assert trace["version"] == 8
    assert trace["answer_strategy"]["version"] == "definition-first.v1"
    assert trace["answer_strategy"]["primary_evidence_ids"] == ["E1"]
    assert trace["answer_strategy"]["supporting_evidence_ids"] == ["E2"]
    assert trace["answer_strategy"]["intermediate_context_role"] == "secondary_context"
    assert [event["stage"] for event in trace["selection_events"]] == [
        "answer_strategy.query_classification",
        "answer_strategy.context_partition",
    ]
