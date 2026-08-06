"""Provider-neutral web search contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from deepsearcher.vector_db.base import RetrievalResult


class WebSearchError(RuntimeError):
    """Safe provider failure that can be reported without leaking response bodies."""

    code = "WEB_SEARCH_FAILED"
    retryable = True

    def __init__(self, code: str | None = None, *, retryable: bool | None = None):
        super().__init__(code or self.code)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = bool(retryable)


class BaseWebSearch(ABC):
    """Return bounded, citable search snippets without crawling result pages."""

    provider_name = "unknown"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this runtime has the credentials needed to search."""

    @abstractmethod
    def search(self, query: str, *, max_results: int = 5) -> List[RetrievalResult]:
        """Search the public web and return provider-ranked snippets."""


class DisabledWebSearch(BaseWebSearch):
    """Backwards-compatible runtime component used when no provider is configured."""

    provider_name = "disabled"

    @property
    def enabled(self) -> bool:
        return False

    def search(self, query: str, *, max_results: int = 5) -> List[RetrievalResult]:
        del query, max_results
        return []
