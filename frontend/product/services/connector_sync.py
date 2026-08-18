"""v0.6 local-directory connector sync (aligned with ragent schedule stack).

- SyncLease: DB lease (CAS acquire / renew / release) — ScheduleLockManager.
- 5-field cron, Asia/Shanghai interpretation, UTC storage — CronScheduleHelper.
- Lease-loss abort per phase + run state machine — ScheduleRefreshProcessor.
- ConnectorSyncRun.status=success means the source scan and enqueue of incremental
  ingest jobs were submitted; it does NOT mean every IngestJob finished indexing.
- next_run_at is only advanced on success; lease-loss / failure never skips the
  current cycle (source identity + content_hash give idempotent re-runs).
"""

from __future__ import annotations

import logging
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from frontend.product.connectors import Connector, create_connector
from frontend.product.db import SessionLocal
from frontend.product.errors import ProductError
from frontend.product.models import (
    ConnectorSync,
    ConnectorSyncRun,
    Document,
    KnowledgeBase,
    KnowledgeBaseMember,
    User,
    utcnow,
)
from frontend.product.repositories import create_ingest_job
from frontend.product.services import documents as document_service
from frontend.product.services.access import add_kb_member, set_kb_member_role
from frontend.product.services.audit import AuditContext, bind_audit_context

logger = logging.getLogger("deepsearcher.connector_sync")

SCHEDULE_TIMEZONE = "Asia/Shanghai"
SYNC_LEASE_SECONDS = 180
SYNC_STATUS_ACTIVE = "active"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_ABORTED = "aborted"
INGEST_SOURCE_CONNECTOR = "connector"


def validate_cron(cron: str) -> None:
    """Require a standard 5-field cron expression (no seconds/years extension)."""
    fields = str(cron or "").split()
    if len(fields) != 5:
        raise ValueError("cron 必须是标准 5 段表达式")


def compute_next_run(cron: str, now: datetime, *, tz_name: str = SCHEDULE_TIMEZONE) -> datetime:
    validate_cron(cron)
    zone = ZoneInfo(tz_name)
    local_now = now.astimezone(zone)
    iterator = croniter(cron, local_now)
    next_local = iterator.get_next(datetime)
    return next_local.astimezone(timezone.utc)


class SyncLease:
    """DB lease over a ConnectorSync row (ScheduleLockManager equivalent)."""

    def __init__(self, sync_id: str, token: str, until: datetime):
        self.sync_id = sync_id
        self.token = token
        self.until = until


def try_acquire_lease(
    session: Session, sync: ConnectorSync, *, token: str, now: datetime
) -> SyncLease | None:
    until = now + timedelta(seconds=SYNC_LEASE_SECONDS)
    result = session.execute(
        update(ConnectorSync)
        .where(
            ConnectorSync.id == sync.id,
            or_(ConnectorSync.lock_until.is_(None), ConnectorSync.lock_until < now),
        )
        .values(lock_owner=token, lock_until=until)
        .execution_options(synchronize_session=False)
    )
    if int(result.rowcount or 0) != 1:
        session.rollback()
        return None
    session.commit()
    return SyncLease(sync.id, token, until)


def renew_lease(session: Session, lease: SyncLease, *, now: datetime) -> bool:
    until = now + timedelta(seconds=SYNC_LEASE_SECONDS)
    result = session.execute(
        update(ConnectorSync)
        .where(ConnectorSync.id == lease.sync_id, ConnectorSync.lock_owner == lease.token)
        .values(lock_until=until)
        .execution_options(synchronize_session=False)
    )
    ok = int(result.rowcount or 0) == 1
    session.commit()
    return ok


def release_lease(session: Session, lease: SyncLease) -> None:
    session.execute(
        update(ConnectorSync)
        .where(ConnectorSync.id == lease.sync_id, ConnectorSync.lock_owner == lease.token)
        .values(lock_owner=None, lock_until=None)
        .execution_options(synchronize_session=False)
    )
    session.commit()


def claim_due_sync(session: Session, *, worker_id: str) -> str | None:
    """Claim the next due active sync (CAS; no FOR UPDATE on SQLite)."""
    now = utcnow()
    candidate = session.scalar(
        select(ConnectorSync)
        .where(
            ConnectorSync.status == SYNC_STATUS_ACTIVE,
            or_(ConnectorSync.next_run_at.is_(None), ConnectorSync.next_run_at <= now),
        )
        .order_by(ConnectorSync.next_run_at, ConnectorSync.id)
        .limit(1)
    )
    if candidate is None:
        return None
    token = f"{worker_id}:{secrets.token_hex(8)}"
    lease = try_acquire_lease(session, candidate, token=token, now=now)
    if lease is None:
        return None
    return candidate.id


