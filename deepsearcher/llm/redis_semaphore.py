"""Distributed fair semaphore (aligned with ragent FairDistributedRateLimiter).

Multiple API instances share one LLM/embedding quota. A Redis ZSet holds the fair
queue (score = arrival sequence); an entry TTL marker per request lets the Lua
claim atomically drop zombie entries; permit consumption/release is atomic too,
so concurrent instances never overdraw the shared quota and late requests queue
instead of being rejected outright.

Synchronous implementation (matches the synchronous LLM call path):
PENDING -> GRANTED | TIMED_OUT.
"""

from __future__ import annotations

import enum
import time
import uuid
from typing import Any

_CLAIM_LUA = """
local queueKey = KEYS[1]
local permitKey = KEYS[2]
local requestId = ARGV[1]
local maxRank = tonumber(ARGV[2])
local entryPrefix = ARGV[3]
local permits = tonumber(redis.call("GET", permitKey) or "0")
if permits <= 0 then return {0} end
local slack = 16
local head = redis.call("ZRANGE", queueKey, 0, maxRank + slack - 1)
local liveRank = -1
local liveCount = 0
for i = 1, #head do
    local member = head[i]
    if redis.call("EXISTS", entryPrefix .. member) == 1 then
        if member == requestId then liveRank = liveCount end
        liveCount = liveCount + 1
    else
        redis.call("ZREM", queueKey, member)
    end
end
if liveRank < 0 or liveRank >= maxRank then return {0} end
redis.call("DECR", permitKey)
redis.call("ZREM", queueKey, requestId)
redis.call("DEL", entryPrefix .. requestId)
return {1}
"""

_ENQUEUE_LUA = """
redis.call("ZADD", KEYS[1], tonumber(ARGV[2]), ARGV[1])
return 1
"""

_RELEASE_LUA = """
redis.call("INCR", KEYS[1])
return redis.call("GET", KEYS[1])
"""


class PermitState(str, enum.Enum):
    PENDING = "pending"
    GRANTED = "granted"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class DistributedFairSemaphore:
    """Synchronous distributed fair semaphore over Redis atomics."""

    def __init__(
        self,
        *,
        redis: Any,
        permits: int = 1,
        queue_key: str = "deepsearcher:rl:queue",
        seq_key: str = "deepsearcher:rl:seq",
        permit_key: str = "deepsearcher:rl:permits",
        entry_prefix: str = "deepsearcher:rl:entry:",
        poll_interval_ms: float = 20.0,
        max_wait_ms: int = 30000,
    ) -> None:
        if permits <= 0:
            raise ValueError("permits 必须大于 0")
        self.redis = redis
        self.permits = permits
        self.queue_key = queue_key
        self.seq_key = seq_key
        self.permit_key = permit_key
        self.entry_prefix = entry_prefix
        self.poll_interval = poll_interval_ms / 1000.0
        self.max_wait_ms = max_wait_ms
        self._granted: set[str] = set()
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        # Init the shared permit counter exactly once (NX) so multiple instances
        # touching the same Redis agree on the quota.
        self.redis.set(self.permit_key, self.permits, nx=True)
        self._initialized = True

    def _evict(self, request_id: str) -> None:
        self.redis.zrem(self.queue_key, request_id)
        self.redis.delete(self.entry_prefix + request_id)
        self._granted.discard(request_id)

    @staticmethod
    def _was_granted(result: Any) -> bool:
        return isinstance(result, (list, tuple)) and bool(result) and int(result[0]) == 1

    def acquire(
        self,
        request_id: str | None = None,
        *,
        max_wait_ms: int | None = None,
    ) -> tuple[bool, PermitState]:
        """Enqueue and wait for a permit. (True, GRANTED) on grant, (False, TIMED_OUT)."""
        self._ensure_initialized()
        request_id = request_id or f"{uuid.uuid4().hex}"
        wait_ms = max_wait_ms if max_wait_ms is not None else self.max_wait_ms
        deadline = time.monotonic() + wait_ms / 1000.0

        self.redis.set(self.entry_prefix + request_id, "1", ex=max(1, wait_ms // 1000 + 10))
        seq = self.redis.incr(self.seq_key)
        self.redis.eval(_ENQUEUE_LUA, 1, self.queue_key, request_id, seq)

        while True:
            result = self.redis.eval(
                _CLAIM_LUA,
                2,
                self.queue_key,
                self.permit_key,
                request_id,
                self.permits,
                self.entry_prefix,
            )
            if self._was_granted(result):
                self._granted.add(request_id)
                return True, PermitState.GRANTED
            if time.monotonic() >= deadline:
                self._evict(request_id)
                return False, PermitState.TIMED_OUT
            time.sleep(self.poll_interval)

    def release(self, request_id: str) -> None:
        """Release a granted permit back to the shared pool."""
        if request_id not in self._granted:
            return
        self.redis.eval(_RELEASE_LUA, 1, self.permit_key)
        self._granted.discard(request_id)
