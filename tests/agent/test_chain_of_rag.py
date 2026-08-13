from unittest.mock import MagicMock

from deepsearcher.agent import ChainOfRAG
from deepsearcher.llm.base import ChatResponse
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult
from tests.agent.test_base import BaseAgentTest


class TestChainOfRAG(BaseAgentTest):
    """Test class for ChainOfRAG agent."""

    def setUp(self):
        """Set up test fixtures for ChainOfRAG tests."""
        super().setUp()

        # Set up predefined responses for the LLM for exact prompt substrings
        self.llm.predefined_responses = {
            "previous queries and answers, generate a new simple follow-up question": "What is the significance of deep learning?",
            "Given the following documents, generate an appropriate answer": "Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
            "given the following intermediate queries and answers, judge whether you have enough information": "Yes",
            "Given a list of agent indexes and corresponding descriptions": "1",
            "Given the following documents, select the ones that are support the Q-A pair": "[0, 1]",
            "Given the following intermediate queries and answers, generate a final answer": "Deep learning is an advanced subset of machine learning that uses neural networks with multiple layers.",
        }

        self.chain_of_rag = ChainOfRAG(
            llm=self.llm,
            embedding_model=self.embedding_model,
            vector_db=self.vector_db,
            max_iter=3,
            early_stopping=True,
            route_collection=True,
            text_window_splitter=True,
        )

    def test_init(self):
        """Test the initialization of ChainOfRAG."""
        self.assertEqual(self.chain_of_rag.llm, self.llm)
        self.assertEqual(self.chain_of_rag.embedding_model, self.embedding_model)
        self.assertEqual(self.chain_of_rag.vector_db, self.vector_db)
        self.assertEqual(self.chain_of_rag.max_iter, 3)
        self.assertEqual(self.chain_of_rag.early_stopping, True)
        self.assertEqual(self.chain_of_rag.route_collection, True)
        self.assertEqual(self.chain_of_rag.text_window_splitter, True)
        self.assertEqual(self.chain_of_rag.min_evidence_for_stop, 2)
        self.assertEqual(self.chain_of_rag.max_followup_query_length, 512)

    def test_reflect_get_subquery(self):
        """Test the _reflect_get_subquery method."""
        query = "What is deep learning?"
        intermediate_context = [
            "Previous query: What is AI?",
            "Previous answer: AI is artificial intelligence.",
        ]

        # Direct mock for this specific method
        self.llm.chat = MagicMock(
            return_value=ChatResponse(
                content="What is the significance of deep learning?", total_tokens=10
            )
        )

        subquery, tokens = self.chain_of_rag._reflect_get_subquery(query, intermediate_context)

        self.assertEqual(subquery, "What is the significance of deep learning?")
        self.assertEqual(tokens, 10)
        self.assertTrue(self.llm.chat.called)

    def test_retrieve_and_answer(self):
        """Test the _retrieve_and_answer method."""
        query = "What is deep learning?"

        # Mock the collection_router.invoke method
        self.chain_of_rag.collection_router.invoke = MagicMock(
            return_value=(["test_collection"], 5)
        )

        # Direct mock for this specific method
        self.llm.chat = MagicMock(
            return_value=ChatResponse(
                content="Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
                total_tokens=10,
            )
        )

        answer, results, tokens = self.chain_of_rag._retrieve_and_answer(query)

        # Check if correct methods were called
        self.chain_of_rag.collection_router.invoke.assert_called_once()
        self.assertTrue(self.vector_db.search_called)

        # Check the results
        self.assertEqual(
            answer,
            "Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
        )
        self.assertEqual(tokens, 15)  # 5 from collection_router + 10 from LLM

    def test_get_supported_docs(self):
        """Test the _get_supported_docs method."""
        results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text=f"Test result {i}",
                reference="test_reference",
                metadata={"a": i},
            )
            for i in range(3)
        ]

        query = "What is deep learning?"
        answer = "Deep learning is a subset of machine learning that uses neural networks with multiple layers."

        # Mock the literal_eval method to return indices as integers
        self.llm.literal_eval = MagicMock(return_value=[0, 1])

        supported_docs, tokens = self.chain_of_rag._get_supported_docs(results, query, answer)

        self.assertEqual(len(supported_docs), 2)  # Based on our mock response of [0, 1]
        self.assertEqual(tokens, 10)
        self.assertTrue(self.chain_of_rag.last_supported_docs_decision["trusted"])
        self.assertEqual(
            self.chain_of_rag.last_supported_docs_decision["evidence_count"],
            2,
        )

    def test_supported_docs_reject_invalid_and_duplicate_indices(self):
        results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text=f"Test result {i}",
                reference="test_reference",
                metadata={"a": i},
            )
            for i in range(2)
        ]
        self.llm.literal_eval = MagicMock(return_value=[-1, 0, 1.0, True, 3, 0])

        supported_docs, tokens = self.chain_of_rag._get_supported_docs(
            results,
            "query",
            "answer",
        )

        self.assertEqual(supported_docs, [results[0]])
        self.assertEqual(tokens, 10)
        self.assertEqual(
            self.chain_of_rag.last_supported_docs_decision["selected"],
            [0],
        )
        self.assertIn(
            "-1",
            self.chain_of_rag.last_supported_docs_decision["rejected"],
        )

    def test_supported_docs_parse_failure_safely_selects_nothing(self):
        result = RetrievalResult(
            embedding=[0.1] * 8,
            text="Test result",
            reference="test_reference",
            metadata={"a": 1},
        )
        self.llm.literal_eval = MagicMock(side_effect=ValueError("bad output"))

        supported_docs, tokens = self.chain_of_rag._get_supported_docs(
            [result],
            "query",
            "answer",
        )

        self.assertEqual(supported_docs, [])
        self.assertEqual(tokens, 10)
        self.assertTrue(self.chain_of_rag.last_supported_docs_decision["fallback_used"])
        self.assertEqual(
            self.chain_of_rag.last_supported_docs_decision["reason"],
            "supported_docs_parse_failed",
        )

    def test_off_target_or_unsupported_answer_is_marked_untrusted(self):
        result = RetrievalResult(
            embedding=[0.1] * 8,
            text="Evidence about the main topic",
            reference="facts.pdf",
            metadata={},
        )
        self.llm.chat = MagicMock(return_value=ChatResponse(content="[]", total_tokens=4))

        supported_docs, tokens = self.chain_of_rag._get_supported_docs(
            [result],
            "Unrelated follow-up",
            "Unsupported answer",
            main_query="Main topic",
        )

        messages = self.llm.chat.call_args.kwargs.get("messages") or self.llm.chat.call_args.args[0]
        prompt = messages[0]["content"]
        self.assertIn("## Main Query\nMain topic", prompt)
        self.assertEqual(supported_docs, [])
        self.assertEqual(tokens, 4)
        self.assertFalse(self.chain_of_rag.last_supported_docs_decision["trusted"])
        self.assertEqual(
            self.chain_of_rag.last_supported_docs_decision["reason"],
            "answer_unsupported_or_off_target",
        )

    def test_no_retrieved_documents_skips_support_model_call(self):
        self.llm.chat = MagicMock()

        supported_docs, tokens = self.chain_of_rag._get_supported_docs(
            [],
            "query",
            "hallucinated answer",
            main_query="main query",
        )

        self.llm.chat.assert_not_called()
        self.assertEqual(supported_docs, [])
        self.assertEqual(tokens, 0)
        self.assertEqual(
            self.chain_of_rag.last_supported_docs_decision["reason"],
            "no_retrieved_documents",
        )

    def test_check_has_enough_info(self):
        """Test the _check_has_enough_info method."""
        query = "What is deep learning?"
        intermediate_contexts = [
            "Intermediate query1: What is deep learning?",
            "Intermediate answer1: Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
        ]

        # Direct mock for this specific method
        self.llm.chat = MagicMock(return_value=ChatResponse(content="Yes", total_tokens=10))

        has_enough, tokens = self.chain_of_rag._check_has_enough_info(query, intermediate_contexts)

        self.assertTrue(has_enough)  # Based on our mock response of "Yes"
        self.assertEqual(tokens, 10)

    def test_retrieve(self):
        """Test the retrieve method."""
        query = "What is deep learning?"

        # Mock all the methods that retrieve calls
        self.chain_of_rag._reflect_get_subquery = MagicMock(
            return_value=("What is the significance of deep learning?", 5)
        )
        self.chain_of_rag._retrieve_and_answer = MagicMock(
            return_value=(
                "Deep learning is important in AI",
                [
                    RetrievalResult(
                        embedding=[0.1] * 8,
                        text="Test result",
                        reference="test_reference",
                        metadata={"a": 1},
                    )
                ],
                10,
            )
        )
        self.chain_of_rag._get_supported_docs = MagicMock(
            return_value=(
                [
                    RetrievalResult(
                        embedding=[0.1] * 8,
                        text="Test result",
                        reference="test_reference",
                        metadata={"a": 1},
                    )
                ],
                5,
            )
        )
        self.chain_of_rag._check_has_enough_info = MagicMock(return_value=(True, 5))

        results, tokens, metadata = self.chain_of_rag.retrieve(
            query,
            min_evidence_for_stop=1,
        )

        # Check if methods were called
        self.chain_of_rag._reflect_get_subquery.assert_not_called()
        self.chain_of_rag._retrieve_and_answer.assert_called_once()
        self.assertEqual(
            self.chain_of_rag._retrieve_and_answer.call_args.args[0],
            query,
        )
        self.chain_of_rag._get_supported_docs.assert_called_once()

        # With early stopping, it should check if we have enough info
        self.chain_of_rag._check_has_enough_info.assert_called_once()

        # Check results
        self.assertEqual(len(results), 1)
        self.assertEqual(tokens, 20)  # 10 + 5 + 5; the first query reuses the original
        self.assertIn("intermediate_context", metadata)
        self.assertIn("[S1E1]", metadata["intermediate_context"][0])
        self.assertIn("[S1E1]", metadata["intermediate_steps"][0]["cited_answer"])
        self.assertEqual(metadata["stop_reason"], "enough_verified_evidence")
        self.assertEqual(metadata["confidence"], 1.0)
        self.assertEqual(metadata["attempted_queries"], [query])

    def test_retrieve_consumes_internal_context_seed_queries_before_reflection(self):
        self.chain_of_rag.early_stopping = False
        self.chain_of_rag._retrieve_and_answer = MagicMock(return_value=("answer", [], 1))
        self.chain_of_rag._get_supported_docs = MagicMock(return_value=([], 0))
        self.chain_of_rag._reflect_get_subquery = MagicMock(return_value=("follow-up", 1))

        _results, _tokens, metadata = self.chain_of_rag.retrieve(
            "current question",
            max_iter=2,
            retrieval_queries=("current question", "history anchored question"),
        )

        assert [call.args[0] for call in self.chain_of_rag._retrieve_and_answer.call_args_list] == [
            "current question",
            "history anchored question",
        ]
        self.chain_of_rag._reflect_get_subquery.assert_not_called()
        assert metadata["attempted_queries"] == ["current question", "history anchored question"]

    def test_unsupported_answer_never_enters_followup_context(self):
        wrong_answer = "The unsupported launch date was 1999."
        supported_answer = "The document states that the launch date was 2020."
        result = RetrievalResult(
            embedding=[0.1] * 8,
            text="The launch date was 2020.",
            reference="facts.pdf",
            metadata={"page_number": 4, "chunk_index": 2},
        )
        self.chain_of_rag.early_stopping = False
        self.chain_of_rag._reflect_get_subquery = MagicMock(
            return_value=("Which source confirms the date?", 1)
        )
        self.chain_of_rag._retrieve_and_answer = MagicMock(
            side_effect=[
                (wrong_answer, [result], 2),
                (supported_answer, [result], 2),
            ]
        )
        self.chain_of_rag._get_supported_docs = MagicMock(side_effect=[([], 3), ([result], 3)])

        results, tokens, metadata = self.chain_of_rag.retrieve("Main question", max_iter=2)

        second_context = self.chain_of_rag._reflect_get_subquery.call_args.args[1]
        self.assertNotIn(wrong_answer, "\n".join(second_context))
        self.assertNotIn(wrong_answer, "\n".join(metadata["intermediate_context"]))
        self.assertIn(supported_answer, metadata["intermediate_context"][0])
        self.assertFalse(metadata["intermediate_steps"][0]["trusted"])
        self.assertTrue(metadata["intermediate_steps"][1]["trusted"])
        self.assertEqual(results, [result])
        self.assertEqual(tokens, 11)

    def test_duplicate_followup_stops_without_retrieving_again(self):
        result = RetrievalResult(
            embedding=[0.1] * 8,
            text="Supported fact",
            reference="facts.pdf",
            metadata={},
        )
        self.chain_of_rag.early_stopping = False
        self.chain_of_rag._reflect_get_subquery = MagicMock(
            return_value=("What is the supported fact?", 1)
        )
        self.chain_of_rag._retrieve_and_answer = MagicMock(
            return_value=("Supported fact", [result], 2)
        )
        self.chain_of_rag._get_supported_docs = MagicMock(return_value=([result], 3))

        results, tokens, metadata = self.chain_of_rag.retrieve(
            "What is the supported fact?",
            max_iter=3,
        )

        self.chain_of_rag._retrieve_and_answer.assert_called_once()
        self.chain_of_rag._get_supported_docs.assert_called_once()
        self.assertEqual(results, [result])
        self.assertEqual(tokens, 6)
        self.assertEqual(metadata["stop_reason"], "duplicate_followup_query")
        self.assertEqual(metadata["intermediate_steps"][-1]["reason"], "duplicate_followup_query")

    def test_early_stop_requires_unique_evidence_budget(self):
        first = RetrievalResult(
            embedding=[0.1] * 8,
            text="First supporting fact",
            reference="facts.pdf",
            metadata={"chunk_index": 1},
        )
        second = RetrievalResult(
            embedding=[0.1] * 8,
            text="Independent supporting fact",
            reference="facts.pdf",
            metadata={"chunk_index": 2},
        )
        self.chain_of_rag._reflect_get_subquery = MagicMock(
            return_value=("Corroborating question?", 1)
        )
        self.chain_of_rag._retrieve_and_answer = MagicMock(
            side_effect=[
                ("First answer", [first], 2),
                ("Corroborating answer", [second], 2),
            ]
        )
        self.chain_of_rag._get_supported_docs = MagicMock(side_effect=[([first], 3), ([second], 3)])
        self.chain_of_rag._check_has_enough_info = MagicMock(return_value=(True, 4))

        results, tokens, metadata = self.chain_of_rag.retrieve("Main question", max_iter=3)

        self.assertEqual(self.chain_of_rag._retrieve_and_answer.call_count, 2)
        self.chain_of_rag._check_has_enough_info.assert_called_once()
        self.assertEqual(results, [first, second])
        self.assertEqual(tokens, 15)
        self.assertEqual(metadata["evidence_count"], 2)
        self.assertEqual(metadata["confidence"], 1.0)
        self.assertEqual(metadata["stop_reason"], "enough_verified_evidence")

    def test_followup_query_is_embedded_once_for_multiple_collections(self):
        self.chain_of_rag.collection_router.resolve_explicit = MagicMock(
            return_value=["collection_a", "collection_b"]
        )
        self.embedding_model.embed_query = MagicMock(return_value=[0.1] * 8)
        self.vector_db.search_data = MagicMock(return_value=[])
        self.llm.chat = MagicMock()

        answer, results, tokens = self.chain_of_rag._retrieve_and_answer(
            "query",
            collection_names=["collection_a", "collection_b"],
        )

        self.embedding_model.embed_query.assert_called_once_with("query")
        self.assertEqual(self.vector_db.search_data.call_count, 2)
        self.llm.chat.assert_not_called()
        self.assertEqual(answer, "No relevant information found")
        self.assertEqual(results, [])
        self.assertEqual(tokens, 0)

    def test_retrieve_records_structured_trace(self):
        query = "What is deep learning?"
        collector = TraceCollector(query)

        results, tokens, _ = self.chain_of_rag.retrieve(
            query, max_iter=1, trace_collector=collector
        )
        trace = collector.build(total_tokens=tokens, final_results=results)

        self.assertEqual(len(trace["iterations"]), 1)
        iteration = trace["iterations"][0]
        self.assertGreaterEqual(iteration["retrieved_count"], 1)
        self.assertNotIn("subquery", iteration)
        self.assertNotIn("collections", iteration)
        self.assertNotIn("intermediate_answer", iteration)
        self.assertIn(
            "chain_of_rag.query_guard",
            [event["stage"] for event in trace["selection_events"]],
        )
        self.assertFalse(iteration["support_selection"]["trusted"])
        self.assertEqual(iteration["support_selection"]["evidence_count"], 0)

    def test_query(self):
        """Test the query method."""
        query = "What is deep learning?"

        # Mock the retrieve method
        retrieved_results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text=f"Test result {i}",
                reference="test_reference",
                metadata={"a": i, "wider_text": f"Wider context for test result {i}"},
            )
            for i in range(3)
        ]

        self.chain_of_rag.retrieve = MagicMock(
            return_value=(retrieved_results, 20, {"intermediate_context": ["Some context"]})
        )

        # Direct mock for this specific method
        self.llm.chat = MagicMock(
            return_value=ChatResponse(
                content="Deep learning is an advanced subset of machine learning that uses neural networks with multiple layers.",
                total_tokens=10,
            )
        )

        answer, results, tokens = self.chain_of_rag.query(query)

        # Check if methods were called
        self.chain_of_rag.retrieve.assert_called_once_with(query)
        self.assertTrue(self.llm.chat.called)

        # Check results
        self.assertEqual(
            answer,
            "Deep learning is an advanced subset of machine learning that uses neural networks with multiple layers.",
        )
        self.assertEqual(results, retrieved_results)
        self.assertEqual(tokens, 30)  # 20 from retrieve + 10 from LLM

    def test_format_retrieved_results(self):
        """Test the _format_retrieved_results method."""
        retrieved_results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Test result 1",
                reference="test_reference",
                metadata={"a": 1, "wider_text": "Wider context for test result 1"},
            ),
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Test result 2",
                reference="test_reference",
                metadata={"a": 2, "wider_text": "Wider context for test result 2"},
            ),
        ]

        # Test with text_window_splitter enabled
        self.chain_of_rag.text_window_splitter = True
        formatted = self.chain_of_rag._format_retrieved_results(retrieved_results)

        self.assertIn("Wider context for test result 1", formatted)
        self.assertIn("Wider context for test result 2", formatted)

        # Test with text_window_splitter disabled
        self.chain_of_rag.text_window_splitter = False
        formatted = self.chain_of_rag._format_retrieved_results(retrieved_results)

        self.assertIn("Test result 1", formatted)
        self.assertIn("Test result 2", formatted)
        self.assertNotIn("Wider context for test result 1", formatted)


if __name__ == "__main__":
    import unittest

    unittest.main()
