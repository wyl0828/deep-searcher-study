from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

from frontend.product.db import (  # noqa: E402
    SessionLocal,
    init_database,
    record_worker_heartbeat,
    recover_interrupted_work,
)
from frontend.product.services.documents import (  # noqa: E402
    claim_next_ingest_job,
    process_claimed_ingest_job,
)

logger = logging.getLogger("deepsearcher.ingest_worker")
POLL_INTERVAL_SECONDS = 1.0
RECOVERY_INTERVAL_SECONDS = 30.0
HEARTBEAT_INTERVAL_SECONDS = 5.0
WORKER_NAME = "document-ingest"


async def heartbeat(worker_id: str, stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        with SessionLocal() as session:
            record_worker_heartbeat(
                session,
                worker_name=WORKER_NAME,
                worker_id=worker_id,
                status="running",
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run_worker(*, once: bool = False) -> None:
    init_database()
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:12]}"
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stopping.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal_name, lambda *_args: stopping.set())

    logger.info("ingest_worker_started worker_id=%s", worker_id)
    heartbeat_task = asyncio.create_task(heartbeat(worker_id, stopping))
    last_recovery = 0.0
    try:
        while not stopping.is_set():
            now = loop.time()
            if now - last_recovery >= RECOVERY_INTERVAL_SECONDS:
                from frontend.product.messaging import rocketmq_enabled

                if rocketmq_enabled():
                    recovered = 0
                else:
                    with SessionLocal() as session:
                        recovered = recover_interrupted_work(session)
                if recovered:
                    logger.warning("ingest_worker_recovered_items count=%s", recovered)
                last_recovery = now

            with SessionLocal() as session:
                job_id = claim_next_ingest_job(session, worker_id=worker_id)
            if job_id is not None:
                logger.info("ingest_job_started job_id=%s", job_id)
                try:
                    await process_claimed_ingest_job(job_id, worker_id=worker_id)
                except Exception as exc:
                    logger.error(
                        "ingest_job_unhandled_error job_id=%s exception_type=%s",
                        job_id,
                        type(exc).__name__,
                    )
                else:
                    logger.info("ingest_job_finished job_id=%s", job_id)
                if once:
                    return
                continue
            if once:
                return
            try:
                await asyncio.wait_for(stopping.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        stopping.set()
        await heartbeat_task
        with SessionLocal() as session:
            record_worker_heartbeat(
                session,
                worker_name=WORKER_NAME,
                worker_id=worker_id,
                status="stopped",
            )
        logger.info("ingest_worker_stopped worker_id=%s", worker_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from frontend.product.messaging import rocketmq_enabled

    if rocketmq_enabled():
        from frontend.product.rocketmq_worker import run_rocketmq_worker

        asyncio.run(run_rocketmq_worker())
    else:
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
