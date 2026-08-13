from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from uuid import uuid4

from frontend.product.db import init_database
from frontend.product.messaging import RocketMQSettings
from frontend.product.services.documents import process_rocketmq_ingest_job

logger = logging.getLogger("deepsearcher.rocketmq_ingest_worker")


def _consumer(settings: RocketMQSettings):
    try:
        from rocketmq import ClientConfiguration, Credentials, FilterExpression, SimpleConsumer
    except ImportError as exc:
        raise RuntimeError("RocketMQ ingestion requires rocketmq-python-client") from exc
    return SimpleConsumer(
        ClientConfiguration(settings.endpoints, Credentials()),
        settings.consumer_group,
        {settings.topic: FilterExpression("document-ingest")},
        await_duration=5,
    )


def _renew_visibility(consumer, message, *, seconds: int, stop: threading.Event) -> None:
    interval = max(seconds // 2, 5)
    while not stop.wait(interval):
        try:
            consumer.change_invisible_duration(message, seconds)
        except Exception as exc:
            logger.warning(
                "rocketmq_visibility_renewal_failed message_id=%s exception_type=%s",
                message.message_id,
                type(exc).__name__,
            )


async def run_rocketmq_worker(*, once: bool = False) -> None:
    init_database()
    settings = RocketMQSettings.from_environment()
    consumer = _consumer(settings)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:12]}"
    consumer.startup()
    logger.info("rocketmq_ingest_worker_started worker_id=%s", worker_id)
    try:
        while True:
            try:
                messages = await asyncio.to_thread(
                    consumer.receive,
                    1,
                    settings.invisible_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "rocketmq_receive_failed exception_type=%s",
                    type(exc).__name__,
                )
                if once:
                    raise
                await asyncio.sleep(1)
                continue
            if not messages:
                if once:
                    return
                continue
            for message in messages:
                job_id = str(message.properties.get("job_id", ""))
                if not job_id:
                    logger.warning("rocketmq_ingest_message_missing_job_id")
                    consumer.ack(message)
                    continue
                stop_renewal = threading.Event()
                renewal = threading.Thread(
                    target=_renew_visibility,
                    args=(consumer, message),
                    kwargs={"seconds": settings.invisible_seconds, "stop": stop_renewal},
                    daemon=True,
                )
                renewal.start()
                try:
                    should_ack = await process_rocketmq_ingest_job(
                        job_id,
                        worker_id=worker_id,
                        delivery_attempt=int(message.delivery_attempt or 1),
                        lease_seconds=settings.invisible_seconds,
                    )
                finally:
                    stop_renewal.set()
                    renewal.join(timeout=2)
                if should_ack:
                    consumer.ack(message)
                if once:
                    return
    finally:
        consumer.shutdown()
        logger.info("rocketmq_ingest_worker_stopped worker_id=%s", worker_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_rocketmq_worker())


if __name__ == "__main__":
    main()
