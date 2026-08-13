from deepsearcher.llm.base import ChatResponse
from deepsearcher.query_planner import (
    merge_ranked_results,
    needs_decomposition,
    plan_explicit_queries,
    plan_queries,
)
from deepsearcher.vector_db.base import RetrievalResult


class FakeLLM:
    def __init__(self, content, *, error=None):
        self.content = content
        self.error = error

    def chat(self, _messages):
        if self.error:
            raise self.error
        return ChatResponse(content=self.content, total_tokens=7)

    @staticmethod
    def remove_think(content):
        return content


def _result(reference, text, *, page=None, document_id=None):
    metadata = {}
    if page is not None:
        metadata["page_number"] = page
    if document_id is not None:
        metadata["document_id"] = document_id
    return RetrievalResult([], text, reference, metadata)


def test_query_plan_keeps_original_filters_drift_and_limits_topics():
    plan = plan_queries(
        FakeLLM(
            '{"queries":["第一份资料的版本策略", "第二份资料的召回策略", '
            '"无关 FastAPI 路由", "两份资料如何比较", "超额两份资料"]}'
        ),
        "比较第一份和第二份资料的版本与召回策略",
    )

    assert plan.queries[0] == "比较第一份和第二份资料的版本与召回策略"
    assert len(plan.queries) == 4
    assert all("FastAPI" not in query for query in plan.queries)
    assert plan.decomposed is True


def test_query_plan_falls_back_on_provider_failure():
    plan = plan_queries(FakeLLM("", error=RuntimeError("down")), "比较两份资料")

    assert plan.queries == ("比较两份资料",)
    assert plan.fallback_used is True


def test_explicit_query_plan_keeps_original_and_filters_drift():
    plan = plan_explicit_queries(
        "Milvus 有什么能力？",
        ("Milvus 有什么能力？", "Milvus 支持哪些检索？", "FastAPI 路由配置"),
    )

    assert plan.queries == ("Milvus 有什么能力？", "Milvus 支持哪些检索？")


def test_decomposition_detects_relational_and_multi_entity_questions():
    assert needs_decomposition("DeepSearcher 从文档切分到浏览器引用的完整可信链路是什么？")
    assert needs_decomposition("为什么默认 Agent 路由与 Token 成本必须一起分析？")
    assert needs_decomposition("Milvus 的说明是否意味着 DeepSearcher 已启用 Hybrid？")
    assert not needs_decomposition("Milvus 是什么？")


def test_explicit_document_roles_use_deterministic_decomposition():
    llm = FakeLLM("not used", error=AssertionError("provider must not be called"))

    plan = plan_queries(
        llm,
        "为什么不能把 Milvus 文档中的性能描述直接当成 DeepSearcher 的容量证明？",
    )

    assert plan.reason == "deterministic_decomposition"
    assert plan.queries == (
        "为什么不能把 Milvus 文档中的性能描述直接当成 DeepSearcher 的容量证明？",
        "Milvus 文档中的性能描述",
        "DeepSearcher 的容量证明",
    )


def test_merge_ranked_results_is_round_robin_deduplicated_and_stable():
    shared = _result("one.pdf", "shared")
    merged = merge_ranked_results(
        [
            [_result("one.pdf", "one-a"), shared],
            [_result("two.pdf", "two-a"), shared],
        ],
        limit=4,
    )

    assert [(item.reference, item.text) for item in merged] == [
        ("one.pdf", "one-a"),
        ("two.pdf", "two-a"),
        ("one.pdf", "shared"),
    ]


def test_text_identity_policy_preserves_deep_search_deduplication_contract():
    first = _result("one.pdf", "same text")
    duplicate = _result("two.pdf", "same text")

    assert merge_ranked_results([[first], [duplicate]], limit=4, identity_policy="text") == [first]
    assert merge_ranked_results([[first], [duplicate]], limit=4) == [first, duplicate]


def test_source_chunk_identity_keeps_pages_and_deduplicates_same_chunk():
    first_page = _result("guide.pdf", "same excerpt", page=1, document_id="doc")
    second_page = _result("guide.pdf", "same excerpt", page=2, document_id="doc")
    duplicate = _result("guide.pdf", "same excerpt", page=1, document_id="doc")

    assert merge_ranked_results([[first_page, second_page], [duplicate]], limit=4) == [
        first_page,
        second_page,
    ]


def test_merge_preserves_original_anchors_then_prefers_unseen_documents():
    merged = merge_ranked_results(
        [
            [_result("one.pdf", "one-a"), _result("one.pdf", "one-b")],
            [_result("one.pdf", "one-c"), _result("two.pdf", "two-a")],
        ],
        limit=4,
        anchor_count=2,
        per_group_anchor_count=1,
        diversify_documents=True,
    )

    assert [(item.reference, item.text) for item in merged] == [
        ("one.pdf", "one-a"),
        ("one.pdf", "one-b"),
        ("one.pdf", "one-c"),
        ("two.pdf", "two-a"),
    ]


def test_cross_query_rrf_promotes_repeated_deeper_evidence_after_anchors():
    anchor_a = _result("one.pdf", "anchor-a")
    anchor_b = _result("one.pdf", "anchor-b")
    repeated = _result("two.pdf", "repeated evidence")
    merged = merge_ranked_results(
        [
            [anchor_a, anchor_b, _result("one.pdf", "x"), repeated],
            [_result("one.pdf", "y"), _result("one.pdf", "z"), repeated],
            [_result("one.pdf", "w"), repeated],
        ],
        limit=4,
        anchor_count=2,
        cross_query_rrf_k=5,
    )

    assert merged[:3] == [anchor_a, anchor_b, repeated]
