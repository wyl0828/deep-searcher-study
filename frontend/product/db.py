from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "product"
DATA_DIR = Path(os.environ.get("DEEPSEARCHER_DATA_DIR", DEFAULT_DATA_DIR))
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
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


class DatabaseSchemaError(RuntimeError):
    """Raised when a managed database schema is not at the required Alembic revision."""


def is_sqlite_engine(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def is_postgresql_engine(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


def required_alembic_heads() -> tuple[str, ...]:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "frontend" / "product" / "migrations"),
    )
    return tuple(ScriptDirectory.from_config(config).get_heads())


def validate_alembic_schema(engine: Engine) -> None:
    """Require non-SQLite deployments to be migrated explicitly with Alembic."""

    required = required_alembic_heads()
    with engine.connect() as connection:
        current = tuple(MigrationContext.configure(connection).get_current_heads())
    if set(current) != set(required):
        current_label = ", ".join(current) if current else "<none>"
        required_label = ", ".join(required) if required else "<none>"
        raise DatabaseSchemaError(
            "Database schema is not current. "
            f"Current Alembic revision: {current_label}; required: {required_label}. "
            "Run `python -m alembic upgrade head` before starting the service."
        )


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
            text(
                "UPDATE ingest_jobs SET available_at = CURRENT_TIMESTAMP WHERE available_at IS NULL"
            )
        )


def ensure_ingest_pipeline_columns(engine: Engine) -> None:
    """Upgrade local SQLite workspaces with ingest pipeline configuration (P2-B)."""
    job_columns = {column["name"] for column in inspect(engine).get_columns("ingest_jobs")}
    definitions = {
        "pipeline_steps": "JSON",
        "pipeline_version": "VARCHAR(16)",
    }
    for column_name, column_type in definitions.items():
        if column_name in job_columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE ingest_jobs ADD COLUMN {column_name} {column_type}")
            )


def ensure_connector_sync_columns(engine: Engine) -> None:
    """Upgrade local SQLite workspaces with v0.6 connector sync identity."""
    document_columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    document_definitions = {
        "connector_sync_id": "VARCHAR(40)",
        "external_id": "VARCHAR(255)",
        "connector_source": "VARCHAR(32)",
        "content_hash": "VARCHAR(64)",
    }
    for column_name, column_type in document_definitions.items():
        if column_name in document_columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE documents ADD COLUMN {column_name} {column_type}")
            )

    job_columns = {column["name"] for column in inspect(engine).get_columns("ingest_jobs")}
    job_definitions = {
        "source": "VARCHAR(16)",
        "source_metadata": "JSON",
    }
    for column_name, column_type in job_definitions.items():
        if column_name in job_columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE ingest_jobs ADD COLUMN {column_name} {column_type}")
            )

    document_indexes = {item["name"] for item in inspect(engine).get_indexes("documents")}
    # Replace the legacy full unique (kb_id, sha256) with the upload-only partial one.
    if "uq_documents_knowledge_base_sha256" in document_indexes:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX uq_documents_knowledge_base_sha256"))
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_knowledge_base_sha256 "
                "ON documents (knowledge_base_id, sha256) WHERE connector_sync_id IS NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_connector_external "
                "ON documents (connector_sync_id, external_id)"
            )
        )

    from frontend.product.models import ConnectorSync, ConnectorSyncRun

    ConnectorSync.__table__.create(bind=engine, checkfirst=True)
    ConnectorSyncRun.__table__.create(bind=engine, checkfirst=True)


