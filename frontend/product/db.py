from __future__ import annotations

import os
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "product"
DATA_DIR = Path(os.environ.get("DEEPSEARCHER_DATA_DIR", DEFAULT_DATA_DIR))
DATABASE_URL = os.environ.get(
    "DEEPSEARCHER_DATABASE_URL",
    f"sqlite:///{(DATA_DIR / 'deepsearcher-product.db').as_posix()}",
)


class Base(DeclarativeBase):
    pass


def create_database_engine(database_url: str = DATABASE_URL) -> Engine:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


ENGINE = create_database_engine()
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def recover_interrupted_work(session: Session) -> int:
    from frontend.product.models import Document, IngestJob, utcnow

    now = utcnow()
    interrupted_jobs = session.scalars(
        select(IngestJob).where(
            IngestJob.status == "processing",
            (IngestJob.lease_expires_at.is_(None)) | (IngestJob.lease_expires_at <= now),
        )
    ).all()
    recovered_documents: set[str] = set()
    for job in interrupted_jobs:
        job.status = "queued"
        job.available_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = "PROCESS_INTERRUPTED"
        job.error_message = "上次处理进程中断，任务已自动恢复。"
        document = session.get(Document, job.document_id)
        if document is not None:
            document.status = "queued"
            document.error_code = None
            document.error_message = None
            recovered_documents.add(document.id)
    session.commit()
    return len(interrupted_jobs) + len(recovered_documents)


def repair_citation_display_names(session: Session) -> int:
    from frontend.product.models import Citation, Document

    repaired = 0
    citations = session.scalars(select(Citation).where(Citation.document_id.is_not(None))).all()
    for citation in citations:
        document = session.get(Document, citation.document_id)
        if document is not None and citation.display_name != document.display_name:
            citation.display_name = document.display_name
            repaired += 1
    if repaired:
        session.commit()
    return repaired


def record_worker_heartbeat(
    session: Session,
    *,
    worker_name: str,
    worker_id: str,
    status: str,
) -> None:
    from frontend.product.models import WorkerHeartbeat, utcnow

    heartbeat = session.get(WorkerHeartbeat, worker_name)
    if heartbeat is None:
        heartbeat = WorkerHeartbeat(
            worker_name=worker_name,
            worker_id=worker_id,
            status=status,
            heartbeat_at=utcnow(),
        )
        session.add(heartbeat)
    else:
        heartbeat.worker_id = worker_id
        heartbeat.status = status
        heartbeat.heartbeat_at = utcnow()
    session.commit()


def worker_is_ready(
    session: Session,
    *,
    worker_name: str,
    max_age_seconds: int = 15,
) -> bool:
    from frontend.product.models import WorkerHeartbeat, utcnow

    heartbeat = session.get(WorkerHeartbeat, worker_name)
    if heartbeat is None or heartbeat.status != "running":
        return False
    threshold = utcnow() - timedelta(seconds=max_age_seconds)
    observed = heartbeat.heartbeat_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=threshold.tzinfo)
    return observed >= threshold


def ensure_knowledge_base_index_columns(engine: Engine) -> None:
    """Upgrade pre-manifest product databases without requiring a manual reset."""
    columns = {column["name"] for column in inspect(engine).get_columns("knowledge_bases")}
    if "index_manifest" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE knowledge_bases ADD COLUMN index_manifest TEXT"))
    if "index_previous_collection" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE knowledge_bases ADD COLUMN index_previous_collection VARCHAR(64)")
            )


def ensure_citation_locator_columns(engine: Engine) -> None:
    """Upgrade existing local citation rows with optional source locator fields."""
    columns = {column["name"] for column in inspect(engine).get_columns("citations")}
    definitions = {
        "section_title": "VARCHAR(255)",
        "section_path": "JSON",
        "char_start": "INTEGER",
        "char_end": "INTEGER",
        "bbox": "JSON",
        "location_id": "VARCHAR(64)",
        "source_locator": "VARCHAR(128)",
        "parser_version": "VARCHAR(128)",
        "extraction_method": "VARCHAR(32)",
        "source_type": "VARCHAR(32) NOT NULL DEFAULT 'knowledge_base'",
        "source_url": "VARCHAR(2048)",
        "source_domain": "VARCHAR(253)",
        "trusted": "BOOLEAN NOT NULL DEFAULT 1",
    }
    for column_name, column_type in definitions.items():
        if column_name in columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE citations ADD COLUMN {column_name} {column_type}")
            )


def ensure_ingest_lifecycle_columns(engine: Engine) -> None:
    """Upgrade local product databases with bounded upload and durable job fields."""
    document_columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    if "page_count" not in document_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE documents ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0")
            )

    job_columns = {column["name"] for column in inspect(engine).get_columns("ingest_jobs")}
    definitions = {
        "available_at": "DATETIME",
        "lease_owner": "VARCHAR(80)",
        "lease_expires_at": "DATETIME",
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "max_retries": "INTEGER NOT NULL DEFAULT 3",
        "error_code": "VARCHAR(64)",
        "error_message": "VARCHAR(300)",
    }
    for column_name, column_type in definitions.items():
        if column_name in job_columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE ingest_jobs ADD COLUMN {column_name} {column_type}")
            )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE ingest_jobs SET available_at = CURRENT_TIMESTAMP WHERE available_at IS NULL")
        )


def init_database() -> None:
    from frontend.product import models  # noqa: F401

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(ENGINE)
    ensure_knowledge_base_index_columns(ENGINE)
    ensure_citation_locator_columns(ENGINE)
    ensure_ingest_lifecycle_columns(ENGINE)
    from frontend.product.services.documents import (
        cleanup_orphaned_uploads,
        cleanup_stale_uploads,
    )

    with SessionLocal() as session:
        recover_interrupted_work(session)
        repair_citation_display_names(session)
        cleanup_orphaned_uploads(session)

    cleanup_stale_uploads()
