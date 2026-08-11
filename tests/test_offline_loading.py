from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.documents import Document

from deepsearcher import offline_loading
from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.vector_db.exceptions import (
    CollectionActivationFailed,
    CollectionIngestionProfileMismatch,
    CollectionManifestMissing,
)


def make_chunk(text: str = "prepared"):
    return SimpleNamespace(
        text=text,
        reference="guide.pdf",
        metadata={"document_id": "document"},
        embedding=[0.1, 0.2, 0.3],
    )


def make_dependencies():
    vector_db = Mock()
    vector_db.default_collection = "deepsearcher"
    vector_db.default_metric_type = "L2"
    configure_manifest_storage(vector_db)
    embedding_model = Mock()
    embedding_model.dimension = 3
    file_loader = Mock()
    return vector_db, embedding_model, file_loader


def make_manifest(chunks=None, *, model="embedding-a"):
    return CollectionManifest.create(
        logical_collection="kb_safe",
        embedding=EmbeddingProfile(
            provider="TestEmbedding",
            model=model,
            version=f"{model}-v1",
            dimension=3,
            normalization="none",
        ),
        metric_type="L2",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=chunks or [make_chunk()],
    )


def configure_manifest_storage(vector_db, *, exists=False, manifest=None):
    state = {"manifest": manifest}
    vector_db.collection_exists.return_value = exists
    vector_db.init_collection.return_value = {"created": not exists}
    vector_db.get_collection_manifest.side_effect = lambda _collection: state["manifest"]
    vector_db.set_collection_manifest.side_effect = (
        lambda _collection, stored_manifest: state.update(manifest=stored_manifest)
    )
    vector_db.assert_append_compatible = Mock()
    return state


def test_missing_path_is_rejected_before_any_collection_mutation(tmp_path):
    vector_db, embedding_model, file_loader = make_dependencies()
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"%PDF")
    missing = tmp_path / "missing.pdf"

    with pytest.raises(FileNotFoundError):
        offline_loading.load_from_local_files(
            [str(existing), str(missing)],
            collection_name="kb_safe",
            force_new_collection=True,
            vector_db_instance=vector_db,
            embedding_model_instance=embedding_model,
            file_loader_instance=file_loader,
        )

    file_loader.load_file.assert_not_called()
    embedding_model.embed_chunks.assert_not_called()
    vector_db.init_collection.assert_not_called()
    vector_db.insert_data.assert_not_called()


def test_document_temporal_metadata_reaches_every_chunk_and_manifest(tmp_path, monkeypatch):
    vector_db, embedding_model, file_loader = make_dependencies()
    source = tmp_path / "policy.pdf"
    source.write_bytes(b"%PDF")
    loaded_document = Document(
        page_content="The policy starts tomorrow.",
        metadata={"document_id": "policy-document", "page_number": 1},
    )
    file_loader.load_file.return_value = [loaded_document]
    captured = {}

    def split(documents, **_kwargs):
        captured["metadata"] = dict(documents[0].metadata)
        return [
            SimpleNamespace(
                text=documents[0].page_content,
                reference="policy.pdf",
                metadata=dict(documents[0].metadata),
                embedding=[0.1, 0.2, 0.3],
            )
        ]

    monkeypatch.setattr(offline_loading, "split_docs_to_chunks", split)
    embedding_model.embed_chunks.side_effect = lambda chunks, **_kwargs: chunks

    result = offline_loading.load_from_local_files(
        str(source),
        collection_name="kb_safe",
        vector_db_instance=vector_db,
        embedding_model_instance=embedding_model,
        file_loader_instance=file_loader,
        document_metadata={
            "published_at": "2026-08-11",
            "effective_at": "2026-08-12",
            "temporal_metadata_source": "user_declared",
            "version_family": "Travel Expense Policy",
            "version_family_source": "user_declared",
        },
    )

    assert captured["metadata"]["published_at"] == "2026-08-11"
    inserted_chunk = vector_db.insert_data.call_args.kwargs["chunks"][0]
    assert inserted_chunk.metadata["effective_at"] == "2026-08-12"
    assert inserted_chunk.metadata["temporal_metadata_source"] == "user_declared"
    assert inserted_chunk.metadata["version_family"] == "travel-expense-policy"
    assert inserted_chunk.metadata["version_family_source"] == "user_declared"
    assert result["manifest"]["document_version"]


