from dataclasses import replace
from types import SimpleNamespace

import pytest

from deepsearcher.collection_manifest import (
    CollectionManifest,
    EmbeddingProfile,
    LEGACY_CHUNK_ALGORITHM,
    bind_embedding_identity,
    chunk_config_version,
)


class FakeEmbedding:
    dimension = 8
    model = "fallback-model"


def make_chunk(text: str, *, document_id: str, chunk_index: int):
    return SimpleNamespace(
        text=text,
        reference="guide.pdf",
        metadata={
            "document_id": document_id,
            "page_number": 1,
            "chunk_index": chunk_index,
        },
    )


def make_manifest(
    *,
    model: str = "embedding-a",
    chunks=None,
    chunk_size: int = 1500,
):
    return CollectionManifest.create(
        logical_collection="kb_safe",
        embedding=EmbeddingProfile(
            provider="Provider",
            model=model,
            version=f"{model}-v1",
            dimension=8,
            normalization="l2",
        ),
        metric_type="COSINE",
        chunk_size=chunk_size,
        chunk_overlap=100,
        chunks=chunks or [make_chunk("content", document_id="doc-a", chunk_index=0)],
        user_description="Knowledge base",
    )


def test_embedding_identity_is_stable_and_does_not_retain_credentials():
    embedding = bind_embedding_identity(
        FakeEmbedding(),
        provider="OpenAIEmbedding",
        config={
            "model": "text-embedding-v4",
            "dimension": 8,
            "api_key": "must-not-be-retained",
            "base_url": "https://private.example",
        },
        identity={
            "version": "2026-07-31",
            "normalization": "l2",
        },
    )

    profile = (
        embedding.profile
        if hasattr(embedding, "profile")
        else EmbeddingProfile.from_embedding(embedding)
    )
    serialized = str(profile.to_dict())

    assert profile.provider == "OpenAIEmbedding"
    assert profile.model == "text-embedding-v4"
    assert profile.version == "2026-07-31"
    assert profile.dimension == 8
    assert "must-not-be-retained" not in serialized
    assert "private.example" not in serialized


def test_same_dimension_different_models_have_different_fingerprints():
    first = make_manifest(model="embedding-a")
    second = make_manifest(model="embedding-b")

    assert first.embedding.dimension == second.embedding.dimension
    assert first.embedding.fingerprint != second.embedding.fingerprint
    assert first.embedding_mismatches(second.embedding) == ["model", "version"]


def test_manifest_round_trip_verifies_fingerprints():
    manifest = make_manifest()

    restored = CollectionManifest.from_json(manifest.to_json())

    assert restored == manifest
    assert restored.schema_version == 2
    assert restored.chunk_algorithm == "section-aware-recursive-character-window-v2"
    assert restored.fingerprint == manifest.fingerprint

    tampered = manifest.to_dict()
    tampered["embedding_model"] = "tampered-model"
    with pytest.raises(ValueError, match="fingerprint"):
        CollectionManifest.from_dict(tampered)

    missing_manifest_fingerprint = manifest.to_dict()
    missing_manifest_fingerprint.pop("manifest_fingerprint")
    with pytest.raises(ValueError, match="required fields"):
        CollectionManifest.from_dict(missing_manifest_fingerprint)

    missing_embedding_fingerprint = manifest.to_dict()
    missing_embedding_fingerprint.pop("embedding_fingerprint")
    with pytest.raises(ValueError, match="required fields"):
        CollectionManifest.from_dict(missing_embedding_fingerprint)

    missing_collection_identity = manifest.to_dict()
    missing_collection_identity.pop("logical_collection")
    with pytest.raises(ValueError, match="required fields"):
        CollectionManifest.from_dict(missing_collection_identity)


def test_legacy_v1_manifest_remains_readable_after_chunk_algorithm_upgrade():
    current = make_manifest()
    legacy = replace(
        current,
        schema_version=1,
        chunk_algorithm=LEGACY_CHUNK_ALGORITHM,
        chunk_config_version=chunk_config_version(
            chunk_size=current.chunk_size,
            chunk_overlap=current.chunk_overlap,
            algorithm=LEGACY_CHUNK_ALGORITHM,
        ),
    )

    serialized = legacy.to_dict()
    restored = CollectionManifest.from_dict(serialized)

    assert "chunk_algorithm" not in serialized
    assert restored.schema_version == 1
    assert restored.chunk_algorithm == LEGACY_CHUNK_ALGORITHM
    assert restored.fingerprint == legacy.fingerprint


def test_document_version_is_content_based_and_order_independent():
    chunks = [
        make_chunk("first", document_id="doc-a", chunk_index=0),
        make_chunk("second", document_id="doc-b", chunk_index=0),
    ]

    first = make_manifest(chunks=chunks)
    second = make_manifest(chunks=list(reversed(chunks)))

    assert first.document_version == second.document_version


def test_append_requires_same_embedding_metric_and_chunk_contract():
    existing = make_manifest()
    compatible = make_manifest(chunks=[make_chunk("new", document_id="doc-b", chunk_index=0)])

    merged = existing.merge_append(compatible)

    assert merged.embedding == existing.embedding
    assert merged.chunk_config_version == existing.chunk_config_version
    assert merged.document_version not in {
        existing.document_version,
        compatible.document_version,
    }
    assert merged.data_version != existing.data_version

    with pytest.raises(ValueError, match="embedding"):
        existing.merge_append(make_manifest(model="embedding-b"))
    with pytest.raises(ValueError, match="chunk"):
        existing.merge_append(make_manifest(chunk_size=800))
