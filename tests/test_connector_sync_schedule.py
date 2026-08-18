"""Connector sync scheduler tests (lease / cron / claim / integrated processing)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from frontend.product.db import Base, create_database_engine
from frontend.product.models import (
    ConnectorSync,
    ConnectorSyncRun,
    Document,
    IngestJob,
    KnowledgeBase,
    User,
    Workspace,
    WorkspaceMember,
    utcnow,
)
from frontend.product.services import connector_sync as cs


@pytest.fixture()
def factory(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'conn.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(factory, tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    sample = root / "a.txt"
    if not sample.exists():
        sample.write_text("hello connector", encoding="utf-8")
    with factory() as session:
        user = User(username="owner", display_name="O", password_hash="x" * 60, role="admin")
        session.add(user)
        session.flush()
        workspace = Workspace(name="ws", description="", owner_id=user.id)
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        knowledge_base = KnowledgeBase(
            owner_id=user.id,
            workspace_id=workspace.id,
            name="kb",
            description="",
            collection_name="coll",
            is_current=True,
        )
        session.add(knowledge_base)
        session.flush()
        sync = ConnectorSync(
            knowledge_base_id=knowledge_base.id,
            source_type="local_directory",
            config={"root": str(root)},
            cron="0 * * * *",
            status="active",
            next_run_at=utcnow() - timedelta(seconds=1),
        )
        session.add(sync)
        session.commit()
        return sync.id, knowledge_base.id


def _patch_env(monkeypatch, factory, tmp_path):
    monkeypatch.setattr(cs, "SessionLocal", factory)

    async def fake_inspect(_path, _extension):
        return 1

    monkeypatch.setattr("frontend.product.services.documents.inspect_document_pages", fake_inspect)
    monkeypatch.setattr(
        "frontend.product.services.documents._dispatch_ingest_or_mark_failed",
        lambda session, *, document, job: None,
    )
    monkeypatch.setattr("frontend.product.services.documents.UPLOAD_DIR", tmp_path / "uploads")


def test_validate_cron_requires_five_fields():
    cs.validate_cron("0 * * * *")
    with pytest.raises(ValueError):
        cs.validate_cron("*/5 * * * * *")  # six-field (seconds) rejected


def test_compute_next_run_interprets_cron_and_stores_utc():
    now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    nxt = cs.compute_next_run("0 * * * *", now)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset() == timedelta(0)
    assert nxt.hour == 13


def test_lease_cas_acquire_renew_release(factory):
    with factory() as session:
        user = User(username="u2", display_name="U2", password_hash="x" * 60, role="admin")
        session.add(user)
        session.flush()
        ws = Workspace(name="ws2", description="", owner_id=user.id)
        session.add(ws)
        session.flush()
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        kb = KnowledgeBase(
            owner_id=user.id,
            workspace_id=ws.id,
            name="kb2",
            description="",
            collection_name="coll2",
            is_current=True,
        )
        session.add(kb)
        session.flush()
        sync = ConnectorSync(
            knowledge_base_id=kb.id,
            source_type="local_directory",
            config={"root": "unused"},
            cron="0 * * * *",
            status="active",
        )
        session.add(sync)
        session.commit()
        now = utcnow()
        lease1 = cs.try_acquire_lease(session, sync, token="w1", now=now)
        assert lease1 is not None
        # not expired -> not reacquirable
        assert (
            cs.try_acquire_lease(session, sync, token="w2", now=now + timedelta(seconds=10)) is None
        )
        # renew is owner-scoped
        assert cs.renew_lease(session, lease1, now=now + timedelta(seconds=30))
        # expired -> reacquirable
        lease3 = cs.try_acquire_lease(session, sync, token="w3", now=now + timedelta(seconds=400))
        assert lease3 is not None
        cs.release_lease(session, lease3)


def test_process_due_sync_ingests_and_records_run(tmp_path, factory, monkeypatch):
    _patch_env(monkeypatch, factory, tmp_path)
    with factory() as session:
        sync_id, _kb_id = _seed(factory, tmp_path)
        claimed = cs.claim_due_sync(session, worker_id="worker-1")
        assert claimed == sync_id

    asyncio.run(cs.process_due_sync(sync_id, worker_id="worker-1"))

    with factory() as session:
        document = session.scalar(select(Document).where(Document.connector_sync_id == sync_id))
        assert document is not None
        assert document.external_id == "a.txt"
        assert document.connector_source == "local_directory"
        job = session.scalar(select(IngestJob).where(IngestJob.document_id == document.id))
        assert job.source == "connector"
        assert job.source_metadata["replace_document_id"] == document.id
        run = session.scalar(select(ConnectorSyncRun).where(ConnectorSyncRun.sync_id == sync_id))
        assert run.status == "success"
        assert run.added == 1
        sync = session.get(ConnectorSync, sync_id)
        assert sync.lock_owner is None
        assert sync.cursor.get("a.txt") is not None
        assert sync.next_run_at is not None


def test_process_due_sync_idempotent_does_not_reingest(tmp_path, factory, monkeypatch):
    _patch_env(monkeypatch, factory, tmp_path)
    with factory() as session:
        sync_id, _kb_id = _seed(factory, tmp_path)
        assert cs.claim_due_sync(session, worker_id="worker-1") == sync_id
    asyncio.run(cs.process_due_sync(sync_id, worker_id="worker-1"))
    with factory() as session:
        assert (
            session.scalar(select(Document).where(Document.connector_sync_id == sync_id))
            is not None
        )

    # second run with cursor advanced -> no candidates, run added=0, document count stable.
    with factory() as session:
        session.execute(
            update(ConnectorSync).where(ConnectorSync.id == sync_id).values(next_run_at=utcnow())
        )
        session.commit()
        assert cs.claim_due_sync(session, worker_id="worker-2") == sync_id
    asyncio.run(cs.process_due_sync(sync_id, worker_id="worker-2"))
    with factory() as session:
        docs = session.scalars(select(Document).where(Document.connector_sync_id == sync_id)).all()
        assert len(docs) == 1
        runs = session.scalars(
            select(ConnectorSyncRun).where(ConnectorSyncRun.sync_id == sync_id)
        ).all()
        latest = max(runs, key=lambda r: r.created_at)
        assert latest.status == "success"
        assert latest.added == 0 and latest.updated == 0
