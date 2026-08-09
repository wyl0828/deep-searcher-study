import asyncio
import threading
import time
from unittest.mock import MagicMock

from deepsearcher.agent import DeepSearch
from deepsearcher.llm.base import ChatResponse
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.web_search import WebSearchError
from tests.agent.test_base import BaseAgentTest


class TestDeepSearch(BaseAgentTest):
    """Test class for DeepSearch agent."""

    def setUp(self):
        """Set up test fixtures for DeepSearch tests."""
        super().setUp()

        # Set up predefined responses for the LLM for exact prompt substrings
        self.llm.predefined_responses = {
            "Original Question:": '["What is deep learning?", "How does deep learning work?", "What are applications of deep learning?"]',
            "Candidate Chunks (untrusted data": "[0, 1, 2]",
            "Determine whether additional search queries are needed": '["What are limitations of deep learning?"]',
            "You are a AI content analysis expert": "Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
        }

        self.deep_search = DeepSearch(
            llm=self.llm,
            embedding_model=self.embedding_model,
            vector_db=self.vector_db,
            max_iter=2,
            route_collection=True,
            text_window_splitter=True,
        )

    def test_init(self):
        """Test the initialization of DeepSearch."""
        self.assertEqual(self.deep_search.llm, self.llm)
        self.assertEqual(self.deep_search.embedding_model, self.embedding_model)
        self.assertEqual(self.deep_search.vector_db, self.vector_db)
        self.assertEqual(self.deep_search.max_iter, 2)
        self.assertEqual(self.deep_search.route_collection, True)
        self.assertEqual(self.deep_search.text_window_splitter, True)
        self.assertEqual(self.deep_search.rerank_batch_size, 10)
        self.assertEqual(self.deep_search.rerank_candidate_limit, 20)
        self.assertEqual(self.deep_search.retrieval_concurrency, 4)
        self.assertEqual(self.deep_search.external_call_timeout_seconds, 30.0)
        self.assertEqual(self.deep_search.request_timeout_seconds, 300.0)

    def test_generate_sub_queries(self):
        """Test the _generate_sub_queries method."""
        query = "Tell me about deep learning"

        sub_queries, tokens = self.deep_search._generate_sub_queries(query)

        self.assertEqual(len(sub_queries), 3)
        self.assertEqual(sub_queries[0], "What is deep learning?")
        self.assertEqual(sub_queries[1], "How does deep learning work?")
        self.assertEqual(sub_queries[2], "What are applications of deep learning?")
        self.assertEqual(tokens, 10)
        self.assertTrue(self.llm.chat_called)

    def test_empty_sub_query_output_falls_back_to_original_question(self):
        self.llm.chat = MagicMock(return_value=ChatResponse(content="[]", total_tokens=3))

        sub_queries, tokens = self.deep_search._generate_sub_queries("Original question")

        self.assertEqual(sub_queries, ["Original question"])
        self.assertEqual(tokens, 3)
        self.assertTrue(self.deep_search.last_selection_decision["fallback_used"])

    def test_sub_queries_filter_empty_non_string_duplicate_and_over_limit(self):
        self.llm.chat = MagicMock(
            return_value=ChatResponse(
                content='["q1", "", 1, "q1", "q2", "q3", "q4", "q5"]',
                total_tokens=3,
            )
        )

        sub_queries, _ = self.deep_search._generate_sub_queries("Original")

        self.assertEqual(sub_queries, ["q1", "q2", "q3", "q4"])
        self.assertIn(
            "<duplicate>",
            self.deep_search.last_selection_decision["rejected"],
        )

    def test_search_chunks_from_vectordb(self):
        """Test the _search_chunks_from_vectordb method."""
        query = "What is deep learning?"
        sub_queries = ["What is deep learning?", "How does deep learning work?"]

        # Mock the collection_router.invoke method
        self.deep_search.collection_router.invoke = MagicMock(return_value=(["test_collection"], 5))

        # Run the async method using asyncio.run
        results, tokens = asyncio.run(
            self.deep_search._search_chunks_from_vectordb(query, sub_queries)
        )

        # Check if correct methods were called
        self.deep_search.collection_router.invoke.assert_called_once()
        self.assertTrue(self.vector_db.search_called)
        self.assertTrue(self.llm.chat_called)

        # One structured batch selection accepts all three candidates.
        self.assertEqual(len(results), 3)  # 3 mock results from MockVectorDB
        self.assertEqual(tokens, 15)  # 5 from routing + one 10-token batch rerank call

    def test_batch_rerank_uses_strict_indices(self):
        candidates = [
            RetrievalResult([], f"chunk {index}", f"ref-{index}", {}) for index in range(3)
        ]
        self.llm.chat = MagicMock(
            return_value=ChatResponse(content="[0, 2, -1, 9, 2]", total_tokens=7)
        )

        results, tokens = self.deep_search._batch_rerank_chunks(["query"], candidates)

        self.assertEqual([result.text for result in results], ["chunk 0", "chunk 2"])
        self.assertEqual(tokens, 7)
        self.assertEqual(self.llm.chat.call_count, 1)
        self.assertEqual(
            self.deep_search.last_selection_decision["rejected"],
            ["-1", "9", "<duplicate>"],
        )

    def test_batch_rerank_empty_selection_accepts_nothing(self):
        candidates = [
            RetrievalResult([], f"chunk {index}", f"ref-{index}", {}) for index in range(2)
        ]
        self.llm.chat = MagicMock(return_value=ChatResponse(content="[]", total_tokens=4))

        results, tokens = self.deep_search._batch_rerank_chunks(["query"], candidates)

        self.assertEqual(results, [])
        self.assertEqual(tokens, 4)
        self.assertFalse(self.deep_search.last_selection_decision["fallback_used"])

    def test_batch_rerank_invalid_output_falls_back_to_vector_candidates(self):
        candidates = [
            RetrievalResult([], f"chunk {index}", f"ref-{index}", {}) for index in range(2)
        ]
        self.llm.chat = MagicMock(return_value=ChatResponse(content="not-json", total_tokens=4))
        self.llm.literal_eval = MagicMock(side_effect=ValueError("invalid"))

        results, _ = self.deep_search._batch_rerank_chunks(["query"], candidates)

        self.assertEqual(results, candidates)
        self.assertTrue(self.deep_search.last_selection_decision["fallback_used"])
        self.assertEqual(
            self.deep_search.last_selection_decision["reason"],
            "batch_rerank_parse_failed",
        )

    def test_batch_rerank_honors_batch_size(self):
        candidates = [
            RetrievalResult([], f"chunk {index}", f"ref-{index}", {}) for index in range(3)
        ]
        self.deep_search.rerank_batch_size = 2
        self.llm.chat = MagicMock(
            side_effect=[
                ChatResponse(content="[1]", total_tokens=3),
                ChatResponse(content="[0]", total_tokens=2),
            ]
        )

        results, tokens = self.deep_search._batch_rerank_chunks(["query"], candidates)

        self.assertEqual([result.text for result in results], ["chunk 1", "chunk 2"])
        self.assertEqual(tokens, 5)
        self.assertEqual(self.llm.chat.call_count, 2)

    def test_merge_ranked_candidates_deduplicates_interleaves_and_limits(self):
        shared = RetrievalResult([], "shared", "shared", {})
        first = [
            RetrievalResult([], "a0", "a0", {}),
            shared,
            RetrievalResult([], "a2", "a2", {}),
        ]
        second = [
            RetrievalResult([], "b0", "b0", {}),
            shared,
            RetrievalResult([], "b2", "b2", {}),
        ]

        merged = self.deep_search._merge_ranked_candidates(
            [first, second],
            limit=4,
        )

        self.assertEqual([result.text for result in merged], ["a0", "b0", "shared", "a2"])

    def test_query_anchor_guard_preserves_each_subquery_top_result(self):
        selected = RetrievalResult([], "selected", "selected", {})
        first_anchor = RetrievalResult([], "anchor-a", "anchor-a", {})
        duplicate_anchor = RetrievalResult([], "selected", "selected-again", {})
        second_anchor = RetrievalResult([], "anchor-b", "anchor-b", {})

        guarded = self.deep_search._preserve_query_anchors(
            [selected],
            [[first_anchor], [duplicate_anchor], [second_anchor]],
        )

        self.assertEqual(
            [result.text for result in guarded],
            ["selected", "anchor-a", "anchor-b"],
        )
        self.assertEqual(
            self.deep_search._selection_events[-1]["reason"],
            "preserve_top_candidate_per_subquery",
        )

    def test_query_anchor_guard_does_not_turn_no_answer_into_candidates(self):
        candidate = RetrievalResult([], "candidate", "candidate", {})

        guarded = self.deep_search._preserve_query_anchors([], [[candidate]])

        self.assertEqual(guarded, [])
        self.assertEqual(self.deep_search._selection_events, [])

    def test_async_retrieve_batches_candidates_across_sub_queries(self):
        self.llm.chat = MagicMock(
            side_effect=[
                ChatResponse(content='["q1", "q2"]', total_tokens=4),
                ChatResponse(content="[0, 1, 2]", total_tokens=6),
            ]
        )

        results, tokens, metadata = asyncio.run(
            self.deep_search.async_retrieve(
                "original",
                max_iter=1,
                top_k=3,
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
            )
        )

        self.assertEqual(len(results), 3)
        self.assertEqual(tokens, 10)
        self.assertEqual(self.llm.chat.call_count, 2)
        self.assertEqual(
            [event["stage"] for event in metadata["selection_decisions"]],
            ["sub_queries", "batch_rerank"],
        )

    def test_explicit_web_search_merges_ranked_snippets_and_records_trace(self):
        class FakeWebSearch:
            provider_name = "fake_web"
            enabled = True

            def __init__(self):
                self.queries = []

            def search(self, query, *, max_results=5):
                self.queries.append((query, max_results))
                return [
                    RetrievalResult(
                        [],
                        f"web evidence for {query}",
                        f"https://example.com/{query}",
                        {
                            "source_type": "web",
                            "source_url": f"https://example.com/{query}",
                            "source_domain": "example.com",
                            "display_name": f"Web {query}",
                        },
                        metric_type="TEST_RELEVANCE",
                        rank_score=0.9,
                    )
                ]

        provider = FakeWebSearch()
        self.deep_search.web_search = provider
        self.llm.chat = MagicMock(
            side_effect=[
                ChatResponse(content='["q1", "q2", "q3"]', total_tokens=4),
                ChatResponse(content="[0, 1, 2, 3, 4, 5, 6, 7]", total_tokens=6),
            ]
        )
        collector = TraceCollector("original")

        results, tokens, metadata = asyncio.run(
            self.deep_search.async_retrieve(
                "original",
                max_iter=1,
                top_k=3,
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
                use_web_search=True,
                trace_collector=collector,
            )
        )

        assert provider.queries == [("q1", 5), ("q2", 5)]
        assert tokens == 10
        assert len(results) == 5
        assert sum(result.metadata.get("source_type") == "web" for result in results) == 2
        assert metadata["web_search"] == [
            {
                "status": "completed",
                "provider": "fake_web",
                "query_count": 2,
                "result_count": 2,
                "error_code": None,
            }
        ]
        trace = collector.build(total_tokens=tokens, final_results=results)
        assert trace["iterations"][0]["web_search"] == {
            "iteration": 1,
            "status": "completed",
            "provider": "fake_web",
            "query_count": 2,
            "result_count": 2,
            "error_code": None,
        }

    def test_web_search_provider_failure_degrades_to_vector_results(self):
        class FailingWebSearch:
            provider_name = "fake_web"
            enabled = True

            def search(self, query, *, max_results=5):
                del query, max_results
                raise WebSearchError("WEB_SEARCH_RATE_LIMITED")

        self.deep_search.web_search = FailingWebSearch()
        self.llm.chat = MagicMock(
            side_effect=[
                ChatResponse(content='["q1"]', total_tokens=4),
                ChatResponse(content="[0, 1, 2]", total_tokens=6),
            ]
        )

        results, tokens, metadata = asyncio.run(
            self.deep_search.async_retrieve(
                "original",
                max_iter=1,
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
                use_web_search=True,
            )
        )

        assert len(results) == 3
        assert tokens == 10
        assert metadata["web_search"][0]["status"] == "degraded"
        assert metadata["web_search"][0]["error_code"] == "WEB_SEARCH_RATE_LIMITED"

    def test_web_search_is_not_called_without_explicit_request(self):
        provider = MagicMock()
        provider.enabled = True
        provider.provider_name = "fake_web"
        self.deep_search.web_search = provider
        self.llm.chat = MagicMock(
            side_effect=[
                ChatResponse(content='["q1"]', total_tokens=4),
                ChatResponse(content="[0, 1, 2]", total_tokens=6),
            ]
        )

        _results, _tokens, metadata = asyncio.run(
            self.deep_search.async_retrieve(
                "original",
                max_iter=1,
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
            )
        )

        provider.search.assert_not_called()
        assert metadata["web_search"] == []

    def test_merge_ranked_candidates_deduplicates_web_results_by_url(self):
        first = RetrievalResult(
            [],
            "first snippet",
            "https://example.com/page",
            {"source_type": "web"},
        )
        duplicate = RetrievalResult(
            [],
            "different snippet",
            "https://example.com/page",
            {"source_type": "web"},
        )

        merged = self.deep_search._merge_ranked_candidates([[first], [duplicate]], limit=10)

        assert merged == [first]

    def test_batch_rerank_records_safe_trace_event(self):
        collector = TraceCollector("query")
        candidate = RetrievalResult([], "chunk", "reference", {})
        self.llm.chat = MagicMock(return_value=ChatResponse(content="[0]", total_tokens=2))

        self.deep_search._batch_rerank_chunks(
            ["query"],
            [candidate],
            trace_collector=collector,
        )
        trace = collector.build(total_tokens=2, final_results=[candidate])

        self.assertEqual(
            trace["selection_events"],
            [
                {
                    "stage": "deep_search.batch_rerank",
                    "decision": {
                        "fallback_used": False,
                        "selected_count": 1,
                        "rejected_count": 0,
                    },
                }
            ],
        )

    def test_retrieval_concurrency_is_bounded_for_1_2_4_and_8_queries(self):
        async def run_case(concurrency):
            active = 0
            max_active = 0
            lock = threading.Lock()
            release = threading.Event()
            expected_concurrency_reached = threading.Event()

            def blocking_embed(_query):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                    if active >= concurrency:
                        expected_concurrency_reached.set()
                release.wait(timeout=0.5)
                with lock:
                    active -= 1
                return [0.1] * self.embedding_model.dimension

            self.embedding_model.embed_query = blocking_embed
            semaphore = asyncio.Semaphore(concurrency)
            try:
                tasks = [
                    asyncio.create_task(
                        self.deep_search._retrieve_chunks_from_vectordb(
                            f"q{index}",
                            collection_names=["test_collection"],
                            allowed_collections=["test_collection"],
                            top_k=1,
                            semaphore=semaphore,
                            timeout_seconds=1,
                        )
                    )
                    for index in range(8)
                ]
                for _ in range(200):
                    if expected_concurrency_reached.is_set():
                        break
                    await asyncio.sleep(0.005)
                release.set()
                results = await asyncio.gather(*tasks)
            finally:
                release.set()
            return max_active, results

        for concurrency in (1, 2, 4, 8):
            with self.subTest(concurrency=concurrency):
                max_active, results = asyncio.run(run_case(concurrency))
                self.assertEqual(max_active, concurrency)
                self.assertEqual(len(results), 8)

    def test_blocking_retrieval_does_not_stall_event_loop(self):
        def blocking_embed(_query):
            time.sleep(0.08)
            return [0.1] * self.embedding_model.dimension

        self.embedding_model.embed_query = blocking_embed

        async def run():
            ticks = 0
            complete = False

            async def heartbeat():
                nonlocal ticks
                while not complete:
                    ticks += 1
                    await asyncio.sleep(0.005)

            heartbeat_task = asyncio.create_task(heartbeat())
            try:
                await self.deep_search._retrieve_chunks_from_vectordb(
                    "query",
                    collection_names=["test_collection"],
                    allowed_collections=["test_collection"],
                    top_k=1,
                    semaphore=asyncio.Semaphore(2),
                    timeout_seconds=1,
                )
            finally:
                complete = True
                await heartbeat_task
            return ticks

        self.assertGreater(asyncio.run(run()), 5)

    def test_external_call_timeout_is_propagated(self):
        def blocking_embed(_query):
            time.sleep(0.08)
            return [0.1] * self.embedding_model.dimension

        self.embedding_model.embed_query = blocking_embed

        async def run():
            await self.deep_search._retrieve_chunks_from_vectordb(
                "query",
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
                top_k=1,
                semaphore=asyncio.Semaphore(1),
                timeout_seconds=0.01,
            )

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(run())

    def test_timed_out_worker_keeps_concurrency_slot_until_it_finishes(self):
        first_started = threading.Event()
        first_release = threading.Event()
        second_started = threading.Event()

        def first_call():
            first_started.set()
            first_release.wait(timeout=1)

        def second_call():
            second_started.set()
            return "done"

        async def run():
            semaphore = asyncio.Semaphore(1)
            with self.assertRaises(asyncio.TimeoutError):
                await self.deep_search._run_blocking_call(
                    first_call,
                    semaphore=semaphore,
                    timeout_seconds=0.01,
                )
            self.assertTrue(first_started.is_set())
            second_task = asyncio.create_task(
                self.deep_search._run_blocking_call(
                    second_call,
                    semaphore=semaphore,
                    timeout_seconds=1,
                )
            )
            await asyncio.sleep(0.03)
            self.assertFalse(second_started.is_set())
            first_release.set()
            self.assertEqual(await second_task, "done")

        asyncio.run(run())

    def test_request_deadline_cancels_logical_retrieval(self):
        def slow_sub_queries(_query):
            time.sleep(0.08)
            return ["query"], 1

        self.deep_search._generate_sub_queries = slow_sub_queries

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(
                self.deep_search.async_retrieve(
                    "query",
                    request_timeout_seconds=0.01,
                )
            )

    def test_retrieval_cancellation_propagates(self):
        def blocking_embed(_query):
            time.sleep(0.08)
            return [0.1] * self.embedding_model.dimension

        self.embedding_model.embed_query = blocking_embed

        async def run():
            task = asyncio.create_task(
                self.deep_search._retrieve_chunks_from_vectordb(
                    "query",
                    collection_names=["test_collection"],
                    allowed_collections=["test_collection"],
                    top_k=1,
                    semaphore=asyncio.Semaphore(1),
                    timeout_seconds=1,
                )
            )
            await asyncio.sleep(0.01)
            task.cancel()
            await task

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(run())

    def test_retrieval_exception_is_not_converted_to_empty_results(self):
        self.embedding_model.embed_query = MagicMock(side_effect=RuntimeError("embedding failed"))

        async def run():
            await self.deep_search._retrieve_chunks_from_vectordb(
                "query",
                collection_names=["test_collection"],
                allowed_collections=["test_collection"],
                top_k=1,
                semaphore=asyncio.Semaphore(1),
                timeout_seconds=1,
            )

        with self.assertRaisesRegex(RuntimeError, "embedding failed"):
            asyncio.run(run())

    def test_concurrent_routing_trace_preserves_subquery_order(self):
        self.llm.chat = MagicMock(return_value=ChatResponse(content='["q1", "q2"]', total_tokens=2))

        def route(query, **_kwargs):
            time.sleep(0.04 if query == "q1" else 0.005)
            return (
                [],
                0,
                {
                    "source": query,
                    "selected": [],
                    "requested": [],
                    "rejected": [],
                    "fallback_used": False,
                },
            )

        self.deep_search._resolve_collections_for_query = route
        collector = TraceCollector("original")

        asyncio.run(
            self.deep_search.async_retrieve(
                "original",
                max_iter=1,
                trace_collector=collector,
                retrieval_concurrency=2,
            )
        )
        trace = collector.build(total_tokens=2, final_results=[])
        routing_sources = [
            event["decision"]["source"]
            for event in trace["selection_events"]
            if event["stage"] == "collection_routing"
        ]

        self.assertEqual(routing_sources, ["q1", "q2"])

    def test_generate_gap_queries(self):
        """Test the _generate_gap_queries method."""
        query = "Tell me about deep learning"
        all_sub_queries = ["What is deep learning?", "How does deep learning work?"]
        all_chunks = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Deep learning is a subset of machine learning",
                reference="test_reference",
                metadata={"a": 1},
            ),
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Deep learning uses neural networks",
                reference="test_reference",
                metadata={"a": 2},
            ),
        ]

        gap_queries, tokens = self.deep_search._generate_gap_queries(
            query, all_sub_queries, all_chunks
        )

        self.assertEqual(len(gap_queries), 1)
        self.assertEqual(gap_queries[0], "What are limitations of deep learning?")
        self.assertEqual(tokens, 10)

    def test_gap_queries_remove_previous_empty_duplicate_and_invalid_items(self):
        self.llm.chat = MagicMock(
            return_value=ChatResponse(
                content='["previous", "", 1.5, "new", "new"]',
                total_tokens=4,
            )
        )

        gap_queries, tokens = self.deep_search._generate_gap_queries(
            "Original",
            ["previous"],
            [],
        )

        self.assertEqual(gap_queries, ["new"])
        self.assertEqual(tokens, 4)
        self.assertEqual(
            self.deep_search.last_selection_decision["reason"],
            "invalid_items_filtered",
        )

    def test_retrieve(self):
        """Test the retrieve method."""
        query = "Tell me about deep learning"

        # Mock async method to run synchronously
        async def mock_async_retrieve(*args, **kwargs):
            # Create some test results
            results = [
                RetrievalResult(
                    embedding=[0.1] * 8,
                    text="Deep learning is a subset of machine learning",
                    reference="test_reference",
                    metadata={"a": 1},
                ),
                RetrievalResult(
                    embedding=[0.1] * 8,
                    text="Deep learning uses neural networks",
                    reference="test_reference",
                    metadata={"a": 2},
                ),
            ]
            # Return the results, token count, and additional info
            return (
                results,
                30,
                {"all_sub_queries": ["What is deep learning?", "How does deep learning work?"]},
            )

        # Replace the async method with our mock
        self.deep_search.async_retrieve = mock_async_retrieve

        results, tokens, metadata = self.deep_search.retrieve(query)

        # Check results
        self.assertEqual(len(results), 2)
        self.assertEqual(tokens, 30)
        self.assertIn("all_sub_queries", metadata)
        self.assertEqual(len(metadata["all_sub_queries"]), 2)

    def test_retrieve_does_not_wait_for_a_timed_out_executor_worker(self):
        async def mock_async_retrieve(*_args, **_kwargs):
            await asyncio.wait_for(asyncio.to_thread(time.sleep, 0.2), timeout=0.01)

        self.deep_search.async_retrieve = mock_async_retrieve
        started = time.perf_counter()
        with self.assertRaises(asyncio.TimeoutError):
            self.deep_search.retrieve("slow provider")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        time.sleep(0.2)

    def test_async_retrieve(self):
        """Test the async_retrieve method."""
        query = "Tell me about deep learning"

        # Create mock results
        mock_results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Deep learning is a subset of machine learning",
                reference="test_reference",
                metadata={"a": 1},
            )
        ]

        # Create a mock async_retrieve result
        mock_retrieve_result = (
            mock_results,
            20,
            {"all_sub_queries": ["What is deep learning?", "How does deep learning work?"]},
        )

        # Mock the async_retrieve method
        async def mock_async_retrieve(*args, **kwargs):
            return mock_retrieve_result

        self.deep_search.async_retrieve = mock_async_retrieve

        # Run the async method using asyncio.run
        results, tokens, metadata = asyncio.run(self.deep_search.async_retrieve(query))

        # Check results
        self.assertEqual(len(results), 1)
        self.assertEqual(tokens, 20)
        self.assertIn("all_sub_queries", metadata)

    def test_query(self):
        """Test the query method."""
        query = "Tell me about deep learning"

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

        self.deep_search.retrieve = MagicMock(
            return_value=(retrieved_results, 20, {"all_sub_queries": ["What is deep learning?"]})
        )

        answer, results, tokens = self.deep_search.query(query)

        # Check if methods were called
        self.deep_search.retrieve.assert_called_once_with(query)
        self.assertTrue(self.llm.chat_called)

        # Check results
        self.assertEqual(
            answer,
            "Deep learning is a subset of machine learning that uses neural networks with multiple layers.",
        )
        self.assertEqual(results, retrieved_results)
        self.assertEqual(tokens, 30)  # 20 from retrieve + 10 from LLM

    def test_query_no_results(self):
        """Test the query method when no results are found."""
        query = "Tell me about deep learning"

        # Mock the retrieve method to return no results
        self.deep_search.retrieve = MagicMock(
            return_value=([], 10, {"all_sub_queries": ["What is deep learning?"]})
        )

        answer, results, tokens = self.deep_search.query(query)

        # Should return a message saying no results found
        self.assertIn("No relevant information found", answer)
        self.assertEqual(results, [])
        self.assertEqual(tokens, 10)  # Only tokens from retrieve

    def test_format_chunk_texts(self):
        """Test the _format_chunk_texts method."""
        chunk_texts = ["Text 1", "Text 2", "Text 3"]

        formatted = self.deep_search._format_chunk_texts(chunk_texts)

        self.assertIn("<chunk_0>", formatted)
        self.assertIn("Text 1", formatted)
        self.assertIn("<chunk_1>", formatted)
        self.assertIn("Text 2", formatted)
        self.assertIn("<chunk_2>", formatted)
        self.assertIn("Text 3", formatted)


if __name__ == "__main__":
    import unittest

    unittest.main()
