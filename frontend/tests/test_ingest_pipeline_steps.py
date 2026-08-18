"""P2-B lifecycle: pipeline steps persistence and retry preservation."""

from __future__ import annotations

import asyncio

from frontend.product.repositories import create_knowledge_base
from frontend.product.services import documents
from frontend.tests.test_ingest_lifecycle import (
    pdf_bytes,
    session_factory,
    upload_file,
)


def test_ingest_job_persists_default_pipeline_steps(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="流水线", description="")
        _document, job = asyncio.run(
            documents.create_document_from_upload(
                session,
                knowledge_base=knowledge_base,
                file=upload_file(pdf_bytes()),
            )
        )
        assert job.pipeline_steps is not None
        node_types = [step["node_type"] for step in job.pipeline_steps]
        assert node_types == ["parse", "chunk", "embed", "index"]
        assert job.pipeline_version == "1"


def test_retry_document_preserves_pipeline_steps(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    custom_steps = [
        {
            "node_id": "parse",
            "node_type": "parse",
            "enabled": True,
            "settings": {},
            "next_node_id": "index",
        },
        {"node_id": "index", "node_type": "index", "enabled": True, "settings": {}},
    ]

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="重试保留", description="")
        document, job = asyncio.run(
            documents.create_document_from_upload(
                session,
                knowledge_base=knowledge_base,
                file=upload_file(pdf_bytes()),
            )
        )
        job.pipeline_steps = custom_steps
        job.pipeline_version = "1"
        session.commit()
        document.status = "failed"
        document.error_code = "NODE_INDEX_FAILED"
        session.commit()

        retried = documents.retry_document(session, document)
        assert retried.pipeline_steps == custom_steps
        assert retried.pipeline_version == "1"
