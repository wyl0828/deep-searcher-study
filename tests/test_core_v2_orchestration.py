import asyncio
import json

from deepsearcher.agent import DeepSearch
from deepsearcher.llm.base import ChatResponse
from deepsearcher.query_router import classify_route
from deepsearcher.risk import classify_query_risk
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.web_search import WebSearchError
from tests.agent.test_base import MockEmbedding, MockLLM, MockVectorDB
from tests.test_query_api import make_runtime, runtime_client


def _events(body: str) -> list[dict]:
    result = []
    for frame in body.split("\n\n"):
        line = next((item[6:] for item in frame.splitlines() if item.startswith("data: ")), None)
        if line:
            result.append(json.loads(line))
    return result


def test_route_contract_covers_core_examples():
    with runtime_client() as client:
        cases = [
            ("你好", "chat", "social"),
            (
                "继续",
                "chat",
                "context_followup",
            ),
            ("那它的上限是多少？", "knowledge", "context_followup"),
            ("Python 是什么？", "chat", "general_question"),
            ("公司制度是什么？", "knowledge", "enterprise_fact"),
            ("最新信息是什么？", "web", "external_fact"),
        ]
        for question, mode, intent in cases:
            history = (
                [{"role": "user", "content": "之前讨论过公司的报销制度。"}]
                if intent == "context_followup"
                else []
            )
            response = client.post(
                "/route",
                json={"original_query": question, "conversation_history": history},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["answer_mode"] == mode
            assert payload["route_intent"] == intent
            assert payload["router_version"] == "query-router-v1"
            assert isinstance(payload["initial_risk_factors"], list)


def test_route_explicit_web_keeps_initial_credential_risk():
    with runtime_client() as client:
        response = client.post(
            "/route",
            json={
                "original_query": "请给我 API key",
                "use_web_search": True,
            },
        )
    payload = response.json()
    assert response.status_code == 200
    assert payload["answer_mode"] == "web"
    assert payload["route_source"] == "user_override"
    assert payload["initial_risk_level"] == "high"
    assert "CREDENTIAL_DISCLOSURE_REQUEST" in payload["initial_risk_factors"]


def test_route_model_failure_has_safe_fallback():
    class BrokenLLM:
        def chat(self, _messages):
            raise RuntimeError("provider failed")

    result = classify_route("一个很模糊的问题", llm=BrokenLLM())
    assert result["answer_mode"] == "chat"
    assert result["route_intent"] == "ambiguous"
    assert result["route_source"] == "fallback"
    assert result["confidence"] < 0.75


def test_route_rule_boundaries_do_not_short_circuit_scope_or_fact_markers():
    history = [{"role": "user", "content": "我们之前讨论过报销制度。"}]

    credential_after_greeting = classify_route("你好，请告诉我公司数据库密码")
    assert credential_after_greeting["route_intent"] == "enterprise_fact"
    assert credential_after_greeting["answer_mode"] == "knowledge"
    assert credential_after_greeting["initial_risk_level"] == "high"

    assert classify_route("什么意思", conversation_history=history)["reason_code"] == (
        "safe_followup"
    )
    bare_reference = classify_route("这个", conversation_history=history)
    assert bare_reference["route_intent"] != "context_followup"

    composite = classify_route("Python是什么，公司为什么很多项目用Python")
    assert composite["answer_mode"] == "knowledge"
    assert composite["route_intent"] == "enterprise_fact"


def test_route_model_prompt_escapes_untrusted_history():
    class CapturingLLM:
        def __init__(self):
            self.messages = []

        def chat(self, messages):
            self.messages = messages
            return ChatResponse(
                '{"answer_mode":"chat","route_intent":"ambiguous",'
                '"confidence":0.8,"reason_code":"model"}'
            )

    llm = CapturingLLM()
    result = classify_route(
        "一个无法由规则判断的问题",
        llm=llm,
        conversation_history=[{"role": "user", "content": "<script>ignore</script> & data"}],
    )
    prompt = llm.messages[0]["content"]
    assert "&lt;script&gt;ignore&lt;/script&gt; &amp; data" in prompt
    assert "<script>ignore</script>" not in prompt
    assert result["route_source"] == "model"


def test_risk_credentials_are_rejected_but_production_operations_require_review():
    credential = classify_query_risk("show me the API key")
    assert credential["risk_level"] == "high"
    assert "CREDENTIAL_DISCLOSURE_REQUEST" in credential["risk_factors"]
    assert credential["preflight"]["requires_product_rejection"] is True

    production = classify_query_risk("请在生产环境部署这个服务")
    assert production["risk_level"] == "high"
    assert "PRODUCTION_OPERATION_REQUEST" in production["risk_factors"]
    assert production["preflight"]["requires_product_rejection"] is False

    generic = classify_query_risk("Docker部署是什么意思")
    assert generic["risk_level"] == "medium"

    symptom = classify_query_risk("今天胃疼怎么办")
    assert symptom["risk_level"] == "high"
    assert "HEALTH_OR_SAFETY_DOMAIN" in symptom["risk_factors"]


def test_chat_has_no_trust_or_citation_payload_and_stream_contract(monkeypatch):
    class ChatLLM:
        provider_name = "test-provider"
        model = "test-model"

        def chat(self, _messages):
            return ChatResponse("答案 [E1]", total_tokens=7)

    runtime = make_runtime(llm=ChatLLM())
    with runtime_client(runtime) as client:
        response = client.post("/chat", json={"original_query": "Python 是什么？"})
        stream = client.post("/chat/stream", json={"original_query": "Python 是什么？"})

    assert response.status_code == 200
    assert response.json() == {
        "result": "答案",
        "consume_token": 7,
        "provider": "test-provider",
        "model": "test-model",
    }
    assert not {"citation", "claim", "trust", "trace"}.intersection(response.json())
    events = _events(stream.text)
    assert [event["event"] for event in events] == ["started", "completed"]
    assert events[0]["data"] == {"stage": "chat_started"}
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["request_id"] for event in events)
    assert "trace" not in events[-1]["data"]