def _should_abort_for_lease_loss(session: Session, lease: SyncLease, stage: str) -> bool:
    if not renew_lease(session, lease, now=utcnow()):
        logger.warning("connector_sync_lease_lost sync_id=%s stage=%s", lease.sync_id, stage)
        return True
    return False


def _stage_copy(source: Path) -> Path:
    staging_dir = document_service.UPLOAD_DIR / ".staging"
    document_service._make_private_directory(staging_dir)
    staging = staging_dir / f"{secrets.token_hex(24)}{source.suffix or '.part'}"
    shutil.copy2(source, staging)
    return staging


def _existing_document(session: Session, sync_id: str, external_id: str) -> Document | None:
    return session.scalar(
        select(Document).where(
            Document.connector_sync_id == sync_id,
            Document.external_id == external_id,
        )
    )


async def _upsert_and_enqueue(
    *,
    sync: ConnectorSync,
    knowledge_base: KnowledgeBase,
    item,
    content_hash: str,
    size: int,
) -> str:
    """Create or update a connector Document and enqueue one ingest job.

    Returns "added", "updated" or "skipped".
    """
    extension = item.id.rsplit(".", 1)[-1].lower() if "." in item.id else ""
    page_count = await document_service.inspect_document_pages(Path(item.path), extension)
    storage = document_service.get_object_storage(local_root=document_service.UPLOAD_DIR)
    staged = _stage_copy(Path(item.path))
    try:
        storage_key = storage.put_staged(staged, knowledge_base_id=knowledge_base.id)
    finally:
        if staged.exists():
            try:
                staged.unlink()
            except OSError:
                pass

    with SessionLocal() as session:
        existing = _existing_document(session, sync.id, item.id)
        if existing is not None and existing.content_hash == content_hash:
            return "skipped"
        if existing is None:
            document = Document(
                knowledge_base_id=knowledge_base.id,
                display_name=Path(item.id).name or item.id,
                storage_path=storage_key,
                storage_key=storage_key,
                size_bytes=size,
                page_count=page_count,
                sha256=content_hash,
                content_hash=content_hash,
                connector_sync_id=sync.id,
                external_id=item.id,
                connector_source=sync.source_type,
                status="queued",
            )
            session.add(document)
            session.flush()
            outcome = "added"
        else:
            existing.sha256 = content_hash
            existing.content_hash = content_hash
            existing.size_bytes = size
            existing.page_count = page_count
            existing.storage_key = storage_key
            existing.storage_path = storage_key
            existing.status = "queued"
            existing.error_code = None
            existing.error_message = None
            session.flush()
            document = existing
            outcome = "updated"

        steps, pipeline_version = document_service._pipeline_config_for_document(session, document)
        job = create_ingest_job(
            session,
            document,
            pipeline_steps=steps,
            pipeline_version=pipeline_version,
        )
        job.max_retries = document_service.INGEST_MAX_RETRIES
        job.source = INGEST_SOURCE_CONNECTOR
        job.source_metadata = {
            "connector_sync_id": sync.id,
            "external_id": item.id,
            "content_hash": content_hash,
            "replace_document_id": document.id,
        }
        session.commit()
        session.refresh(job)
        document_service._dispatch_ingest_or_mark_failed(session, document=document, job=job)
        return outcome


async def _delete_removed(*, sync: ConnectorSync, external_id: str) -> bool:
    """Delete a removed source document; False when it is still busy (retry later)."""
    with SessionLocal() as session:
        existing = _existing_document(session, sync.id, external_id)
        if existing is None:
            return True
        try:
            await document_service.delete_document(session, existing)
        except ProductError as exc:
            if exc.code == "DOCUMENT_BUSY":
                return False
            raise
    return True


def _apply_permissions(
    *, sync: ConnectorSync, knowledge_base: KnowledgeBase, connector: Connector
) -> int:
    """Additive-only permission sync (grants applied; revocation is not performed)."""
    bind_audit_context(
        AuditContext(
            operator_id=knowledge_base.owner_id,
            operator_name="connector-sync",
            operator_role="owner",
        )
    )
    applied = 0
    try:
        with SessionLocal() as session:
            for grant in connector.fetch_permissions(""):
                username = (grant or {}).get("username") or ""
                role = (grant or {}).get("role") or ""
                if not username or not role:
                    continue
                user = session.scalar(select(User).where(User.username == username))
                if user is None:
                    continue
                member = session.scalar(
                    select(KnowledgeBaseMember).where(
                        KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
                        KnowledgeBaseMember.user_id == user.id,
                    )
                )
                if member is None:
                    try:
                        add_kb_member(session, knowledge_base, user.id, role)
                        applied += 1
                    except ProductError:
                        continue
                elif member.role != role:
                    try:
                        set_kb_member_role(session, knowledge_base, user.id, role)
                        applied += 1
                    except ProductError:
                        continue
            session.commit()
        return applied
    finally:
        bind_audit_context(None)


