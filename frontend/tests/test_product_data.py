import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import (
    Base,
    DatabaseSchemaError,
    create_database_engine,
    ensure_citation_locator_columns,
    ensure_ingest_lifecycle_columns,
    ensure_knowledge_base_index_columns,
    record_worker_heartbeat,
    recover_interrupted_work,
    repair_citation_display_names,
    required_alembic_heads,
    validate_alembic_schema,
    worker_is_ready,
)
from frontend.product.models import (
    Citation,
    Conversation,
    Document,
    IngestJob,
    KnowledgeBase,
    Message,
)
from frontend.product.repositories import (
    create_ingest_job,
    create_knowledge_base,
    list_knowledge_bases,
)
from frontend.product.services import documents as document_service


def make_session(tmp_path) -> Session:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'product.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_required_alembic_revision_matches_repository_head():
    assert required_alembic_heads() == ("20260811_0013",)


def test_postgresql_schema_validation_rejects_missing_revision(monkeypatch):
    engine = MagicMock()
    connection = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    context = MagicMock()
    context.get_current_heads.return_value = ()
    monkeypatch.setattr(
        "frontend.product.db.MigrationContext.configure",
        lambda _connection: context,
    )
    monkeypatch.setattr(
        "frontend.product.db.required_alembic_heads",
        lambda: ("20260811_0013",),
    )

    with pytest.raises(DatabaseSchemaError, match="alembic upgrade head"):
        validate_alembic_schema(engine)


def test_postgresql_schema_validation_accepts_current_revision(monkeypatch):
    engine = MagicMock()
    connection = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    context = MagicMock()
    context.get_current_heads.return_value = ("20260811_0013",)
    monkeypatch.setattr(
        "frontend.product.db.MigrationContext.configure",
        lambda _connection: context,
    )
    monkeypatch.setattr(
        "frontend.product.db.required_alembic_heads",
        lambda: ("20260811_0013",),
    )

    validate_alembic_schema(engine)


def test_postgresql_claim_uses_skip_locked_and_preserves_claim_semantics():
    candidate = IngestJob(document_id="doc-1", status="queued", retry_count=0)
    candidate.id = "job-1"
    document = Document(
        id="doc-1",
        knowledge_base_id="kb-1",
        display_name="paper.pdf",
        storage_path="paper.pdf",
        size_bytes=1,
        sha256="a" * 64,
        status="queued",
    )
    session = MagicMock()
    session.get_bind.return_value = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    captured = {}

    def scalar(query):
        captured["query"] = query
        return candidate

    session.scalar.side_effect = scalar
    session.get.return_value = document

    claimed = document_service.claim_next_ingest_job(session, worker_id="worker-1")

    sql = str(captured["query"].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert claimed == "job-1"
    assert candidate.status == "processing"
    assert candidate.lease_owner == "worker-1"
    assert candidate.retry_count == 1
    assert document.status == "processing"
    session.commit.assert_called_once()


def test_knowledge_bases_are_persistent_and_only_one_is_current(tmp_path):
    with make_session(tmp_path) as session:
        first = create_knowledge_base(session, name="AI 学习资料", description="核心资料")
        second = create_knowledge_base(session, name="产品文档", description="")

        assert first.is_current is True
        assert second.is_current is False
        assert first.collection_name.startswith("kb_")
        assert second.collection_name.startswith("kb_")
        assert first.collection_name != second.collection_name

        items = list_knowledge_bases(session)
        assert [item["name"] for item in items] == ["AI 学习资料", "产品文档"]
        assert items[0]["document_count"] == 0


def test_legacy_database_adds_collection_manifest_columns(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE knowledge_bases ("
                "id VARCHAR(36) PRIMARY KEY, "
                "name VARCHAR(120) NOT NULL"
                ")"
            )
        )

    ensure_knowledge_base_index_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("knowledge_bases")}
    assert "index_manifest" in columns
    assert "index_previous_collection" in columns


def test_legacy_database_adds_citation_locator_columns(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'legacy-citations.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE citations (id VARCHAR(36) PRIMARY KEY, text TEXT NOT NULL)")
        )
        connection.execute(text("INSERT INTO citations (id, text) VALUES ('legacy', 'evidence')"))

    ensure_citation_locator_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("citations")}
    assert {
        "section_title",
        "section_path",
        "char_start",
        "char_end",
        "bbox",
        "location_id",
        "source_locator",
        "parser_version",
        "extraction_method",
        "source_type",
        "source_url",
        "source_domain",
        "trusted",
    } <= columns
    with engine.connect() as connection:
        legacy = connection.execute(
            text("SELECT source_type, source_url, source_domain, trusted FROM citations")
        ).one()
    assert legacy.source_type == "knowledge_base"
    assert legacy.source_url is None
    assert legacy.source_domain is None
    assert bool(legacy.trusted) is True


