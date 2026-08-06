"""Tavily Search API adapter with local source-policy enforcement."""

from __future__ import annotations

import ipaddress
import math
import os
import re
from typing import Iterable, List
from urllib.parse import urlsplit, urlunsplit

import requests

from deepsearcher.vector_db.base import RetrievalResult
from deepsearcher.web_search.base import BaseWebSearch, WebSearchError

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)


def _clean_text(value: object, *, max_length: int) -> str:
    return _CONTROL_CHARACTERS.sub(" ", str(value or "")).strip()[:max_length]


def _normalize_domain(value: object) -> str:
    domain = str(value or "").strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        is_ip_address = False
    else:
        is_ip_address = True
    if (
        not domain
        or "." not in domain
        or "://" in domain
        or "/" in domain
        or is_ip_address
        or not _DOMAIN.fullmatch(domain)
    ):
        raise ValueError("web search domains must be plain DNS names")
    return domain


def _normalize_domains(values: Iterable[object] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_normalize_domain(value) for value in (values or ())))


def _domain_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def canonical_public_url(value: object) -> tuple[str, str] | None:
    """Return a query-free public HTTP(S) URL and hostname, or reject it."""
    raw = _clean_text(value, max_length=2048)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local"))
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname or not _DOMAIN.fullmatch(hostname):
            return None
    else:
        # Citation URLs are display/navigation targets, not request targets. Rejecting
        # every literal address avoids surprising internal or rebinding-style links.
        return None
    scheme = parsed.scheme.lower()
    default_port = 80 if scheme == "http" else 443
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, "", "")), hostname


class TavilySearch(BaseWebSearch):
    """Search Tavily and map native relevance scores to citable results."""

    ENDPOINT = "https://api.tavily.com/search"
    provider_name = "tavily"
    MAX_RESPONSE_BYTES = 2_000_000

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout_seconds: float = 10.0,
        include_domains: Iterable[str] | None = None,
        exclude_domains: Iterable[str] | None = None,
        search_depth: str = "basic",
        session: requests.Session | None = None,
    ):
        self._api_key = str(api_key or os.environ.get("TAVILY_API_KEY") or "").strip()
        self.timeout_seconds = max(min(float(timeout_seconds), 30.0), 0.1)
        self.include_domains = _normalize_domains(include_domains)
        self.exclude_domains = _normalize_domains(exclude_domains)
        if set(self.include_domains) & set(self.exclude_domains):
            raise ValueError("the same web domain cannot be both included and excluded")
        if search_depth not in {"basic", "fast", "ultra-fast"}:
            raise ValueError("unsupported Tavily search depth")
        self.search_depth = search_depth
        self.client = session or requests.Session()
        self.client.trust_env = False

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def close(self) -> None:
        self.client.close()

    def _source_allowed(self, hostname: str) -> bool:
        if any(_domain_matches(hostname, domain) for domain in self.exclude_domains):
            return False
        return not self.include_domains or any(
            _domain_matches(hostname, domain) for domain in self.include_domains
        )

    def search(self, query: str, *, max_results: int = 5) -> List[RetrievalResult]:
        cleaned_query = _clean_text(query, max_length=1000)
        if not cleaned_query:
            return []
        if not self.enabled:
            raise WebSearchError("WEB_SEARCH_NOT_CONFIGURED", retryable=False)
        bounded_results = max(min(int(max_results), 10), 1)
        payload = {
            "query": cleaned_query,
            "search_depth": self.search_depth,
            "chunks_per_source": 1,
            "max_results": bounded_results,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": False,
            "safe_search": True,
        }
        if self.include_domains:
            payload["include_domains"] = list(self.include_domains)
        if self.exclude_domains:
            payload["exclude_domains"] = list(self.exclude_domains)
        try:
            response = self.client.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            raise WebSearchError("WEB_SEARCH_TIMEOUT", retryable=True) from exc
        except requests.RequestException as exc:
            raise WebSearchError("WEB_SEARCH_UNAVAILABLE", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise WebSearchError("WEB_SEARCH_AUTH_FAILED", retryable=False)
        if response.status_code in {429, 432, 433}:
            raise WebSearchError("WEB_SEARCH_RATE_LIMITED", retryable=True)
        if response.status_code != 200:
            raise WebSearchError("WEB_SEARCH_UPSTREAM_FAILED", retryable=True)
        if len(response.content) > self.MAX_RESPONSE_BYTES:
            raise WebSearchError("WEB_SEARCH_RESPONSE_TOO_LARGE", retryable=True)
        try:
            data = response.json()
        except ValueError as exc:
            raise WebSearchError("WEB_SEARCH_RESPONSE_INVALID", retryable=True) from exc
        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise WebSearchError("WEB_SEARCH_RESPONSE_INVALID", retryable=True)

        results: List[RetrievalResult] = []
        seen_urls: set[str] = set()
        for item in raw_results[:bounded_results]:
            if not isinstance(item, dict):
                continue
            source = canonical_public_url(item.get("url"))
            if source is None:
                continue
            url, hostname = source
            if url in seen_urls or not self._source_allowed(hostname):
                continue
            content = _clean_text(item.get("content"), max_length=1500)
            if not content:
                continue
            title = _clean_text(item.get("title"), max_length=255) or hostname
            score = item.get("score")
            try:
                rank_score = (
                    float(score) if score is not None and not isinstance(score, bool) else None
                )
            except (TypeError, ValueError):
                rank_score = None
            if rank_score is not None and not math.isfinite(rank_score):
                rank_score = None
            results.append(
                RetrievalResult(
                    embedding=[],
                    text=content,
                    reference=url,
                    metadata={
                        "display_name": title,
                        "source_type": "web",
                        "source_domain": hostname,
                        "source_url": url,
                        "trusted": bool(self.include_domains),
                        "web_search_provider": self.provider_name,
                    },
                    metric_type="TAVILY_RELEVANCE",
                    rank_score=rank_score,
                )
            )
            seen_urls.add(url)
        return results
