from unittest.mock import MagicMock

from deepsearcher.agent import ChainOfRAG, NaiveRAG
from deepsearcher.agent.rag_router import RAGRouter
from deepsearcher.llm.base import ChatResponse
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult
from tests.agent.test_base import BaseAgentTest


class TestRAGRouter(BaseAgentTest):
    """Test class for RAGRouter agent."""

    def setUp(self):
        """Set up test fixtures for RAGRouter tests."""
        super().setUp()

        # Create mock agent instances
        self.naive_rag = MagicMock(spec=NaiveRAG)
        self.chain_of_rag = MagicMock(spec=ChainOfRAG)

        # Create the RAGRouter with the mock agents
        self.rag_router = RAGRouter(
            llm=self.llm,
            rag_agents=[self.naive_rag, self.chain_of_rag],
            agent_descriptions=[
                "This agent is suitable for simple factual queries",
                "This agent is suitable for complex multi-hop questions",
            ],
        )

    def test_init(self):
        """Test the initialization of RAGRouter."""
        self.assertEqual(self.rag_router.llm, self.llm)
        self.assertEqual(len(self.rag_router.rag_agents), 2)
        self.assertEqual(len(self.rag_router.agent_descriptions), 2)
        self.assertEqual(
            self.rag_router.agent_descriptions[0],
            "This agent is suitable for simple factual queries",
        )
        self.assertEqual(
            self.rag_router.agent_descriptions[1],
            "This agent is suitable for complex multi-hop questions",
        )

    def test_route(self):
        """Test the _route method."""
        query = "What is the capital of France?"

        # Directly mock the chat method to return a numeric response
        self.llm.chat = MagicMock(return_value=ChatResponse(content="1", total_tokens=10))

        agent, tokens = self.rag_router._route(query)

        # Should select the first agent based on our mock response
        self.assertEqual(agent, self.naive_rag)
        self.assertEqual(tokens, 10)
        self.assertTrue(self.llm.chat.called)

    def test_route_with_non_numeric_response_uses_configured_fallback(self):
        """Explanations are not mined for digits and use the safe baseline."""
        query = "What is the history of deep learning?"

        # Mock the LLM to return a response with a trailing digit
        self.llm.chat = MagicMock(
            return_value=ChatResponse(content="I recommend agent 2", total_tokens=10)
        )

        agent, tokens = self.rag_router._route(query)

        self.assertEqual(agent, self.naive_rag)
        self.assertEqual(tokens, 10)
        self.assertTrue(self.rag_router.last_route_decision["fallback_used"])
        self.assertEqual(
            self.rag_router.last_route_decision["reason"],
            "invalid_index_format",
        )

    def test_retrieve(self):
        """Test the retrieve method."""
        query = "What is the capital of France?"

        # Mock the _route method to return the first agent
        mock_retrieved_results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Paris is the capital of France",
                reference="test_reference",
                metadata={"a": 1},
            )
        ]
        self.rag_router._route = MagicMock(return_value=(self.naive_rag, 5))
        self.naive_rag.retrieve = MagicMock(
            return_value=(mock_retrieved_results, 10, {"some": "metadata"})
        )

        results, tokens, metadata = self.rag_router.retrieve(query)

        # Check if methods were called
        self.rag_router._route.assert_called_once_with(query)
        self.naive_rag.retrieve.assert_called_once_with(query)

        # Check results
        self.assertEqual(results, mock_retrieved_results)
        self.assertEqual(tokens, 15)  # 5 from route + 10 from retrieve
        self.assertEqual(metadata, {"some": "metadata"})

    def test_query(self):
        """Test the query method."""
        query = "What is the capital of France?"

        # Mock the _route method to return the first agent
        mock_retrieved_results = [
            RetrievalResult(
                embedding=[0.1] * 8,
                text="Paris is the capital of France",
                reference="test_reference",
                metadata={"a": 1},
            )
        ]
        self.rag_router._route = MagicMock(return_value=(self.naive_rag, 5))
        self.naive_rag.query = MagicMock(
            return_value=("Paris is the capital of France", mock_retrieved_results, 10)
        )

        answer, results, tokens = self.rag_router.query(query)

        # Check if methods were called
        self.rag_router._route.assert_called_once_with(query)
        self.naive_rag.query.assert_called_once_with(query)

        # Check results
        self.assertEqual(answer, "Paris is the capital of France")
        self.assertEqual(results, mock_retrieved_results)
        self.assertEqual(tokens, 15)  # 5 from route + 10 from query

    def test_query_records_selected_agent_in_trace(self):
        query = "What is the capital of France?"
        collector = TraceCollector(query)
        self.rag_router._route = MagicMock(return_value=(self.chain_of_rag, 5))
        self.chain_of_rag.query = MagicMock(return_value=("answer", [], 10))

        self.rag_router.query(query, trace_collector=collector)
        trace = collector.build(total_tokens=15, final_results=[])

        self.assertEqual(trace["agent"], "ChainOfRAG")
        self.assertEqual(trace["summary"]["routing_tokens"], 5)
        self.chain_of_rag.query.assert_called_once_with(query, trace_collector=collector)

    def test_query_records_invalid_route_fallback_in_trace(self):
        query = "What is the capital of France?"
        collector = TraceCollector(query)
        self.llm.chat = MagicMock(return_value=ChatResponse(content="agent 9", total_tokens=4))
        self.naive_rag.query = MagicMock(return_value=("answer", [], 6))

        self.rag_router.query(query, trace_collector=collector)
        trace = collector.build(total_tokens=10, final_results=[])

        self.assertEqual(trace["agent"], "NaiveRAG")
        self.assertTrue(trace["routing"]["fallback_used"])
        self.assertEqual(
            trace["routing"]["reason"],
            "invalid_index_format",
        )

    def test_invalid_route_outputs_have_deterministic_fallback(self):
        for content in ("", "No digits", "0", "-1", "3", "1 2", "agent 2"):
            with self.subTest(content=content):
                self.llm.chat = MagicMock(
                    return_value=ChatResponse(content=content, total_tokens=2)
                )
                agent, tokens = self.rag_router._route("question")
                self.assertEqual(agent, self.naive_rag)
                self.assertEqual(tokens, 2)
                self.assertTrue(self.rag_router.last_route_decision["fallback_used"])

    def test_valid_route_output_selects_the_requested_agent(self):
        self.llm.chat = MagicMock(return_value=ChatResponse(content="2", total_tokens=2))

        agent, _ = self.rag_router._route("question")

        self.assertEqual(agent, self.chain_of_rag)
        self.assertFalse(self.rag_router.last_route_decision["fallback_used"])

    def test_web_search_request_selects_capable_agent_without_router_llm(self):
        web_agent = MagicMock()
        web_agent.supports_web_search = True
        web_agent.__class__.__name__ = "DeepSearch"
        web_agent.query.return_value = ("web answer", [], 7)
        router = RAGRouter(
            llm=self.llm,
            rag_agents=[self.chain_of_rag, web_agent],
            agent_descriptions=["knowledge only", "web capable"],
        )
        self.llm.chat = MagicMock()
        collector = TraceCollector("latest news")

        answer, results, tokens = router.query(
            "latest news",
            use_web_search=True,
            trace_collector=collector,
        )

        assert (answer, results, tokens) == ("web answer", [], 7)
        self.llm.chat.assert_not_called()
        web_agent.query.assert_called_once_with(
            "latest news",
            use_web_search=True,
            trace_collector=collector,
        )
        trace = collector.build(total_tokens=7, final_results=[])
        assert trace["agent"] == "DeepSearch"
        assert trace["routing"]["source"] == "request_capability"
        assert trace["summary"]["routing_tokens"] == 0

    def test_auto_description_fallback(self):
        """Test that RAGRouter falls back to __description__ when no descriptions provided."""

        # Create classes with __description__ attribute
        class MockAgent1:
            __description__ = "Auto description 1"

        class MockAgent2:
            __description__ = "Auto description 2"

        # Create instances of these classes
        mock_agent1 = MagicMock(spec=MockAgent1)
        mock_agent1.__class__ = MockAgent1

        mock_agent2 = MagicMock(spec=MockAgent2)
        mock_agent2.__class__ = MockAgent2

        # Create the RAGRouter without explicit descriptions
        router = RAGRouter(llm=self.llm, rag_agents=[mock_agent1, mock_agent2])

        # Check that descriptions were pulled from the class attributes
        self.assertEqual(len(router.agent_descriptions), 2)
        self.assertEqual(router.agent_descriptions[0], "Auto description 1")
        self.assertEqual(router.agent_descriptions[1], "Auto description 2")


if __name__ == "__main__":
    import unittest

    unittest.main()
