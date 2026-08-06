from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from numbers import Real
from time import monotonic
from typing import Any

HEALTH_COMPONENTS = ("runtime", "vector_db", "llm", "embedding")


class InvalidHealthProbeResponse(RuntimeError):
    """Internal marker for a dependency call that returned an unusable result."""


def ready_check() -> dict[str, Any]:
    return {"status": "ready"}


def unknown_check(code: str) -> dict[str, Any]:
    return {"status": "unknown", "code": code, "retryable": False}


def failed_check(code: str, *, retryable: bool) -> dict[str, Any]:
    return {"status": "not_ready", "code": code, "retryable": retryable}


def blocked_checks(code: str = "PROBE_NOT_RUN") -> dict[str, dict[str, Any]]:
    return {component: unknown_check(code) for component in HEALTH_COMPONENTS}


class RuntimeHealthMonitor:
    """Probe runtime dependencies without exposing provider exception details."""

    def __init__(self, *, timeout_seconds: float = 8.0, cache_seconds: float = 300.0):
        self.timeout_seconds = max(0.1, timeout_seconds)
        self.cache_seconds = max(0.0, cache_seconds)
        self._provider_cache: tuple[int, float, dict[str, dict[str, Any]]] | None = None
        self._provider_lock = asyncio.Lock()

    @staticmethod
    def _is_authentication_error(exc: Exception) -> bool:
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            status_code = getattr(current, "status_code", None)
            response = getattr(current, "response", None)
            response_status = getattr(response, "status_code", None)
            type_name = type(current).__name__.lower()
            if status_code in {401, 403} or response_status in {401, 403}:
                return True
            if any(
                marker in type_name
                for marker in ("authentication", "permission", "unauthorized", "forbidden")
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    async def _run_probe(
        self,
        operation: Callable[[], Any],
        *,
        provider: bool = False,
    ) -> dict[str, Any]:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(operation),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return failed_check("DEPENDENCY_TIMEOUT", retryable=True)
        except InvalidHealthProbeResponse:
            return failed_check("DEPENDENCY_INVALID_RESPONSE", retryable=True)
        except Exception as exc:
            if provider and self._is_authentication_error(exc):
                return failed_check("PROVIDER_AUTH_FAILED", retryable=False)
            return failed_check("DEPENDENCY_UNAVAILABLE", retryable=True)
        return ready_check()

    async def _provider_checks(self, runtime: Any) -> dict[str, dict[str, Any]]:
        runtime_key = id(runtime)
        async with self._provider_lock:
            now = monotonic()
            if self._provider_cache is not None:
                cached_key, expires_at, cached_checks = self._provider_cache
                if cached_key == runtime_key and now < expires_at:
                    return {name: dict(check) for name, check in cached_checks.items()}

            llm = getattr(runtime, "llm", None)
            embedding = getattr(runtime, "embedding_model", None)
            llm_operation = None
            if llm is not None and callable(getattr(llm, "chat", None)):

                def probe_llm():
                    response = llm.chat(
                        [
                            {
                                "role": "user",
                                "content": "Health check. Reply with OK only.",
                            }
                        ]
                    )
                    content = getattr(response, "content", None)
                    if not isinstance(content, str) or not content.strip():
                        raise InvalidHealthProbeResponse()
                    return response

                llm_operation = probe_llm

            embedding_operation = None
            if embedding is not None and callable(getattr(embedding, "embed_query", None)):

                def probe_embedding():
                    vector = embedding.embed_query("health check")
                    if not isinstance(vector, (list, tuple)) or not vector:
                        raise InvalidHealthProbeResponse()
                    if any(
                        isinstance(value, bool)
                        or not isinstance(value, Real)
                        or not math.isfinite(float(value))
                        for value in vector
                    ):
                        raise InvalidHealthProbeResponse()
                    dimension = getattr(embedding, "dimension", None)
                    if (
                        isinstance(dimension, int)
                        and not isinstance(dimension, bool)
                        and dimension > 0
                        and len(vector) != dimension
                    ):
                        raise InvalidHealthProbeResponse()
                    return vector

                embedding_operation = probe_embedding

            async def probe_or_missing(operation: Callable[[], Any] | None) -> dict[str, Any]:
                if operation is None:
                    return failed_check("RUNTIME_COMPONENT_MISSING", retryable=False)
                return await self._run_probe(operation, provider=True)

            llm_check, embedding_check = await asyncio.gather(
                probe_or_missing(llm_operation),
                probe_or_missing(embedding_operation),
            )
            checks = {"llm": llm_check, "embedding": embedding_check}
            self._provider_cache = (
                runtime_key,
                monotonic() + self.cache_seconds,
                checks,
            )
            return {name: dict(check) for name, check in checks.items()}

    async def inspect(self, runtime: Any, *, deep: bool) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {
            "runtime": ready_check(),
            "vector_db": unknown_check("PROBE_NOT_RUN"),
            "llm": unknown_check("DEEP_PROBE_NOT_RUN"),
            "embedding": unknown_check("DEEP_PROBE_NOT_RUN"),
        }
        vector_db = getattr(runtime, "vector_db", None)
        vector_probe = getattr(vector_db, "health_check", None)
        if not callable(vector_probe):
            vector_probe = getattr(vector_db, "list_collections", None)
        if vector_db is None or not callable(vector_probe):
            checks["vector_db"] = failed_check(
                "RUNTIME_COMPONENT_MISSING",
                retryable=False,
            )
            return {"status": "not_ready", "checks": checks}

        checks["vector_db"] = await self._run_probe(vector_probe)
        if checks["vector_db"]["status"] != "ready":
            checks["llm"] = unknown_check("BLOCKED_BY_REQUIRED_DEPENDENCY")
            checks["embedding"] = unknown_check("BLOCKED_BY_REQUIRED_DEPENDENCY")
            return {"status": "not_ready", "checks": checks}

        if not deep:
            return {"status": "ready", "checks": checks}

        checks.update(await self._provider_checks(runtime))
        provider_failed = any(
            checks[component]["status"] != "ready" for component in ("llm", "embedding")
        )
        return {
            "status": "degraded" if provider_failed else "ready",
            "checks": checks,
        }
