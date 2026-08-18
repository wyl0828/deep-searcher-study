from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Iterator, Sequence

from deepsearcher.llm.base import BaseLLM, ChatOptions, ChatResponse, _legacy_chat


class AllModelsFailed(RuntimeError):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class StreamEvent:
    type: str
    content: str = ""
    data: Any = None


@dataclass(frozen=True)
class CallPermit:
    half_open: bool = False


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_seconds: float = 30.0):
        self.failure_threshold = max(int(failure_threshold), 1)
        self.recovery_seconds = max(float(recovery_seconds), 0.01)
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._failures < self.failure_threshold:
                return CircuitState.CLOSED
            if time.monotonic() - self._opened_at >= self.recovery_seconds:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN

    def allow(self) -> CallPermit | None:
        with self._lock:
            if self._failures < self.failure_threshold:
                return CallPermit()
            if time.monotonic() - self._opened_at < self.recovery_seconds:
                return None
            if self._half_open_in_flight:
                return None
            self._half_open_in_flight = True
            return CallPermit(half_open=True)

    def success(self, permit: CallPermit) -> None:
        with self._lock:
            self._failures = 0
            if permit.half_open:
                self._half_open_in_flight = False

    def failure(self, permit: CallPermit) -> None:
        with self._lock:
            self._failures = max(
                self._failures + 1, self.failure_threshold if permit.half_open else 0
            )
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()
            if permit.half_open:
                self._half_open_in_flight = False

    def release(self, permit: CallPermit) -> None:
        if permit.half_open:
            with self._lock:
                self._half_open_in_flight = False


def is_effective_output(event: StreamEvent, *, expose_thinking: bool) -> bool:
    if event.type == "content":
        return bool(event.content.strip())
    if event.type == "thinking" and expose_thinking:
        return bool(event.content.strip())
    return False


class RoutingLLM(BaseLLM):
    """Ordered Chat fallback with process-local circuit breakers."""

    def __init__(
        self,
        candidates: Sequence[BaseLLM],
        *,
        failure_threshold: int = 3,
        recovery_seconds: float = 30.0,
        first_packet_timeout: float = 10.0,
        expose_thinking: bool = False,
        limiter: Any | None = None,
        rate_limit_timeout_ms: int = 30000,
    ):
        if not candidates:
            raise ValueError("RoutingLLM requires at least one candidate")
        self.candidates = tuple(candidates)
        self.model = str(getattr(candidates[0], "model", candidates[0].__class__.__name__))
        self.first_packet_timeout = max(float(first_packet_timeout), 0.01)
        self.expose_thinking = bool(expose_thinking)
        self._limiter = limiter
        self._rate_limit_timeout_ms = max(int(rate_limit_timeout_ms), 1)
        self.breakers = {
            id(candidate): CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_seconds=recovery_seconds,
            )
            for candidate in self.candidates
        }

    @staticmethod
    def _identity(candidate: BaseLLM) -> str:
        return str(getattr(candidate, "model", candidate.__class__.__name__))

    def _acquire_permit(self) -> str | None:
        """Acquire a distributed permit when a limiter is configured (None = disabled)."""
        if self._limiter is None:
            return ""  # disabled: treat as granted, nothing to release
        request_id = uuid.uuid4().hex
        granted, _state = self._limiter.acquire(request_id, max_wait_ms=self._rate_limit_timeout_ms)
        return request_id if granted else None

    def _release_permit(self, request_id: str | None) -> None:
        if request_id is not None and self._limiter is not None:
            self._limiter.release(request_id)

    def chat(self, messages):
        return self.chat_with_options(messages)

    def chat_with_options(self, messages, options: ChatOptions | None = None) -> ChatResponse:
        request_id = self._acquire_permit()
        if request_id is None:
            raise AllModelsFailed("rate limited (all LLM permits busy)")
        try:
            reasons: list[str] = []
            for candidate in self.candidates:
                breaker = self.breakers[id(candidate)]
                permit = breaker.allow()
                if permit is None:
                    reasons.append(f"{self._identity(candidate)}:circuit_open")
                    continue
                try:
                    option_chat = getattr(candidate, "chat_with_options", None)
                    response = (
                        option_chat(messages, options)
                        if callable(option_chat)
                        else _legacy_chat(candidate, messages)
                    )
                    if not str(response.content or "").strip():
                        raise ValueError("empty_response")
                except Exception as exc:
                    breaker.failure(permit)
                    reasons.append(f"{self._identity(candidate)}:{type(exc).__name__}")
                    continue
                breaker.success(permit)
                response.model = self._identity(candidate)
                response.fallback_reason = ";".join(reasons) or None
                return response
            raise AllModelsFailed("all configured Chat candidates failed")
        finally:
            self._release_permit(request_id)

    def stream_with_options(
        self,
        messages,
        options: ChatOptions | None = None,
    ) -> Iterator[StreamEvent]:
        request_id = self._acquire_permit()
        if request_id is None:
            raise AllModelsFailed("rate limited (all LLM permits busy)")
        try:
            reasons: list[str] = []
            for candidate in self.candidates:
                breaker = self.breakers[id(candidate)]
                permit = breaker.allow()
                if permit is None:
                    reasons.append(f"{self._identity(candidate)}:circuit_open")
                    continue
                stream = None
                try:
                    stream_call = getattr(candidate, "stream_with_options")
                    stream = iter(stream_call(messages, options))
                    first, buffered = self._probe_stream(stream)
                    if first is None:
                        raise ValueError("empty_stream")
                except Exception as exc:
                    breaker.failure(permit)
                    self._cancel_stream(stream)
                    reasons.append(f"{self._identity(candidate)}:{type(exc).__name__}")
                    continue
                breaker.success(permit)
                try:
                    yield StreamEvent(
                        "selected_model",
                        data={
                            "model": self._identity(candidate),
                            "fallback_reason": ";".join(reasons) or None,
                        },
                    )
                    yield from buffered
                    yield from stream
                    return
                finally:
                    breaker.release(permit)
                    self._cancel_stream(stream)
            raise AllModelsFailed("all configured streaming Chat candidates failed")
        finally:
            self._release_permit(request_id)

    def _probe_stream(
        self, stream: Iterator[StreamEvent]
    ) -> tuple[StreamEvent | None, list[StreamEvent]]:
        output: queue.Queue[tuple[bool, Any]] = queue.Queue()

        def read() -> None:
            try:
                output.put((True, next(stream)))
            except BaseException as exc:
                output.put((False, exc))

        buffered: list[StreamEvent] = []
        deadline = time.monotonic() + self.first_packet_timeout
        while True:
            threading.Thread(target=read, daemon=True).start()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("effective first packet timed out")
            try:
                success, value = output.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError("effective first packet timed out") from exc
            if not success:
                if isinstance(value, StopIteration):
                    return None, buffered
                raise value
            event = value
            buffered.append(event)
            if is_effective_output(event, expose_thinking=self.expose_thinking):
                return event, buffered

    @staticmethod
    def _cancel_stream(stream: Iterable[StreamEvent] | None) -> None:
        cancel = getattr(stream, "cancel", None)
        if callable(cancel):
            cancel()
            return
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except (RuntimeError, ValueError):
                # A timed-out synchronous iterator may still be blocked in the
                # probe thread. Provider adapters must cancel their HTTP request;
                # the routing layer must still continue to the next candidate.
                pass