def test_document_temporal_metadata_count_must_match_files(tmp_path):
    vector_db, embedding_model, file_loader = make_dependencies()
    paths = []
    for name in ("a.pdf", "b.pdf"):
        path = tmp_path / name
        path.write_bytes(b"%PDF")
        paths.append(str(path))

    with pytest.raises(ValueError, match="count must match"):
        offline_loading.load_from_local_files(
            paths,
            vector_db_instance=vector_db,
            embedding_model_instance=embedding_model,
            file_loader_instance=file_loader,
            document_metadata=[
                {
                    "published_at": "2026-08-11",
                    "temporal_metadata_source": "user_declared",
                }
            ],
        )

    file_loader.load_file.assert_not_called()


def test_embedding_failure_leaves_existing_collection_untouched(tmp_path, monkeypatch):
    vector_db, embedding_model, file_loader = make_dependencies()
    source = tmp_path / "guide.pdf"
    source.write_bytes(b"%PDF")
    file_loader.load_file.return_value = [object()]
    chunks = [make_chunk()]
    monkeypatch.setattr(
        offline_loading,
        "split_docs_to_chunks",
        lambda *_args, **_kwargs: chunks,
    )
    embedding_model.embed_chunks.side_effect = RuntimeError("embedding failed")

    with pytest.raises(RuntimeError, match="embedding failed"):
        offline_loading.load_from_local_files(
            str(source),
            collection_name="kb_safe",
            force_new_collection=True,
            vector_db_instance=vector_db,
            embedding_model_instance=embedding_model,
            file_loader_instance=file_loader,
        )

    vector_db.init_collection.assert_not_called()
    vector_db.insert_data.assert_not_called()
    vector_db.activate_collection_version.assert_not_called()


def test_force_rebuild_prepares_candidate_then_activates_alias(tmp_path, monkeypatch):
    vector_db, embedding_model, file_loader = make_dependencies()
    source = tmp_path / "guide.pdf"
    source.write_bytes(b"%PDF")
    chunks = [make_chunk("new content")]
    candidate = "kb_safe__v_20260730120000_aaaaaaaa"
    previous = "kb_safe__previous_20260730110000_bbbbbbbb"
    vector_db.versioned_collection_name.return_value = candidate
    vector_db.activate_collection_version.return_value = {
        "active_collection": "kb_safe",
        "backing_collection": candidate,
        "previous_collection": previous,
        "alias_switched": True,
        "rollback_available": True,
    }
    file_loader.load_file.return_value = [object()]
    embedding_model.embed_chunks.return_value = chunks
    monkeypatch.setattr(
        offline_loading,
        "split_docs_to_chunks",
        lambda *_args, **_kwargs: chunks,
    )

    result = offline_loading.load_from_local_files(
        str(source),
        collection_name="kb-safe",
        force_new_collection=True,
        vector_db_instance=vector_db,
        embedding_model_instance=embedding_model,
        file_loader_instance=file_loader,
    )

    vector_db.init_collection.assert_called_once_with(
        dim=3,
        collection=candidate,
        description=None,
        force_new_collection=False,
    )
    vector_db.insert_data.assert_called_once_with(
        collection=candidate,
        chunks=chunks,
    )
    vector_db.activate_collection_version.assert_called_once_with(
        alias="kb_safe",
        candidate_collection=candidate,
    )
    vector_db.delete_collection.assert_not_called()
    manifest = result.pop("manifest")
    assert manifest["embedding_fingerprint"]
    assert manifest["chunk_config_version"]
    assert manifest["document_version"]
    assert result == {
        "requested_collection": "kb_safe",
        "manual_activation_required": False,
        "chunk_count": 1,
        "active_collection": "kb_safe",
        "backing_collection": candidate,
        "previous_collection": previous,
        "alias_switched": True,
        "rollback_available": True,
    }


