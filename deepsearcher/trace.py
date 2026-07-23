"""Structured, request-scoped tracing for DeepSearcher queries."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit, urlunsplit

from deepsearcher.vector_db.base import RetrievalResult


class TraceCollector:
    """Collect explicit Agent events without parsing logs or exposing hidden reasoning."""

    VERSION = 1
    MAX_VISIBLE_DOCUMENTS = 5
    MAX_DOCUMENT_TEXT = 600

    def __init__(self, original_query: str):
        self.original_query = original_query
        self.agent: Optional[str] = None
        self.routing_tokens = 0
        self.final_answer_tokens = 0
        self._iterations: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None

    def select_agent(self, agent: str, token_usage: int = 0) -> None:
        self.agent = agent
        self.routing_tokens = int(token_usage or 0)

    def start_iteration(self, index: int) -> None:
        iteration = {
            "index": int(index),
            "subquery": None,
            "collections": [],
            "_retrieved_results": [],
            "_supported_result_ids": set(),
            "intermediate_answer": None,
            "has_enough_information": None,
            "token_usage": {
                "subquery": 0,
                "collection_routing": 0,
                "retrieval_answer": 0,
                "support_filter": 0,
                "reflection": 0,
            },
        }
        self._iterations.append(iteration)
        self._current = iteration

    def record_subquery(self, subquery: str, token_usage: int = 0) -> None:
        if not self._current:
            return
        self._current["subquery"] = subquery
        self._current["token_usage"]["subquery"] = int(token_usage or 0)

    def record_collections(self, collections: Iterable[str], token_usage: int = 0) -> None:
        if not self._current:
            return
        self._current["collections"] = list(collections)
        self._current["token_usage"]["collection_routing"] = int(token_usage or 0)

    def record_documents_retrieved(self, results: Iterable[RetrievalResult]) -> None:
        if not self._current:
            return
        self._current["_retrieved_results"] = list(results)

    def record_intermediate_answer(self, answer: str, token_usage: int = 0) -> None:
        if not self._current:
            return
        self._current["intermediate_answer"] = answer
        self._current["token_usage"]["retrieval_answer"] = int(token_usage or 0)

    def record_documents_supported(
        self, results: Iterable[RetrievalResult], token_usage: int = 0
    ) -> None:
        if not self._current:
            return
        self._current["_supported_result_ids"] = {id(result) for result in results}
        self._current["token_usage"]["support_filter"] = int(token_usage or 0)

    def record_reflection(self, has_enough_information: bool, token_usage: int = 0) -> None:
        if not self._current:
            return
        self._current["has_enough_information"] = bool(has_enough_information)
        self._current["token_usage"]["reflection"] = int(token_usage or 0)

    def record_final_answer(self, token_usage: int = 0) -> None:
        self.final_answer_tokens = int(token_usage or 0)

    def build(
        self,
        total_tokens: int,
        final_results: Iterable[RetrievalResult],
        final_answer_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        iterations = [self._serialize_iteration(iteration) for iteration in self._iterations]
        final_results_list = list(final_results)
        return {
            "version": self.VERSION,
            "agent": self.agent,
            "original_query": self.original_query,
            "iterations": iterations,
            "summary": {
                "iteration_count": len(iterations),
                "supported_document_count": len(final_results_list),
                "final_answer_tokens": (
                    self.final_answer_tokens
                    if final_answer_tokens is None
                    else int(final_answer_tokens or 0)
                ),
                "routing_tokens": self.routing_tokens,
                "total_tokens": int(total_tokens or 0),
            },
        }

    def _serialize_iteration(self, iteration: Dict[str, Any]) -> Dict[str, Any]:
        raw_results = iteration["_retrieved_results"]
        supported_ids = iteration["_supported_result_ids"]
        documents = [
            self._serialize_document(result, id(result) in supported_ids)
            for result in raw_results[: self.MAX_VISIBLE_DOCUMENTS]
        ]
        token_usage = dict(iteration["token_usage"])
        token_usage["total"] = sum(token_usage.values())
        return {
            "index": iteration["index"],
            "subquery": iteration["subquery"],
            "collections": list(iteration["collections"]),
            "retrieved_documents": documents,
            "retrieved_count": len(raw_results),
            "intermediate_answer": iteration["intermediate_answer"],
            "has_enough_information": iteration["has_enough_information"],
            "token_usage": token_usage,
        }

    def _serialize_document(self, result: RetrievalResult, supported: bool) -> Dict[str, Any]:
        text = str(result.text or "")[: self.MAX_DOCUMENT_TEXT]
        reference = self._safe_reference(result.reference)
        try:
            score = float(result.score) if result.score is not None else None
        except (TypeError, ValueError):
            score = None
        return {
            "text": text,
            "reference": reference,
            "score": score,
            "supported": supported,
        }

    @staticmethod
    def _safe_reference(reference: Any) -> Optional[str]:
        if reference is None:
            return None
        value = str(reference)
        if "://" in value:
            parsed = urlsplit(value)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
        return value.replace("\\", "/").rsplit("/", 1)[-1]
