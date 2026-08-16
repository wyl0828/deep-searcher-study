from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import Integer, func, select, update
from sqlalchemy.orm import Session, selectinload

from frontend.product.models import (
    LEGACY_OWNER_ID,
    Conversation,
    Document,
    IngestJob,
    KnowledgeBase,
    Message,
    User,
    Workspace,
    WorkspaceMember,
)
from frontend.product.errors import ProductError


def _resolved_owner_id(session: Session, owner_id: str | None) -> str:
    if owner_id is not None:
        return owner_id
    from frontend.product.auth import ensure_legacy_owner

    ensure_legacy_owner(session)
    return LEGACY_OWNER_ID


def list_knowledge_bases(
    session: Session,
    user_id: str | None = None,
) -> list[dict]:
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
    query = select(
        KnowledgeBase,
        func.coalesce(document_counts.c.document_count, 0),
        func.coalesce(document_counts.c.ready_document_count, 0),
        func.coalesce(conversation_counts.c.conversation_count, 0),
    )
    if user_id is None:
        query = query.outerjoin(
            document_counts,
            document_counts.c.knowledge_base_id == KnowledgeBase.id,
        ).outerjoin(
            conversation_counts,
            conversation_counts.c.knowledge_base_id == KnowledgeBase.id,
        )
    else:
        query = query.join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == KnowledgeBase.workspace_id,
        ).where(
            WorkspaceMember.user_id == user_id
        ).outerjoin(
            document_counts,
            document_counts.c.knowledge_base_id == KnowledgeBase.id,
        ).outerjoin(
            conversation_counts,
            conversation_counts.c.knowledge_base_id == KnowledgeBase.id,
        )
    rows = session.execute(
        query.order_by(
            KnowledgeBase.is_current.desc(),
            KnowledgeBase.updated_at.desc(),
        )
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
                "workspace_id": knowledge_base.workspace_id,
                "workspace_name": (
                    session.get(Workspace, knowledge_base.workspace_id).name
                    if knowledge_base.workspace_id
                    else None
                ),
                "role": (
                    session.scalar(
                        select(WorkspaceMember.role).where(
                            WorkspaceMember.workspace_id
                            == knowledge_base.workspace_id,
                            WorkspaceMember.user_id == user_id,
                        )
                    )
                    if user_id is not None
                    else None
                ),
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
    workspace_id: str | None = None,
    owner_id: str | None = None,
) -> KnowledgeBase:
    resolved_owner_id = _resolved_owner_id(session, owner_id)
    if workspace_id is None:
        from frontend.product.services.access import ensure_personal_workspaces

        ensure_personal_workspaces(session)
        workspace = session.scalar(
            select(Workspace).where(
                Workspace.owner_id == resolved_owner_id,
                Workspace.name == (
                    session.get(User, resolved_owner_id).username
                    if resolved_owner_id != LEGACY_OWNER_ID
                    else "__legacy__"
                ),
            )
        )
        if workspace is None:
            workspace = session.scalar(
                select(Workspace).where(Workspace.name == "__legacy__")
            )
        if workspace is None:
            raise ProductError(
                "WORKSPACE_MISSING",
                "无法确定知识库所属工作区。",
                status_code=400,
            )
        workspace_id = workspace.id
    has_current = session.scalar(
        select(func.count())
        .select_from(KnowledgeBase)
        .where(
            KnowledgeBase.workspace_id == workspace_id,
            KnowledgeBase.is_current,
        )
    )
    knowledge_base = KnowledgeBase(
        owner_id=resolved_owner_id,
        workspace_id=workspace_id,
        name=name.strip(),
        description=description.strip(),
        collection_name=f"kb_{uuid4().hex}",
        is_current=not bool(has_current),
    )
    session.add(knowledge_base)
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def set_current_knowledge_base(
    session: Session,
    knowledge_base_id: str,
) -> KnowledgeBase | None:
    knowledge_base = session.scalar(
        select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
    )
    if knowledge_base is None:
        return None
    session.execute(
        update(KnowledgeBase)
        .where(KnowledgeBase.workspace_id == knowledge_base.workspace_id)
        .values(is_current=False)
    )
    knowledge_base.is_current = True
    session.commit()
    session.refresh(knowledge_base)
    return knowledge_base


def get_conversation(
    session: Session,
    conversation_id: str,
    owner_id: str | None = None,
) -> Conversation | None:
    query = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.knowledge_base),
            selectinload(Conversation.messages).selectinload(Message.citations),
            selectinload(Conversation.messages).selectinload(Message.claims),
        )
    )
    if owner_id is not None:
        query = query.where(Conversation.owner_id == owner_id)
    return session.scalar(query)


def create_ingest_job(session: Session, document: Document) -> IngestJob:
    latest_attempt = session.scalar(
        select(func.max(IngestJob.attempt)).where(IngestJob.document_id == document.id)
    )
    job = IngestJob(document_id=document.id, attempt=int(latest_attempt or 0) + 1)
    session.add(job)
    session.flush()
    return job
