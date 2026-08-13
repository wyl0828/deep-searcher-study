from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from frontend.product.models import Document, IngestJob, utcnow

logger = logging.getLogger(__name__)


class MessageDispatchError(RuntimeError):
    pass


def configured_ingest_transport() -> str:
    value = os.environ.get("DEEPSEARCHER_INGEST_TRANSPORT", "local").strip().lower()
    if value not in {"local", "rocketmq"}:
        raise MessageDispatchError("DEEPSEARCHER_INGEST_TRANSPORT must be local or rocketmq")
    return value


def rocketmq_enabled() -> bool:
    return configured_ingest_transport() == "rocketmq"


@dataclass(frozen=True)
class RocketMQSettings:
    endpoints: str
    topic: str
    consumer_group: str
    invisible_seconds: int

    @classmethod
    def from_environment(cls) -> "RocketMQSettings":
        endpoints = os.environ.get("DEEPSEARCHER_ROCKETMQ_ENDPOINTS", "").strip()
        if not endpoints:
            raise MessageDispatchError(
                "DEEPSEARCHER_ROCKETMQ_ENDPOINTS is required for RocketMQ ingestion"
            )
        topic = os.environ.get(
            "DEEPSEARCHER_ROCKETMQ_INGEST_TOPIC", "deepsearcher-document-ingest"
        ).strip()
        consumer_group = os.environ.get(
            "DEEPSEARCHER_ROCKETMQ_CONSUMER_GROUP", "deepsearcher-document-ingest"
        ).strip()
        try:
            invisible_seconds = int(
                os.environ.get("DEEPSEARCHER_ROCKETMQ_INVISIBLE_SECONDS", "900")
            )
        except ValueError as exc:
            raise MessageDispatchError(
                "DEEPSEARCHER_ROCKETMQ_INVISIBLE_SECONDS must be an integer"
            ) from exc
        if not topic or not consumer_group or invisible_seconds < 10:
            raise MessageDispatchError("invalid RocketMQ ingestion settings")
        return cls(endpoints, topic, consumer_group, invisible_seconds)


def _rocketmq_types() -> tuple[Any, ...]:
    try:
        from rocketmq import (
            ClientConfiguration,
            Credentials,
            Message,
            Producer,
            TransactionChecker,
            TransactionResolution,
        )
    except ImportError as exc:
        raise MessageDispatchError("RocketMQ ingestion requires rocketmq-python-client") from exc
    return (
        ClientConfiguration,
        Credentials,
        Message,
        Producer,
        TransactionChecker,
        TransactionResolution,
    )


class _DocumentTransactionChecker:
    def __init__(self, transaction_resolution: Any):
        self.transaction_resolution = transaction_resolution

    def check(self, message: Any) -> Any:
        from frontend.product.db import SessionLocal

        job_id = str(message.properties.get("job_id", ""))
        with SessionLocal() as session:
            job = session.get(IngestJob, job_id) if job_id else None
            committed = job is not None and job.status in {
                "processing",
                "succeeded",
                "dead_letter",
            }
        return (
            self.transaction_resolution.COMMIT
            if committed
            else self.transaction_resolution.ROLLBACK
        )


_producer_lock = threading.Lock()
_producer: Any | None = None
_producer_settings: RocketMQSettings | None = None


def get_transaction_producer(settings: RocketMQSettings | None = None) -> Any:
    global _producer, _producer_settings
    selected = settings or RocketMQSettings.from_environment()
    with _producer_lock:
        if _producer is not None and _producer_settings == selected:
            return _producer
        if _producer is not None:
            _producer.shutdown()
        (
            ClientConfiguration,
            Credentials,
            _Message,
            Producer,
            _TransactionChecker,
            TransactionResolution,
        ) = _rocketmq_types()
        checker = _DocumentTransactionChecker(TransactionResolution)
        producer = Producer(
            ClientConfiguration(selected.endpoints, Credentials()),
            (selected.topic,),
            checker=checker,
        )
        producer.startup()
        _producer = producer
        _producer_settings = selected
        return producer


def shutdown_transaction_producer() -> None:
    global _producer, _producer_settings
    with _producer_lock:
        producer = _producer
        _producer = None
        _producer_settings = None
    if producer is not None:
        producer.shutdown()


def dispatch_ingest_transaction(
    session: Session,
    *,
    document: Document,
    job: IngestJob,
    producer: Any | None = None,
    settings: RocketMQSettings | None = None,
) -> None:
    """Atomically bind the queued->processing transition to MQ visibility."""

    if not rocketmq_enabled() and producer is None:
        return
    selected = settings or RocketMQSettings.from_environment()
    active_producer = producer or get_transaction_producer(selected)
    (_ClientConfiguration, _Credentials, Message, _Producer, _Checker, _Resolution) = (
        _rocketmq_types()
    )
    message = Message()
    message.topic = selected.topic
    message.tag = "document-ingest"
    message.keys = job.id
    message.body = document.id.encode("utf-8")
    message.add_property("document_id", document.id)
    message.add_property("job_id", job.id)
    transaction = active_producer.begin_transaction()
    try:
        active_producer.send(message, transaction)
    except Exception as exc:
        raise MessageDispatchError("failed to send RocketMQ half message") from exc

    try:
        current_document = session.get(Document, document.id)
        current_job = session.get(IngestJob, job.id)
        if (
            current_document is None
            or current_job is None
            or current_document.status != "queued"
            or current_job.status != "queued"
        ):
            raise MessageDispatchError("document ingest state is not dispatchable")
        now = utcnow()
        current_document.status = "processing"
        current_document.error_code = None
        current_document.error_message = None
        current_job.status = "processing"
        current_job.started_at = now
        current_job.finished_at = None
        current_job.lease_owner = None
        current_job.lease_expires_at = None
        session.commit()
    except Exception:
        session.rollback()
        try:
            transaction.rollback()
        except Exception:
            logger.warning("rocketmq_transaction_rollback_confirmation_failed job_id=%s", job.id)
        raise

    try:
        transaction.commit()
    except Exception:
        # The database is the transaction check source of truth. A lost second ACK
        # is resolved by Broker check; rolling the database back here would be wrong.
        logger.warning("rocketmq_transaction_commit_confirmation_pending job_id=%s", job.id)
