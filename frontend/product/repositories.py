from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import Integer, func, select, update
from sqlalchemy.orm import Session, selectinload

from frontend.product.models import (
    Conversation,
    Document,
    IngestJob,
    KnowledgeBase,
    Message,
)


def list_knowledge_bases(session: Session) -> list[dict]:
    document_counts = (
        select(
            Document.knowledge_base_id,
            func.count(Document.id).label("document_count"),
            func.sum(func.cast(Document.status == "ready", Integer)).label("ready_document_count"),
        )
        .group_by(Document.knowledge_base_id)
        .subquery()
    )
    conversation_counts = (
        select(
            Conversation.knowledge_base_id,
            func.count(Conversation.id).label("conversation_count"),
        )
        .group_by(Conversation.knowledge_base_id)
        .subquery()
    )
    rows = session.execute(
        select(
            KnowledgeBase,
            func.coalesce(document_counts.c.document_count, 0),
            func.coalesce(document_counts.c.ready_document_count, 0),
            func.coalesce(conversation_counts.c.conversation_count, 0),
        )
        .outerjoin(
            document_counts,
            document_counts.c.knowledge_base_id == KnowledgeBase.id,
        )
        .outerjoin(
            conversation_counts,
            conversation_counts.c.knowledge_base_id == KnowledgeBase.id,
        )
        .order_by(KnowledgeBase.is_current.desc(), KnowledgeBase.updated_at.desc())
    ).all()
    items = []
    for knowledge_base, document_count, ready_document_count, conversation_count in rows:
        try:
            index_manifest = (
                json.loads(knowledge_base.index_manifest) if knowledge_base.index_manifest else None
            )
        except (TypeError, ValueError):
            index_manifest = None
        if not isinstance(index_manifest, dict):
            index_manifest = None
        items.append(
            {
                "id": knowledge_base.id,
                "name": knowledge_base.name,
                "description": knowledge_base.description,
                "document_count": int(document_count),
                "ready_document_count": int(ready_document_count),
                "conversation_count": int(conversation_count),
                "is_current": knowledge_base.is_current,
                "index_manifest": index_manifest,
                "index_status": "verified" if index_manifest is not None else "not_indexed",
                "index_previous_collection": knowledge_base.index_previous_collection,
                "created_at": knowledge_base.created_at,
                "updated_at": knowledge_base.updated_at,
            }
        )
    return items


def create_knowledge_base(
    session: Session,
    *,
    name: str,
    description: str,
) -> KnowledgeBase:
    has_current = session.scalar(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.is_current)
    )
    knowledge_base = KnowledgeBase(
        name=name.strip(),
        description=description.strip(),
        collection_name=f"kb_{uuid4().hex}",
        is_current=not bool(has_current),
    )
    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def set_current_knowledge_base(session: Session, knowledge_base_id: str) -> KnowledgeBase | None:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        return None
    session.execute(update(KnowledgeBase).values(is_current=False))
    knowledge_base.is_current = True
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    return session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.knowledge_base),
            selectinload(Conversation.messages).selectinload(Message.citations),
        )
    )


def create_ingest_job(session: Session, document: Document) -> IngestJob:
    latest_attempt = session.scalar(
        select(func.max(IngestJob.attempt)).where(IngestJob.document_id == document.id)
    )
    job = IngestJob(document_id=document.id, attempt=int(latest_attempt or 0) + 1)
    session.add(job)
    session.flush()
    return job
