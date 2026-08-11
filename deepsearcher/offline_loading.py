import os
from collections.abc import Sequence
from typing import List, Mapping, Union

from tqdm import tqdm

from deepsearcher import configuration
from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.loader.splitter import split_docs_to_chunks
from deepsearcher.utils import log
from deepsearcher.vector_db.exceptions import (
    CollectionManifestInvalid,
    CollectionManifestMissing,
)
from deepsearcher.versioning import sanitize_document_governance_metadata


def _normalize_collection_name(collection_name: str | None, default_collection: str) -> str:
    normalized = (collection_name or default_collection).replace(" ", "_").replace("-", "_")
    if not normalized:
        raise ValueError("collection name must not be empty")
    return normalized


def _cleanup_failed_candidate(vector_db, collection: str) -> None:
    delete_collection = getattr(vector_db, "delete_collection", None)
    if not callable(delete_collection):
        return
    try:
        delete_collection(collection)
    except Exception:
        log.warning(f"failed_candidate_cleanup_failed collection={collection}")


def _annotate_chunks(chunks, manifest: CollectionManifest) -> None:
    governance_metadata = {
        "collection_data_version": manifest.data_version,
        "embedding_fingerprint": manifest.embedding.fingerprint,
        "chunk_algorithm": manifest.chunk_algorithm,
        "chunk_config_version": manifest.chunk_config_version,
        "document_version": manifest.document_version,
    }
    for chunk in chunks:
        metadata = dict(chunk.metadata) if isinstance(chunk.metadata, dict) else {}
        metadata.update(governance_metadata)
        chunk.metadata = metadata


def _store_chunks(
    *,
    vector_db,
    chunks,
    requested_collection: str,
    collection_description: str | None,
    dimension: int,
    force_new_collection: bool,
    manifest: CollectionManifest,
) -> dict:
    """Store prepared chunks without ever dropping the requested collection in place."""
    backing_collection = (
        vector_db.versioned_collection_name(requested_collection)
        if force_new_collection
        else requested_collection
    )
    existed_before = bool(vector_db.collection_exists(backing_collection))
    existing_manifest = (
        vector_db.get_collection_manifest(backing_collection) if existed_before else None
    )
    if existed_before and existing_manifest is None:
        raise CollectionManifestMissing(
            operation="append",
            collection=backing_collection,
        )
    if existing_manifest is not None:
        vector_db.assert_append_compatible(
            existing_manifest,
            manifest,
            collection=backing_collection,
        )
        stored_manifest = existing_manifest.merge_append(manifest)
    else:
        stored_manifest = manifest

    candidate_initialized = False
    created_collection = False
    try:
        init_result = vector_db.init_collection(
            dim=dimension,
            collection=backing_collection,
            description=collection_description,
            force_new_collection=False,
        )
        candidate_initialized = True
        created_collection = (
            bool(init_result.get("created"))
            if isinstance(init_result, dict)
            else not existed_before
        )
        if created_collection:
            vector_db.set_collection_manifest(
                backing_collection,
                stored_manifest,
            )
        elif existing_manifest is None:
            existing_manifest = vector_db.get_collection_manifest(backing_collection)
            if existing_manifest is None:
                raise CollectionManifestMissing(
                    operation="append",
                    collection=backing_collection,
                )
            vector_db.assert_append_compatible(
                existing_manifest,
                manifest,
                collection=backing_collection,
            )
            stored_manifest = existing_manifest.merge_append(manifest)
        _annotate_chunks(chunks, manifest)
        vector_db.insert_data(collection=backing_collection, chunks=chunks)
        if not created_collection:
            vector_db.set_collection_manifest(
                backing_collection,
                stored_manifest,
            )
    except Exception:
        if candidate_initialized and created_collection:
            _cleanup_failed_candidate(vector_db, backing_collection)
        raise

    persisted_manifest = vector_db.get_collection_manifest(backing_collection)
    if persisted_manifest is None or persisted_manifest.fingerprint != stored_manifest.fingerprint:
        if created_collection:
            _cleanup_failed_candidate(vector_db, backing_collection)
        raise CollectionManifestInvalid(
            operation="verify_manifest",
            collection=backing_collection,
        )

    if not force_new_collection:
        return {
            "requested_collection": requested_collection,
            "active_collection": requested_collection,
            "backing_collection": requested_collection,
            "previous_collection": None,
            "alias_switched": False,
            "rollback_available": False,
            "manual_activation_required": False,
            "chunk_count": len(chunks),
            "manifest": stored_manifest.to_dict(),
        }

    try:
        activation = vector_db.activate_collection_version(
            alias=requested_collection,
            candidate_collection=backing_collection,
        )
    except NotImplementedError:
        log.warning(
            "collection_version_manual_activation_required "
            f"requested={requested_collection} candidate={backing_collection}"
        )
        activation = {
            "active_collection": backing_collection,
            "backing_collection": backing_collection,
            "previous_collection": requested_collection,
            "alias_switched": False,
            "rollback_available": True,
            "manual_activation_required": True,
        }

    return {
        "requested_collection": requested_collection,
        "manual_activation_required": False,
        "chunk_count": len(chunks),
        "manifest": stored_manifest.to_dict(),
        **activation,
    }


