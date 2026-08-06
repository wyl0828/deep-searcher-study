from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.datastructures import Headers, UploadFile

from frontend.product.db import Base, create_database_engine, recover_interrupted_work
from frontend.product.errors import ProductError
from frontend.product.models import Document, IngestJob, utcnow
from frontend.product.repositories import create_ingest_job, create_knowledge_base
from frontend.product.services import documents


def pdf_bytes(*, pages: int = 1) -> bytes:
    output = BytesIO()
    document = canvas.Canvas(output)
    for index in range(pages):
        document.drawString(72, 720, f"Page {index + 1}")
        document.showPage()
    document.save()
    return output.getvalue()


def upload_file(payload: bytes, *, filename: str = "paper.pdf") -> UploadFile:
    return UploadFile(
        BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def session_factory(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'ingest.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_streamed_upload_is_page_checked_hashed_and_randomly_named(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    payload = pdf_bytes(pages=2)

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="流式上传", description="")
        document, job = asyncio.run(
            documents.create_document_from_upload(
                session,
                knowledge_base=knowledge_base,
                file=upload_file(payload),
            )
        )

    stored_path = Path(document.storage_path)
    assert document.size_bytes == len(payload)
    assert document.page_count == 2
    assert len(document.sha256) == 64
    assert stored_path.parent == upload_root / knowledge_base.id
    assert stored_path.name != f"{document.id}.pdf"
    assert len(stored_path.stem) == 48
    assert stored_path.read_bytes() == payload
    assert job.status == "queued"
    assert job.max_retries == documents.INGEST_MAX_RETRIES
    assert not list(upload_root.joinpath(".staging").glob("*.part"))


def test_streaming_limit_stops_oversized_upload_and_removes_staging_file(
    tmp_path,
    monkeypatch,
):
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 8)

    with pytest.raises(ProductError) as exc_info:
        asyncio.run(documents.stage_pdf_upload(upload_file(b"%PDF-123456789")))

    assert exc_info.value.code == "DOCUMENT_TOO_LARGE"
    assert not list(upload_root.joinpath(".staging").glob("*.part"))


def test_upload_reader_is_consumed_in_fixed_size_chunks(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    payload = b"%PDF-" + (b"x" * (documents.UPLOAD_CHUNK_BYTES + 17))

    class TrackingUpload:
        filename = "paper.pdf"
        content_type = "application/pdf"

        def __init__(self):
            self.offset = 0
            self.requested_sizes = []

        async def read(self, size):
            self.requested_sizes.append(size)
            chunk = payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    upload = TrackingUpload()
    staged = asyncio.run(documents.stage_pdf_upload(upload))
    try:
        assert staged.size_bytes == len(payload)
        assert upload.requested_sizes == [
            documents.UPLOAD_CHUNK_BYTES,
            documents.UPLOAD_CHUNK_BYTES,
            documents.UPLOAD_CHUNK_BYTES,
        ]
    finally:
        staged.path.unlink()


def test_page_limit_rejects_pdf_before_database_commit(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(documents, "MAX_PDF_PAGES", 1)

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="页数限制", description="")
        with pytest.raises(ProductError) as exc_info:
            asyncio.run(
                documents.create_document_from_upload(
                    session,
                    knowledge_base=knowledge_base,
                    file=upload_file(pdf_bytes(pages=2)),
                )
            )
        assert session.scalar(select(Document)) is None

    assert exc_info.value.code == "DOCUMENT_TOO_MANY_PAGES"
    assert not list(upload_root.rglob("*.part"))
    assert not list(upload_root.rglob("*.pdf"))


def test_knowledge_base_storage_quota_is_server_enforced(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    payload = pdf_bytes()
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(documents, "KNOWLEDGE_BASE_STORAGE_QUOTA_BYTES", len(payload) - 1)

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="容量限制", description="")
        with pytest.raises(ProductError) as exc_info:
            asyncio.run(
                documents.create_document_from_upload(
                    session,
                    knowledge_base=knowledge_base,
                    file=upload_file(payload),
                )
            )

    assert exc_info.value.code == "KNOWLEDGE_BASE_STORAGE_QUOTA_EXCEEDED"
    assert not list(upload_root.rglob("*.pdf"))


def test_expired_lease_is_requeued_but_active_lease_is_preserved(tmp_path):
    factory = session_factory(tmp_path)
    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="租约恢复", description="")
        first = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="expired.pdf",
            storage_path=str(tmp_path / "expired.pdf"),
            size_bytes=10,
            sha256="a" * 64,
            status="processing",
        )
        second = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="active.pdf",
            storage_path=str(tmp_path / "active.pdf"),
            size_bytes=10,
            sha256="b" * 64,
            status="processing",
        )
        session.add_all([first, second])
        session.flush()
        expired = IngestJob(
            document_id=first.id,
            status="processing",
            lease_owner="old-worker",
            lease_expires_at=utcnow() - timedelta(seconds=1),
        )
        active = IngestJob(
            document_id=second.id,
            status="processing",
            lease_owner="active-worker",
            lease_expires_at=utcnow() + timedelta(minutes=5),
        )
        session.add_all([expired, active])
        session.commit()

        assert recover_interrupted_work(session) == 2
        session.refresh(expired)
        session.refresh(active)
        session.refresh(first)
        session.refresh(second)

        assert expired.status == "queued"
        assert expired.error_code == "PROCESS_INTERRUPTED"
        assert first.status == "queued"
        assert active.status == "processing"
        assert active.lease_owner == "active-worker"
        assert second.status == "processing"


