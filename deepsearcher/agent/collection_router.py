from __future__ import annotations

from contextvars import ContextVar
from typing import Iterable, List, Optional, Tuple

from deepsearcher.agent.base import BaseAgent
from deepsearcher.agent.selection import (
    fallback_selection,
    safe_collection_name,
    validate_string_list,
)
from deepsearcher.llm.base import BaseLLM
from deepsearcher.utils import log
from deepsearcher.vector_db.base import BaseVectorDB, CollectionInfo

COLLECTION_ROUTE_PROMPT = """
I provide collection names and descriptions. Select every collection that may be
related to the question.

"QUESTION": {question}
"COLLECTION_INFO": {collection_info}

Return only a valid JSON array of collection-name strings. Every returned value
must exactly match a collection_name from COLLECTION_INFO. Return [] when none
is relevant. Do not return markdown, explanations, or invented names.
"""


class CollectionRouter(BaseAgent):
    """Route queries through an explicit collection allowlist."""

    def __init__(self, llm: BaseLLM, vector_db: BaseVectorDB, dim: int, **kwargs):
        self.llm = llm
        self.vector_db = vector_db
        self.dim = dim
        self._all_collections = ContextVar(
            f"collection_router_all_collections_{id(self)}",
            default=tuple(
                info.collection_name for info in self.vector_db.list_collections(dim=dim)
            ),
        )
        self._last_decision = ContextVar(
            f"collection_router_last_decision_{id(self)}",
            default=None,
        )

    @property
    def all_collections(self) -> List[str]:
        """Compatibility snapshot scoped to the current request context."""
        return list(self._all_collections.get())

    @property
    def last_decision(self) -> Optional[dict]:
        """Last routing decision for this request context, never another request."""
        decision = self._last_decision.get()
        return dict(decision) if isinstance(decision, dict) else None

    @staticmethod
    def _effective_infos(
        collection_infos: List[CollectionInfo],
        allowed_collections: Optional[Iterable[str]],
    ) -> List[CollectionInfo]:
        if allowed_collections is None:
            return list(collection_infos)
        allowed = {name for name in allowed_collections if isinstance(name, str) and name}
        return [info for info in collection_infos if info.collection_name in allowed]

    def _list_effective_infos(
        self,
        *,
        dim: int,
        allowed_collections: Optional[Iterable[str]],
    ) -> List[CollectionInfo]:
        collection_infos = list(self.vector_db.list_collections(dim=dim))
        self._all_collections.set(tuple(info.collection_name for info in collection_infos))
        return self._effective_infos(collection_infos, allowed_collections)

    def _record_decision(
        self,
        *,
        source: str,
        requested: Iterable[str],
        selected: Iterable[str],
        rejected: Iterable[str] = (),
        fallback_used: bool = False,
        reason: Optional[str] = None,
    ) -> dict:
        decision = {
            "source": source,
            "requested": [safe_collection_name(name) for name in requested],
            "selected": list(selected),
            "rejected": [safe_collection_name(name) for name in rejected],
            "fallback_used": bool(fallback_used),
            "reason": reason,
        }
        self._last_decision.set(decision)
        return dict(decision)

    def resolve_explicit(
        self,
        collection_names,
        *,
        dim: Optional[int] = None,
        allowed_collections: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Filter caller-provided collection names without broadening their scope."""
        effective_infos = self._list_effective_infos(
            dim=dim or self.dim,
            allowed_collections=allowed_collections,
        )
        effective_names = [info.collection_name for info in effective_infos]
        effective_set = set(effective_names)
        requested_count = len(collection_names) if isinstance(collection_names, list) else 1
        parsed = validate_string_list(
            collection_names,
            max_items=max(min(requested_count, 128), len(effective_names), 1),
        )
        selected = [name for name in parsed.values if name in effective_set]
        rejected_names = [name for name in parsed.values if name not in effective_set]
        rejected_trace = rejected_names + parsed.rejected
        reason = parsed.reason
        if rejected_names:
            reason = "explicit_collections_filtered"
        self._record_decision(
            source="explicit",
            requested=parsed.values,
            selected=selected,
            rejected=rejected_trace,
            fallback_used=parsed.fallback_used,
            reason=reason,
        )
        return selected

    def resolve_all(
        self,
        *,
        dim: Optional[int] = None,
        allowed_collections: Optional[Iterable[str]] = None,
    ) -> List[str]:
        effective_infos = self._list_effective_infos(
            dim=dim or self.dim,
            allowed_collections=allowed_collections,
        )
        selected = [info.collection_name for info in effective_infos]
        self._record_decision(
            source="all",
            requested=selected,
            selected=selected,
        )
        return selected

    def invoke(
        self,
        query: str,
        dim: int,
        *,
        allowed_collections: Optional[Iterable[str]] = None,
        **kwargs,
    ) -> Tuple[List[str], int]:
        """Select collections, then enforce real and caller-supplied allowlists."""
        collection_infos = self._list_effective_infos(
            dim=dim,
            allowed_collections=allowed_collections,
        )
        if not collection_infos:
            self._record_decision(
                source="model",
                requested=[],
                selected=[],
                reason="no_allowed_collections",
            )
            log.warning("No allowed collections are available for collection routing.")
            return [], 0

        if len(collection_infos) == 1:
            selected = [collection_infos[0].collection_name]
            self._record_decision(
                source="single",
                requested=selected,
                selected=selected,
            )
            log.color_print("<route> Selected 1 authorized vector collection </route>\n")
            return selected, 0

        prompt = COLLECTION_ROUTE_PROMPT.format(
            question=query,
            collection_info=[
                {
                    "collection_name": info.collection_name,
                    "collection_description": info.description,
                }
                for info in collection_infos
            ],
        )
        chat_response = self.llm.chat(messages=[{"role": "user", "content": prompt}])
        try:
            parsed_value = self.llm.literal_eval(self.llm.remove_think(chat_response.content))
            parsed = validate_string_list(
                parsed_value,
                max_items=len(collection_infos),
            )
        except Exception:
            parsed = fallback_selection([], "collection_output_parse_failed")

        effective_names = [info.collection_name for info in collection_infos]
        effective_set = set(effective_names)
        requested = parsed.values
        rejected = [name for name in requested if name not in effective_set]
        rejected_trace = rejected + parsed.rejected
        automatic = [
            info.collection_name
            for info in collection_infos
            if not info.description or info.collection_name == self.vector_db.default_collection
        ]

        selected = []
        valid_requested = []
        for name in requested:
            if name in effective_set and name not in valid_requested:
                valid_requested.append(name)
        for name in requested + automatic:
            if name in effective_set and name not in selected:
                selected.append(name)

        fallback_used = parsed.fallback_used
        reason = parsed.reason
        if not selected:
            fallback = (
                self.vector_db.default_collection
                if self.vector_db.default_collection in effective_set
                else effective_names[0]
            )
            selected = [fallback]
            fallback_used = True
            reason = reason or "no_valid_collection_selected"
        elif not valid_requested:
            fallback_used = True
            reason = reason or "automatic_collection_fallback"
        elif rejected:
            reason = "hallucinated_collections_filtered"

        self._record_decision(
            source="model",
            requested=requested,
            selected=selected,
            rejected=rejected_trace,
            fallback_used=fallback_used,
            reason=reason,
        )
        if fallback_used or rejected_trace:
            log.warning(f"CollectionRouter constrained model output: {reason}.")
        log.color_print(
            f"<route> Selected {len(selected)} authorized vector collection(s) </route>\n"
        )
        return selected, chat_response.total_tokens
