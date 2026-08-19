from __future__ import annotations

import numpy as np
import pytest

from deepsearcher.agent import naive_rag as naive_rag_module
from deepsearcher.vector_db.base import RetrievalResult

HEADER = "DeepSearcher 第一阶段学习讲义"
DEFINITION = "DeepSearcher 是一个基于 RAG 的文档问答项目，解决的是通用大模型不了解用户私有文档、回答缺少可核验依据的问题。"
IMPLEMENTATION = "当前项目采用短文本检索、宽上下文生成思路，通过较短 Chunk 保持检索颗粒度。"
OTHER = "文档清洗后会切分成片段并转为向量写入 Milvus。"


def _result(text, page):
    return RetrievalResult(
        embedding=np.zeros(48),
        text=text,
        reference=f"/app/notes.pdf",
        metadata={"document_id":"doc1", "page": page, "chunk_id": f"c{page:02d}"},
        score=1.0,
    )


class FakeVectorDB:
    hybrid_candidate_multiplier = 4

    def __init__(self, results):
        self.results = results
        self.called_top_k = None

    def search_data(self, collection, vector, top_k, query_text=None):
        # 记录 search_data 实际收到的 top_k（应已是扩展后的 search_top_k）
        self.called_top_k = top_k
        return self.results[:top_k]

    def assert_collections_compatible(self, collections, profile):
        return None


class FakeRouter:
    last_decision = None

    def __init__(self, **kwargs):
        pass

    def resolve_explicit(self, collections, dim, allowed_collections=None):
        return list(collections)


class FakeEmbed:
    dimension = 48
    model = "dummy"

    def embed_query(self, query):
        return np.zeros(self.dimension)


@pytest.fixture
def patched_router(monkeypatch):
    monkeypatch.setattr(naive_rag_module, "CollectionRouter", FakeRouter)


def _build_results():
    # 前 8 名全是不同 chunk_id/page、但内容相同的重复页眉，definition 被挤出 8 槽;
    results = [_result(HEADER, page=i) for i in list(range(3, 11)) + [11, 12]]
    #       ^ 8 个重复页眉
    results.extend([_result(IMPLEMENTATION, 4), _result(DEFINITION, 3), _result(OTHER, 20)])
    return results


def test_candidate_expansion_happens_before_truncation(patched_router):
    results = _build_results()
    vdb = FakeVectorDB(results)
    agent = naive_rag_module.NaiveRAG(
        llm=object(),
        embedding_model=FakeEmbed(),
        vector_db=vdb,
        top_k=5,
        route_collection=True,
    )
    retrieved, _tokens, _info = agent.retrieve("DeepSearcher 是什么", collection_names=["kb_x"])

    # 1) 候选扩展必须发生在 search_data 第一次截断之前：search_top_k = top_k * 4
    assert vdb.called_top_k == 5 * 4

    texts = [r.text for r in retrieved]
    # 2) definition 进入 final top_k（这是候选扩展要修复的核心问题）
    assert any(DEFINITION in t for t in texts), texts
    # 3) 相同页眉不同 chunk_id 只保留一次（归一化文本去重）
    assert texts.count(HEADER) <= 1, texts
    # 4) final 不超 top_k，且不足时不回填重复项拼凑
    assert len(retrieved) <= 5
    # 5) first-occurrence-wins：首个页眉出现在 DEFINITION 之前
    assert texts.index(HEADER) < next(i for i, t in enumerate(texts) if DEFINITION in t)


def test_exact_text_duplicates_dropped_first_wins(patched_router):
    dup_a = _result("同一句话，中文文档里重复出现。", 9)
    dup_b = _result("同一句话，中文文档里重复出现。", 10)  # 不同 chunk 但 text 相同
    good = _result("另一个有效的正文段落。", 12)
    vdb = FakeVectorDB([dup_a, dup_b, good])
    agent = naive_rag_module.NaiveRAG(
        llm=object(), embedding_model=FakeEmbed(), vector_db=vdb, top_k=10, route_collection=True
    )
    retrieved, _t, _i = agent.retrieve("q", collection_names=["kb_x"])
    texts = [r.text for r in retrieved]
    assert texts.count("同一句话，中文文档里重复出现。") == 1
    assert any("另一个有效" in t for t in texts)
