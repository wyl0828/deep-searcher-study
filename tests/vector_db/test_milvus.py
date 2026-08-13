import importlib.util
import unittest
import warnings
from unittest.mock import Mock

import numpy as np
from pymilvus import DataType

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.vector_db import Milvus
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.vector_db.exceptions import (
    CollectionActivationFailed,
    CollectionManifestInvalid,
    CollectionNotFound,
    CollectionRollbackFailed,
    EmbeddingProfileMismatch,
    UnsafeCollectionReplacement,
    VectorDBUnavailable,
    VectorDimensionMismatch,
    VectorInitializationFailed,
    VectorInsertFailed,
    VectorListFailed,
    VectorSearchFailed,
)
from deepsearcher.vector_db.milvus import _weighted_rrf_hits

# Filter out the pkg_resources deprecation warning from milvus_lite
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pkg_resources")


def test_weighted_rrf_fuses_duplicates_and_keeps_dense_head_anchor():
    def hit(hit_id, text):
        return {
            "id": hit_id,
            "entity": {
                "embedding": [],
                "text": text,
                "reference": "guide.pdf",
                "metadata": {},
            },
            "distance": 0.0,
        }

    dense = [hit("dense-only", "dense"), hit("shared", "shared")]
    sparse = [hit("shared", "shared"), hit("sparse-only", "sparse")]

    fused = _weighted_rrf_hits(
        sparse,
        dense,
        sparse_weight=1.0,
        dense_weight=1.5,
        rrf_k=5,
        dense_anchor_count=1,
        limit=3,
    )

    assert [item["id"] for item in fused] == ["dense-only", "shared", "sparse-only"]
    assert fused[1]["distance"] > fused[0]["distance"]


def test_weighted_rrf_prefers_close_scoring_unseen_document():
    def hit(hit_id, document_id, reference):
        return {
            "id": hit_id,
            "entity": {
                "embedding": [],
                "text": hit_id,
                "reference": reference,
                "metadata": {"document_id": document_id},
            },
        }

    dense = [
        hit("a1", "doc-a", "a.pdf"),
        hit("a2", "doc-a", "a.pdf"),
        hit("b1", "doc-b", "b.pdf"),
    ]
    diagnostics = {}

    fused = _weighted_rrf_hits(
        [],
        dense,
        sparse_weight=1,
        dense_weight=1,
        rrf_k=60,
        dense_anchor_count=1,
        diversity_tolerance=0.1,
        limit=2,
        diagnostics=diagnostics,
    )

    assert [item["id"] for item in fused] == ["a1", "b1"]
    assert diagnostics["selected_documents"] == ["document:doc-a", "document:doc-b"]


def test_weighted_rrf_document_fallback_normalizes_reference_path():
    def hit(hit_id, reference):
        return {
            "id": hit_id,
            "entity": {
                "embedding": [],
                "text": hit_id,
                "reference": reference,
                "metadata": {},
            },
        }

    fused = _weighted_rrf_hits(
        [],
        [hit("a1", "Docs\\Guide.PDF"), hit("a2", "docs/guide.pdf"), hit("b", "other.pdf")],
        sparse_weight=1,
        dense_weight=1,
        rrf_k=60,
        dense_anchor_count=1,
        diversity_tolerance=0.1,
        limit=2,
    )

    assert [item["id"] for item in fused] == ["a1", "b"]


