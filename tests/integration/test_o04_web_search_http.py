from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from deepsearcher.web_search import TavilySearch


def test_tavily_provider_uses_real_http_contract_without_leaking_query_parameters(
    monkeypatch,
):
    received: dict[str, object] = {}

    class SearchHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - required by BaseHTTPRequestHandler
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["payload"] = json.loads(self.rfile.read(length))
            response = json.dumps(
                {
                    "results": [
                        {
                            "title": "HTTP contract result",
                            "url": "https://docs.example.com/current?q=private#section",
                            "content": "A controlled provider response.",
                            "score": 0.93,
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SearchHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(
        TavilySearch,
        "ENDPOINT",
        f"http://127.0.0.1:{server.server_port}/search",
    )
    provider = TavilySearch(api_key="integration-key", timeout_seconds=2)
    try:
        results = provider.search("controlled HTTP query", max_results=3)
    finally:
        provider.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

    assert received == {
        "path": "/search",
        "authorization": "Bearer integration-key",
        "payload": {
            "query": "controlled HTTP query",
            "search_depth": "basic",
            "chunks_per_source": 1,
            "max_results": 3,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": False,
            "safe_search": True,
        },
    }
    assert len(results) == 1
    assert results[0].reference == "https://docs.example.com/current"
    assert results[0].rank_score == 0.93
    assert results[0].metadata["source_domain"] == "docs.example.com"
