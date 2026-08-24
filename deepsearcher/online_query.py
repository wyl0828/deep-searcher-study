from typing import List, Optional, Tuple

# from deepsearcher.configuration import vector_db, embedding_model, llm
from deepsearcher import configuration
from deepsearcher.retrieval_mode import resolve_retrieval_mode
from deepsearcher.trace import TraceCollector
from deepsearcher.vector_db.base import RetrievalResult


def query(
    original_query: str,
    max_iter: int = 2,
    collection_names: Optional[List[str]] = None,
    use_web_search: bool = False,
    *,
    searcher=None,
    initial_tokens: int = 0,
    enforce_trust: bool = False,
    entailment_checker=None,
    provenance=None,
    provenance_resolver=None,
    evidence_provenance_resolver=None,
    temporal_timezone: str = "UTC",
    reference_time=None,
    retrieval_queries: Optional[List[str] | Tuple[str, ...]] = None,
    token_control: Optional[dict] = None,
    retrieval_mode: str | None = None,
    risk_profile: Optional[dict] = None,
) -> Tuple[str, List[RetrievalResult], int]:
    """
    Query the knowledge base with a question and get an answer.

    This function uses the default searcher to query the knowledge base and generate
    an answer based on the retrieved information.

    Args:
        original_query: The question or query to search for.
        max_iter: Maximum number of iterations for the search process.

    Returns:
        A tuple containing:
            - The generated answer as a string
            - A list of retrieval results that were used to generate the answer
            - The number of tokens consumed during the process
    """
    default_searcher = searcher or configuration.default_searcher
    effective_retrieval_mode = resolve_retrieval_mode(
        retrieval_mode,
        use_web_search=use_web_search,
    )
    collector = (
        TraceCollector(
            original_query,
            entailment_checker=entailment_checker,
            provenance=provenance,
            provenance_resolver=provenance_resolver,
            evidence_provenance_resolver=evidence_provenance_resolver,
            temporal_timezone=temporal_timezone,
            reference_time=reference_time,
            token_control=token_control,
            retrieval_mode=effective_retrieval_mode,
            risk_profile=risk_profile,
        )
        if enforce_trust
        else None
    )
    kwargs = {"max_iter": max_iter}
    if collector is not None:
        kwargs["trace_collector"] = collector
    if collection_names is not None:
        kwargs["collection_names"] = list(collection_names)
    if use_web_search:
        kwargs["use_web_search"] = True
    if retrieval_mode is not None:
        kwargs["retrieval_mode"] = effective_retrieval_mode
    if retrieval_queries:
        kwargs["retrieval_queries"] = tuple(retrieval_queries)
    answer, results, consume_tokens = default_searcher.query(original_query, **kwargs)
    trust_tokens = 0
    if collector is not None:
        answer = collector.finalize_answer(answer, results, enforce_policy=True)
        trust_tokens = collector.trust_tokens
    return (
        answer,
        results,
        int(consume_tokens or 0) + max(int(initial_tokens or 0), 0) + trust_tokens,
    )


