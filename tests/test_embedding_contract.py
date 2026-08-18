"""Embedding contract: vector_text is embedding-only and never leaks."""

from __future__ import annotations

from deepsearcher.embedding.base import BaseEmbedding
from deepsearcher.loader.splitter import Chunk


class _RecordingEmbedding(BaseEmbedding):
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[0.1] for _ in texts]

    @property
    def dimension(self) -> int:
        return 1


def test_embed_chunks_uses_vector_text_first_and_falls_back_to_text():
    embedding = _RecordingEmbedding()
    chunks = [
        Chunk(text="markdown table", reference="a", vector_text="指标: 收入; 数值: 100"),
        Chunk(text="plain text", reference="b"),
    ]
    embedding.embed_chunks(chunks, batch_size=10)
    assert embedding.calls == [["指标: 收入; 数值: 100", "plain text"]]
    assert all(chunk.embedding is not None for chunk in chunks)


def test_vector_text_never_leaks_into_payload_identity_or_manifest():
    chunk = Chunk(text="display text", reference="ref", vector_text="embed-only")
    # Vector store payloads keep chunk.text and never contain vector_text.
    payload = {
        "embedding": [0.1],
        "text": chunk.text,
        "reference": chunk.reference,
        "metadata": dict(chunk.metadata),
    }
    assert payload["text"] == "display text"
    assert "vector_text" not in payload
    assert "vector_text" not in chunk.metadata
    # Manifest identity and citation text keep using chunk.text.
    assert chunk.text == "display text"