def test_legacy_database_adds_document_and_ingest_lifecycle_columns(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'legacy-ingest.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE documents (id VARCHAR(40) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE ingest_jobs (id VARCHAR(40) PRIMARY KEY, status VARCHAR(20) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO ingest_jobs (id, status) VALUES ('legacy-job', 'queued')")
        )

    ensure_ingest_lifecycle_columns(engine)

    document_columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    job_columns = {column["name"] for column in inspect(engine).get_columns("ingest_jobs")}
    assert "page_count" in document_columns
    assert {
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "retry_count",
        "max_retries",
        "error_code",
        "error_message",
    } <= job_columns
    with engine.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT available_at, retry_count, max_retries "
                "FROM ingest_jobs WHERE id = 'legacy-job'"
            )
        ).one()
    assert legacy.available_at is not None
    assert legacy.retry_count == 0
    assert legacy.max_retries == 3


def test_restart_recovery_requeues_expired_processing_lease(tmp_path):
    with make_session(tmp_path) as session:
        knowledge_base = create_knowledge_base(session, name="测试库", description="")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="paper.pdf",
            storage_path=str(tmp_path / "paper.pdf"),
            size_bytes=100,
            sha256="a" * 64,
            status="processing",
        )
        session.add(document)
        session.flush()
        session.add(IngestJob(document_id=document.id, status="processing"))
        session.commit()

        assert recover_interrupted_work(session) == 2

        recovered_document = session.scalar(select(Document).where(Document.id == document.id))
        assert recovered_document is not None
        assert recovered_document.status == "queued"
        assert recovered_document.error_code is None
        recovered_job = session.scalar(
            select(IngestJob).where(IngestJob.document_id == document.id)
        )
        assert recovered_job is not None
        assert recovered_job.status == "queued"
        assert recovered_job.lease_owner is None
        assert recovered_job.lease_expires_at is None
        assert recovered_job.error_code == "PROCESS_INTERRUPTED"


def test_worker_heartbeat_reports_running_and_stopped_states(tmp_path):
    with make_session(tmp_path) as session:
        record_worker_heartbeat(
            session,
            worker_name="document-ingest",
            worker_id="worker-1",
            status="running",
        )
        assert worker_is_ready(session, worker_name="document-ingest") is True

        record_worker_heartbeat(
            session,
            worker_name="document-ingest",
            worker_id="worker-1",
            status="stopped",
        )
        assert worker_is_ready(session, worker_name="document-ingest") is False


def test_startup_repairs_legacy_citation_display_names(tmp_path):
    with make_session(tmp_path) as session:
        knowledge_base = create_knowledge_base(session, name="测试库", description="")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="原始文件名.pdf",
            storage_path=str(tmp_path / "paper.pdf"),
            size_bytes=100,
            sha256="c" * 64,
            status="ready",
        )
        conversation = Conversation(knowledge_base_id=knowledge_base.id)
        session.add_all([document, conversation])
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="答案",
            status="succeeded",
        )
        session.add(message)
        session.flush()
        citation = Citation(
            message_id=message.id,
            document_id=document.id,
            index=1,
            display_name=f"{document.id}.pdf",
            text="证据",
        )
        session.add(citation)
        session.commit()

        assert repair_citation_display_names(session) == 1
        session.refresh(citation)
        assert citation.display_name == "原始文件名.pdf"


def test_document_processing_uses_supported_embedding_batch_size(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'product.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    captured_payloads: list[dict[str, object]] = []

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="测试库", description="")
        upload_root = tmp_path / "uploads"
        source_path = upload_root / knowledge_base.id / "random.pdf"
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(b"%PDF-1.7\nworker")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="paper.pdf",
            storage_path=str(source_path),
            size_bytes=100,
            sha256="b" * 64,
            status="queued",
        )
        session.add(document)
        session.flush()
        create_ingest_job(session, document)
        session.commit()
        document_id = document.id

    class FakeResponse:
        is_success = True

        @staticmethod
        def json():
            return {
                "collection": {
                    "manifest": {
                        "schema_version": 1,
                        "embedding_provider": "TestEmbedding",
                        "embedding_model": "embedding-a",
                        "embedding_version": "embedding-a-v1",
                        "dimension": 8,
                    }
                }
            }

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json):
            captured_payloads.append(json)
            return FakeResponse()

    monkeypatch.setattr(document_service, "SessionLocal", factory)
    monkeypatch.setattr(document_service, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(document_service.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(document_service.process_document(document_id))

    assert captured_payloads == [
        {
            "paths": str(source_path),
            "collection_name": knowledge_base.collection_name,
            "batch_size": 10,
            "replace_document_id": "b" * 64,
            "document_metadata": {},
        }
    ]
    with factory() as session:
        persisted = session.get(Document, document_id)
        assert persisted is not None
        assert persisted.status == "ready"
        persisted_knowledge_base = session.get(
            KnowledgeBase,
            persisted.knowledge_base_id,
        )
        assert persisted_knowledge_base is not None
        assert '"embedding_model":"embedding-a"' in (persisted_knowledge_base.index_manifest or "")
