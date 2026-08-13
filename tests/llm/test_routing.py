from __future__ import annotations

import time

import pytest

from deepsearcher.llm.base import BaseLLM, ChatResponse, TokenUsage, chat_with_stage
from deepsearcher.llm.routing import (
    AllModelsFailed,
    CircuitBreaker,
    CircuitState,
    RoutingLLM,
    StreamEvent,
)


class FakeLLM(BaseLLM):
    def __init__(self, model: str, *, response="ok", error=None, events=None):
        self.model = model
        self.response = response
        self.error = error
        self.events = events
        self.calls = 0

    def chat_with_options(self, messages, options=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ChatResponse(
            self.response,
            usage=TokenUsage(total_tokens=1, usage_source="provider"),
        )

    def stream_with_options(self, messages, options=None):
        self.calls += 1
        yield from self.events or []


def test_sync_fallback_skips_error_and_empty_response():
    router = RoutingLLM(
        [
            FakeLLM("broken", error=RuntimeError("unavailable")),
            FakeLLM("empty", response="   "),
            FakeLLM("healthy", response="answer"),
        ]
    )

    response = router.chat([{"role": "user", "content": "question"}])

    assert response.content == "answer"
    assert response.model == "healthy"
    assert response.fallback_reason == "broken:RuntimeError;empty:ValueError"


def test_all_sync_candidates_fail_with_clear_error():
    router = RoutingLLM([FakeLLM("empty", response="")])

    with pytest.raises(AllModelsFailed, match="all configured Chat candidates failed"):
        router.chat([])


def test_stream_ignores_metadata_heartbeat_whitespace_and_completion():
    fallback = FakeLLM("fallback", events=[StreamEvent("content", "answer")])
    router = RoutingLLM(
        [
            FakeLLM(
                "empty-stream",
                events=[
                    StreamEvent("metadata", data={"request_id": "x"}),
                    StreamEvent("heartbeat"),
                    StreamEvent("content", "   "),
                    StreamEvent("completed"),
                ],
            ),
            fallback,
        ]
    )

    events = list(router.stream_with_options([]))

    assert events[0].type == "selected_model"
    assert events[0].data["model"] == "fallback"
    assert events[-1] == StreamEvent("content", "answer")


def test_stream_does_not_fallback_after_first_effective_output():
    def failing_after_content():
        yield StreamEvent("content", "first")
        raise RuntimeError("late failure")

    first = FakeLLM("first")
    first.events = failing_after_content()
    fallback = FakeLLM("fallback", events=[StreamEvent("content", "unused")])
    router = RoutingLLM([first, fallback])

    with pytest.raises(RuntimeError, match="late failure"):
        list(router.stream_with_options([]))
    assert fallback.calls == 0


def test_stream_first_packet_timeout_falls_back():
    class SlowLLM(FakeLLM):
        def stream_with_options(self, messages, options=None):
            self.calls += 1
            time.sleep(0.08)
            yield StreamEvent("content", "too late")

    fallback = FakeLLM("fallback", events=[StreamEvent("content", "answer")])
    router = RoutingLLM([SlowLLM("slow"), fallback], first_packet_timeout=0.02)

    events = list(router.stream_with_options([]))

    assert events[0].data["model"] == "fallback"


def test_unexposed_thinking_does_not_count_as_effective_output():
    fallback = FakeLLM("fallback", events=[StreamEvent("content", "answer")])
    router = RoutingLLM(
        [FakeLLM("thinking", events=[StreamEvent("thinking", "hidden")]), fallback],
        expose_thinking=False,
    )

    assert list(router.stream_with_options([]))[0].data["model"] == "fallback"


def test_half_open_allows_one_probe_and_releases_cancelled_permit():
    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=0.01)
    first = breaker.allow()
    breaker.failure(first)
    assert breaker.state == CircuitState.OPEN
    time.sleep(0.02)
    probe = breaker.allow()
    assert probe is not None and probe.half_open
    assert breaker.allow() is None

    breaker.release(probe)

    assert breaker.allow() is not None


def test_trace_records_actual_fallback_model_and_reason():
    class Trace:
        def reserve_llm_call(self, **kwargs):
            return kwargs["requested_max_tokens"]

        def record_llm_call(self, **kwargs):
            self.call = kwargs

    trace = Trace()
    router = RoutingLLM([FakeLLM("broken", error=RuntimeError("down")), FakeLLM("healthy")])

    chat_with_stage(
        router,
        [{"role": "user", "content": "question"}],
        stage="final_answer",
        max_tokens=100,
        trace_collector=trace,
    )

    assert trace.call["model"] == "healthy"
    assert trace.call["fallback_reason"] == "broken:RuntimeError"
