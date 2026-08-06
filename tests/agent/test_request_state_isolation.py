import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import MagicMock

from deepsearcher.agent import ChainOfRAG, DeepSearch
from deepsearcher.agent.collection_router import CollectionRouter
from deepsearcher.agent.rag_router import RAGRouter
from deepsearcher.llm.base import ChatResponse
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import CollectionInfo, RetrievalResult
from tests.agent.test_base import MockEmbedding, MockLLM, MockVectorDB


def test_collection_router_decision_snapshot_is_isolated_between_threads():
    vector_db = MagicMock()
    vector_db.list_collections.return_value = [
        CollectionInfo(collection_name="alpha", description="Alpha"),
        CollectionInfo(collection_name="beta", description="Beta"),
    ]
    vector_db.default_collection = "alpha"
    router = CollectionRouter(llm=MockLLM(), vector_db=vector_db, dim=8)
    rendezvous = Barrier(2)

    def resolve(collection_name: str):
        router.resolve_explicit([collection_name], dim=8)
        rendezvous.wait(timeout=5)
        return router.last_decision

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha = executor.submit(resolve, "alpha")
        beta = executor.submit(resolve, "beta")

    assert alpha.result()["selected"] == ["alpha"]
    assert beta.result()["selected"] == ["beta"]


def test_rag_router_trace_is_isolated_for_concurrent_requests():
    class AlphaAgent:
        def query(self, query, **kwargs):
            return f"alpha:{query}", [], 1

    class BetaAgent:
        def query(self, query, **kwargs):
            return f"beta:{query}", [], 1

    llm = MockLLM()

    def route_chat(messages, **kwargs):
        prompt = messages[0]["content"]
        selected = "invalid" if "alpha request" in prompt else "2"
        return ChatResponse(content=selected, total_tokens=2)

    llm.chat = route_chat
    router = RAGRouter(
        llm=llm,
        rag_agents=[AlphaAgent(), BetaAgent()],
        agent_descriptions=["alpha", "beta"],
    )
    rendezvous = Barrier(2)
    original_select_agent = router._select_agent

    def delayed_select_agent(query: str, *, use_web_search: bool = False):
        selected = original_select_agent(query, use_web_search=use_web_search)
        rendezvous.wait(timeout=5)
        return selected

    router._select_agent = delayed_select_agent

    def query(text: str):
        collector = TraceCollector(text)
        router.query(text, trace_collector=collector)
        return collector.build(total_tokens=3, final_results=[])

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha = executor.submit(query, "alpha request")
        beta = executor.submit(query, "beta request")

    alpha_trace = alpha.result()
    beta_trace = beta.result()
    assert alpha_trace["agent"] == "AlphaAgent"
    assert alpha_trace["routing"]["fallback_used"] is True
    assert alpha_trace["routing"]["reason"] == "invalid_index_format"
    assert beta_trace["agent"] == "BetaAgent"
    assert beta_trace["routing"]["fallback_used"] is False


def test_chain_of_rag_support_decision_is_isolated_between_threads():
    llm = MockLLM()

    def support_chat(messages, **kwargs):
        prompt = messages[0]["content"]
        selected = "[0]" if "alpha question" in prompt else "[]"
        return ChatResponse(content=selected, total_tokens=2)

    llm.chat = support_chat
    agent = ChainOfRAG(
        llm=llm,
        embedding_model=MockEmbedding(),
        vector_db=MockVectorDB(),
    )
    result = RetrievalResult([], "evidence", "source", {})
    rendezvous = Barrier(2)

    def select_support(query: str):
        agent._get_supported_docs([result], query, "answer", main_query=query)
        rendezvous.wait(timeout=5)
        return agent.last_supported_docs_decision

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha = executor.submit(select_support, "alpha question")
        beta = executor.submit(select_support, "beta question")

    assert alpha.result()["selected"] == [0]
    assert alpha.result()["trusted"] is True
    assert beta.result()["selected"] == []
    assert beta.result()["trusted"] is False


def test_deep_search_selection_events_are_isolated_between_async_requests():
    llm = MockLLM()
    rendezvous = Barrier(2)

    def subquery_chat(messages, **kwargs):
        prompt = messages[0]["content"]
        rendezvous.wait(timeout=5)
        selected = "alpha subquery" if "alpha request" in prompt else "beta subquery"
        return ChatResponse(content=f'["{selected}"]', total_tokens=2)

    llm.chat = subquery_chat
    agent = DeepSearch(
        llm=llm,
        embedding_model=MockEmbedding(),
        vector_db=MockVectorDB(),
    )

    async def run_concurrently():
        return await asyncio.gather(
            agent.async_retrieve("alpha request", max_iter=0),
            agent.async_retrieve("beta request", max_iter=0),
        )

    alpha_result, beta_result = asyncio.run(run_concurrently())
    alpha_info = alpha_result[2]
    beta_info = beta_result[2]

    assert alpha_info["all_sub_queries"] == ["alpha subquery"]
    assert beta_info["all_sub_queries"] == ["beta subquery"]
    assert [event["selected"] for event in alpha_info["selection_decisions"]] == [
        ["alpha subquery"]
    ]
    assert [event["selected"] for event in beta_info["selection_decisions"]] == [
        ["beta subquery"]
    ]