def load_from_local_files(
    paths_or_directory: Union[str, List[str]],
    collection_name: str = None,
    collection_description: str = None,
    force_new_collection: bool = False,
    chunk_size: int = 1500,
    chunk_overlap: int = 100,
    batch_size: int = 256,
    *,
    vector_db_instance=None,
    embedding_model_instance=None,
    file_loader_instance=None,
    document_metadata: Mapping | Sequence[Mapping] | None = None,
):
    """
    Load knowledge from local files or directories into the vector database.

    This function processes files from the specified paths or directories,
    splits them into chunks, embeds the chunks, and stores them in the vector database.

    Args:
        paths_or_directory: A single path or a list of paths to files or directories to load.
        collection_name: Name of the collection to store the data in. If None, uses the default collection.
        collection_description: Description of the collection. If None, no description is set.
        force_new_collection: If True, builds a versioned collection and safely
            activates it. The existing collection is retained for rollback.
        chunk_size: Size of each chunk in characters.
        chunk_overlap: Number of characters to overlap between chunks.
        batch_size: Number of chunks to process at once during embedding.

    Raises:
        FileNotFoundError: If any of the specified paths do not exist.
    """
    vector_db = vector_db_instance or configuration.vector_db
    collection_name = _normalize_collection_name(
        collection_name,
        vector_db.default_collection,
    )
    embedding_model = embedding_model_instance or configuration.embedding_model
    file_loader = file_loader_instance or configuration.file_loader
    if isinstance(paths_or_directory, str):
        paths_or_directory = [paths_or_directory]
    for path in paths_or_directory:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Error: File or directory '{path}' does not exist.")
    if isinstance(document_metadata, Mapping) or document_metadata is None:
        governance_metadata = sanitize_document_governance_metadata(document_metadata)
        if governance_metadata is None:
            raise ValueError("invalid document governance metadata")
        metadata_items = (
            [governance_metadata] if governance_metadata else [{} for _ in paths_or_directory]
        )
        if governance_metadata and len(paths_or_directory) != 1:
            raise ValueError("one document metadata object requires exactly one file")
    elif isinstance(document_metadata, Sequence):
        if len(document_metadata) != len(paths_or_directory):
            raise ValueError("document metadata count must match file count")
        metadata_items = []
        for item in document_metadata:
            sanitized = sanitize_document_governance_metadata(item)
            if sanitized is None:
                raise ValueError("invalid document governance metadata")
            metadata_items.append(sanitized)
    else:
        raise ValueError("invalid document governance metadata")
    if any(metadata_items) and any(os.path.isdir(path) for path in paths_or_directory):
        raise ValueError("document governance metadata cannot be applied to a directory")

    all_docs = []
    for path, governance_metadata in tqdm(
        zip(paths_or_directory, metadata_items),
        total=len(paths_or_directory),
        desc="Loading files",
    ):
        if os.path.isdir(path):
            docs = file_loader.load_directory(path)
        else:
            docs = file_loader.load_file(path)
        if governance_metadata:
            for document in docs:
                metadata = dict(document.metadata) if isinstance(document.metadata, dict) else {}
                metadata.update(governance_metadata)
                document.metadata = metadata
        all_docs.extend(docs)
    # print("Splitting docs to chunks...")
    chunks = split_docs_to_chunks(
        all_docs,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = embedding_model.embed_chunks(chunks, batch_size=batch_size)
    if not chunks:
        raise ValueError("No document chunks were produced; collection was not changed.")
    manifest = CollectionManifest.create(
        logical_collection=collection_name,
        embedding=EmbeddingProfile.from_embedding(embedding_model),
        metric_type=vector_db.default_metric_type,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunks=chunks,
        user_description=collection_description,
    )
    return _store_chunks(
        vector_db=vector_db,
        chunks=chunks,
        requested_collection=collection_name,
        collection_description=collection_description,
        dimension=embedding_model.dimension,
        force_new_collection=force_new_collection,
        manifest=manifest,
    )


def load_from_website(
    urls: Union[str, List[str]],
    collection_name: str = None,
    collection_description: str = None,
    force_new_collection: bool = False,
    chunk_size: int = 1500,
    chunk_overlap: int = 100,
    batch_size: int = 256,
    vector_db_instance=None,
    embedding_model_instance=None,
    web_crawler_instance=None,
    **crawl_kwargs,
):
    """
    Load knowledge from websites into the vector database.

    This function crawls the specified URLs, processes the content,
    splits it into chunks, embeds the chunks, and stores them in the vector database.

    Args:
        urls: A single URL or a list of URLs to crawl.
        collection_name: Name of the collection to store the data in. If None, uses the default collection.
        collection_description: Description of the collection. If None, no description is set.
        force_new_collection: If True, builds a versioned collection and safely
            activates it. The existing collection is retained for rollback.
        chunk_size: Size of each chunk in characters.
        chunk_overlap: Number of characters to overlap between chunks.
        batch_size: Number of chunks to process at once during embedding.
        **crawl_kwargs: Additional keyword arguments to pass to the web crawler.
    """
    if isinstance(urls, str):
        urls = [urls]
    vector_db = vector_db_instance or configuration.vector_db
    embedding_model = embedding_model_instance or configuration.embedding_model
    web_crawler = web_crawler_instance or configuration.web_crawler
    collection_name = _normalize_collection_name(
        collection_name,
        vector_db.default_collection,
    )

    all_docs = web_crawler.crawl_urls(urls, **crawl_kwargs)

    chunks = split_docs_to_chunks(
        all_docs,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = embedding_model.embed_chunks(chunks, batch_size=batch_size)
    if not chunks:
        raise ValueError("No document chunks were produced; collection was not changed.")
    manifest = CollectionManifest.create(
        logical_collection=collection_name,
        embedding=EmbeddingProfile.from_embedding(embedding_model),
        metric_type=vector_db.default_metric_type,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunks=chunks,
        user_description=collection_description,
    )
    return _store_chunks(
        vector_db=vector_db,
        chunks=chunks,
        requested_collection=collection_name,
        collection_description=collection_description,
        dimension=embedding_model.dimension,
        force_new_collection=force_new_collection,
        manifest=manifest,
    )