def test_worker_retries_with_backoff_then_moves_job_to_dead_letter(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "SessionLocal", factory)
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)

    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="失败队列", description="")
        source_path = upload_root / knowledge_base.id / "random.pdf"
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(pdf_bytes())
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="paper.pdf",
            storage_path=str(source_path),
            size_bytes=source_path.stat().st_size,
            page_count=1,
            sha256="c" * 64,
            status="queued",
        )
        session.add(document)
        session.flush()
        job = create_ingest_job(session, document)
        job.max_retries = 2
        session.commit()
        document_id = document.id
        job_id = job.id

    class FailedResponse:
        is_success = False
        status_code = 503

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json):
            assert json["replace_document_id"] == "c" * 64
            return FailedResponse()

    monkeypatch.setattr(documents.httpx, "AsyncClient", Client)

    asyncio.run(documents.process_document(document_id))
    with factory() as session:
        retried_job = session.get(IngestJob, job_id)
        retried_document = session.get(Document, document_id)
        assert retried_job is not None
        assert retried_document is not None
        assert retried_job.status == "queued"
        assert retried_job.retry_count == 1
        assert retried_job.error_code == "DOCUMENT_PROCESSING_FAILED"
        assert retried_document.status == "queued"
        assert retried_document.error_code == "DOCUMENT_RETRY_SCHEDULED"
        retried_job.available_at = utcnow()
        session.commit()

    asyncio.run(documents.process_document(document_id))
    with factory() as session:
        dead_job = session.get(IngestJob, job_id)
        failed_document = session.get(Document, document_id)
        assert dead_job is not None
        assert failed_document is not None
        assert dead_job.status == "dead_letter"
        assert dead_job.retry_count == 2
        assert dead_job.lease_owner is None
        assert dead_job.finished_at is not None
        assert failed_document.status == "failed"
        assert failed_document.error_code == "DOCUMENT_PROCESSING_FAILED"


@pytest.mark.parametrize(
    ("transport_error", "retryable"),
    [
        (httpx.ConnectError("offline"), True),
        (httpx.ReadTimeout("uncertain completion"), False),
    ],
)
def test_worker_only_retries_transport_failures_known_safe_to_repeat(
    tmp_path,
    monkeypatch,
    transport_error,
    retryable,
):
    upload_root = tmp_path / "uploads"
    source_path = upload_root / "kb_safe" / "random.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(pdf_bytes())
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    document = Document(
        knowledge_base_id="kb_safe",
        display_name="paper.pdf",
        storage_path=str(source_path),
        size_bytes=source_path.stat().st_size,
        page_count=1,
        sha256="e" * 64,
        status="processing",
    )

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, json):
            raise transport_error

    monkeypatch.setattr(documents.httpx, "AsyncClient", Client)

    with pytest.raises(documents.IngestProcessingError) as exc_info:
        asyncio.run(
            documents._load_document_into_backend(
                document=document,
                knowledge_base=type("KnowledgeBaseStub", (), {"collection_name": "kb_safe"})(),
            )
        )

    assert exc_info.value.retryable is retryable


def test_stale_staging_cleanup_only_removes_expired_parts(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    staging = upload_root / ".staging"
    staging.mkdir(parents=True)
    expired = staging / "expired.part"
    current = staging / "current.part"
    expired.write_bytes(b"old")
    current.write_bytes(b"new")
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(documents, "STAGING_MAX_AGE_SECONDS", 60)
    now = 10_000.0
    expired.touch()
    current.touch()
    os.utime(expired, (now - 61, now - 61))
    os.utime(current, (now - 59, now - 59))

    assert documents.cleanup_stale_uploads(now=now) == 1
    assert not expired.exists()
    assert current.exists()


def test_orphan_cleanup_preserves_referenced_files_and_removes_old_untracked_files(
    tmp_path,
    monkeypatch,
):
    factory = session_factory(tmp_path)
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(documents, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(documents, "STAGING_MAX_AGE_SECONDS", 60)
    now = 10_000.0
    with factory() as session:
        knowledge_base = create_knowledge_base(session, name="孤儿清理", description="")
        directory = upload_root / knowledge_base.id
        directory.mkdir(parents=True)
        referenced = directory / "referenced.pdf"
        orphaned = directory / "orphaned.pdf"
        current = directory / "current.pdf"
        referenced.write_bytes(b"referenced")
        orphaned.write_bytes(b"orphaned")
        current.write_bytes(b"current")
        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name="referenced.pdf",
            storage_path=str(referenced),
            size_bytes=10,
            page_count=1,
            sha256="d" * 64,
            status="ready",
        )
        session.add(document)
        session.commit()
        os.utime(referenced, (now - 120, now - 120))
        os.utime(orphaned, (now - 120, now - 120))
        os.utime(current, (now - 10, now - 10))

        assert documents.cleanup_orphaned_uploads(session, now=now) == 1

    assert referenced.exists()
    assert not orphaned.exists()
    assert current.exists()
