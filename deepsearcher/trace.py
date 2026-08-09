"""Structured, request-scoped tracing for DeepSearcher queries."""

from __future__ import annotations

import math
import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

from deepsearcher.grounding import MAX_GROUNDING_EVIDENCE_TEXT, build_grounding
from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.web_search.tavily import canonical_public_url


class QueryCancelled(RuntimeError):
    """Cooperative cancellation raised at safe query stage boundaries."""

    code = "QUERY_CANCELLED"
    safe_message = "The query was cancelled."


def redact_sensitive_text(value: Any, *, max_length: int) -> Optional[str]:
    """Redact common credentials, PII and absolute local paths from visible text."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    text = re.sub(
        r"(?i)\b(api[_-]?key|access[_-]?token|token|password|secret)"
        r"\b\s*[:=]\s*([^\s,;]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", text)
    text = re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^<>:\"|?*\s]+[\\/]?)+",
        "[LOCAL_PATH]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])/(?:Users|home|tmp|var/tmp)/[^\s,;]+",
        "[LOCAL_PATH]",
        text,
    )
    text = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED_EMAIL]",
        text,
        flags=re.IGNORECASE,
    )
    return text[:max_length] or None


class TraceCollector:
    """Collect explicit Agent events without parsing logs or exposing hidden reasoning."""

    VERSION = 4
    EVENT_VERSION = 1
    MAX_VISIBLE_DOCUMENTS = 5
    MAX_DOCUMENT_TEXT = 600

    def __init__(
        self,
        original_query: str,
        *,
        event_callback: Callable[[Dict[str, Any]], None] | None = None,
        cancellation_event: threading.Event | None = None,
        request_id: str | None = None,
    ):
        del original_query
        self.agent: Optional[str] = None
        self.routing_decision: Optional[Dict[str, Any]] = None
        self.routing_tokens = 0
        self.final_answer_tokens = 0
        self._selection_events: List[Dict[str, Any]] = []
        self.contextualization: Optional[Dict[str, Any]] = None
        self._grounding_evidence_text: Dict[int, str] = {}
        self._iterations: List[Dict[str, Any]] = []
        self._current: Optional[Dict[str, Any]] = None
        self._event_callback = event_callback
        self._cancellation_event = cancellation_event
        self.request_id = request_id or uuid4().hex
        self._event_sequence = 0
        self._event_lock = threading.Lock()

    def raise_if_cancelled(self) -> None:
        if self._cancellation_event is not None and self._cancellation_event.is_set():
            raise QueryCancelled(QueryCancelled.safe_message)

    def emit_event(
        self,
        event: str,
        data: Dict[str, Any],
        *,
        check_cancelled: bool = True,
    ) -> Dict[str, Any]:
        if check_cancelled:
            self.raise_if_cancelled()
        with self._event_lock:
            self._event_sequence += 1
            envelope = {
                "version": self.EVENT_VERSION,
                "request_id": self.request_id,
                "sequence": self._event_sequence,
                "event": event,
                "data": data,
            }
        if self._event_callback is not None:
            self._event_callback(envelope)
        return envelope

    def emit_started(self) -> None:
        self.emit_event("started", {"stage": "query_started"})

    def record_contextualization(
        self,
        *,
        depends_on_history: bool,
        history_turn_count: int,
        fallback_used: bool,
        reason: str,
        token_usage: int = 0,
    ) -> None:
        self.raise_if_cancelled()
        safe_reason = self._safe_identifier(reason) or "unknown"
        self.contextualization = {
            "depends_on_history": bool(depends_on_history),
            "history_turn_count": max(int(history_turn_count or 0), 0),
            "fallback_used": bool(fallback_used),
            "reason": safe_reason,
            "token_usage": max(int(token_usage or 0), 0),
        }
        if self.contextualization["history_turn_count"]:
            self.emit_event("contextualization", dict(self.contextualization))

    def select_agent(
        self,
        agent: str,
        token_usage: int = 0,
        decision: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.raise_if_cancelled()
        self.agent = agent
        self.routing_tokens = int(token_usage or 0)
        self.routing_decision = self._safe_decision(decision)
        self.emit_event(
            "routing",
            {
                "agent": self._safe_identifier(agent) or "RAGAgent",
                "fallback_used": bool((self.routing_decision or {}).get("fallback_used", False)),
            },
        )

    def start_iteration(self, index: int) -> None:
        self.raise_if_cancelled()
        iteration = {
            "index": int(index),
            "subquery": None,
            "collections": [],
            "collection_routing": None,
            "web_search": None,
            "_retrieved_results": [],
            "_supported_result_ids": set(),
            "support_selection": None,
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
        self.emit_event("iteration", {"iteration": int(index)})

    def record_subquery(self, subquery: str, token_usage: int = 0) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        self._current["subquery"] = None
        self._current["token_usage"]["subquery"] = int(token_usage or 0)

    def record_collections(
        self,
        collections: Iterable[str],
        token_usage: int = 0,
        decision: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        self._current["collections"] = []
        self._current["collection_routing"] = self._safe_decision(decision)
        self._current["token_usage"]["collection_routing"] = int(token_usage or 0)

    def record_documents_retrieved(self, results: Iterable[RetrievalResult]) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        self._current["_retrieved_results"] = list(results)
        self.emit_event(
            "retrieval",
            {
                "iteration": int(self._current["index"]),
                "retrieved_count": len(self._current["_retrieved_results"]),
            },
        )

    def record_web_search(self, summary: Dict[str, Any]) -> None:
        self.raise_if_cancelled()
        if not self._current or not isinstance(summary, dict):
            return
        status = str(summary.get("status") or "degraded")
        if status not in {"disabled", "completed", "partial", "degraded", "empty"}:
            status = "degraded"
        safe = {
            "iteration": int(self._current["index"]),
            "status": status,
            "provider": self._safe_identifier(summary.get("provider")) or "unknown",
            "query_count": self._safe_index(summary.get("query_count"), minimum=0) or 0,
            "result_count": self._safe_index(summary.get("result_count"), minimum=0) or 0,
            "error_code": self._safe_identifier(summary.get("error_code")),
        }
        self._current["web_search"] = safe
        self.emit_event("web_search", dict(safe))

    def record_intermediate_answer(self, answer: str, token_usage: int = 0) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        self._current["intermediate_answer"] = None
        self._current["token_usage"]["retrieval_answer"] = int(token_usage or 0)

    def record_documents_supported(
        self,
        results: Iterable[RetrievalResult],
        token_usage: int = 0,
        decision: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        supported_results = list(results)
        self._current["_supported_result_ids"] = {id(result) for result in supported_results}
        self._current["support_selection"] = self._safe_decision(decision)
        self._current["token_usage"]["support_filter"] = int(token_usage or 0)
        self.emit_event(
            "support",
            {
                "iteration": int(self._current["index"]),
                "supported_count": len(supported_results),
            },
        )

    def record_reflection(self, has_enough_information: bool, token_usage: int = 0) -> None:
        self.raise_if_cancelled()
        if not self._current:
            return
        self._current["has_enough_information"] = bool(has_enough_information)
        self._current["token_usage"]["reflection"] = int(token_usage or 0)
        self.emit_event(
            "reflection",
            {
                "iteration": int(self._current["index"]),
                "has_enough_information": bool(has_enough_information),
            },
        )

    def record_final_answer(self, token_usage: int = 0) -> None:
        self.raise_if_cancelled()
        self.final_answer_tokens = int(token_usage or 0)

    def record_grounding_evidence(
        self,
        evidence_snapshot: Iterable[tuple[RetrievalResult, str]],
    ) -> None:
        """Retain the exact evidence text shown to the final-answer model."""
        self.raise_if_cancelled()
        self._grounding_evidence_text = {
            id(result): str(text) for result, text in evidence_snapshot
        }

    def record_selection_event(
        self,
        stage: str,
        decision: Optional[Dict[str, Any]],
    ) -> None:
        self.raise_if_cancelled()
        safe_decision = self._safe_decision(decision)
        if safe_decision is None:
            return
        self._selection_events.append(
            {
                "stage": str(stage or "selection")[:64],
                "decision": safe_decision,
            }
        )

    def build(
        self,
        total_tokens: int,
        final_results: Iterable[RetrievalResult],
        final_answer_tokens: Optional[int] = None,
        answer: str | None = None,
    ) -> Dict[str, Any]:
        final_results_list = list(final_results)
        final_result_ids = {id(result) for result in final_results_list}
        iterations = [
            self._serialize_iteration(iteration, final_result_ids) for iteration in self._iterations
        ]
        trace = {
            "version": self.VERSION,
            "agent": self.agent,
            "contextualization": self.contextualization,
            "routing": self.routing_decision,
            "iterations": iterations,
            "selection_events": list(self._selection_events),
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
        if answer is not None:
            trace["grounding"] = build_grounding(
                answer,
                final_results_list,
                serialize_evidence=self._serialize_grounding_document,
            )
        return trace

    def _serialize_iteration(
        self,
        iteration: Dict[str, Any],
        final_result_ids: set[int],
    ) -> Dict[str, Any]:
        raw_results = iteration["_retrieved_results"]
        supported_ids = iteration["_supported_result_ids"] | final_result_ids
        supported_results = [result for result in raw_results if id(result) in supported_ids]
        documents = [
            self._serialize_document(result, True)
            for result in supported_results[: self.MAX_VISIBLE_DOCUMENTS]
        ]
        token_usage = dict(iteration["token_usage"])
        token_usage["total"] = sum(token_usage.values())
        return {
            "index": iteration["index"],
            "collection_routing": iteration["collection_routing"],
            "web_search": iteration["web_search"],
            "retrieved_documents": documents,
            "retrieved_count": len(raw_results),
            "supported_count": len(supported_results),
            "support_selection": iteration["support_selection"],
            "has_enough_information": iteration["has_enough_information"],
            "token_usage": token_usage,
        }

    def _serialize_grounding_document(
        self,
        result: RetrievalResult,
        supported: bool,
    ) -> Dict[str, Any]:
        document = self._serialize_document(result, supported)
        evidence_text = self._grounding_evidence_text.get(id(result))
        if evidence_text is not None:
            document["text"] = redact_sensitive_text(
                evidence_text,
                max_length=MAX_GROUNDING_EVIDENCE_TEXT,
            )
        return document

    def _serialize_document(self, result: RetrievalResult, supported: bool) -> Dict[str, Any]:
        text = redact_sensitive_text(result.text, max_length=self.MAX_DOCUMENT_TEXT)
        reference = self._safe_reference(result.reference)
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        char_start = self._safe_index(metadata.get("char_start"), minimum=0)
        char_end = self._safe_index(metadata.get("char_end"), minimum=0)
        if char_start is not None and char_end is not None and char_end < char_start:
            char_start = None
            char_end = None

        def safe_retrieval_value(value: Any) -> float | None:
            try:
                numeric = float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
            return numeric if numeric is not None and math.isfinite(numeric) else None

        metric_type = self._safe_identifier(getattr(result, "metric_type", None)) or "UNKNOWN"
        score_kind = getattr(result, "score_kind", None)
        if score_kind not in {"distance", "similarity", "rank_score"}:
            score_kind = None
        source_type = "web" if metadata.get("source_type") == "web" else "knowledge_base"
        source_url = (
            self._safe_reference(metadata.get("source_url")) if source_type == "web" else None
        )
        return {
            "text": text,
            "reference": reference,
            "display_name": self._safe_reference(metadata.get("display_name")),
            "document_id": self._safe_identifier(metadata.get("document_id")),
            "page_number": self._safe_index(metadata.get("page_number"), minimum=1),
            "chunk_index": self._safe_index(metadata.get("chunk_index"), minimum=0),
            "section_title": self._safe_text(
                metadata.get("section_title"),
                max_length=255,
            ),
            "section_path": self._safe_string_list(
                metadata.get("section_path"),
                max_items=8,
                max_item_length=160,
            ),
            "char_start": char_start,
            "char_end": char_end,
            "bbox": self._safe_bbox(metadata.get("bbox")),
            "location_id": self._safe_identifier(metadata.get("location_id")),
            "source_locator": self._safe_text(
                metadata.get("source_locator"),
                max_length=128,
            ),
            "parser_version": self._safe_text(
                metadata.get("parser_version"),
                max_length=128,
            ),
            "extraction_method": self._safe_text(
                metadata.get("extraction_method"),
                max_length=32,
            ),
            "source_type": source_type,
            "source_url": source_url,
            "source_domain": (
                self._safe_identifier(metadata.get("source_domain"))
                if source_type == "web"
                else None
            ),
            "trusted": bool(metadata.get("trusted", False)) if source_type == "web" else True,
            "metric_type": metric_type,
            "score_kind": score_kind,
            "distance": safe_retrieval_value(getattr(result, "distance", None)),
            "similarity": safe_retrieval_value(getattr(result, "similarity", None)),
            "rank_score": safe_retrieval_value(getattr(result, "rank_score", None)),
            "higher_is_better": getattr(result, "higher_is_better", None),
            "supported": supported,
        }

    @staticmethod
    def _safe_reference(reference: Any) -> Optional[str]:
        if reference is None:
            return None
        value = str(reference)
        if "://" in value:
            canonical = canonical_public_url(value)
            return canonical[0] if canonical is not None else None
        filename = value.replace("\\", "/").rsplit("/", 1)[-1]
        return redact_sensitive_text(filename, max_length=255)

    @staticmethod
    def _safe_identifier(identifier: Any) -> Optional[str]:
        if identifier is None:
            return None
        value = str(identifier)
        if len(value) > 128 or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None:
            return None
        return value

    @staticmethod
    def _safe_text(value: Any, *, max_length: int) -> Optional[str]:
        return redact_sensitive_text(value, max_length=max_length)

    @classmethod
    def _safe_string_list(
        cls,
        value: Any,
        *,
        max_items: int,
        max_item_length: int,
    ) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            safe
            for item in value[:max_items]
            if (safe := cls._safe_text(item, max_length=max_item_length)) is not None
        ]

    @staticmethod
    def _safe_bbox(value: Any) -> Optional[list[float]]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            bbox = [round(float(item), 6) for item in value]
        except (TypeError, ValueError):
            return None
        if (
            any(not math.isfinite(item) or item < 0 or item > 1 for item in bbox)
            or bbox[0] > bbox[2]
            or bbox[1] > bbox[3]
        ):
            return None
        return bbox

    @staticmethod
    def _safe_index(value: Any, minimum: int) -> Optional[int]:
        if isinstance(value, bool):
            return None
        try:
            index = int(value)
        except (TypeError, ValueError):
            return None
        return index if index >= minimum else None

    @classmethod
    def _safe_decision(cls, decision: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(decision, dict):
            return None
        safe = {}
        for key in ("source", "reason"):
            value = decision.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
                safe[key] = value
        for key in ("fallback_used", "trusted"):
            value = decision.get(key)
            if isinstance(value, bool):
                safe[key] = value
        evidence_count = decision.get("evidence_count")
        if isinstance(evidence_count, int) and not isinstance(evidence_count, bool):
            safe["evidence_count"] = max(evidence_count, 0)
        for key in ("selected", "requested", "rejected"):
            value = decision.get(key)
            if isinstance(value, list):
                safe[f"{key}_count"] = len(value)
            elif value is not None:
                safe[f"{key}_count"] = 1
        return safe or None
