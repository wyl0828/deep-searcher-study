"""P5 distributed fair semaphore + RoutingLLM integration tests."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import fakeredis

from deepsearcher.llm import routing
from deepsearcher.llm.redis_semaphore import DistributedFairSemaphore, PermitState


def _sem(redis=None, *, permits=1, **kwargs):
    return DistributedFairSemaphore(
        redis=redis or fakeredis.FakeRedis(),
        permits=permits,
        poll_interval_ms=2,
        max_wait_ms=200,
        **kwargs,
    )


def test_acquire_and_release():
    sem = _sem(permits=1)
    ok, state = sem.acquire("r1")
    assert ok and state == PermitState.GRANTED
    sem.release("r1")


def test_second_waiter_times_out_with_no_permit():
    sem = _sem(permits=1)
    assert sem.acquire("r1")[0] is True
    ok, state = sem.acquire("r2", max_wait_ms=60)
    assert ok is False and state == PermitState.TIMED_OUT
    # r2 has been evicted from the queue.
    assert "r2" not in set(sem.redis.zrange(sem.queue_key, 0, -1))
    sem.release("r1")


def test_multi_instance_share_same_quota():
    redis = fakeredis.FakeRedis()
    sem_a = _sem(redis, permits=2)
    sem_b = _sem(redis, permits=2)
    assert sem_a.acquire("a1")[0] is True
    assert sem_b.acquire("b1")[0] is True
    # shared quota of 2 is exhausted across two instances -> third waits/times out
    assert sem_a.acquire("a2", max_wait_ms=60)[0] is False
    # after b1 releases, a2 can be granted by the same shared pool
    sem_b.release("b1")
    assert sem_a.acquire("a2", max_wait_ms=300)[0] is True


def test_release_unblocks_fair_waiter():
    sem = _sem(permits=1)
    assert sem.acquire("r1")[0] is True
    result: dict = {}
    t = threading.Thread(
        target=lambda: result.setdefault("state", sem.acquire("r2", max_wait_ms=800))
    )
    t.start()
    time.sleep(0.05)  # let r2 enqueue and wait
    sem.release("r1")
    t.join(timeout=2)
    assert result["state"][0] is True


def test_zombie_entry_cleaned_by_claim():
    sem = _sem(permits=1)
    sem.redis.zadd(sem.queue_key, {"dead": 5})  # zombie without entry marker
    ok, _ = sem.acquire("live")
    assert ok is True
    assert "dead" not in set(sem.redis.zrange(sem.queue_key, 0, -1))


# ---- RoutingLLM integration ----


class _FakeLimiter:
    def __init__(self):
        self.acquired: list[str] = []
        self.released: list[str] = []

    def acquire(self, request_id, *, max_wait_ms=None):
        self.acquired.append(request_id)
        return True, PermitState.GRANTED

    def release(self, request_id):
        self.released.append(request_id)


class _FakeLLM:
    model = "fake"

    def chat_with_options(self, messages, options=None):
        return SimpleNamespace(content="hello", model="fake", fallback_reason=None)


def test_routing_llm_acquires_and_releases_permit():
    limiter = _FakeLimiter()
    llm = routing.RoutingLLM(candidates=[_FakeLLM()], limiter=limiter)
    response = llm.chat_with_options([])
    assert response.content == "hello"
    assert len(limiter.acquired) == 1
    assert len(limiter.released) == 1


def test_routing_llm_releases_permit_on_failure():
    class _BoomLLM(_FakeLLM):
        def chat_with_options(self, messages, options=None):
            raise ValueError("boom")

    limiter = _FakeLimiter()
    llm = routing.RoutingLLM(candidates=[_BoomLLM()], limiter=limiter)
    try:
        llm.chat_with_options([])
    except Exception:
        pass
    assert len(limiter.acquired) == 1
    assert len(limiter.released) == 1  # released even on failure