async def process_due_sync(sync_id: str, *, worker_id: str) -> None:
    """Run one claimed sync: changes -> enqueue -> delete -> permissions -> run record."""
    with SessionLocal() as session:
        sync = session.get(ConnectorSync, sync_id)
        if sync is None or sync.status != SYNC_STATUS_ACTIVE or not sync.lock_owner:
            return
        if not sync.lock_owner.startswith(worker_id):
            return
        knowledge_base = session.get(KnowledgeBase, sync.knowledge_base_id)
        if knowledge_base is None:
            return
        scheduled_for = sync.next_run_at or utcnow()
        run = ConnectorSyncRun(
            sync_id=sync.id,
            scheduled_for=scheduled_for,
            status=RUN_STATUS_RUNNING,
            started_at=utcnow(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        source_type = sync.source_type
        config = dict(sync.config or {})
        cron = sync.cron
        cursor = sync.cursor
        lease = SyncLease(sync.id, sync.lock_owner, sync.lock_until)
        sync_id_frozen = sync.id
        session.expunge(sync)
        session.expunge(knowledge_base)
        session.expunge(run)

    counts = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0, "enqueue_failed": 0}

    def finalize(final_status: str, message: str | None) -> None:
        with SessionLocal() as session:
            stored_run = session.get(ConnectorSyncRun, run.id)
            if stored_run is not None and stored_run.status == RUN_STATUS_RUNNING:
                stored_run.status = final_status
                stored_run.finished_at = utcnow()
                stored_run.added = counts.get("added", 0)
                stored_run.updated = counts.get("updated", 0)
                stored_run.deleted = counts.get("deleted", 0)
                stored_run.skipped = counts.get("skipped", 0)
                stored_run.enqueue_failed = counts.get("enqueue_failed", 0)
                stored_run.error_message = message
                session.commit()

    try:
        connector = create_connector(source_type, config)
        changes = connector.detect_changes(cursor)

        candidates = list(changes.added) + list(changes.modified_candidates)
        for item in candidates:
            with SessionLocal() as session:
                if _should_abort_for_lease_loss(session, lease, "upsert"):
                    release_lease(session, lease)
                    finalize(RUN_STATUS_ABORTED, "同步锁已丢失")
                    return
            result = connector.fetch_item(item.id)
            if not result.content_hash:
                counts["skipped"] += 1
                continue
            try:
                outcome = await _upsert_and_enqueue(
                    sync=sync,
                    knowledge_base=knowledge_base,
                    item=item,
                    content_hash=result.content_hash,
                    size=result.size,
                )
            except Exception:  # noqa: BLE001
                logger.exception("connector_sync_enqueue_failed item=%s", item.id)
                counts["enqueue_failed"] += 1
                continue
            counts[outcome] += 1

        retained_removed: list[str] = []
        for external_id in changes.removed:
            with SessionLocal() as session:
                if _should_abort_for_lease_loss(session, lease, "delete"):
                    release_lease(session, lease)
                    finalize(RUN_STATUS_ABORTED, "同步锁已丢失")
                    return
            try:
                deleted = await _delete_removed(sync=sync, external_id=external_id)
            except Exception:  # noqa: BLE001
                logger.exception("connector_sync_delete_failed item=%s", external_id)
                retained_removed.append(external_id)
                continue
            if deleted:
                counts["deleted"] += 1
            else:
                retained_removed.append(external_id)

        with SessionLocal() as session:
            if _should_abort_for_lease_loss(session, lease, "permissions"):
                release_lease(session, lease)
                finalize(RUN_STATUS_ABORTED, "同步锁已丢失")
                return
        _apply_permissions(sync=sync, knowledge_base=knowledge_base, connector=connector)

        # Success: advance cursor + next_run_at, release the lease.
        new_cursor = changes.cursor
        for retained in retained_removed:
            new_cursor.setdefault(retained, {"mtime": 0, "size": 0})
        with SessionLocal() as session:
            session.execute(
                update(ConnectorSync)
                .where(ConnectorSync.id == sync_id_frozen, ConnectorSync.lock_owner == lease.token)
                .values(
                    cursor=new_cursor,
                    next_run_at=compute_next_run(cron, utcnow()),
                    lock_owner=None,
                    lock_until=None,
                )
                .execution_options(synchronize_session=False)
            )
            session.commit()
        finalize(RUN_STATUS_SUCCESS, None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("connector_sync_failed sync_id=%s", sync_id_frozen)
        with SessionLocal() as session:
            release_lease(session, lease)
        finalize(RUN_STATUS_FAILED, str(exc)[:280])
