import os
from uuid import uuid4

import pytest

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.offline_loading import _store_chunks
from deepsearcher.vector_db import Milvus
from deepsearcher.vector_db.exceptions import EmbeddingProfileMismatch

pytestmark = pytest.mark.skipif(
    os.environ.get("DEEPSEARCHER_RUN_LIVE_MILVUS") != "1",
    reason="set DEEPSEARCHER_RUN_LIVE_MILVUS=1 to run against local Milvus",
)


def make_chunk(text: str, value: float) -> Chunk:
    return Chunk(
        text=text,
        reference=f"{text}.txt",
        metadata={"document_id": f"document-{text}", "chunk_index": 0},
        embedding=[value] * 8,
    )


def make_profile(model: str) -> EmbeddingProfile:
    return EmbeddingProfile(
        provider="LiveTestEmbedding",
        model=model,
        version=f"{model}-v1",
        dimension=8,
        normalization="none",
    )


def make_manifest(
    alias: str,
    profile: EmbeddingProfile,
    chunk: Chunk,
) -> CollectionManifest:
    return CollectionManifest.create(
        logical_collection=alias,
        embedding=profile,
        metric_type="L2",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=[chunk],
        user_description="D-01 live validation",
    )


def first_text(vector_db: Milvus, collection: str, value: float) -> str:
    results = vector_db.search_data(
        collection=collection,
        vector=[value] * 8,
        top_k=1,
    )
    assert results
    return results[0].text


def test_manifest_blocks_same_dimension_model_drift_and_survives_switch_and_rollback():
    alias = f"d01_{uuid4().hex[:12]}"
    vector_db = Milvus(
        uri="http://127.0.0.1:19530",
        token="root:Milvus",
    )
    old_profile = make_profile("embedding-a")
    new_profile = make_profile("embedding-b")
    old_chunk = make_chunk("old-profile", 0.1)
    new_chunk = make_chunk("new-profile", 0.9)

    try:
        initial = _store_chunks(
            vector_db=vector_db,
            chunks=[old_chunk],
            requested_collection=alias,
            collection_description="D-01 live validation",
            dimension=8,
            force_new_collection=False,
            manifest=make_manifest(alias, old_profile, old_chunk),
        )
        assert initial["manifest"]["embedding_model"] == "embedding-a"
        assert vector_db.get_collection_manifest(alias).embedding == old_profile
        vector_db.assert_collection_compatible(alias, old_profile)
        with pytest.raises(EmbeddingProfileMismatch):
            vector_db.assert_collection_compatible(alias, new_profile)
        assert first_text(vector_db, alias, 0.1) == "old-profile"

        activation = _store_chunks(
            vector_db=vector_db,
            chunks=[new_chunk],
            requested_collection=alias,
            collection_description="D-01 live validation",
            dimension=8,
            force_new_collection=True,
            manifest=make_manifest(alias, new_profile, new_chunk),
        )
        backing_collection = activation["backing_collection"]
        previous_collection = activation["previous_collection"]

        assert vector_db.get_collection_manifest(alias).embedding == new_profile
        assert (
            vector_db.get_collection_manifest(previous_collection).embedding
            == old_profile
        )
        vector_db.assert_collection_compatible(alias, new_profile)
        with pytest.raises(EmbeddingProfileMismatch):
            vector_db.assert_collection_compatible(alias, old_profile)
        assert first_text(vector_db, alias, 0.9) == "new-profile"

        vector_db.rollback_collection_version(
            alias=alias,
            target_collection=previous_collection,
        )
        assert vector_db.get_collection_manifest(alias).embedding == old_profile
        vector_db.assert_collection_compatible(alias, old_profile)
        with pytest.raises(EmbeddingProfileMismatch):
            vector_db.assert_collection_compatible(alias, new_profile)
        assert first_text(vector_db, alias, 0.1) == "old-profile"
        assert first_text(vector_db, backing_collection, 0.9) == "new-profile"
    finally:
        try:
            vector_db.delete_collection(alias)
        except Exception:
            try:
                vector_db.client.drop_alias(alias=alias, timeout=10)
            except Exception:
                pass
            for collection in vector_db.client.list_collections():
                if collection == alias or Milvus._version_belongs_to_alias(
                    alias,
                    collection,
                ):
                    vector_db.client.drop_collection(collection)
        vector_db.client.close()
