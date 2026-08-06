import asyncio
from unittest.mock import MagicMock

from deepsearcher.agent import ChainOfRAG, DeepSearch, NaiveRAG
from tests.agent.test_base import MockEmbedding, MockLLM, MockVectorDB

SELECTED_COLLECTION = "kb_selected"
OTHER_COLLECTION = "kb_other"


def make_components():
    return (
        MockLLM(predefined_responses={"Is the chunk helpful": "YES"}),
        MockEmbedding(dimension=8),
        MockVectorDB(collections=[SELECTED_COLLECTION, OTHER_COLLECTION]),
    )


def test_naive_rag_explicit_scope_bypasses_collection_router():
    llm, embedding, vector_db = make_components()
    agent = NaiveRAG(llm=llm, embedding_model=embedding, vector_db=vector_db)
    agent.collection_router.invoke = MagicMock(
        side_effect=AssertionError("router must be bypassed")
    )

    results, routing_tokens, metadata = agent.retrieve(
        "What is DeepSearcher?",
        collection_names=[SELECTED_COLLECTION],
    )

    assert results
    assert vector_db.last_search_collection == SELECTED_COLLECTION
    assert routing_tokens == 0
    assert metadata["collections"] == [SELECTED_COLLECTION]
    agent.collection_router.invoke.assert_not_called()


def test_chain_of_rag_explicit_scope_bypasses_collection_router_and_records_trace():
    llm, embedding, vector_db = make_components()
    agent = ChainOfRAG(llm=llm, embedding_model=embedding, vector_db=vector_db)
    agent.collection_router.invoke = MagicMock(
        side_effect=AssertionError("router must be bypassed")
    )

    answer, results, tokens = agent._retrieve_and_answer(
        "What is DeepSearcher?",
        collection_names=[SELECTED_COLLECTION],
    )

    assert answer
    assert results
    assert vector_db.last_search_collection == SELECTED_COLLECTION
    assert tokens == 10
    agent.collection_router.invoke.assert_not_called()


def test_deep_search_explicit_scope_bypasses_collection_router():
    llm, embedding, vector_db = make_components()
    agent = DeepSearch(llm=llm, embedding_model=embedding, vector_db=vector_db)
    agent.collection_router.invoke = MagicMock(
        side_effect=AssertionError("router must be bypassed")
    )

    results, _ = asyncio.run(
        agent._search_chunks_from_vectordb(
            "What is DeepSearcher?",
            ["How does it retrieve documents?"],
            collection_names=[SELECTED_COLLECTION],
        )
    )

    assert results
    assert vector_db.last_search_collection == SELECTED_COLLECTION
    agent.collection_router.invoke.assert_not_called()


def test_empty_explicit_scope_does_not_fall_back_to_all_collections():
    llm, embedding, vector_db = make_components()
    agent = NaiveRAG(llm=llm, embedding_model=embedding, vector_db=vector_db)
    agent.collection_router.invoke = MagicMock(
        side_effect=AssertionError("router must be bypassed")
    )

    results, routing_tokens, metadata = agent.retrieve(
        "What is DeepSearcher?",
        collection_names=[],
    )

    assert results == []
    assert routing_tokens == 0
    assert metadata["collections"] == []
    assert vector_db.search_called is False
    agent.collection_router.invoke.assert_not_called()


def test_explicit_scope_intersects_real_and_authorized_collections():
    llm, embedding, vector_db = make_components()
    agent = NaiveRAG(llm=llm, embedding_model=embedding, vector_db=vector_db)

    results, routing_tokens, metadata = agent.retrieve(
        "What is DeepSearcher?",
        collection_names=[OTHER_COLLECTION, "kb_invented"],
        allowed_collections=[SELECTED_COLLECTION],
    )

    assert results == []
    assert routing_tokens == 0
    assert metadata["collections"] == []
    assert vector_db.search_called is False
    assert agent.collection_router.last_decision["rejected"] == [
        OTHER_COLLECTION,
        "kb_invented",
    ]