def query_with_trace(
    original_query: str,
    max_iter: int = 2,
    collection_names: Optional[List[str]] = None,
    use_web_search: bool = False,
    *,
    searcher=None,
    trace_collector=None,
    initial_tokens: int = 0,
    entailment_checker=None,
    provenance=None,
    provenance_resolver=None,
    evidence_provenance_resolver=None,
    temporal_timezone: str = "UTC",
    reference_time=None,
    retrieval_queries: Optional[List[str] | Tuple[str, ...]] = None,
    token_control: Optional[dict] = None,
    retrieval_mode: str | None = None,
    risk_profile: Optional[dict] = None,
):
    """Query the knowledge base and return an additional structured execution trace."""
    effective_retrieval_mode = resolve_retrieval_mode(
        retrieval_mode,
        use_web_search=use_web_search,
    )
    collector = trace_collector or TraceCollector(
        original_query,
        entailment_checker=entailment_checker,
        provenance=provenance,
        provenance_resolver=provenance_resolver,
        evidence_provenance_resolver=evidence_provenance_resolver,
        temporal_timezone=temporal_timezone,
        reference_time=reference_time,
        token_control=token_control,
        retrieval_mode=effective_retrieval_mode,
        risk_profile=risk_profile,
    )
    if getattr(collector, "retrieval_mode", None) is None:
        try:
            collector.retrieval_mode = effective_retrieval_mode
        except Exception:
            pass
    default_searcher = searcher or configuration.default_searcher
    kwargs = {"max_iter": max_iter, "trace_collector": collector}
    if collection_names is not None:
        kwargs["collection_names"] = list(collection_names)
    if use_web_search:
        kwargs["use_web_search"] = True
    if retrieval_mode is not None:
        kwargs["retrieval_mode"] = effective_retrieval_mode
    if retrieval_queries:
        kwargs["retrieval_queries"] = tuple(retrieval_queries)
    answer, results, agent_tokens = default_searcher.query(original_query, **kwargs)
    consume_tokens = int(agent_tokens or 0) + max(int(initial_tokens or 0), 0)
    answer = collector.finalize_answer(answer, results, enforce_policy=True)
    consume_tokens += collector.trust_tokens
    return (
        answer,
        results,
        consume_tokens,
        collector.build(
            total_tokens=consume_tokens,
            final_results=results,
            answer=answer,
        ),
    )


def retrieve(
    original_query: str,
    max_iter: int = 2,
    collection_names: Optional[List[str]] = None,
    use_web_search: bool = False,
    retrieval_mode: str | None = None,
) -> Tuple[List[RetrievalResult], List[str], int]:
    """
    Retrieve relevant information from the knowledge base without generating an answer.

    This function uses the default searcher to retrieve information from the knowledge base
    that is relevant to the query.

    Args:
        original_query: The question or query to search for.
        max_iter: Maximum number of iterations for the search process.

    Returns:
        A tuple containing:
            - A list of retrieval results
            - An empty list (placeholder for future use)
            - The number of tokens consumed during the process
    """
    default_searcher = configuration.default_searcher
    kwargs = {"max_iter": max_iter}
    if collection_names is not None:
        kwargs["collection_names"] = list(collection_names)
    if use_web_search:
        kwargs["use_web_search"] = True
    if retrieval_mode is not None:
        kwargs["retrieval_mode"] = resolve_retrieval_mode(
            retrieval_mode,
            use_web_search=use_web_search,
        )
    retrieved_results, consume_tokens, metadata = default_searcher.retrieve(
        original_query, **kwargs
    )
    return retrieved_results, [], consume_tokens


def naive_retrieve(query: str, collection: str = None, top_k=10) -> List[RetrievalResult]:
    """
    Perform a simple retrieval from the knowledge base using the naive RAG approach.

    This function uses the naive RAG agent to retrieve information from the knowledge base
    without any advanced techniques like iterative refinement.

    Args:
        query: The question or query to search for.
        collection: The name of the collection to search in. If None, searches in all collections.
        top_k: The maximum number of results to return.

    Returns:
        A list of retrieval results.
    """
    naive_rag = configuration.naive_rag
    kwargs = {"top_k": top_k}
    if collection is not None:
        kwargs["collection_names"] = [collection]
    all_retrieved_results, consume_tokens, _ = naive_rag.retrieve(query, **kwargs)
    return all_retrieved_results


def naive_rag_query(
    query: str, collection: str = None, top_k=10
) -> Tuple[str, List[RetrievalResult]]:
    """
    Query the knowledge base using the naive RAG approach and get an answer.

    This function uses the naive RAG agent to query the knowledge base and generate
    an answer based on the retrieved information, without any advanced techniques.

    Args:
        query: The question or query to search for.
        collection: The name of the collection to search in. If None, searches in all collections.
        top_k: The maximum number of results to consider.

    Returns:
        A tuple containing:
            - The generated answer as a string
            - A list of retrieval results that were used to generate the answer
    """
    naive_rag = configuration.naive_rag
    kwargs = {"top_k": top_k}
    if collection is not None:
        kwargs["collection_names"] = [collection]
    answer, retrieved_results, consume_tokens = naive_rag.query(query, **kwargs)
    return answer, retrieved_results