def test_failed_candidate_insert_cleans_candidate_without_activating():
    vector_db = Mock()
    candidate = "kb_safe__v_20260730120000_aaaaaaaa"
    vector_db.versioned_collection_name.return_value = candidate
    configure_manifest_storage(vector_db)
    vector_db.insert_data.side_effect = RuntimeError("insert failed")

    with pytest.raises(RuntimeError, match="insert failed"):
        offline_loading._store_chunks(
            vector_db=vector_db,
            chunks=[make_chunk()],
            requested_collection="kb_safe",
            collection_description=None,
            dimension=3,
            force_new_collection=True,
            manifest=make_manifest(),
        )

    vector_db.delete_collection.assert_called_once_with(candidate)
    vector_db.activate_collection_version.assert_not_called()


def test_failed_activation_retains_both_old_and_completed_candidate():
    vector_db = Mock()
    candidate = "kb_safe__v_20260730120000_aaaaaaaa"
    vector_db.versioned_collection_name.return_value = candidate
    configure_manifest_storage(vector_db)
    vector_db.activate_collection_version.side_effect = CollectionActivationFailed(
        operation="activate",
        collection="kb_safe",
    )

    with pytest.raises(CollectionActivationFailed):
        offline_loading._store_chunks(
            vector_db=vector_db,
            chunks=[make_chunk()],
            requested_collection="kb_safe",
            collection_description=None,
            dimension=3,
            force_new_collection=True,
            manifest=make_manifest(),
        )

    vector_db.insert_data.assert_called_once()
    vector_db.delete_collection.assert_not_called()


def test_driver_without_alias_support_requires_manual_activation():
    vector_db = Mock()
    candidate = "kb_safe__v_20260730120000_aaaaaaaa"
    vector_db.versioned_collection_name.return_value = candidate
    configure_manifest_storage(vector_db)
    vector_db.activate_collection_version.side_effect = NotImplementedError

    result = offline_loading._store_chunks(
        vector_db=vector_db,
        chunks=[make_chunk()],
        requested_collection="kb_safe",
        collection_description=None,
        dimension=3,
        force_new_collection=True,
        manifest=make_manifest(),
    )

    assert result["active_collection"] == candidate
    assert result["previous_collection"] == "kb_safe"
    assert result["alias_switched"] is False
    assert result["manual_activation_required"] is True
    vector_db.delete_collection.assert_not_called()


def test_append_rejects_legacy_collection_before_insert():
    vector_db = Mock()
    configure_manifest_storage(vector_db, exists=True, manifest=None)

    with pytest.raises(CollectionManifestMissing):
        offline_loading._store_chunks(
            vector_db=vector_db,
            chunks=[make_chunk()],
            requested_collection="kb_safe",
            collection_description=None,
            dimension=3,
            force_new_collection=False,
            manifest=make_manifest(),
        )

    vector_db.init_collection.assert_not_called()
    vector_db.insert_data.assert_not_called()


def test_append_rejects_same_dimension_but_different_embedding_model():
    vector_db = Mock()
    existing = make_manifest(model="embedding-a")
    incoming = make_manifest(model="embedding-b")
    configure_manifest_storage(vector_db, exists=True, manifest=existing)
    vector_db.assert_append_compatible.side_effect = CollectionIngestionProfileMismatch(
        operation="append",
        collection="kb_safe",
    )

    with pytest.raises(CollectionIngestionProfileMismatch):
        offline_loading._store_chunks(
            vector_db=vector_db,
            chunks=[make_chunk()],
            requested_collection="kb_safe",
            collection_description=None,
            dimension=3,
            force_new_collection=False,
            manifest=incoming,
        )

    vector_db.init_collection.assert_not_called()
    vector_db.insert_data.assert_not_called()
