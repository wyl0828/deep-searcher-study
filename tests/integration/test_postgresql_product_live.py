from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from frontend.product import db as product_db
from frontend.product.models import Document
from frontend.product.repositories import create_ingest_job, create_knowledge_base
from frontend.product.services.documents import claim_next_ingest_job

POSTGRES_URL = os.environ.get("DEEPSEARCHER_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set DEEPSEARCHER_TEST_POSTGRES_URL to run the live PostgreSQL product test",
)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(product_db.ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_postgresql_migrations_schema_validation_and_job_claim():
    url = make_url(POSTGRES_URL)
    if url.get_backend_name() != "postgresql":
        pytest.fail("DEEPSEARCHER_TEST_POSTGRES_URL must use PostgreSQL")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))

    try:
        command.upgrade(_alembic_config(url.render_as_string(hide_password=False)), "head")
        product_db.validate_alembic_schema(engine)

        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            knowledge_base = create_knowledge_base(session, name="PostgreSQL集成", description="")
            document = Document(
                knowledge_base_id=knowledge_base.id,
                display_name="paper.pdf",
                storage_path="paper.pdf",
                size_bytes=1,
                sha256="f" * 64,
                status="queued",
            )
            session.add(document)
            session.flush()
            job = create_ingest_job(session, document)
            session.commit()

            assert claim_next_ingest_job(session, worker_id="postgres-worker") == job.id
            session.refresh(job)
            assert job.status == "processing"
            assert job.lease_owner == "postgres-worker"
    finally:
        engine.dispose()
