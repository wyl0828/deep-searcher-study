from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontend.product import messaging
from frontend.product.models import Base, Document, IngestJob, utcnow
from frontend.product.repositories import create_ingest_job, create_knowledge_base
from frontend.product.services import documents


class FakeTransaction:
    def __init__(self, *, commit_error: Exception | None = None):
        self.committed = False
        self.rolled_back = False
        self.commit_error = commit_error

    def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class FakeProducer:
    def __init__(self, *, send_error: Exception | None = None, commit_error=None):
        self.transaction = FakeTransaction(commit_error=commit_error)
        self.send_error = send_error
        self.sent = []

    def begin_transaction(self):
        return self.transaction

    def send(self, message, transaction):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)
        return SimpleNamespace(message_id="receipt-id")


class FakeMessage:
    def __init__(self):
        self.properties = {}

    def add_property(self, key, value):
        self.properties[key] = value


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'messages.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_queued_document(factory):
    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="消息库", description="")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="paper.pdf",
            storage_path="paper.pdf",
            size_bytes=10,
            sha256="a" * 64,
            status="queued",
        )
        session.add(document)
        session.flush()
        job = create_ingest_job(session, document)
        session.commit()
        return document.id, job.id


@pytest.fixture(autouse=True)
def rocketmq_mode(monkeypatch):
    monkeypatch.setenv("DEEPSEARCHER_INGEST_TRANSPORT", "rocketmq")
    monkeypatch.setenv("DEEPSEARCHER_ROCKETMQ_ENDPOINTS", "localhost:8081")
    monkeypatch.setattr(
        messaging,
        "_rocketmq_types",
        lambda: (object, object, FakeMessage, object, object, object),
    )


def test_dispatch_commits_state_then_message(tmp_path):
    factory = make_session(tmp_path)
    document_id, job_id = add_queued_document(factory)
    producer = FakeProducer()

    with factory() as session:
        messaging.dispatch_ingest_transaction(
            session,
            document=session.get(Document, document_id),
            job=session.get(IngestJob, job_id),
            producer=producer,
            settings=messaging.RocketMQSettings("localhost:8081", "topic", "group", 30),
        )

    assert producer.transaction.committed is True
    assert producer.sent[0].properties == {"document_id": document_id, "job_id": job_id}
    with factory() as session:
        assert session.get(Document, document_id).status == "processing"
        assert session.get(IngestJob, job_id).status == "processing"


def test_dispatch_rolls_back_half_message_when_database_transition_fails(tmp_path):
    factory = make_session(tmp_path)
    document_id, job_id = add_queued_document(factory)
    producer = FakeProducer()

    with factory() as session:
        document = session.get(Document, document_id)
        job = session.get(IngestJob, job_id)
        document.status = "failed"
        session.commit()
        with pytest.raises(messaging.MessageDispatchError, match="not dispatchable"):
            messaging.dispatch_ingest_transaction(
                session,
                document=document,
                job=job,
                producer=producer,
                settings=messaging.RocketMQSettings("localhost:8081", "topic", "group", 30),
            )

    assert producer.transaction.rolled_back is True


def test_transaction_checker_uses_persisted_job_status(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    _document_id, job_id = add_queued_document(factory)
    monkeypatch.setattr("frontend.product.db.SessionLocal", factory)
    resolution = SimpleNamespace(COMMIT="commit", ROLLBACK="rollback")
    checker = messaging._DocumentTransactionChecker(resolution)
    message = SimpleNamespace(properties={"job_id": job_id})

    assert checker.check(message) == "rollback"
    with factory() as session:
        session.get(IngestJob, job_id).status = "processing"
        session.commit()
    assert checker.check(message) == "commit"


def test_duplicate_completed_delivery_is_acked(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    _document_id, job_id = add_queued_document(factory)
    with factory() as session:
        session.get(IngestJob, job_id).status = "succeeded"
        session.commit()
    monkeypatch.setattr(documents, "SessionLocal", factory)

    assert (
        asyncio.run(
            documents.process_rocketmq_ingest_job(
                job_id,
                worker_id="worker-a",
                delivery_attempt=2,
            )
        )
        is True
    )


def test_expired_consumer_lease_can_be_reclaimed(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    _document_id, job_id = add_queued_document(factory)
    with factory() as session:
        job = session.get(IngestJob, job_id)
        job.status = "processing"
        job.lease_owner = "crashed-worker"
        job.lease_expires_at = utcnow()
        session.commit()
    monkeypatch.setattr(documents, "SessionLocal", factory)

    async def successful_load(**_kwargs):
        return {"schema_version": 1}

    monkeypatch.setattr(documents, "_load_document_into_backend", successful_load)

    assert (
        asyncio.run(
            documents.process_rocketmq_ingest_job(
                job_id,
                worker_id="replacement-worker",
                delivery_attempt=2,
                lease_seconds=30,
            )
        )
        is True
    )
    with factory() as session:
        assert session.get(IngestJob, job_id).status == "succeeded"


def test_final_retryable_delivery_moves_job_to_dead_letter(tmp_path, monkeypatch):
    factory = make_session(tmp_path)
    _document_id, job_id = add_queued_document(factory)
    with factory() as session:
        job = session.get(IngestJob, job_id)
        job.status = "processing"
        job.max_retries = 3
        session.commit()
    monkeypatch.setattr(documents, "SessionLocal", factory)

    async def failing_load(**_kwargs):
        raise documents.IngestProcessingError("DOCUMENT_PROCESSING_UNAVAILABLE", retryable=True)

    monkeypatch.setattr(documents, "_load_document_into_backend", failing_load)

    assert (
        asyncio.run(
            documents.process_rocketmq_ingest_job(
                job_id,
                worker_id="worker-a",
                delivery_attempt=3,
            )
        )
        is True
    )
    with factory() as session:
        job = session.get(IngestJob, job_id)
        assert job.status == "dead_letter"
        assert session.get(Document, job.document_id).status == "failed"