def ensure_auth_ownership_schema(engine: Engine) -> None:
    """Make existing local SQLite workspaces claimable by the first administrator."""
    if not str(engine.url).startswith("sqlite"):
        return
    from frontend.product.models import LEGACY_OWNER_ID

    now = datetime.now(timezone.utc).isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO users "
                "(id, username, display_name, password_hash, role, is_active, created_at, updated_at) "
                "VALUES (:id, '__legacy__', '待接管的旧数据', 'disabled', "
                "'system_pending', 0, :now, :now)"
            ),
            {"id": LEGACY_OWNER_ID, "now": now},
        )
        connection.execute(
            text(
                "UPDATE users SET role = 'system_pending' "
                "WHERE id = :id AND role = 'admin' "
                "AND NOT EXISTS (SELECT 1 FROM users WHERE id != :id)"
            ),
            {"id": LEGACY_OWNER_ID},
        )
    inspector = inspect(engine)
    knowledge_base_columns = {column["name"] for column in inspector.get_columns("knowledge_bases")}
    if "owner_id" not in knowledge_base_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE knowledge_bases ADD COLUMN owner_id VARCHAR(40)"))
            connection.execute(
                text("UPDATE knowledge_bases SET owner_id = :owner_id WHERE owner_id IS NULL"),
                {"owner_id": LEGACY_OWNER_ID},
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_owner_id "
                    "ON knowledge_bases (owner_id)"
                )
            )
    conversation_columns = {
        column["name"] for column in inspect(engine).get_columns("conversations")
    }
    if "owner_id" not in conversation_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE conversations ADD COLUMN owner_id VARCHAR(40)"))
            connection.execute(
                text(
                    "UPDATE conversations SET owner_id = "
                    "(SELECT owner_id FROM knowledge_bases "
                    "WHERE knowledge_bases.id = conversations.knowledge_base_id) "
                    "WHERE owner_id IS NULL"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_owner_id "
                    "ON conversations (owner_id)"
                )
            )


def ensure_trust_layer_columns(engine: Engine) -> None:
    """Upgrade local databases to the versioned Trust Layer contract."""

    message_columns = {column["name"] for column in inspect(engine).get_columns("messages")}
    message_definitions = {
        "trust_contract_version": "INTEGER",
        "trust_status": "VARCHAR(32)",
        "safety_status": "VARCHAR(24)",
        "policy_action": "VARCHAR(24)",
        "policy_profile": "VARCHAR(32)",
        "policy_reason_codes": "JSON",
        "risk_level": "VARCHAR(16)",
        "query_type": "VARCHAR(32)",
        "risk_factors": "JSON",
        "provenance_contract_version": "INTEGER",
        "provenance_digest": "VARCHAR(71)",
        "trust_details": "JSON",
    }
    for column_name, column_type in message_definitions.items():
        if column_name in message_columns:
            continue
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE messages ADD COLUMN {column_name} {column_type}"))
    message_indexes = {item["name"] for item in inspect(engine).get_indexes("messages")}
    if "ix_messages_provenance_digest" not in message_indexes:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE INDEX ix_messages_provenance_digest ON messages (provenance_digest)")
            )

    claim_columns = {column["name"] for column in inspect(engine).get_columns("answer_claims")}
    claim_definitions = {
        "structural_support_status": "VARCHAR(32) NOT NULL DEFAULT 'unsupported'",
        "citation_status": "VARCHAR(24) NOT NULL DEFAULT 'missing'",
        "entailment_status": "VARCHAR(24) NOT NULL DEFAULT 'not_checked'",
        "consistency_status": "VARCHAR(24) NOT NULL DEFAULT 'not_checked'",
        "consistency_checks": "JSON NOT NULL DEFAULT '[]'",
        "risk_status": "VARCHAR(24) NOT NULL DEFAULT 'not_assessed'",
        "risk_checks": "JSON NOT NULL DEFAULT '[]'",
        "citation_spans": "JSON NOT NULL DEFAULT '[]'",
        "confidence": "FLOAT",
        "reason_codes": "JSON NOT NULL DEFAULT '[]'",
    }
    for column_name, column_type in claim_definitions.items():
        if column_name in claim_columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE answer_claims ADD COLUMN {column_name} {column_type}")
            )


