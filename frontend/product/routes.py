from __future__ import annotations

import json
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from frontend.product.auth import (
    acquire_setup_lock,
    authenticate,
    claim_legacy_data,
    create_login_session,
    create_user,
    delete_login_session,
    optional_user,
    require_admin,
    require_user,
    setup_required,
    user_response,
)
from frontend.product.db import get_session
from frontend.product.errors import ProductError
from frontend.product.models import (
    LEGACY_OWNER_ID,
    Conversation,
    Document,
    IngestJob,
    KnowledgeBase,
    Message,
    User,
)
from frontend.product.repositories import (
    create_knowledge_base,
    get_conversation,
    list_knowledge_bases,
    set_current_knowledge_base,
)
from frontend.product.schemas import (
    AuthLogin,
    AuthSetup,
    ConversationCreate,
    DocumentGovernanceUpdate,
    DocumentTemporalUpdate,
    KnowledgeBaseCreate,
    MessageCreate,
    MessageResponse,
    UserCreate,
)
from frontend.product.services import documents as document_service
from frontend.product.services.conversations import stream_message_events, submit_message
from frontend.product.services.documents import (
    create_document_from_upload,
    delete_document,
    retry_document,
    update_document_governance_metadata,
    update_document_temporal_metadata,
)
from frontend.product.services.knowledge_bases import (
    delete_knowledge_base,
    reindex_knowledge_base,
)
from frontend.product.services.knowledge_health import (
    assemble_health_payload,
    create_health_snapshot,
    health_snapshot_response,
    latest_health_snapshot,
    list_health_snapshots,
)

router = APIRouter(prefix="/api")


def _owned_knowledge_base(
    session: Session,
    knowledge_base_id: str,
    owner_id: str,
) -> KnowledgeBase:
    knowledge_base = session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.owner_id == owner_id,
        )
    )
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    return knowledge_base


def _owned_document(session: Session, document_id: str, owner_id: str) -> Document:
    document = session.scalar(
        select(Document)
        .join(Document.knowledge_base)
        .where(
            Document.id == document_id,
            KnowledgeBase.owner_id == owner_id,
        )
    )
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
    return document


def _owned_ingest_job(session: Session, job_id: str, owner_id: str) -> IngestJob:
    job = session.scalar(
        select(IngestJob)
        .join(IngestJob.document)
        .join(Document.knowledge_base)
        .where(
            IngestJob.id == job_id,
            KnowledgeBase.owner_id == owner_id,
        )
    )
    if job is None:
        raise ProductError(
            "INGEST_JOB_NOT_FOUND",
            "没有找到这个文档处理任务。",
            status_code=404,
        )
    return job


@router.get("/auth/status")
def auth_status(
    user: User | None = Depends(optional_user),
    session: Session = Depends(get_session),
) -> dict:
    return {
        "setup_required": setup_required(session),
        "authenticated": user is not None,
        "user": user_response(user) if user is not None else None,
    }


