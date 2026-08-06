from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from frontend.product.db import get_session
from frontend.product.errors import ProductError
from frontend.product.models import Conversation, Document, IngestJob, KnowledgeBase, Message
from frontend.product.repositories import (
    create_knowledge_base,
    get_conversation,
    list_knowledge_bases,
    set_current_knowledge_base,
)
from frontend.product.schemas import (
    ConversationCreate,
    KnowledgeBaseCreate,
    MessageCreate,
    MessageResponse,
)
from frontend.product.services import documents as document_service
from frontend.product.services.conversations import stream_message_events, submit_message
from frontend.product.services.documents import (
    create_document_from_upload,
    delete_document,
    retry_document,
)
from frontend.product.services.knowledge_bases import (
    delete_knowledge_base,
    reindex_knowledge_base,
)

router = APIRouter(prefix="/api")


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
def knowledge_bases(session: Session = Depends(get_session)) -> dict:
    return {"items": list_knowledge_bases(session)}


@router.post("/knowledge-bases", status_code=201)
def add_knowledge_base(
    request: KnowledgeBaseCreate,
    session: Session = Depends(get_session),
) -> dict:
    try:
        knowledge_base = create_knowledge_base(
            session,
            name=request.name,
            description=request.description,
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
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    return knowledge_base_detail(session, knowledge_base)


@router.put("/knowledge-bases/{knowledge_base_id}/current")
def select_knowledge_base(
    knowledge_base_id: str,
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = set_current_knowledge_base(session, knowledge_base_id)
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
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    next_current = await delete_knowledge_base(session, knowledge_base)
    return {
        "deleted_id": knowledge_base_id,
        "current_knowledge_base_id": next_current.id if next_current else None,
    }


@router.post("/knowledge-bases/{knowledge_base_id}/reindex")
async def reindex_knowledge_base_route(
    knowledge_base_id: str,
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    await reindex_knowledge_base(session, knowledge_base)
    session.refresh(knowledge_base)
    return knowledge_base_detail(session, knowledge_base)


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
def documents(
    knowledge_base_id: str,
    session: Session = Depends(get_session),
) -> dict:
    if session.get(KnowledgeBase, knowledge_base_id) is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    items = session.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc())
    ).all()
    return {"items": [document_response(document) for document in items]}


@router.get("/documents/{document_id}/content")
def document_content(
    document_id: str,
    session: Session = Depends(get_session),
) -> FileResponse:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError(
            "DOCUMENT_NOT_FOUND",
            "没有找到这份文档。",
            status_code=404,
        )
    upload_root = document_service.UPLOAD_DIR.resolve()
    source_path = Path(document.storage_path).resolve()
    if source_path != upload_root and upload_root not in source_path.parents:
        raise ProductError(
            "DOCUMENT_STORAGE_INVALID",
            "文档存储路径异常，无法打开原文。",
            status_code=500,
        )
    if not source_path.is_file():
        raise ProductError(
            "DOCUMENT_CONTENT_MISSING",
            "原始 PDF 已不存在，无法打开原文。",
            status_code=404,
        )
    return FileResponse(
        source_path,
        media_type="application/pdf",
        filename=document.display_name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/knowledge-bases/{knowledge_base_id}/documents", status_code=202)
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    try:
        document, job = await create_document_from_upload(
            session,
            knowledge_base=knowledge_base,
            file=file,
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
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError(
            "DOCUMENT_NOT_FOUND",
            "没有找到这个文档。",
            status_code=404,
        )
    return document_response(document)


@router.get("/ingest-jobs/{job_id}")
def get_ingest_job(
    job_id: str,
    session: Session = Depends(get_session),
) -> dict:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise ProductError(
            "INGEST_JOB_NOT_FOUND",
            "没有找到这个文档处理任务。",
            status_code=404,
        )
    return ingest_job_response(job)


@router.delete("/documents/{document_id}", status_code=204)
async def remove_document(
    document_id: str,
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError(
            "DOCUMENT_NOT_FOUND",
            "没有找到这个文档。",
            status_code=404,
        )
    await delete_document(session, document)
    return Response(status_code=204)


@router.post("/documents/{document_id}/retry", status_code=202)
def retry_failed_document(
    document_id: str,
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError(
            "DOCUMENT_NOT_FOUND",
            "没有找到这个文档。",
            status_code=404,
        )
    job = retry_document(session, document)
    return {"document": document_response(document), "job": ingest_job_response(job)}


@router.get("/conversations")
def conversations(
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict:
    items = session.scalars(
        select(Conversation)
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
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, request.knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    conversation = Conversation(knowledge_base_id=knowledge_base.id)
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
    session: Session = Depends(get_session),
) -> dict:
    conversation = get_conversation(session, conversation_id)
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
    session: Session = Depends(get_session),
) -> Response:
    conversation = session.get(Conversation, conversation_id)
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
    session: Session = Depends(get_session),
) -> dict:
    conversation = get_conversation(session, conversation_id)
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
    refreshed = get_conversation(session, conversation_id)
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
    session: Session = Depends(get_session),
) -> StreamingResponse:
    conversation = get_conversation(session, conversation_id)
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
