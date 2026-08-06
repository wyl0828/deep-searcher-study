import os
from uuid import uuid4

import pytest

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import Chunk
from deepsearcher.offline_loading import _store_chunks
from deepsearcher.vector_db import Milvus
from deepsearcher.vector_db.exceptions import UnsafeCollectionReplacement

pytestmark = pytest.mark.skipif(
    os.environ.get("DEEPSEARCHER_RUN_LIVE_MILVUS") != "1",
    reason="set DEEPSEARCHER_RUN_LIVE_MILVUS=1 to run against local Milvus",
)


def make_chunk(text: str, value: float) -> Chunk:
    return Chunk(
        text=text,
        reference=f"{text}.txt",
        metadata={"version": text},
        embedding=[value] * 8,
    )


def first_text(vector_db: Milvus, collection: str, value: float) -> str:
    results = vector_db.search_data(
        collection=collection,
        vector=[value] * 8,
        top_k=1,
    )
    assert results
    return results[0].text


def test_versioned_rebuild_switches_and_rolls_back_without_losing_old_data():
    alias = f"d02_{uuid4().hex[:12]}"
    vector_db = Milvus(
        uri="http://127.0.0.1:19530",
        token="root:Milvus",
    )
    backing_collection = None
    previous_collection = None

    try:
        vector_db.init_collection(dim=8, collection=alias)
        vector_db.insert_data(alias, [make_chunk("old-version", 0.1)])

        with pytest.raises(UnsafeCollectionReplacement):
            vector_db.init_collection(
                dim=8,
                collection=alias,
                force_new_collection=True,
            )
        assert first_text(vector_db, alias, 0.1) == "old-version"

        activation = _store_chunks(
            vector_db=vector_db,
            chunks=[make_chunk("new-version", 0.9)],
            requested_collection=alias,
            collection_description="D-02 live validation",
            dimension=8,
            force_new_collection=True,
            manifest=CollectionManifest.create(
                logical_collection=alias,
                embedding=EmbeddingProfile(
                    provider="LiveTestEmbedding",
                    model="live-test-v1",
                    version="1",
                    dimension=8,
                    normalization="none",
                ),
                metric_type="L2",
                chunk_size=1500,
                chunk_overlap=100,
                chunks=[make_chunk("new-version", 0.9)],
                user_description="D-02 live validation",
            ),
        )
        backing_collection = activation["backing_collection"]
        previous_collection = activation["previous_collection"]

        assert activation["active_collection"] == alias
        assert activation["alias_switched"] is True
        assert activation["rollback_available"] is True
        assert backing_collection != alias
        assert previous_collection != alias
        assert first_text(vector_db, alias, 0.9) == "new-version"
        assert first_text(vector_db, previous_collection, 0.1) == "old-version"

        visible_collections = {info.collection_name for info in vector_db.list_collections(dim=8)}
        assert alias in visible_collections
        assert backing_collection not in visible_collections
        assert previous_collection not in visible_collections

        rollback = vector_db.rollback_collection_version(
            alias=alias,
            target_collection=previous_collection,
        )
        assert rollback["backing_collection"] == previous_collection
        assert rollback["previous_collection"] == backing_collection
        assert first_text(vector_db, alias, 0.1) == "old-version"
        assert first_text(vector_db, backing_collection, 0.9) == "new-version"
    finally:
        try:
            vector_db.delete_collection(alias)
        except Exception:
            try:
                vector_db.client.drop_alias(alias=alias, timeout=10)
            except Exception:
                pass
            for collection in vector_db.client.list_collections():
                if collection == alias or Milvus._version_belongs_to_alias(alias, collection):
                    vector_db.client.drop_collection(collection)
        vector_db.client.close()
