import asyncio
from unittest.mock import Mock

import pytest

from deepsearcher.agent import ChainOfRAG, DeepSearch, NaiveRAG
from deepsearcher.collection_manifest import (
    CollectionManifest,
    EmbeddingProfile,
    bind_embedding_identity,
)
from deepsearcher.vector_db.exceptions import EmbeddingProfileMismatch
from tests.agent.test_base import MockEmbedding, MockLLM, MockVectorDB


class ManagedVectorDB(MockVectorDB):
    allow_unmanaged_collections = False

    def __init__(self, manifest):
        super().__init__()
        self.manifest = manifest

    def get_collection_manifest(self, collection):
        return self.manifest


def configured_embedding(model: str):
    return bind_embedding_identity(
        MockEmbedding(dimension=8),
        provider="TestEmbedding",
        config={"model": model},
        identity={"version": f"{model}-v1", "normalization": "none"},
    )


def manifest_for(embedding):
    return CollectionManifest.create(
        logical_collection="test_collection",
        embedding=EmbeddingProfile.from_embedding(embedding),
        metric_type="L2",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=[],
    )


@pytest.mark.parametrize("agent_name", ["naive", "chain", "deep"])
def test_embedding_mismatch_stops_before_query_embedding_and_vector_search(agent_name):
    indexed_embedding = configured_embedding("embedding-a")
    query_embedding = configured_embedding("embedding-b")
    vector_db = ManagedVectorDB(manifest_for(indexed_embedding))
    query_embedding.embed_query = Mock(wraps=query_embedding.embed_query)

    if agent_name == "naive":
        agent = NaiveRAG(
            llm=MockLLM(),
            embedding_model=query_embedding,
            vector_db=vector_db,
        )
        def invoke():
            return agent.retrieve(
                "question",
                collection_names=["test_collection"],
            )
    elif agent_name == "chain":
        agent = ChainOfRAG(
            llm=MockLLM(),
            embedding_model=query_embedding,
            vector_db=vector_db,
            max_iter=1,
        )
        def invoke():
            return agent._retrieve_and_answer(
                "question",
                collection_names=["test_collection"],
            )
    else:
        agent = DeepSearch(
            llm=MockLLM(),
            embedding_model=query_embedding,
            vector_db=vector_db,
            max_iter=1,
        )
        def invoke():
            return asyncio.run(
                agent._retrieve_chunks_from_vectordb(
                    "question",
                    collection_names=["test_collection"],
                )
            )

    with pytest.raises(EmbeddingProfileMismatch) as exc_info:
        invoke()

    assert set(exc_info.value.mismatches) == {"model", "version"}
    query_embedding.embed_query.assert_not_called()
    assert vector_db.search_called is False


def test_compatible_manifest_allows_retrieval():
    embedding = configured_embedding("embedding-a")
    vector_db = ManagedVectorDB(manifest_for(embedding))
    agent = NaiveRAG(
        llm=MockLLM(),
        embedding_model=embedding,
        vector_db=vector_db,
    )

    results, _tokens, metadata = agent.retrieve(
        "question",
        collection_names=["test_collection"],
    )

    assert results
    assert metadata["collections"] == ["test_collection"]
    assert vector_db.search_called is True