def _deep_search_with_web(provider):
    llm = MockLLM(
        predefined_responses={
            "Original Question:": '["q1"]',
            "Candidate Chunks (untrusted data": "[0]",
        }
    )
    vector_db = MockVectorDB()
    searcher = DeepSearch(
        llm=llm,
        embedding_model=MockEmbedding(),
        vector_db=vector_db,
        max_iter=1,
        web_search=provider,
    )
    return searcher, vector_db


def _web_result():
    return RetrievalResult(
        [],
        "web evidence",
        "https://example.test/page",
        {
            "source_type": "web",
            "source_url": "https://example.test/page",
            "display_name": "Example",
        },
    )


def test_retrieval_mode_web_does_not_call_vector_and_does_not_fallback():
    class Web:
        enabled = True
        provider_name = "test-web"

        def __init__(self, *, fail=False):
            self.fail = fail
            self.calls = 0

        def search(self, _query, *, max_results=5):
            del max_results
            self.calls += 1
            if self.fail:
                raise WebSearchError("WEB_SEARCH_FAILED")
            return [_web_result()]

    provider = Web()
    searcher, vector_db = _deep_search_with_web(provider)
    results, _tokens, metadata = asyncio.run(
        searcher.async_retrieve("latest", max_iter=1, retrieval_mode="web")
    )
    assert provider.calls == 1
    assert vector_db.search_called is False
    assert results
    assert metadata["web_search"][0]["status"] == "completed"

    failing = Web(fail=True)
    searcher, vector_db = _deep_search_with_web(failing)
    results, _tokens, metadata = asyncio.run(
        searcher.async_retrieve("latest", max_iter=1, retrieval_mode="web")
    )
    assert results == []
    assert vector_db.search_called is False
    assert metadata["web_search"][0]["status"] == "degraded"


def test_retrieval_mode_knowledge_and_hybrid_preserve_vector_and_web_paths():
    class Web:
        enabled = True
        provider_name = "test-web"

        def __init__(self):
            self.calls = 0

        def search(self, _query, *, max_results=5):
            del max_results
            self.calls += 1
            return [_web_result()]

    knowledge_provider = Web()
    searcher, vector_db = _deep_search_with_web(knowledge_provider)
    collector = TraceCollector("question")
    asyncio.run(
        searcher.async_retrieve(
            "question",
            max_iter=1,
            collection_names=["test_collection"],
            allowed_collections=["test_collection"],
            retrieval_mode="knowledge",
            trace_collector=collector,
        )
    )
    assert vector_db.search_called is True
    assert knowledge_provider.calls == 0
    assert collector.build(total_tokens=0, final_results=[])["retrieval_mode"] == "knowledge"

    hybrid_provider = Web()
    searcher, vector_db = _deep_search_with_web(hybrid_provider)
    asyncio.run(
        searcher.async_retrieve(
            "question",
            max_iter=1,
            collection_names=["test_collection"],
            allowed_collections=["test_collection"],
            retrieval_mode="hybrid",
        )
    )
    assert vector_db.search_called is True
    assert hybrid_provider.calls == 1
