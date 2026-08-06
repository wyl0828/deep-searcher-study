"""Deterministic ASGI probe used by the live SSE disconnect regression test."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import main
from deepsearcher.runtime_registry import RuntimeControlStore
from deepsearcher.trace import QueryCancelled

PROBE_STATE = {
    "started": False,
    "cancelled": False,
    "finished": False,
}
PROBE_LOCK = threading.Lock()
STORE_DIRECTORY = tempfile.TemporaryDirectory()


class ProbeConfig:
    provide_settings = {}
    query_settings = {}
    load_settings = {}

    def set_provider_config(self, _feature, _provider, _config):
        return None


def runtime_factory(config):
    return SimpleNamespace(
        config=config,
        vector_db=SimpleNamespace(client=None),
        default_searcher=object(),
        embedding_model=object(),
        file_loader=object(),
        web_crawler=object(),
    )


def slow_query_with_trace(
    _question,
    _max_iter,
    *,
    trace_collector,
    **_kwargs,
):
    with PROBE_LOCK:
        PROBE_STATE["started"] = True
    trace_collector.select_agent("ProbeAgent")
    try:
        for iteration in range(1, 201):
            trace_collector.start_iteration(iteration)
            time.sleep(0.025)
    except QueryCancelled:
        with PROBE_LOCK:
            PROBE_STATE["cancelled"] = True
        raise
    finally:
        with PROBE_LOCK:
            PROBE_STATE["finished"] = True
    return "probe answer", [], 0, trace_collector.build(0, [])


main.query_with_trace = slow_query_with_trace
app = main.create_app(
    runtime_factory=runtime_factory,
    config_factory=ProbeConfig,
    runtime_store_factory=lambda: RuntimeControlStore(
        Path(STORE_DIRECTORY.name) / "runtime.db"
    ),
    service_token="probe-service-token",
)


@app.get("/probe/state")
def probe_state():
    registry = getattr(app.state, "runtime_registry", None)
    references = 0
    if registry is not None:
        references = sum(entry.references for entry in registry._entries.values())
    with PROBE_LOCK:
        state = dict(PROBE_STATE)
    return {
        **state,
        "runtime_references": references,
        "cleanup_tasks": len(getattr(app.state, "stream_cleanup_tasks", set())),
    }
