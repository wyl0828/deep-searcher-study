import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from deepsearcher.collection_manifest import CollectionManifest, EmbeddingProfile
from deepsearcher.vector_db.exceptions import (
    CollectionManifestInvalid,
    UnsafeCollectionReplacement,
    VectorInsertFailed,
    VectorSearchFailed,
)


def make_manifest(model: str = "embedding-a") -> CollectionManifest:
    return CollectionManifest.create(
        logical_collection="test_collection",
        embedding=EmbeddingProfile(
            provider="TestEmbedding",
            model=model,
            version=f"{model}-v1",
            dimension=8,
            normalization="none",
        ),
        metric_type="COSINE",
        chunk_size=1500,
        chunk_overlap=100,
        chunks=[],
    )


class TestQdrant(unittest.TestCase):
    """Tests for the Qdrant vector database implementation."""

    def setUp(self):
        """Set up test fixtures."""
        # Create mock modules
        self.mock_qdrant = MagicMock()
        self.mock_models = MagicMock()
        self.mock_qdrant.models = self.mock_models

        # Create the module patcher
        self.module_patcher = patch.dict(
            "sys.modules",
            {"qdrant_client": self.mock_qdrant, "qdrant_client.models": self.mock_models},
        )
        self.module_patcher.start()

        # Import after mocking
        from deepsearcher.loader.splitter import Chunk
        from deepsearcher.vector_db import Qdrant
        from deepsearcher.vector_db.base import RetrievalResult

        self.Qdrant = Qdrant
        self.Chunk = Chunk
        self.RetrievalResult = RetrievalResult

    def tearDown(self):
        """Clean up test fixtures."""
        self.module_patcher.stop()

    @patch("qdrant_client.QdrantClient")
    def test_init(self, mock_client_class):
        """Test basic initialization."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        qdrant = self.Qdrant(
            location="memory",
            url="http://custom:6333",
            port=6333,
            api_key="test_key",
            default_collection="custom",
        )

        # Verify initialization - just check basic properties
        self.assertEqual(qdrant.default_collection, "custom")
        self.assertIsNotNone(qdrant.client)

    @patch("qdrant_client.QdrantClient")
    def test_init_collection(self, mock_client_class):
        """Test collection initialization."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.collection_exists.return_value = False

        qdrant = self.Qdrant()

        # Test collection initialization
        d = 8
        collection = "test_collection"

        try:
            qdrant.init_collection(dim=d, collection=collection)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "init_collection should work")

    @patch("qdrant_client.QdrantClient")
    def test_force_replacement_never_deletes_existing_collection(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.collection_exists.return_value = True
        qdrant = self.Qdrant()

        with self.assertRaises(UnsafeCollectionReplacement):
            qdrant.init_collection(
                dim=8,
                collection="test_collection",
                force_new_collection=True,
            )

        mock_client.delete_collection.assert_not_called()
        mock_client.create_collection.assert_not_called()

    @patch("qdrant_client.QdrantClient")
    def test_insert_data(self, mock_client_class):
        """Test inserting data."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.upsert.return_value = None

        qdrant = self.Qdrant()

        # Create test data
        d = 8
        collection = "test_collection"
        rng = np.random.default_rng(seed=42)

        # Create test chunks with numpy arrays converted to lists
        chunks = [
            self.Chunk(
                embedding=rng.random(d).tolist(),  # Convert to list
                text="hello world",
                reference="test.txt",
                metadata={"key": "value1"},
            ),
            self.Chunk(
                embedding=rng.random(d).tolist(),  # Convert to list
                text="hello qdrant",
                reference="test.txt",
                metadata={"key": "value2"},
            ),
        ]

        try:
            qdrant.insert_data(collection=collection, chunks=chunks)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "insert_data should work")

    @patch("qdrant_client.QdrantClient")
    def test_insert_failure_is_not_reported_as_success(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.upsert.side_effect = RuntimeError("unavailable")
        qdrant = self.Qdrant()
        chunk = self.Chunk(
            embedding=[0.0] * 8,
            text="content",
            reference="test.txt",
            metadata={},
        )

        with self.assertRaises(VectorInsertFailed):
            qdrant.insert_data(collection="test_collection", chunks=[chunk])

    @patch("qdrant_client.QdrantClient")
    def test_collection_manifest_round_trip_uses_reserved_point(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.collection_exists.return_value = True
        manifest = make_manifest()
        mock_client.retrieve.return_value = [
            SimpleNamespace(
                payload={
                    "_deepsearcher_collection_manifest": manifest.to_dict(),
                }
            )
        ]
        self.mock_models.PointStruct.side_effect = lambda **kwargs: SimpleNamespace(**kwargs)
        qdrant = self.Qdrant()

        restored = qdrant.get_collection_manifest("test_collection")
        qdrant.set_collection_manifest("test_collection", manifest)

        self.assertEqual(restored.fingerprint, manifest.fingerprint)
        call = mock_client.upsert.call_args
        self.assertEqual(call.kwargs["collection_name"], "test_collection")
        point = call.kwargs["points"][0]
        self.assertEqual(point.id, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(point.vector, [0.0] * 8)
        self.assertEqual(
            point.payload["_deepsearcher_collection_manifest"]["manifest_fingerprint"],
            manifest.fingerprint,
        )
        self.assertTrue(call.kwargs["wait"])

    @patch("qdrant_client.QdrantClient")
    def test_invalid_collection_manifest_is_rejected(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.collection_exists.return_value = True
        mock_client.retrieve.return_value = [
            SimpleNamespace(payload={"_deepsearcher_collection_manifest": {"invalid": True}})
        ]
        qdrant = self.Qdrant()

        with self.assertRaises(CollectionManifestInvalid):
            qdrant.get_collection_manifest("test_collection")

    @patch("qdrant_client.QdrantClient")
    def test_search_data(self, mock_client_class):
        """Test search functionality."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock search results
        d = 8
        rng = np.random.default_rng(seed=42)
        mock_point1 = MagicMock()
        mock_point1.vector = rng.random(d)
        mock_point1.payload = {
            "text": "hello world",
            "reference": "test.txt",
            "metadata": {"key": "value1"},
        }
        mock_point1.score = 0.95

        mock_point2 = MagicMock()
        mock_point2.vector = rng.random(d)
        mock_point2.payload = {
            "text": "hello qdrant",
            "reference": "test.txt",
            "metadata": {"key": "value2"},
        }
        mock_point2.score = 0.85

        mock_response = MagicMock()
        mock_response.points = [mock_point1, mock_point2]
        mock_client.query_points.return_value = mock_response

        qdrant = self.Qdrant()

        # Test search
        collection = "test_collection"
        query_vector = rng.random(d)

        try:
            results = qdrant.search_data(collection=collection, vector=query_vector, top_k=2)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "search_data should work")
        if test_passed:
            self.assertIsInstance(results, list)
            self.assertEqual(len(results), 2)
            # Verify results are RetrievalResult objects
            for result in results:
                self.assertIsInstance(result, self.RetrievalResult)
                self.assertEqual(result.metric_type, "COSINE")
                self.assertEqual(result.score_kind, "similarity")
                self.assertTrue(result.higher_is_better)
            self.assertEqual([result.similarity for result in results], [0.95, 0.85])

    @patch("qdrant_client.QdrantClient")
    def test_search_data_preserves_euclidean_distance_direction(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        point = MagicMock()
        point.vector = [0.1, 0.2]
        point.payload = {"text": "near", "reference": "near.txt", "metadata": {}}
        point.score = 0.25
        mock_client.query_points.return_value.points = [point]
        qdrant = self.Qdrant()
        qdrant._collection_metric_types["euclid_collection"] = "EUCLID"

        result = qdrant.search_data("euclid_collection", [0.1, 0.2], top_k=1)[0]

        self.assertEqual(result.metric_type, "EUCLID")
        self.assertEqual(result.distance, 0.25)
        self.assertFalse(result.higher_is_better)

    @patch("qdrant_client.QdrantClient")
    def test_search_failure_is_not_converted_to_no_evidence(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.query_points.side_effect = RuntimeError("unavailable")
        qdrant = self.Qdrant()

        with self.assertRaises(VectorSearchFailed):
            qdrant.search_data(
                collection="test_collection",
                vector=[0.0] * 8,
                top_k=2,
            )

    @patch("qdrant_client.QdrantClient")
    def test_clear_collection(self, mock_client_class):
        """Test clearing collection."""
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.delete_collection.return_value = None

        qdrant = self.Qdrant()
        collection = "test_collection"

        try:
            qdrant.clear_db(collection=collection)
            test_passed = True
        except Exception as e:
            test_passed = False
            print(f"Error: {e}")

        self.assertTrue(test_passed, "clear_db should work")


if __name__ == "__main__":
    unittest.main()
