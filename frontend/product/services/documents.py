from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from frontend.product.backend import backend_request_headers
from frontend.product.db import DATA_DIR, SessionLocal
from frontend.product.errors import ProductError
from frontend.product.models import Document, IngestJob, KnowledgeBase, utcnow
from frontend.product.repositories import create_ingest_job

BACKEND_URL = os.environ.get("DEEPSEARCHER_API_URL", "http://127.0.0.1:8500").rstrip("/")


def _positive_int_environment(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_PDF_BYTES = _positive_int_environment("DEEPSEARCHER_MAX_PDF_BYTES", 20 * 1024 * 1024)
MAX_PDF_PAGES = _positive_int_environment("DEEPSEARCHER_MAX_PDF_PAGES", 500)
MAX_DOCUMENTS_PER_KNOWLEDGE_BASE = _positive_int_environment(
    "DEEPSEARCHER_MAX_DOCUMENTS_PER_KNOWLEDGE_BASE", 200
)
KNOWLEDGE_BASE_STORAGE_QUOTA_BYTES = _positive_int_environment(
    "DEEPSEARCHER_KNOWLEDGE_BASE_STORAGE_QUOTA_BYTES", 512 * 1024 * 1024
)
TOTAL_STORAGE_QUOTA_BYTES = _positive_int_environment(
    "DEEPSEARCHER_TOTAL_STORAGE_QUOTA_BYTES", 2 * 1024 * 1024 * 1024
)
PDF_PROBE_TIMEOUT_SECONDS = _positive_int_environment(
    "DEEPSEARCHER_PDF_PROBE_TIMEOUT_SECONDS", 10
)
INGEST_REQUEST_TIMEOUT_SECONDS = _positive_int_environment(
    "DEEPSEARCHER_INGEST_REQUEST_TIMEOUT_SECONDS", 300
)
INGEST_LEASE_SECONDS = _positive_int_environment("DEEPSEARCHER_INGEST_LEASE_SECONDS", 360)
INGEST_MAX_RETRIES = _positive_int_environment("DEEPSEARCHER_INGEST_MAX_RETRIES", 3)
INGEST_RETRY_BASE_SECONDS = _positive_int_environment(
    "DEEPSEARCHER_INGEST_RETRY_BASE_SECONDS", 5
)
UPLOAD_CHUNK_BYTES = 1024 * 1024
STAGING_MAX_AGE_SECONDS = 60 * 60
EMBEDDING_BATCH_SIZE = 10
UPLOAD_DIR = DATA_DIR / "uploads"


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    display_name: str
    size_bytes: int
    sha256: str


def _safe_display_name(display_name: str) -> str:
    filename = Path(display_name).name.strip()
    filename = "".join(character for character in filename if character >= " " and character != "\x7f")
    return filename[:255]


def validate_pdf_metadata(display_name: str, content_type: str | None) -> str:
    safe_display_name = _safe_display_name(display_name)
    if not safe_display_name:
        raise ProductError("DOCUMENT_NAME_INVALID", "PDF 文件名不能为空。")
    if not safe_display_name.lower().endswith(".pdf"):
        raise ProductError("DOCUMENT_UNSUPPORTED_TYPE", "目前仅支持上传 PDF 文件。")
    if content_type not in {None, "", "application/pdf", "application/octet-stream"}:
        raise ProductError("DOCUMENT_UNSUPPORTED_TYPE", "目前仅支持上传 PDF 文件。")
    return safe_display_name


def _make_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _delete_file_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def stage_pdf_upload(file: UploadFile) -> StagedUpload:
    display_name = validate_pdf_metadata(file.filename or "document.pdf", file.content_type)
    staging_dir = UPLOAD_DIR / ".staging"
    _make_private_directory(staging_dir)
    staging_path = staging_dir / f"{secrets.token_hex(24)}.part"
    digest = hashlib.sha256()
    size_bytes = 0
    prefix = bytearray()
    descriptor = os.open(staging_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > MAX_PDF_BYTES:
                    raise ProductError(
                        "DOCUMENT_TOO_LARGE",
                        f"PDF 文件不能超过 {MAX_PDF_BYTES // (1024 * 1024)} MiB。",
                        status_code=413,
                    )
                if len(prefix) < 5:
                    prefix.extend(chunk[: 5 - len(prefix)])
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size_bytes == 0:
            raise ProductError("DOCUMENT_EMPTY", "PDF 文件不能为空。")
        if bytes(prefix) != b"%PDF-":
            raise ProductError("DOCUMENT_INVALID_PDF", "所选文件不是有效的 PDF。")
        return StagedUpload(
            path=staging_path,
            display_name=display_name,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )
    except BaseException:
        _delete_file_quietly(staging_path)
        raise


async def inspect_pdf_pages(path: Path) -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "frontend.product.pdf_probe",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=PDF_PROBE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProductError(
            "DOCUMENT_PDF_VALIDATION_TIMEOUT",
            "PDF 安全检查超时，请压缩或拆分文件后重试。",
            status_code=422,
            retryable=True,
        ) from exc
    if process.returncode != 0:
        raise ProductError(
            "DOCUMENT_INVALID_PDF",
            "PDF 结构无法解析，可能已损坏或受密码保护。",
            status_code=422,
        )
    try:
        page_count = int(stdout.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProductError(
            "DOCUMENT_INVALID_PDF",
            "PDF 结构无法解析，可能已损坏或受密码保护。",
            status_code=422,
        ) from exc
    if page_count < 1:
        raise ProductError("DOCUMENT_EMPTY", "PDF 文件中没有可处理的页面。", status_code=422)
    if page_count > MAX_PDF_PAGES:
        raise ProductError(
            "DOCUMENT_TOO_MANY_PAGES",
            f"PDF 页数不能超过 {MAX_PDF_PAGES} 页。",
            status_code=413,
        )
    return page_count


def _enforce_storage_quota(
    session: Session,
    *,
    knowledge_base: KnowledgeBase,
    incoming_size: int,
) -> None:
    document_count = session.scalar(
        select(func.count(Document.id)).where(Document.knowledge_base_id == knowledge_base.id)
    )
    if int(document_count or 0) >= MAX_DOCUMENTS_PER_KNOWLEDGE_BASE:
        raise ProductError(
            "KNOWLEDGE_BASE_DOCUMENT_LIMIT_REACHED",
            f"每个知识库最多保存 {MAX_DOCUMENTS_PER_KNOWLEDGE_BASE} 份文档。",
            status_code=409,
        )
    knowledge_base_usage = session.scalar(
        select(func.sum(Document.size_bytes)).where(
            Document.knowledge_base_id == knowledge_base.id
        )
    )
    if int(knowledge_base_usage or 0) + incoming_size > KNOWLEDGE_BASE_STORAGE_QUOTA_BYTES:
        raise ProductError(
            "KNOWLEDGE_BASE_STORAGE_QUOTA_EXCEEDED",
            "这个知识库的文件存储空间已满，请删除不需要的文档后重试。",
            status_code=413,
        )
    total_usage = session.scalar(select(func.sum(Document.size_bytes)))
    if int(total_usage or 0) + incoming_size > TOTAL_STORAGE_QUOTA_BYTES:
        raise ProductError(
            "DOCUMENT_STORAGE_QUOTA_EXCEEDED",
            "本地文档存储空间已满，请清理文件后重试。",
            status_code=413,
        )


async def create_document_from_upload(
    session: Session,
    *,
    knowledge_base: KnowledgeBase,
    file: UploadFile,
) -> tuple[Document, IngestJob]:
    staged = await stage_pdf_upload(file)
    destination: Path | None = None
    try:
        page_count = await inspect_pdf_pages(staged.path)
        _enforce_storage_quota(
            session,
            knowledge_base=knowledge_base,
            incoming_size=staged.size_bytes,
        )
        duplicate = session.scalar(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base.id,
                Document.sha256 == staged.sha256,
            )
        )
        if duplicate is not None:
            raise ProductError(
                "DOCUMENT_DUPLICATE",
                "这个知识库中已经存在相同的 PDF。",
                status_code=409,
            )

        document = Document(
            knowledge_base_id=knowledge_base.id,
            display_name=staged.display_name,
            storage_path="",
            size_bytes=staged.size_bytes,
            page_count=page_count,
            sha256=staged.sha256,
            status="queued",
        )
        session.add(document)
        session.flush()

        destination_dir = UPLOAD_DIR / knowledge_base.id
        _make_private_directory(destination_dir)
        destination = destination_dir / f"{secrets.token_hex(24)}.pdf"
        os.replace(staged.path, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        document.storage_path = str(destination)
        job = create_ingest_job(session, document)
        job.max_retries = INGEST_MAX_RETRIES
        session.commit()
        session.refresh(document)
        session.refresh(job)
        return document, job
    except IntegrityError as exc:
        session.rollback()
        if destination is not None:
            _delete_file_quietly(destination)
        raise ProductError(
            "DOCUMENT_DUPLICATE",
            "这个知识库中已经存在相同的 PDF。",
            status_code=409,
        ) from exc
    except Exception:
        session.rollback()
        if destination is not None:
            _delete_file_quietly(destination)
        raise
    finally:
        _delete_file_quietly(staged.path)


def cleanup_stale_uploads(*, now: float | None = None) -> int:
    staging_dir = UPLOAD_DIR / ".staging"
    if not staging_dir.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - STAGING_MAX_AGE_SECONDS
    removed = 0
    for candidate in staging_dir.glob("*.part"):
        try:
            if candidate.stat().st_mtime <= cutoff:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_orphaned_uploads(session: Session, *, now: float | None = None) -> int:
    if not UPLOAD_DIR.is_dir():
        return 0
    upload_root = UPLOAD_DIR.resolve()
    referenced = {
        str(Path(storage_path).resolve())
        for storage_path in session.scalars(select(Document.storage_path)).all()
        if storage_path
    }
    cutoff = (time.time() if now is None else now) - STAGING_MAX_AGE_SECONDS
    removed = 0
    for candidate in UPLOAD_DIR.glob("*/*.pdf"):
        try:
            resolved = candidate.resolve()
            if upload_root not in resolved.parents or str(resolved) in referenced:
                continue
            if candidate.stat().st_mtime <= cutoff:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


class IngestProcessingError(Exception):
    def __init__(self, code: str, *, retryable: bool):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def claim_next_ingest_job(
    session: Session,
    *,
    worker_id: str,
    document_id: str | None = None,
) -> str | None:
    now = utcnow()
    query = select(IngestJob).where(
        IngestJob.status == "queued",
        or_(IngestJob.available_at.is_(None), IngestJob.available_at <= now),
    )
    if document_id is not None:
        query = query.where(IngestJob.document_id == document_id)
    candidate = session.scalar(query.order_by(IngestJob.created_at, IngestJob.id).limit(1))
    if candidate is None:
        return None
    lease_expires_at = now + timedelta(seconds=INGEST_LEASE_SECONDS)
    result = session.execute(
        update(IngestJob)
        .where(IngestJob.id == candidate.id, IngestJob.status == "queued")
        .values(
            status="processing",
            lease_owner=worker_id,
            lease_expires_at=lease_expires_at,
            retry_count=IngestJob.retry_count + 1,
            started_at=now,
            finished_at=None,
        )
    )
    if int(result.rowcount or 0) != 1:
        session.rollback()
        return None
    document = session.get(Document, candidate.document_id)
    if document is None:
        session.rollback()
        return None
    document.status = "processing"
    document.error_code = None
    document.error_message = None
    session.commit()
    return candidate.id


def _safe_document_path(document: Document) -> Path:
    upload_root = UPLOAD_DIR.resolve()
    source_path = Path(document.storage_path).resolve()
    if source_path == upload_root or upload_root not in source_path.parents:
        raise IngestProcessingError("DOCUMENT_STORAGE_INVALID", retryable=False)
    if not source_path.is_file():
        raise IngestProcessingError("DOCUMENT_CONTENT_MISSING", retryable=False)
    return source_path


async def _load_document_into_backend(
    *,
    document: Document,
    knowledge_base: KnowledgeBase,
) -> dict:
    source_path = _safe_document_path(document)
    try:
        async with httpx.AsyncClient(
            timeout=float(INGEST_REQUEST_TIMEOUT_SECONDS),
            trust_env=False,
            headers=backend_request_headers(),
        ) as client:
            response = await client.post(
                f"{BACKEND_URL}/load-files/",
                json={
                    "paths": str(source_path),
                    "collection_name": knowledge_base.collection_name,
                    "batch_size": EMBEDDING_BATCH_SIZE,
                    "replace_document_id": document.sha256,
                },
            )
    except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise IngestProcessingError("DOCUMENT_PROCESSING_UNAVAILABLE", retryable=True) from exc
    except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
        # The server may still be mutating the index after a response timeout. Do
        # not start a concurrent retry; move the task to manual reconciliation.
        raise IngestProcessingError("DOCUMENT_PROCESSING_TIMEOUT", retryable=False) from exc
    except httpx.ConnectError as exc:
        raise IngestProcessingError("DOCUMENT_PROCESSING_UNAVAILABLE", retryable=True) from exc
    except httpx.RequestError as exc:
        raise IngestProcessingError("DOCUMENT_PROCESSING_TRANSPORT_FAILED", retryable=False) from exc
    if not response.is_success:
        retryable = response.status_code in {429, 502, 503, 504} or response.status_code >= 500
        raise IngestProcessingError("DOCUMENT_PROCESSING_FAILED", retryable=retryable)
    try:
        response_payload = response.json()
    except ValueError as exc:
        raise IngestProcessingError("DOCUMENT_PROCESSING_INVALID_RESPONSE", retryable=True) from exc
    manifest = (
        response_payload.get("collection", {}).get("manifest")
        if isinstance(response_payload, dict)
        else None
    )
    if not isinstance(manifest, dict):
        raise IngestProcessingError("DOCUMENT_INDEX_MANIFEST_MISSING", retryable=True)
    return manifest


def _finish_ingest_job(
    *,
    job_id: str,
    worker_id: str,
    manifest: dict | None = None,
    failure: IngestProcessingError | None = None,
) -> None:
    with SessionLocal() as session:
        job = session.get(IngestJob, job_id)
        if job is None or job.status != "processing" or job.lease_owner != worker_id:
            return
        document = session.get(Document, job.document_id)
        if document is None:
            return
        knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is None:
            return
        now = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        if failure is None and manifest is not None:
            knowledge_base.index_manifest = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            document.status = "ready"
            document.error_code = None
            document.error_message = None
            job.status = "succeeded"
            job.error_code = None
            job.error_message = None
            job.finished_at = now
        elif failure is not None and failure.retryable and job.retry_count < job.max_retries:
            delay = min(
                INGEST_RETRY_BASE_SECONDS * (2 ** max(job.retry_count - 1, 0)),
                60,
            )
            document.status = "queued"
            document.error_code = "DOCUMENT_RETRY_SCHEDULED"
            document.error_message = "文档处理暂时失败，系统将在后台自动重试。"
            job.status = "queued"
            job.available_at = now + timedelta(seconds=delay)
            job.error_code = failure.code
            job.error_message = "文档处理暂时失败，任务已进入重试队列。"
            job.finished_at = None
        else:
            document.status = "failed"
            document.error_code = failure.code if failure is not None else "DOCUMENT_PROCESSING_FAILED"
            document.error_message = "文档处理失败，请确认文件和问答服务状态后重试。"
            job.status = "dead_letter"
            job.error_code = document.error_code
            job.error_message = document.error_message
            job.finished_at = now
        knowledge_base.updated_at = now
        session.commit()


async def process_claimed_ingest_job(job_id: str, *, worker_id: str) -> None:
    with SessionLocal() as session:
        job = session.get(IngestJob, job_id)
        if job is None or job.status != "processing" or job.lease_owner != worker_id:
            return
        document = session.get(Document, job.document_id)
        if document is None:
            return
        knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
        if knowledge_base is None:
            return
        session.expunge(document)
        session.expunge(knowledge_base)
    try:
        manifest = await _load_document_into_backend(
            document=document,
            knowledge_base=knowledge_base,
        )
    except IngestProcessingError as exc:
        _finish_ingest_job(job_id=job_id, worker_id=worker_id, failure=exc)
    except Exception:
        _finish_ingest_job(
            job_id=job_id,
            worker_id=worker_id,
            failure=IngestProcessingError(
                "DOCUMENT_PROCESSING_UNEXPECTED",
                retryable=True,
            ),
        )
    else:
        _finish_ingest_job(job_id=job_id, worker_id=worker_id, manifest=manifest)


async def process_document(document_id: str) -> None:
    """Compatibility helper for tests and direct callers; the product uses the durable worker."""
    worker_id = f"inline-{os.getpid()}-{uuid4().hex[:12]}"
    with SessionLocal() as session:
        job_id = claim_next_ingest_job(
            session,
            worker_id=worker_id,
            document_id=document_id,
        )
    if job_id is not None:
        await process_claimed_ingest_job(job_id, worker_id=worker_id)


def retry_document(session: Session, document: Document) -> IngestJob:
    if document.status != "failed":
        raise ProductError(
            "DOCUMENT_NOT_RETRYABLE",
            "只有处理失败的文档可以重试。",
            status_code=409,
        )
    document.status = "queued"
    document.error_code = None
    document.error_message = None
    job = create_ingest_job(session, document)
    job.max_retries = INGEST_MAX_RETRIES
    session.commit()
    session.refresh(job)
    return job


async def delete_document(session: Session, document: Document) -> None:
    if document.status in {"queued", "processing"}:
        raise ProductError(
            "DOCUMENT_BUSY",
            "文档正在处理，完成后才能删除。",
            status_code=409,
            retryable=True,
        )

    knowledge_base = session.get(KnowledgeBase, document.knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            trust_env=False,
            headers=backend_request_headers(),
        ) as client:
            response = await client.delete(
                f"{BACKEND_URL}/collections/{knowledge_base.collection_name}"
                f"/documents/{document.sha256}"
            )
        if not response.is_success:
            raise ProductError(
                "DOCUMENT_VECTOR_DELETE_FAILED",
                "向量数据删除失败，文档尚未删除，请稍后重试。",
                status_code=502,
                retryable=True,
            )
        try:
            response_payload = response.json()
        except (AttributeError, ValueError):
            response_payload = {}
        manifest = response_payload.get("manifest") if isinstance(response_payload, dict) else None
        if isinstance(manifest, dict):
            knowledge_base.index_manifest = json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    except httpx.RequestError as exc:
        raise ProductError(
            "DOCUMENT_VECTOR_SERVICE_UNAVAILABLE",
            "问答服务暂时不可用，文档尚未删除，请稍后重试。",
            status_code=503,
            retryable=True,
        ) from exc

    storage_path = Path(document.storage_path)
    if storage_path.exists():
        upload_root = UPLOAD_DIR.resolve()
        resolved_path = storage_path.resolve()
        if resolved_path != upload_root and upload_root not in resolved_path.parents:
            raise ProductError(
                "DOCUMENT_STORAGE_INVALID",
                "文档存储路径异常，未执行删除。",
                status_code=500,
            )
        try:
            resolved_path.unlink()
        except OSError as exc:
            raise ProductError(
                "DOCUMENT_FILE_DELETE_FAILED",
                "本地文件删除失败，请检查文件是否被占用后重试。",
                status_code=500,
                retryable=True,
            ) from exc

    knowledge_base.updated_at = utcnow()
    session.delete(document)
    session.commit()
