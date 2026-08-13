from typing import List, Tuple

from deepsearcher.agent.base import RAGAgent, describe_class
from deepsearcher.agent.collection_router import CollectionRouter
from deepsearcher.collection_manifest import EmbeddingProfile
from deepsearcher.embedding.base import BaseEmbedding
from deepsearcher.grounding import GROUNDING_PROMPT, format_grounding_evidence
from deepsearcher.llm.base import BaseLLM, chat_with_stage
from deepsearcher.query_planner import (
    QUERY_PLAN_ORIGINAL_ANCHORS,
    QUERY_PLAN_RRF_K,
    QUERY_PLAN_TOPIC_ANCHORS,
    QueryPlan,
    merge_ranked_results,
    plan_explicit_queries,
    plan_queries,
)
from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, RetrievalResult, deduplicate_results

SUMMARY_PROMPT = """You are an AI content analysis expert. Generate a specific and detailed answer based on the retrieved evidence.

Cover every distinct comparison dimension or process stage explicitly requested by
the question when the evidence supports it. Do not omit supported trade-offs such as
quality, latency, storage, resources, migration, rollback, or failure behavior.

Original Query: {query}

Evidence:
{mini_chunk_str}

{grounding_instructions}
"""


@describe_class(
    "This agent is suitable for direct factual questions that can be answered from a small set "
    "of retrieved evidence without iterative decomposition."
)
class NaiveRAG(RAGAgent):
    """
    Naive Retrieval-Augmented Generation agent implementation.

    This agent implements a straightforward RAG approach, retrieving relevant
    documents and generating answers without complex processing or refinement steps.
    """

    def __init__(
        self,
        llm: BaseLLM,
        embedding_model: BaseEmbedding,
        vector_db: BaseVectorDB,
        top_k: int = 10,
        route_collection: bool = True,
        text_window_splitter: bool = True,
        query_decomposition_enabled: bool = False,
        **kwargs,
    ):
        """
        Initialize the NaiveRAG agent.

        Args:
            llm: The language model to use for generating answers.
            embedding_model: The embedding model to use for query embedding.
            vector_db: The vector database to search for relevant documents.
            **kwargs: Additional keyword arguments for customization.
        """
        self.llm = llm
        self.embedding_model = embedding_model
        self.embedding_profile = EmbeddingProfile.from_embedding(embedding_model)
        self.vector_db = vector_db
        self.top_k = top_k
        self.route_collection = route_collection
        if self.route_collection:
            self.collection_router = CollectionRouter(
                llm=self.llm, vector_db=self.vector_db, dim=embedding_model.dimension
            )
        self.text_window_splitter = text_window_splitter
        self.query_decomposition_enabled = bool(query_decomposition_enabled)

    def retrieve(self, query: str, **kwargs) -> Tuple[List[RetrievalResult], int, dict]:
        """
        Retrieve relevant documents from the knowledge base for the given query.

        This method performs a basic search through the vector database to find
        documents relevant to the query.

        Args:
            query (str): The query to search for.
            **kwargs: Additional keyword arguments for customizing the retrieval.

        Returns:
            Tuple[List[RetrievalResult], int, dict]: A tuple containing:
                - A list of retrieved document results
                - The token usage for the retrieval operation
                - Additional information about the retrieval process
        """
        consume_tokens = 0
        trace_collector = kwargs.get("trace_collector")
        if trace_collector is not None:
            trace_collector.start_iteration(1)
        explicit_collections = kwargs.get("collection_names")
        allowed_collections = kwargs.get("allowed_collections")
        if explicit_collections is not None:
            selected_collections = self.collection_router.resolve_explicit(
                explicit_collections,
                dim=self.embedding_model.dimension,
                allowed_collections=allowed_collections,
            )
            n_token_route = 0
        elif self.route_collection:
            selected_collections, n_token_route = self.collection_router.invoke(
                query=query,
                dim=self.embedding_model.dimension,
                allowed_collections=allowed_collections,
                trace_collector=trace_collector,
                iteration=1,
            )
        else:
            selected_collections = self.collection_router.resolve_all(
                dim=self.embedding_model.dimension,
                allowed_collections=allowed_collections,
            )
            n_token_route = 0
        if trace_collector is not None:
            trace_collector.record_collections(
                selected_collections,
                n_token_route,
                decision=self.collection_router.last_decision,
            )
            trace_collector.record_selection_event(
                "collection_routing",
                self.collection_router.last_decision,
            )
        consume_tokens += n_token_route
        self.vector_db.assert_collections_compatible(
            selected_collections,
            self.embedding_profile,
        )
        explicit_queries = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in (kwargs.get("retrieval_queries") or ())
                if str(item).strip()
            )
        )
        if explicit_queries:
            query_plan = plan_explicit_queries(query, explicit_queries)
        elif self.query_decomposition_enabled:
            query_plan = plan_queries(self.llm, query)
            consume_tokens += query_plan.token_usage
        else:
            query_plan = QueryPlan((query,), False, False, "disabled")
        result_groups = []
        top_k = int(kwargs.get("top_k", self.top_k))
        for planned_query in query_plan.queries:
            query_results = []
            for collection in selected_collections:
                retrieval_res = self.vector_db.search_data(
                    collection=collection,
                    vector=self.embedding_model.embed_query(planned_query),
                    top_k=top_k,
                    query_text=planned_query,
                )
                query_results.extend(retrieval_res)
            result_groups.append(deduplicate_results(query_results))
        all_retrieved_results = merge_ranked_results(
            result_groups,
            limit=top_k,
            anchor_count=QUERY_PLAN_ORIGINAL_ANCHORS if query_plan.decomposed else 0,
            per_group_anchor_count=QUERY_PLAN_TOPIC_ANCHORS if query_plan.decomposed else 0,
            cross_query_rrf_k=QUERY_PLAN_RRF_K if query_plan.decomposed else None,
            identity_policy="source_chunk",
        )
        if trace_collector is not None:
            trace_collector.record_documents_retrieved(all_retrieved_results)
            trace_collector.record_documents_supported(all_retrieved_results)
            trace_collector.record_reflection(bool(all_retrieved_results))
        return (
            all_retrieved_results,
            consume_tokens,
            {
                "collections": selected_collections,
                "query_plan": {
                    "queries": list(query_plan.queries),
                    "decomposed": query_plan.decomposed,
                    "fallback_used": query_plan.fallback_used,
                    "reason": query_plan.reason,
                    "token_usage": query_plan.token_usage,
                },
            },
        )

    def query(self, query: str, **kwargs) -> Tuple[str, List[RetrievalResult], int]:
        """
        Query the agent and generate an answer based on retrieved documents.

        This method retrieves relevant documents and uses the language model
        to generate a simple answer to the query.

        Args:
            query (str): The query to answer.
            **kwargs: Additional keyword arguments for customizing the query process.

        Returns:
            Tuple[str, List[RetrievalResult], int]: A tuple containing:
                - The generated answer
                - A list of retrieved document results
                - The total token usage
        """
        all_retrieved_results, n_token_retrieval, _ = self.retrieve(query, **kwargs)
        trace_collector = kwargs.get("trace_collector")
        mini_chunk_str = format_grounding_evidence(
            all_retrieved_results,
            use_wider_text=self.text_window_splitter,
            trace_collector=trace_collector,
        )
        summary_prompt = SUMMARY_PROMPT.format(
            query=query,
            mini_chunk_str=mini_chunk_str,
            grounding_instructions=GROUNDING_PROMPT,
        )
        char_response = chat_with_stage(
            self.llm,
            [{"role": "user", "content": summary_prompt}],
            stage="final_answer",
            max_tokens=4096,
            trace_collector=trace_collector,
            input_evidence_count=len(all_retrieved_results),
            input_evidence_tokens=self.llm.estimate_tokens(
                [{"role": "user", "content": mini_chunk_str}]
            ),
        )
        final_answer = char_response.content
        if trace_collector is not None:
            trace_collector.record_final_answer(char_response.total_tokens)
        log.color_print(
            f"<complete> Final answer generated; tokens={char_response.total_tokens} </complete>\n"
        )
        return final_answer, all_retrieved_results, n_token_retrieval + char_response.total_tokens
