from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from frontend.product import rocketmq_worker
from frontend.product.db import worker_is_ready
from frontend.product.models import Base, WorkerHeartbeat


def test_rocketmq_worker_maintains_document_ingest_heartbeat(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'hb.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr(rocketmq_worker, "SessionLocal", factory)
    monkeypatch.setattr(rocketmq_worker, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    async def _run() -> None:
        stopping = asyncio.Event()
        task = asyncio.create_task(rocketmq_worker.heartbeat("worker-1", stopping))
        await asyncio.sleep(0.15)
        stopping.set()
        await task

    asyncio.run(_run())

    with factory() as session:
        hb = session.get(WorkerHeartbeat, "document-ingest")
        assert hb is not None
        assert hb.status == "running"
        assert worker_is_ready(session, worker_name="document-ingest") is True