@router.post("/auth/setup", status_code=201)
def setup_workspace(
    payload: AuthSetup,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    if not setup_required(session):
        raise ProductError(
            "SETUP_ALREADY_COMPLETED",
            "工作台已经完成初始化，请直接登录。",
            status_code=409,
        )
    try:
        acquire_setup_lock(session)
        user = create_user(
            session,
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            role="admin",
        )
        claim_legacy_data(session, user.id)
        create_login_session(session, user, response)
    except IntegrityError as exc:
        session.rollback()
        raise ProductError(
            "SETUP_ALREADY_COMPLETED",
            "工作台已经完成初始化，请直接登录。",
            status_code=409,
        ) from exc
    session.refresh(user)
    return {"user": user_response(user)}


@router.post("/auth/login")
def login(
    payload: AuthLogin,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    user = authenticate(session, payload.username, payload.password)
    if user is None:
        raise ProductError(
            "INVALID_CREDENTIALS",
            "用户名或密码不正确。",
            status_code=401,
        )
    create_login_session(session, user, response)
    return {"user": user_response(user)}


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> dict:
    delete_login_session(session, request, response)
    return {"logged_out": True}


@router.get("/auth/me")
def current_user(user: User = Depends(require_user)) -> dict:
    return {"user": user_response(user)}


@router.get("/admin/users")
def list_users(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    users = session.scalars(
        select(User).where(User.id != LEGACY_OWNER_ID).order_by(User.created_at, User.username)
    ).all()
    return {"items": [user_response(user) for user in users]}


@router.post("/admin/users", status_code=201)
def add_user(
    payload: UserCreate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    try:
        user = create_user(
            session,
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            role=payload.role,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ProductError(
            "USERNAME_EXISTS",
            "这个用户名已经存在。",
            status_code=409,
        ) from exc
    session.refresh(user)
    return {"user": user_response(user)}


def knowledge_base_detail(session: Session, knowledge_base: KnowledgeBase) -> dict:
    document_count = session.scalar(
        select(func.count(Document.id)).where(Document.knowledge_base_id == knowledge_base.id)
    )
    ready_document_count = session.scalar(
        select(func.count(Document.id)).where(
            Document.knowledge_base_id == knowledge_base.id,
            Document.status == "ready",
        )
    )
    conversation_count = session.scalar(
        select(func.count(Conversation.id)).where(
            Conversation.knowledge_base_id == knowledge_base.id
        )
    )
    try:
        index_manifest = (
            json.loads(knowledge_base.index_manifest) if knowledge_base.index_manifest else None
        )
    except (TypeError, ValueError):
        index_manifest = None
    return {
        "id": knowledge_base.id,
        "name": knowledge_base.name,
        "description": knowledge_base.description,
        "document_count": int(document_count or 0),
        "ready_document_count": int(ready_document_count or 0),
        "conversation_count": int(conversation_count or 0),
        "is_current": knowledge_base.is_current,
        "index_manifest": index_manifest,
        "index_status": "verified" if index_manifest is not None else "not_indexed",
        "index_previous_collection": knowledge_base.index_previous_collection,
        "created_at": knowledge_base.created_at,
        "updated_at": knowledge_base.updated_at,
    }


def document_response(document: Document) -> dict:
    return {
        "id": document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "display_name": document.display_name,
        "size_bytes": document.size_bytes,
        "page_count": document.page_count,
        "status": document.status,
        "error": (
            {
                "code": document.error_code,
                "message": document.error_message,
            }
            if document.error_code
            else None
        ),
        "published_at": document.published_at,
        "effective_at": document.effective_at,
        "superseded_at": document.superseded_at,
        "temporal_metadata_source": document.temporal_metadata_source,
        "version_family": document.version_family,
        "version_family_source": document.version_family_source,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def ingest_job_response(job: IngestJob) -> dict:
    return {
        "id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "attempt": job.attempt,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "available_at": job.available_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code and job.error_message
            else None
        ),
    }


def message_response(message: Message) -> dict:
    return MessageResponse.model_validate(message).model_dump(mode="json")


def _sse_message(envelope: dict) -> str:
    event_name = str(envelope.get("event") or "message")
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.get("/knowledge-bases")
def knowledge_bases(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    return {"items": list_knowledge_bases(session, user.id)}


@router.post("/knowledge-bases", status_code=201)
def add_knowledge_base(
    request: KnowledgeBaseCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    try:
        knowledge_base = create_knowledge_base(
            session,
            name=request.name,
            description=request.description,
            owner_id=user.id,
        )
    except IntegrityError as exc:
        session.rollback()
        raise ProductError(
            "KNOWLEDGE_BASE_NAME_EXISTS",
            "已经存在同名知识库。",
            status_code=409,
        ) from exc
    return knowledge_base_detail(session, knowledge_base)


@router.get("/knowledge-bases/{knowledge_base_id}")
def get_knowledge_base(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    return knowledge_base_detail(session, knowledge_base)


@router.put("/knowledge-bases/{knowledge_base_id}/current")
def select_knowledge_base(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = set_current_knowledge_base(session, knowledge_base_id, user.id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    return knowledge_base_detail(session, knowledge_base)


@router.delete("/knowledge-bases/{knowledge_base_id}")
async def remove_knowledge_base(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    next_current = await delete_knowledge_base(session, knowledge_base)
    return {
        "deleted_id": knowledge_base_id,
        "current_knowledge_base_id": next_current.id if next_current else None,
    }


@router.post("/knowledge-bases/{knowledge_base_id}/reindex")
async def reindex_knowledge_base_route(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    await reindex_knowledge_base(session, knowledge_base)
    session.refresh(knowledge_base)
    return knowledge_base_detail(session, knowledge_base)


@router.get("/knowledge-bases/{knowledge_base_id}/health")
def knowledge_health(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    latest = latest_health_snapshot(session, knowledge_base.id)
    current = assemble_health_payload(session, knowledge_base)
    return {
        "snapshot": health_snapshot_response(latest) if latest is not None else None,
        "current": current,
    }


@router.post("/knowledge-bases/{knowledge_base_id}/health/snapshot", status_code=201)
def create_knowledge_health_snapshot_route(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    snapshot, previous, change = create_health_snapshot(
        session,
        knowledge_base,
        user.id,
    )
    return {
        "snapshot": health_snapshot_response(snapshot),
        "previous": (
            health_snapshot_response(previous) if previous is not None else None
        ),
        "change": change,
    }


@router.get("/knowledge-bases/{knowledge_base_id}/health/history")
def knowledge_health_history(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    _owned_knowledge_base(session, knowledge_base_id, user.id)
    items = list_health_snapshots(session, knowledge_base_id)
    return {"items": [health_snapshot_response(item) for item in items]}


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
def documents(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    _owned_knowledge_base(session, knowledge_base_id, user.id)
    items = session.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc())
    ).all()
    return {"items": [document_response(document) for document in items]}


@router.get("/documents/{document_id}/content")
def document_content(
    document_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    document = _owned_document(session, document_id, user.id)
    try:
        stream = document_service.document_storage(document).open(
            document_service.document_object_key(document)
        )
    except FileNotFoundError:
        raise ProductError(
            "DOCUMENT_CONTENT_MISSING",
            "原始 PDF 已不存在，无法打开原文。",
            status_code=404,
        )
    except document_service.StorageError as exc:
        raise ProductError(
            "DOCUMENT_STORAGE_INVALID",
            "文档存储配置异常，无法打开原文。",
            status_code=500,
        ) from exc

    def chunks():
        try:
            while data := stream.read(1024 * 1024):
                yield data
        finally:
            stream.close()

    try:
        document.display_name.encode("ascii")
    except UnicodeEncodeError:
        content_disposition = f"inline; filename*=UTF-8''{quote(document.display_name, safe='')}"
    else:
        safe_name = document.display_name.replace('"', "")
        content_disposition = f'inline; filename="{safe_name}"'
    return StreamingResponse(
        chunks(),
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": content_disposition,
        },
    )


@router.post("/knowledge-bases/{knowledge_base_id}/documents", status_code=202)
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    published_at: date | None = Form(default=None),
    effective_at: date | None = Form(default=None),
    superseded_at: date | None = Form(default=None),
    version_family: str | None = Form(default=None),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, knowledge_base_id, user.id)
    try:
        document, job = await create_document_from_upload(
            session,
            knowledge_base=knowledge_base,
            file=file,
            published_at=published_at,
            effective_at=effective_at,
            superseded_at=superseded_at,
            version_family=version_family,
        )
    finally:
        await file.close()
    return {
        "document": document_response(document),
        "job": ingest_job_response(job),
    }


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    document = _owned_document(session, document_id, user.id)
    return document_response(document)


@router.patch("/documents/{document_id}/temporal-metadata", status_code=202)
def update_document_temporal_metadata_route(
    document_id: str,
    payload: DocumentTemporalUpdate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    document = _owned_document(session, document_id, user.id)
    job = update_document_temporal_metadata(
        session,
        document,
        published_at=payload.published_at,
        effective_at=payload.effective_at,
        superseded_at=payload.superseded_at,
    )
    return {
        "document": document_response(document),
        "job": ingest_job_response(job) if job is not None else None,
    }


@router.patch("/documents/{document_id}/governance-metadata", status_code=202)
def update_document_governance_metadata_route(
    document_id: str,
    payload: DocumentGovernanceUpdate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    document = _owned_document(session, document_id, user.id)
    job = update_document_governance_metadata(
        session,
        document,
        published_at=payload.published_at,
        effective_at=payload.effective_at,
        superseded_at=payload.superseded_at,
        version_family=payload.version_family,
    )
    return {
        "document": document_response(document),
        "job": ingest_job_response(job) if job is not None else None,
    }


@router.get("/ingest-jobs/{job_id}")
def get_ingest_job(
    job_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    job = _owned_ingest_job(session, job_id, user.id)
    return ingest_job_response(job)


@router.delete("/documents/{document_id}", status_code=204)
async def remove_document(
    document_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    document = _owned_document(session, document_id, user.id)
    await delete_document(session, document)
    return Response(status_code=204)


@router.post("/documents/{document_id}/retry", status_code=202)
def retry_failed_document(
    document_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    document = _owned_document(session, document_id, user.id)
    job = retry_document(session, document)
    return {"document": document_response(document), "job": ingest_job_response(job)}


@router.get("/conversations")
def conversations(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    items = session.scalars(
        select(Conversation)
        .where(Conversation.owner_id == user.id)
        .options(selectinload(Conversation.knowledge_base))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": conversation.id,
                "knowledge_base_id": conversation.knowledge_base_id,
                "knowledge_base_name": conversation.knowledge_base.name,
                "title": conversation.title,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
            for conversation in items
        ]
    }


@router.post("/conversations", status_code=201)
def add_conversation(
    request: ConversationCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _owned_knowledge_base(session, request.knowledge_base_id, user.id)
    conversation = Conversation(
        owner_id=user.id,
        knowledge_base_id=knowledge_base.id,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return {
        "id": conversation.id,
        "knowledge_base_id": conversation.knowledge_base_id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


@router.get("/conversations/{conversation_id}")
def conversation_detail(
    conversation_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    conversation = get_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise ProductError(
            "CONVERSATION_NOT_FOUND",
            "没有找到这个对话。",
            status_code=404,
        )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "knowledge_base": knowledge_base_detail(session, conversation.knowledge_base),
        "messages": [message_response(message) for message in conversation.messages],
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
def remove_conversation(
    conversation_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    conversation = get_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise ProductError(
            "CONVERSATION_NOT_FOUND",
            "没有找到这个对话。",
            status_code=404,
        )
    session.delete(conversation)
    session.commit()
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages")
async def add_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    conversation = get_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise ProductError(
            "CONVERSATION_NOT_FOUND",
            "没有找到这个对话。",
            status_code=404,
        )
    user_message, assistant_message = await submit_message(
        session,
        conversation=conversation,
        content=payload.content,
        use_web_search=payload.use_web_search,
        request_id=getattr(request.state, "request_id", None),
    )
    session.expire_all()
    refreshed = get_conversation(session, conversation_id, user.id)
    assert refreshed is not None
    messages_by_id = {message.id: message for message in refreshed.messages}
    return {
        "user_message": message_response(messages_by_id[user_message.id]),
        "assistant_message": message_response(messages_by_id[assistant_message.id]),
    }


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    conversation = get_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise ProductError(
            "CONVERSATION_NOT_FOUND",
            "没有找到这个对话。",
            status_code=404,
        )
    if not payload.content.strip():
        raise ProductError("MESSAGE_EMPTY", "请输入你想了解的问题。")

    async def relay_events():
        async for envelope in stream_message_events(
            session,
            conversation=conversation,
            content=payload.content,
            use_web_search=payload.use_web_search,
            request_id=getattr(request.state, "request_id", None),
        ):
            yield _sse_message(envelope)

    return StreamingResponse(
        relay_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Trace-Retention": "transient",
        },
    )
