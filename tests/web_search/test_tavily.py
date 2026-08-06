from unittest.mock import MagicMock

import pytest
import requests

from deepsearcher.web_search import TavilySearch, WebSearchError
from deepsearcher.web_search.tavily import canonical_public_url


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"results": []}
        self.content = content if content is not None else b"{}"

    def json(self):
        return self._payload


def result(title, url, content="useful snippet", score=0.8):
    return {"title": title, "url": url, "content": content, "score": score}


def test_canonical_public_url_rejects_private_and_unsafe_targets():
    assert canonical_public_url("https://Example.COM/path?q=secret#part") == (
        "https://example.com/path",
        "example.com",
    )
    for unsafe in (
        "http://127.0.0.1/admin",
        "http://10.0.0.2/private",
        "https://8.8.8.8/public",
        "http://[::1]/",
        "http://localhost/",
        "https://intranet/page",
        "http://service.local/",
        "https://user:password@example.com/",
        "ftp://example.com/file",
        "https://example.com:8443/admin",
    ):
        assert canonical_public_url(unsafe) is None


def test_tavily_search_sends_bounded_safe_request_and_maps_ranked_citations():
    session = MagicMock()
    session.post.return_value = FakeResponse(
        payload={
            "results": [
                result("Official guide", "https://docs.example.com/guide?tracking=1", score=0.91),
                result("Duplicate", "https://docs.example.com/guide?tracking=2", score=0.7),
                result("Blocked", "https://ads.example.net/page", score=0.6),
                result("Private", "http://127.0.0.1/internal", score=0.5),
            ]
        }
    )
    provider = TavilySearch(
        api_key="test-key",
        include_domains=["example.com"],
        exclude_domains=["ads.example.net"],
        session=session,
    )

    results = provider.search(" current\x00 topic ", max_results=10)

    assert len(results) == 1
    citation = results[0]
    assert citation.reference == "https://docs.example.com/guide"
    assert citation.metric_type == "TAVILY_RELEVANCE"
    assert citation.rank_score == 0.91
    assert citation.metadata == {
        "display_name": "Official guide",
        "source_type": "web",
        "source_domain": "docs.example.com",
        "source_url": "https://docs.example.com/guide",
        "trusted": True,
        "web_search_provider": "tavily",
    }
    request = session.post.call_args
    assert request.args == ("https://api.tavily.com/search",)
    assert request.kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert request.kwargs["json"]["query"] == "current  topic"
    assert request.kwargs["json"]["include_answer"] is False
    assert request.kwargs["json"]["include_raw_content"] is False
    assert request.kwargs["json"]["safe_search"] is True
    assert request.kwargs["json"]["max_results"] == 10
    assert request.kwargs["allow_redirects"] is False


def test_tavily_search_requires_credentials_only_when_called(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    provider = TavilySearch()

    assert provider.enabled is False
    with pytest.raises(WebSearchError) as exc_info:
        provider.search("query")

    assert exc_info.value.code == "WEB_SEARCH_NOT_CONFIGURED"
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    ("side_effect", "response", "expected_code", "retryable"),
    [
        (requests.Timeout(), None, "WEB_SEARCH_TIMEOUT", True),
        (requests.ConnectionError(), None, "WEB_SEARCH_UNAVAILABLE", True),
        (None, FakeResponse(status_code=401), "WEB_SEARCH_AUTH_FAILED", False),
        (None, FakeResponse(status_code=429), "WEB_SEARCH_RATE_LIMITED", True),
        (None, FakeResponse(status_code=500), "WEB_SEARCH_UPSTREAM_FAILED", True),
    ],
)
def test_tavily_search_returns_stable_provider_errors(
    side_effect,
    response,
    expected_code,
    retryable,
):
    session = MagicMock()
    session.post.side_effect = side_effect
    session.post.return_value = response
    provider = TavilySearch(api_key="test-key", session=session)

    with pytest.raises(WebSearchError) as exc_info:
        provider.search("query")

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable


def test_tavily_search_rejects_invalid_domain_configuration():
    with pytest.raises(ValueError):
        TavilySearch(api_key="key", include_domains=["https://example.com/path"])
    with pytest.raises(ValueError):
        TavilySearch(api_key="key", include_domains=["8.8.8.8"])
    with pytest.raises(ValueError):
        TavilySearch(api_key="key", include_domains=["intranet"])
    with pytest.raises(ValueError):
        TavilySearch(
            api_key="key",
            include_domains=["example.com"],
            exclude_domains=["example.com"],
        )


def test_tavily_search_rejects_oversized_or_invalid_responses():
    session = MagicMock()
    session.post.return_value = FakeResponse(content=b"x" * 2_000_001)
    provider = TavilySearch(api_key="key", session=session)
    with pytest.raises(WebSearchError) as exc_info:
        provider.search("query")
    assert exc_info.value.code == "WEB_SEARCH_RESPONSE_TOO_LARGE"

    session.post.return_value = FakeResponse(payload={"results": "not-a-list"})
    with pytest.raises(WebSearchError) as exc_info:
        provider.search("query")
    assert exc_info.value.code == "WEB_SEARCH_RESPONSE_INVALID"
