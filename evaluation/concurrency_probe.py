from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from deepsearcher.agent import DeepSearch
from deepsearcher.configuration import Configuration, build_runtime
from evaluation.benchmark import DEFAULT_CONFIG, ROOT, load_env_file

DEFAULT_QUERIES = (
    "Milvus 是什么？",
    "Milvus 支持哪些部署模式？",
    "Milvus 使用什么开源许可证？",
    "Milvus 支持哪些向量索引？",
    "Milvus 如何利用硬件加速？",
    "Milvus 支持哪些搜索方式？",
    "Milvus 提供哪些 SDK？",
    "Milvus 有哪些生态工具？",
)


async def run_retrieval_batch(
    agent: DeepSearch,
    queries: Sequence[str],
    *,
    collection: str,
    concurrency: int,
    top_k: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    heartbeat_done = False
    heartbeat_gaps: list[float] = []

    async def heartbeat() -> None:
        previous = asyncio.get_running_loop().time()
        while not heartbeat_done:
            await asyncio.sleep(0.005)
            current = asyncio.get_running_loop().time()
            heartbeat_gaps.append((current - previous) * 1000)
            previous = current

    heartbeat_task = asyncio.create_task(heartbeat())
    started = time.perf_counter()
    try:
        results = await asyncio.gather(
            *(
                agent._retrieve_chunks_from_vectordb(
                    query,
                    collection_names=[collection],
                    allowed_collections=[collection],
                    top_k=top_k,
                    semaphore=semaphore,
                    timeout_seconds=timeout_seconds,
                )
                for query in queries
            )
        )
    finally:
        heartbeat_done = True
        await heartbeat_task
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "elapsed_ms": round(elapsed_ms, 3),
        "event_loop_max_gap_ms": round(max(heartbeat_gaps, default=0.0), 3),
        "result_count": sum(len(result[0]) for result in results),
    }


async def run_probe(
    agent: DeepSearch,
    *,
    collection: str,
    counts: Sequence[int],
    max_concurrency: int,
    top_k: int,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    measurements = []
    for count in counts:
        queries = DEFAULT_QUERIES[:count]
        serial = await run_retrieval_batch(
            agent,
            queries,
            collection=collection,
            concurrency=1,
            top_k=top_k,
            timeout_seconds=timeout_seconds,
        )
        bounded_concurrency = min(max_concurrency, count)
        concurrent = await run_retrieval_batch(
            agent,
            queries,
            collection=collection,
            concurrency=bounded_concurrency,
            top_k=top_k,
            timeout_seconds=timeout_seconds,
        )
        measurements.append(
            {
                "query_count": count,
                "bounded_concurrency": bounded_concurrency,
                "serial": serial,
                "concurrent": concurrent,
                "speedup": round(
                    serial["elapsed_ms"] / concurrent["elapsed_ms"],
                    3,
                ),
            }
        )
    return measurements


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeepSearch 真实 I/O 并发探针")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--collection", required=True)
    parser.add_argument("--counts", default="1,2,4,8")
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        counts = tuple(int(value.strip()) for value in args.counts.split(","))
    except ValueError as exc:
        raise SystemExit("counts must be comma-separated integers") from exc
    if (
        not counts
        or any(count < 1 or count > len(DEFAULT_QUERIES) for count in counts)
        or args.max_concurrency < 1
        or args.top_k < 1
        or args.timeout_seconds <= 0
    ):
        raise SystemExit("counts, concurrency, top-k and timeout must be within valid ranges")

    load_env_file(args.env_file)
    config = Configuration(str(args.config))
    runtime = build_runtime(config)
    agent = DeepSearch(
        llm=runtime.llm,
        embedding_model=runtime.embedding_model,
        vector_db=runtime.vector_db,
        route_collection=True,
        retrieval_concurrency=args.max_concurrency,
        external_call_timeout_seconds=args.timeout_seconds,
    )
    measurements = asyncio.run(
        run_probe(
            agent,
            collection=args.collection,
            counts=counts,
            max_concurrency=args.max_concurrency,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
        )
    )
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "collection": args.collection,
        "counts": list(counts),
        "max_concurrency": args.max_concurrency,
        "top_k": args.top_k,
        "timeout_seconds": args.timeout_seconds,
        "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
        "providers": {
            feature: config.get_provider_config(feature)["provider"]
            for feature in ("embedding", "vector_db")
        },
        "measurements": measurements,
        "cancellation_note": (
            "Timeout or cancellation stops awaiting and discards late results. "
            "Python cannot forcibly terminate a synchronous call after its worker thread starts; "
            "its concurrency slot remains occupied until the worker actually finishes."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