@unittest.skipIf(
    importlib.util.find_spec("milvus_lite") is None,
    "milvus-lite is not available on this platform",
)
class TestMilvus(unittest.TestCase):
    """Simple tests for the Milvus vector database implementation."""

    def test_init(self):
        """Test basic initialization."""
        milvus = Milvus(default_collection="test_collection", uri="./milvus.db", hybrid=False)

        # Verify initialization - just check basic properties
        self.assertEqual(milvus.default_collection, "test_collection")
        self.assertFalse(milvus.hybrid)
        self.assertEqual(milvus.rrf_k, 60)
        self.assertEqual(milvus.hybrid_ranker, "rrf")
        self.assertEqual(milvus.hybrid_candidate_multiplier, 1)
        self.assertEqual(milvus.hybrid_dense_anchor_count, 0)
        self.assertEqual(milvus.metric_type, "L2")
        self.assertIsNotNone(milvus.client)

    def test_init_collection(self):
        """Test collection initialization."""
        milvus = Milvus(uri="./milvus.db")

        # Test collection initialization
        d = 8
        collection = "hello_deepsearcher"

        try:
            milvus.init_collection(dim=d, collection=collection)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "init_collection should work")

    def test_insert_data_with_retrieval_results(self):
        """Test inserting data using RetrievalResult objects."""
        milvus = Milvus(uri="./milvus.db")

        # Create test data
        d = 8
        collection = "hello_deepsearcher"
        rng = np.random.default_rng(seed=19530)

        # Create RetrievalResult objects
        test_data = [
            RetrievalResult(
                embedding=rng.random((1, d))[0],
                text="hello world",
                reference="local file: hi.txt",
                metadata={"a": 1},
            ),
            RetrievalResult(
                embedding=rng.random((1, d))[0],
                text="hello milvus",
                reference="local file: hi.txt",
                metadata={"a": 1},
            ),
        ]

        try:
            milvus.insert_data(collection=collection, chunks=test_data)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "insert_data should work with RetrievalResult objects")

    def test_search_data(self):
        """Test search functionality."""
        milvus = Milvus(uri="./milvus.db")

        # Test search
        d = 8
        collection = "hello_deepsearcher"
        rng = np.random.default_rng(seed=19530)
        query_vector = rng.random((1, d))[0]

        try:
            top_2 = milvus.search_data(collection=collection, vector=query_vector, top_k=2)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "search_data should work")
        if test_passed:
            self.assertIsInstance(top_2, list)
            # Note: In an empty collection, we might not get 2 results
            self.assertIsInstance(top_2[0], RetrievalResult) if top_2 else None

    def test_clear_collection(self):
        """Test clearing collection."""
        milvus = Milvus(uri="./milvus.db")

        collection = "hello_deepsearcher"

        try:
            milvus.clear_db(collection=collection)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "clear_db should work")

    def test_list_collections(self):
        """Test listing collections."""
        milvus = Milvus(uri="./milvus.db")

        try:
            collections = milvus.list_collections()
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "list_collections should work")
        if test_passed:
            self.assertIsInstance(collections, list)
            self.assertGreaterEqual(len(collections), 0)


class TestMilvusDocumentDeletion(unittest.TestCase):
    def test_delete_by_document_id_uses_json_metadata_filter(self):
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.client = Mock()
        milvus.client.has_collection.return_value = True
        milvus.client.list_collections.return_value = []
        milvus.client.delete.return_value = {"delete_count": 2}
        manifest = TestMilvusManifestGovernance.make_manifest()
        milvus.get_collection_manifest = Mock(return_value=manifest)
        milvus.set_collection_manifest = Mock()
        document_id = "a" * 64

        result = milvus.delete_by_document_id("kb_selected", document_id)

        self.assertEqual(result["delete_count"], 2)
        updated_manifest = CollectionManifest.from_dict(result["manifest"])
        self.assertNotEqual(updated_manifest.data_version, manifest.data_version)
        milvus.set_collection_manifest.assert_called_once_with(
            "kb_selected",
            updated_manifest,
        )
        milvus.client.delete.assert_called_once_with(
            collection_name="kb_selected",
            filter=f'metadata["document_id"] == "{document_id}"',
            timeout=10,
        )

    def test_delete_collection_drops_existing_collection(self):
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.client = Mock()
        milvus.client.has_collection.return_value = True
        milvus.client.list_collections.return_value = []

        result = milvus.delete_collection("kb_selected")

        self.assertEqual(result, {"deleted": True})
        milvus.client.has_collection.assert_called_once_with("kb_selected", timeout=5)
        milvus.client.drop_collection.assert_called_once_with("kb_selected")

    def test_delete_collection_is_idempotent_when_collection_is_absent(self):
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.client = Mock()
        milvus.client.has_collection.return_value = False
        milvus.client.list_collections.return_value = []

        result = milvus.delete_collection("kb_missing")

        self.assertEqual(result, {"deleted": False})
        milvus.client.drop_collection.assert_not_called()