def ensure_document_temporal_columns(engine: Engine) -> None:
    """Upgrade local SQLite workspaces with explicit business-time metadata."""

    definitions = {
        "published_at": "DATE",
        "effective_at": "DATE",
        "superseded_at": "DATE",
        "temporal_metadata_source": "VARCHAR(32)",
    }
    for table_name in ("documents", "citations"):
        columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
        for column_name, column_type in definitions.items():
            if column_name in columns:
                continue
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                )


def ensure_document_version_family_columns(engine: Engine) -> None:
    """Upgrade local SQLite workspaces with explicit document-series identity."""

    definitions = {
        "version_family": "VARCHAR(128)",
        "version_family_source": "VARCHAR(32)",
    }
    for table_name in ("documents", "citations"):
        columns = {column["name"] for column in inspect(engine).get_columns(table_name)}
        for column_name, column_type in definitions.items():
            if column_name in columns:
                continue
            with engine.begin() as connection:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                )
    document_indexes = {item["name"] for item in inspect(engine).get_indexes("documents")}
    if "ix_documents_version_family" not in document_indexes:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE INDEX ix_documents_version_family ON documents (version_family)")
            )


def ensure_document_storage_columns(engine: Engine) -> None:
    """Upgrade local SQLite workspaces with object-storage identity."""

    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    definitions = {
        "storage_type": "VARCHAR(16) NOT NULL DEFAULT 'local'",
        "storage_bucket": "VARCHAR(255)",
        "storage_key": "TEXT",
    }
    for column_name, column_type in definitions.items():
        if column_name in columns:
            continue
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE documents ADD COLUMN {column_name} {column_type}")
            )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET storage_key = storage_path WHERE storage_key IS NULL")
        )


def ensure_conversation_summary_table(engine: Engine) -> None:
    """Create the additive summary table for local SQLite workspaces."""

    from frontend.product.models import ConversationSummary

    ConversationSummary.__table__.create(bind=engine, checkfirst=True)


def ensure_workspace_schema(engine: Engine) -> None:
    """Attach local SQLite knowledge bases to personal workspaces (v0.5.0)."""

    from frontend.product.models import Workspace, WorkspaceMember

    Workspace.__table__.create(bind=engine, checkfirst=True)
    WorkspaceMember.__table__.create(bind=engine, checkfirst=True)
    columns = {column["name"] for column in inspect(engine).get_columns("knowledge_bases")}
    if "workspace_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE knowledge_bases ADD COLUMN workspace_id VARCHAR(40)")
            )
    from frontend.product.services.access import ensure_personal_workspaces

    with SessionLocal() as session:
        ensure_personal_workspaces(session)


def init_database() -> None:
    from frontend.product import models  # noqa: F401

    if is_sqlite_engine(ENGINE):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(ENGINE)
        ensure_knowledge_base_index_columns(ENGINE)
        ensure_citation_locator_columns(ENGINE)
        ensure_ingest_lifecycle_columns(ENGINE)
        ensure_ingest_pipeline_columns(ENGINE)
        ensure_connector_sync_columns(ENGINE)
        ensure_auth_ownership_schema(ENGINE)
        ensure_trust_layer_columns(ENGINE)
        ensure_document_temporal_columns(ENGINE)
        ensure_document_version_family_columns(ENGINE)
        ensure_document_storage_columns(ENGINE)
        ensure_conversation_summary_table(ENGINE)
        ensure_workspace_schema(ENGINE)
    else:
        validate_alembic_schema(ENGINE)
    from frontend.product.messaging import rocketmq_enabled
    from frontend.product.services.documents import (
        cleanup_orphaned_uploads,
        cleanup_stale_uploads,
    )

    with SessionLocal() as session:
        # RocketMQ redelivers unacked messages and the consumer reclaims expired
        # leases without moving the transaction-check source state back to queued.
        if not rocketmq_enabled():
            recover_interrupted_work(session)
        repair_citation_display_names(session)
        cleanup_orphaned_uploads(session)

    cleanup_stale_uploads()
