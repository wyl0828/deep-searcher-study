import asyncio
import time

from evaluation.concurrency_probe import run_probe


class FakeAsyncAgent:
    async def _retrieve_chunks_from_vectordb(
        self,
        query,
        *,
        semaphore,
        **_kwargs,
    ):
        async with semaphore:
            await asyncio.to_thread(time.sleep, 0.08)
        return [], 0, {"source": query}


def test_probe_compares_serial_and_bounded_concurrency():
    measurements = asyncio.run(
        run_probe(
            FakeAsyncAgent(),
            collection="kb_test",
            counts=(1, 2, 4),
            max_concurrency=4,
            top_k=1,
            timeout_seconds=1,
        )
    )

    assert [item["query_count"] for item in measurements] == [1, 2, 4]
    assert measurements[2]["bounded_concurrency"] == 4
    assert measurements[2]["speedup"] > 1.5
    assert measurements[2]["concurrent"]["event_loop_max_gap_ms"] < 50