class TestMilvusSafeCollectionVersioning(unittest.TestCase):
    alias = "kb_selected"
    candidate = "kb_selected__v_20260730120000_aaaaaaaa"
    previous = "kb_selected__previous_20260730110000_bbbbbbbb"

    @staticmethod
    def make_milvus():
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.hybrid = False
        milvus.rrf_k = 60
        milvus.hybrid_ranker = "rrf"
        milvus.hybrid_sparse_weight = 1.0
        milvus.hybrid_dense_weight = 1.0
        milvus.hybrid_candidate_multiplier = 1
        milvus.hybrid_dense_anchor_count = 0
        milvus.client = Mock()
        return milvus

    def test_force_replacement_never_drops_an_existing_collection(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True

        with self.assertRaises(UnsafeCollectionReplacement):
            milvus.init_collection(
                dim=8,
                collection=self.alias,
                force_new_collection=True,
            )

        milvus.client.drop_collection.assert_not_called()
        milvus.client.create_collection.assert_not_called()

    def test_activation_atomically_switches_an_existing_alias(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.list_collections.return_value = [self.previous, self.candidate]
        milvus.client.list_aliases.side_effect = lambda collection_name, **_kwargs: (
            [self.alias] if collection_name == self.previous else []
        )

        result = milvus.activate_collection_version(self.alias, self.candidate)

        milvus.client.alter_alias.assert_called_once_with(
            collection_name=self.candidate,
            alias=self.alias,
            timeout=10,
        )
        milvus.client.drop_collection.assert_not_called()
        self.assertEqual(result["active_collection"], self.alias)
        self.assertEqual(result["previous_collection"], self.previous)
        self.assertTrue(result["rollback_available"])

    def test_first_activation_migrates_physical_collection_without_deleting_it(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.list_collections.return_value = [self.alias, self.candidate]
        milvus.client.list_aliases.return_value = []
        milvus.versioned_collection_name = Mock(return_value=self.previous)

        result = milvus.activate_collection_version(self.alias, self.candidate)

        milvus.client.rename_collection.assert_called_once_with(
            old_name=self.alias,
            new_name=self.previous,
            timeout=30,
        )
        milvus.client.create_alias.assert_called_once_with(
            collection_name=self.previous,
            alias=self.alias,
            timeout=10,
        )
        milvus.client.alter_alias.assert_called_once_with(
            collection_name=self.candidate,
            alias=self.alias,
            timeout=10,
        )
        milvus.client.drop_collection.assert_not_called()
        self.assertTrue(result["migrated_physical_collection"])
        self.assertEqual(result["previous_collection"], self.previous)

    def test_failed_alias_creation_restores_original_physical_name(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.list_collections.return_value = [self.alias, self.candidate]
        milvus.client.list_aliases.return_value = []
        milvus.versioned_collection_name = Mock(return_value=self.previous)
        milvus.client.create_alias.side_effect = RuntimeError("alias failure")

        with self.assertRaises(CollectionActivationFailed):
            milvus.activate_collection_version(self.alias, self.candidate)

        self.assertEqual(
            milvus.client.rename_collection.call_args_list,
            [
                unittest.mock.call(
                    old_name=self.alias,
                    new_name=self.previous,
                    timeout=30,
                ),
                unittest.mock.call(
                    old_name=self.previous,
                    new_name=self.alias,
                    timeout=30,
                ),
            ],
        )
        milvus.client.alter_alias.assert_not_called()
        milvus.client.drop_collection.assert_not_called()

    def test_rollback_only_accepts_a_version_owned_by_the_alias(self):
        milvus = self.make_milvus()
        milvus.client.list_collections.return_value = [self.candidate, self.previous]
        milvus.client.list_aliases.side_effect = lambda collection_name, **_kwargs: (
            [self.alias] if collection_name == self.candidate else []
        )
        milvus.client.has_collection.return_value = True

        result = milvus.rollback_collection_version(self.alias, self.previous)

        milvus.client.alter_alias.assert_called_once_with(
            collection_name=self.previous,
            alias=self.alias,
            timeout=10,
        )
        self.assertEqual(result["backing_collection"], self.previous)
        self.assertEqual(result["previous_collection"], self.candidate)

        milvus.client.alter_alias.reset_mock()
        with self.assertRaises(CollectionRollbackFailed):
            milvus.rollback_collection_version(
                self.alias,
                "kb_other__previous_20260730110000_cccccccc",
            )
        milvus.client.alter_alias.assert_not_called()

    def test_version_scope_rejects_lookalike_prefixes(self):
        self.assertTrue(
            Milvus._version_belongs_to_alias(
                "a",
                "a__v_20260730120000_aaaaaaaa",
            )
        )
        self.assertFalse(
            Milvus._version_belongs_to_alias(
                "a",
                "another__v_20260730120000_aaaaaaaa",
            )
        )

    def test_alias_list_normalizes_server_dictionary_response(self):
        milvus = self.make_milvus()
        milvus.client.list_aliases.return_value = {
            "aliases": [self.alias],
            "collection_name": self.candidate,
            "db_name": "default",
        }

        result = milvus._list_aliases_for_collection(self.candidate)

        self.assertEqual(result, [self.alias])

    def test_listing_exposes_alias_and_hides_internal_versions(self):
        milvus = self.make_milvus()
        public_collection = "public_collection"
        milvus.client.list_collections.return_value = [
            self.candidate,
            self.previous,
            public_collection,
        ]
        milvus.client.describe_collection.return_value = {"description": "test"}
        milvus.client.list_aliases.side_effect = lambda collection_name, **_kwargs: (
            [self.alias] if collection_name == self.candidate else []
        )

        result = milvus.list_collections()

        self.assertEqual(
            [collection.collection_name for collection in result],
            [self.alias, public_collection],
        )

    def test_deleting_alias_cascades_only_its_internal_versions(self):
        milvus = self.make_milvus()
        unrelated = "kb_other__v_20260730120000_cccccccc"
        collections = [self.candidate, self.previous, unrelated]
        milvus.client.list_collections.return_value = collections
        milvus.client.list_aliases.side_effect = lambda collection_name, **_kwargs: (
            [self.alias] if collection_name == self.candidate else []
        )

        result = milvus.delete_collection(self.alias)

        milvus.client.drop_alias.assert_called_once_with(
            alias=self.alias,
            timeout=10,
        )
        self.assertEqual(
            milvus.client.drop_collection.call_args_list,
            [
                unittest.mock.call(self.candidate),
                unittest.mock.call(self.previous),
            ],
        )
        self.assertEqual(
            result["deleted_versions"],
            [self.candidate, self.previous],
        )


class TestMilvusManifestGovernance(unittest.TestCase):
    collection = "kb_selected"

    @staticmethod
    def make_milvus():
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.hybrid = False
        milvus.client = Mock()
        return milvus

    @staticmethod
    def make_manifest(model="embedding-a"):
        return CollectionManifest.create(
            logical_collection="kb_selected",
            embedding=EmbeddingProfile(
                provider="TestEmbedding",
                model=model,
                version=f"{model}-v1",
                dimension=8,
                normalization="none",
            ),
            metric_type="L2",
            chunk_size=1500,
            chunk_overlap=100,
            chunks=[],
            user_description="Verified knowledge base",
        )

    def test_manifest_round_trip_reads_server_property_dictionary(self):
        milvus = self.make_milvus()
        manifest = self.make_manifest()
        milvus.client.list_collections.return_value = [self.collection]
        milvus.client.list_aliases.return_value = {"aliases": []}
        milvus.client.has_collection.return_value = True
        milvus.client.describe_collection.return_value = {
            "description": "legacy description",
            "properties": {
                "deepsearcher_collection_manifest": manifest.to_json(),
            },
        }

        result = milvus.get_collection_manifest(self.collection)

        self.assertEqual(result, manifest)

    def test_missing_manifest_is_not_treated_as_verified(self):
        milvus = self.make_milvus()
        milvus.client.list_collections.return_value = [self.collection]
        milvus.client.list_aliases.return_value = []
        milvus.client.has_collection.return_value = True
        milvus.client.describe_collection.return_value = {
            "description": "legacy",
            "properties": {},
        }

        self.assertIsNone(milvus.get_collection_manifest(self.collection))

    def test_invalid_manifest_property_fails_closed(self):
        milvus = self.make_milvus()
        milvus.client.list_collections.return_value = [self.collection]
        milvus.client.list_aliases.return_value = []
        milvus.client.has_collection.return_value = True
        milvus.client.describe_collection.return_value = {
            "properties": {
                "deepsearcher_collection_manifest": '{"schema_version":1}',
            },
        }

        with self.assertRaises(CollectionManifestInvalid):
            milvus.get_collection_manifest(self.collection)

    def test_set_manifest_resolves_alias_to_physical_collection(self):
        milvus = self.make_milvus()
        manifest = self.make_manifest()
        physical = "kb_selected__v_20260731100000_aaaaaaaa"
        milvus.client.list_collections.return_value = [physical]
        milvus.client.list_aliases.return_value = [self.collection]

        milvus.set_collection_manifest(self.collection, manifest)

        milvus.client.alter_collection_properties.assert_called_once_with(
            collection_name=physical,
            properties={
                "deepsearcher_collection_manifest": manifest.to_json(),
            },
            timeout=10,
        )

    def test_same_dimension_different_model_is_rejected(self):
        milvus = self.make_milvus()
        milvus.get_collection_manifest = Mock(return_value=self.make_manifest(model="embedding-a"))
        query_profile = self.make_manifest(model="embedding-b").embedding

        with self.assertRaises(EmbeddingProfileMismatch) as exc_info:
            milvus.assert_collection_compatible(
                self.collection,
                query_profile,
            )

        self.assertEqual(
            set(exc_info.exception.mismatches),
            {"model", "version"},
        )
        milvus.client.search.assert_not_called()


class TestMilvusFailureVisibility(unittest.TestCase):
    @staticmethod
    def make_milvus():
        milvus = Milvus.__new__(Milvus)
        milvus.default_collection = "deepsearcher"
        milvus.hybrid = False
        milvus.rrf_k = 60
        milvus.client = Mock()
        return milvus

    def test_real_zero_hit_remains_a_successful_empty_result(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.search.return_value = [[]]

        result = milvus.search_data("kb_selected", [0.1, 0.2])

        self.assertEqual(result, [])

    def test_l2_search_result_preserves_distance_semantics(self):
        milvus = self.make_milvus()
        milvus.metric_type = "L2"
        milvus.client.has_collection.return_value = True
        milvus.client.search.return_value = [
            [
                {
                    "entity": {
                        "embedding": [0.1, 0.2],
                        "text": "nearest",
                        "reference": "guide.pdf",
                        "metadata": {},
                    },
                    "distance": 0.125,
                }
            ]
        ]

        result = milvus.search_data("kb_selected", [0.1, 0.2])[0]

        self.assertEqual(result.metric_type, "L2")
        self.assertEqual(result.distance, 0.125)
        self.assertEqual(result.score_kind, "distance")
        self.assertFalse(result.higher_is_better)

    def test_hybrid_result_is_an_rrf_rank_score(self):
        milvus = self.make_milvus()
        milvus.metric_type = "L2"
        milvus.hybrid = True
        milvus.client.has_collection.return_value = True
        milvus.client.hybrid_search.return_value = [
            [
                {
                    "entity": {
                        "embedding": [0.1, 0.2],
                        "text": "hybrid",
                        "reference": "guide.pdf",
                        "metadata": {},
                    },
                    "distance": 0.0328,
                }
            ]
        ]

        result = milvus.search_data(
            "kb_selected",
            [0.1, 0.2],
            query_text="guide",
        )[0]

        self.assertEqual(result.metric_type, "RRF")
        self.assertEqual(result.rank_score, 0.0328)
        self.assertEqual(result.score_kind, "rank_score")
        self.assertTrue(result.higher_is_better)
        ranker = milvus.client.hybrid_search.call_args.kwargs["ranker"]
        self.assertEqual(ranker._k, 60)

    def test_weighted_rrf_hybrid_preserves_dense_anchors_and_expands_candidates(self):
        milvus = self.make_milvus()
        milvus.metric_type = "L2"
        milvus.hybrid = True
        milvus.hybrid_ranker = "weighted_rrf"
        milvus.hybrid_dense_weight = 1.5
        milvus.hybrid_candidate_multiplier = 2
        milvus.hybrid_dense_anchor_count = 2
        milvus.client.has_collection.return_value = True

        def search(**kwargs):
            prefix = "sparse" if kwargs["anns_field"] == "sparse_vector" else "dense"
            return [
                [
                    {
                        "id": f"{prefix}-{index}",
                        "entity": {
                            "embedding": [0.1, 0.2],
                            "text": f"{prefix}-{index}",
                            "reference": "guide.pdf",
                            "metadata": {"chunk_index": index},
                        },
                        "distance": float(index),
                    }
                    for index in range(6)
                ]
            ]

        milvus.client.search.side_effect = search

        results = milvus.search_data(
            "kb_selected",
            [0.1, 0.2],
            query_text="guide",
            retrieval_mode="hybrid",
            top_k=3,
        )

        self.assertEqual([result.text for result in results[:2]], ["dense-0", "dense-1"])
        self.assertTrue(all(result.metric_type == "WEIGHTED_RRF" for result in results))
        self.assertEqual(
            {call.kwargs["limit"] for call in milvus.client.search.call_args_list}, {6}
        )
        milvus.client.hybrid_search.assert_not_called()

    def test_explicit_dense_mode_uses_embedding_field_on_hybrid_collection(self):
        milvus = self.make_milvus()
        milvus.hybrid = True
        milvus.client.has_collection.return_value = True
        milvus.client.search.return_value = [[]]

        result = milvus.search_data(
            "kb_selected",
            [0.1, 0.2],
            query_text="guide",
            retrieval_mode="dense",
        )

        self.assertEqual(result, [])
        self.assertEqual(milvus.client.search.call_args.kwargs["anns_field"], "embedding")
        milvus.client.hybrid_search.assert_not_called()

    def test_bm25_mode_uses_sparse_field_and_preserves_rank_score(self):
        milvus = self.make_milvus()
        milvus.hybrid = True
        milvus.client.has_collection.return_value = True
        milvus.client.search.return_value = [
            [
                {
                    "entity": {
                        "embedding": [0.1, 0.2],
                        "text": "keyword match",
                        "reference": "guide.pdf",
                        "metadata": {},
                    },
                    "distance": 2.5,
                }
            ]
        ]

        result = milvus.search_data(
            "kb_selected",
            [0.1, 0.2],
            query_text="guide",
            retrieval_mode="sparse",
        )[0]

        call = milvus.client.search.call_args.kwargs
        self.assertEqual(call["data"], ["guide"])
        self.assertEqual(call["anns_field"], "sparse_vector")
        self.assertEqual(call["search_params"], {"metric_type": "BM25"})
        self.assertEqual(result.metric_type, "BM25")
        self.assertEqual(result.rank_score, 2.5)
        self.assertTrue(result.higher_is_better)

    def test_invalid_or_unavailable_explicit_retrieval_mode_fails_before_search(self):
        milvus = self.make_milvus()

        with self.assertRaises(ValueError):
            milvus.search_data("kb_selected", [0.1], retrieval_mode="unknown")
        with self.assertRaises(ValueError):
            milvus.search_data(
                "kb_selected",
                [0.1],
                query_text="guide",
                retrieval_mode="hybrid",
            )

        milvus.client.search.assert_not_called()
        milvus.client.hybrid_search.assert_not_called()

    def test_describe_retrieval_profile_reports_sparse_resource_overhead(self):
        milvus = self.make_milvus()
        milvus.hybrid = True
        milvus._physical_collection = Mock(return_value="kb_backing")
        milvus.client.describe_collection.return_value = {
            "fields": [
                {"name": "embedding", "type": DataType.FLOAT_VECTOR, "params": {"dim": 2}},
                {"name": "sparse_vector", "type": DataType.SPARSE_FLOAT_VECTOR, "params": {}},
            ]
        }
        milvus.client.list_indexes.return_value = ["embedding", "sparse_vector"]
        milvus.client.describe_index.side_effect = [
            {
                "index_name": "embedding",
                "field_name": "embedding",
                "index_type": "AUTOINDEX",
                "metric_type": "L2",
            },
            {
                "index_name": "sparse_vector",
                "field_name": "sparse_vector",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
            },
        ]
        milvus.client.get_collection_stats.return_value = {"row_count": 16}

        profile = milvus.describe_retrieval_profile("kb_selected")

        self.assertEqual(profile["physical_collection"], "kb_backing")
        self.assertEqual(profile["row_count"], 16)
        self.assertEqual(profile["capabilities"], ["dense", "bm25", "hybrid"])
        self.assertEqual(
            profile["fusion"],
            {
                "algorithm": "RRF",
                "k": 60,
                "dense_weight": 1.0,
                "sparse_weight": 1.0,
                "candidate_multiplier": 1,
                "dense_anchor_count": 0,
                "diversity_tolerance": 0.0,
            },
        )
        self.assertEqual(len(profile["indexes"]), 2)

    def test_missing_collection_is_not_reported_as_zero_hit(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = False

        with self.assertRaises(CollectionNotFound):
            milvus.search_data("kb_missing", [0.1, 0.2])

    def test_connection_failure_is_not_reported_as_zero_hit(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.side_effect = RuntimeError("failed to connect to server")

        with self.assertRaises(VectorDBUnavailable):
            milvus.search_data("kb_selected", [0.1, 0.2])

    def test_dimension_mismatch_has_a_distinct_error(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.search.side_effect = RuntimeError(
            "vector dimension mismatch, expected vector size 8"
        )

        with self.assertRaises(VectorDimensionMismatch):
            milvus.search_data("kb_selected", [0.1, 0.2, 0.3])

    def test_unknown_search_failure_is_not_reported_as_zero_hit(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = True
        milvus.client.search.side_effect = RuntimeError("unexpected search failure")

        with self.assertRaises(VectorSearchFailed):
            milvus.search_data("kb_selected", [0.1, 0.2])

    def test_list_failure_is_not_reported_as_no_collections(self):
        milvus = self.make_milvus()
        milvus.client.list_collections.side_effect = RuntimeError("unexpected list failure")

        with self.assertRaises(VectorListFailed):
            milvus.list_collections()

    def test_initialization_failure_is_not_swallowed(self):
        milvus = self.make_milvus()
        milvus.client.has_collection.return_value = False
        milvus.client.create_schema.side_effect = RuntimeError("schema failure")

        with self.assertRaises(VectorInitializationFailed):
            milvus.init_collection(dim=2, collection="kb_selected")

    def test_insert_failure_is_not_swallowed(self):
        milvus = self.make_milvus()
        milvus.client.insert.side_effect = RuntimeError("insert failure")
        chunk = Mock(
            text="text",
            reference="source.pdf",
            metadata={},
            embedding=[0.1, 0.2],
        )

        with self.assertRaises(VectorInsertFailed):
            milvus.insert_data("kb_selected", [chunk])


if __name__ == "__main__":
    unittest.main()
