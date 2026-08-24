from contextvars import ContextVar
from typing import List, Optional, Tuple

from deepsearcher.agent.base import RAGAgent
from deepsearcher.agent.selection import parse_one_based_index
from deepsearcher.llm.base import BaseLLM, chat_with_stage
from deepsearcher.retrieval_mode import resolve_retrieval_mode
from deepsearcher.utils import log
from deepsearcher.vector_db import RetrievalResult

RAG_ROUTER_PROMPT = """Given a list of agent indexes and corresponding descriptions, each agent has a specific function. 
Given a query, select only one agent that best matches the agent handling the query, and return the index without any other information.

## Question
{query}

## Agent Indexes and Descriptions
{description_str}

Return exactly one integer from the listed agent indexes. Do not return explanations,
multiple numbers, markdown, or any other text:
"""


class RAGRouter(RAGAgent):
    """
    Routes queries to the most appropriate RAG agent implementation.

    This class analyzes the content and requirements of a query and determines
    which RAG agent implementation is best suited to handle it.
    """

    def __init__(
        self,
        llm: BaseLLM,
        rag_agents: List[RAGAgent],
        agent_descriptions: Optional[List[str]] = None,
        fallback_agent_index: int = 0,
    ):
        """
        Initialize the RAGRouter.

        Args:
            llm: The language model to use for analyzing queries.
            rag_agents: A list of RAGAgent instances.
            agent_descriptions (list, optional): A list of descriptions for each agent.
        """
        self.llm = llm
        self.rag_agents = rag_agents
        self.agent_descriptions = agent_descriptions
        if not self.rag_agents:
            raise ValueError("RAGRouter requires at least one agent.")
        if not self.agent_descriptions:
            try:
                self.agent_descriptions = [
                    agent.__class__.__description__ for agent in self.rag_agents
                ]
            except Exception:
                raise AttributeError(
                    "Please provide agent descriptions or set __description__ attribute for each agent class."
                )
        if len(self.agent_descriptions) != len(self.rag_agents):
            raise ValueError("Agent descriptions must match the number of agents.")
        if not 0 <= fallback_agent_index < len(self.rag_agents):
            raise ValueError("fallback_agent_index is outside the available agents.")
        self.fallback_agent_index = fallback_agent_index
        self._last_route_decision = ContextVar(
            f"rag_router_last_route_decision_{id(self)}",
            default=None,
        )
        self._trace_collector = ContextVar(
            f"rag_router_trace_collector_{id(self)}",
            default=None,
        )

    @property
    def last_route_decision(self) -> Optional[dict]:
        """Last routing decision scoped to the current request context."""
        decision = self._last_route_decision.get()
        return dict(decision) if isinstance(decision, dict) else None

    def _record_route_decision(self, decision: dict) -> dict:
        snapshot = dict(decision)
        self._last_route_decision.set(snapshot)
        return dict(snapshot)

    def _route(self, query: str) -> Tuple[RAGAgent, int]:
        description_str = "\n".join(
            [f"[{i + 1}]: {description}" for i, description in enumerate(self.agent_descriptions)]
        )
        prompt = RAG_ROUTER_PROMPT.format(query=query, description_str=description_str)
        chat_response = chat_with_stage(
            self.llm,
            [{"role": "user", "content": prompt}],
            stage="agent_router",
            max_tokens=32,
            trace_collector=self._trace_collector.get(),
        )
        decision = parse_one_based_index(
            self.llm.remove_think(chat_response.content),
            upper_bound=len(self.rag_agents),
            fallback_index=self.fallback_agent_index,
        )
        self._record_route_decision(decision.as_trace())
        selected_agent_index = decision.values[0]
        if decision.fallback_used:
            log.warning(f"RAGRouter used the configured fallback agent: {decision.reason}.")

        selected_agent = self.rag_agents[selected_agent_index]
        log.color_print(f"<route> Selected agent [{selected_agent.__class__.__name__}] </route>\n")
        return self.rag_agents[selected_agent_index], chat_response.total_tokens

    def _select_agent(
        self,
        query: str,
        *,
        use_web_search: bool = False,
        retrieval_mode: str | None = None,
    ) -> Tuple[RAGAgent, int]:
        effective_mode = resolve_retrieval_mode(
            retrieval_mode,
            use_web_search=use_web_search,
        )
        if effective_mode in {"web", "hybrid"}:
            for index, agent in enumerate(self.rag_agents):
                if getattr(agent, "supports_web_search", False):
                    self._record_route_decision(
                        {
                            "source": "request_capability",
                            "requested": (
                                ["web_search"]
                                if retrieval_mode is None
                                else ["web_search", effective_mode]
                            ),
                            "selected": [index],
                            "rejected": [],
                            "fallback_used": False,
                            "reason": "web_search_requested",
                        }
                    )
                    log.color_print(
                        f"<route> Selected Web-capable agent [{agent.__class__.__name__}] </route>\n"
                    )
                    return agent, 0
        return self._route(query)

    def retrieve(self, query: str, **kwargs) -> Tuple[List[RetrievalResult], int, dict]:
        trace_collector = kwargs.get("trace_collector")
        token = self._trace_collector.set(trace_collector)
        try:
            agent, n_token_router = self._select_agent(
                query,
                use_web_search=bool(kwargs.get("use_web_search", False)),
                retrieval_mode=kwargs.get("retrieval_mode"),
            )
        finally:
            self._trace_collector.reset(token)
        if trace_collector is not None:
            trace_collector.select_agent(
                agent.__class__.__name__,
                n_token_router,
                decision=self.last_route_decision,
            )
        retrieved_results, n_token_retrieval, metadata = agent.retrieve(query, **kwargs)
        return retrieved_results, n_token_router + n_token_retrieval, metadata

    def query(self, query: str, **kwargs) -> Tuple[str, List[RetrievalResult], int]:
        trace_collector = kwargs.get("trace_collector")
        token = self._trace_collector.set(trace_collector)
        try:
            agent, n_token_router = self._select_agent(
                query,
                use_web_search=bool(kwargs.get("use_web_search", False)),
                retrieval_mode=kwargs.get("retrieval_mode"),
            )
        finally:
            self._trace_collector.reset(token)
        if trace_collector is not None:
            trace_collector.select_agent(
                agent.__class__.__name__,
                n_token_router,
                decision=self.last_route_decision,
            )
        answer, retrieved_results, n_token_retrieval = agent.query(query, **kwargs)
        return answer, retrieved_results, n_token_router + n_token_retrieval
