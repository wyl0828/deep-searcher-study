import unittest
from typing import List

import numpy as np

from deepsearcher.vector_db.base import (
    BaseVectorDB,
    CollectionInfo,
    RetrievalResult,
    deduplicate_results,
    score_kind_for_metric,
)


class TestRetrievalResult(unittest.TestCase):
    """Tests for the RetrievalResult class."""

    def setUp(self):
        """Set up test fixtures."""
        self.embedding = np.array([0.1, 0.2, 0.3])
        self.text = "Test text"
        self.reference = "test.txt"
        self.metadata = {"key": "value"}
        self.score = 0.95

    def test_init(self):
        """Test initialization of RetrievalResult."""
        result = RetrievalResult(
            embedding=self.embedding,
            text=self.text,
            reference=self.reference,
            metadata=self.metadata,
            score=self.score,
        )

        self.assertTrue(np.array_equal(result.embedding, self.embedding))
        self.assertEqual(result.text, self.text)
        self.assertEqual(result.reference, self.reference)
        self.assertEqual(result.metadata, self.metadata)
        self.assertEqual(result.score, self.score)
        self.assertEqual(result.score_kind, "rank_score")
        self.assertEqual(result.metric_type, "UNKNOWN")

    def test_init_default_score(self):
        """Test initialization of RetrievalResult with default score."""
        result = RetrievalResult(
            embedding=self.embedding,
            text=self.text,
            reference=self.reference,
            metadata=self.metadata,
        )
        self.assertIsNone(result.score)
        self.assertIsNone(result.score_kind)

    def test_repr(self):
        """Test string representation of RetrievalResult."""
        result = RetrievalResult(
            embedding=self.embedding,
            text=self.text,
            reference=self.reference,
            metadata=self.metadata,
            score=self.score,
        )
        expected = (
            "RetrievalResult(metric_type=UNKNOWN, score_kind=rank_score, value=0.95, "
            f"embedding={self.embedding}, text={self.text}, reference={self.reference}), "
            f"metadata={self.metadata}"
        )
        self.assertEqual(repr(result), expected)

    def test_metric_value_preserves_distance_direction(self):
        result = RetrievalResult.from_metric_value(
            embedding=self.embedding,
            text=self.text,
            reference=self.reference,
            metadata=self.metadata,
            metric_type="l2",
            value=0.25,
        )

        self.assertEqual(result.metric_type, "L2")
        self.assertEqual(result.distance, 0.25)
        self.assertEqual(result.score_kind, "distance")
        self.assertFalse(result.higher_is_better)

    def test_metric_value_preserves_similarity_direction(self):
        result = RetrievalResult.from_metric_value(
            embedding=self.embedding,
            text=self.text,
            reference=self.reference,
            metadata=self.metadata,
            metric_type="cosine",
            value=0.95,
        )

        self.assertEqual(result.similarity, 0.95)
        self.assertEqual(result.score_kind, "similarity")
        self.assertTrue(result.higher_is_better)

    def test_conflicting_semantic_values_are_rejected(self):
        with self.assertRaises(ValueError):
            RetrievalResult(
                self.embedding,
                self.text,
                self.reference,
                self.metadata,
                distance=0.1,
                similarity=0.9,
            )

    def test_metric_mapping_does_not_invent_similarity_conversion(self):
        self.assertEqual(score_kind_for_metric("L2"), "distance")
        self.assertEqual(score_kind_for_metric("COSINE"), "similarity")
        self.assertEqual(score_kind_for_metric("RRF"), "rank_score")


class TestDeduplicateResults(unittest.TestCase):
    """Tests for the deduplicate_results function."""

    def setUp(self):
        """Set up test fixtures."""
        self.embedding1 = np.array([0.1, 0.2, 0.3])
        self.embedding2 = np.array([0.4, 0.5, 0.6])
        self.text1 = "Text 1"
        self.text2 = "Text 2"
        self.reference = "test.txt"
        self.metadata = {"key": "value"}

    def test_no_duplicates(self):
        """Test deduplication with no duplicate results."""
        results = [
            RetrievalResult(self.embedding1, self.text1, self.reference, self.metadata),
            RetrievalResult(self.embedding2, self.text2, self.reference, self.metadata),
        ]
        deduplicated = deduplicate_results(results)
        self.assertEqual(len(deduplicated), 2)
        self.assertEqual(deduplicated, results)

    def test_with_duplicates(self):
        """Test deduplication with duplicate results."""
        results = [
            RetrievalResult(self.embedding1, self.text1, self.reference, self.metadata),
            RetrievalResult(self.embedding2, self.text2, self.reference, self.metadata),
            RetrievalResult(self.embedding1, self.text1, self.reference, self.metadata),
        ]
        deduplicated = deduplicate_results(results)
        self.assertEqual(len(deduplicated), 2)
        self.assertEqual(deduplicated[0].text, self.text1)
        self.assertEqual(deduplicated[1].text, self.text2)

    def test_empty_list(self):
        """Test deduplication with empty list."""
        results = []
        deduplicated = deduplicate_results(results)
        self.assertEqual(len(deduplicated), 0)


class TestCollectionInfo(unittest.TestCase):
    """Tests for the CollectionInfo class."""

    def test_init(self):
        """Test initialization of CollectionInfo."""
        name = "test_collection"
        description = "Test collection description"
        collection_info = CollectionInfo(name, description)

        self.assertEqual(collection_info.collection_name, name)
        self.assertEqual(collection_info.description, description)


class MockVectorDB(BaseVectorDB):
    """Mock implementation of BaseVectorDB for testing."""

    def init_collection(
        self, dim, collection, description, force_new_collection=False, *args, **kwargs
    ):
        pass

    def insert_data(self, collection, chunks, *args, **kwargs):
        pass

    def search_data(self, collection, vector, *args, **kwargs) -> List[RetrievalResult]:
        return []

    def clear_db(self, *args, **kwargs):
        pass


class TestBaseVectorDB(unittest.TestCase):
    """Tests for the BaseVectorDB class."""

    def setUp(self):
        """Set up test fixtures."""
        self.db = MockVectorDB()

    def test_init_default(self):
        """Test initialization with default collection name."""
        self.assertEqual(self.db.default_collection, "deepsearcher")

    def test_init_custom_collection(self):
        """Test initialization with custom collection name."""
        custom_collection = "custom_collection"
        db = MockVectorDB(default_collection=custom_collection)
        self.assertEqual(db.default_collection, custom_collection)

    def test_list_collections_default(self):
        """Test default list_collections implementation."""
        self.assertIsNone(self.db.list_collections())


if __name__ == "__main__":
    unittest.main()
