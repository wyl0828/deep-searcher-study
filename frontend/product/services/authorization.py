"""The single runtime knowledge-access resolver.

This module is deliberately the only place where product knowledge access is
computed.  Workspace, group and legacy ACL rows are not consulted here.  The
old tables may still exist while the local database is migrated, but they have
no effect on a user's current query, document or citation access.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from frontend.product.errors import ProductError
from frontend.product.models import (
    DepartmentKnowledgeBase,
    KnowledgeBase,
    User,
    UserKnowledgeBaseAccess,
)

Permission = Literal["read", "write", "admin"]
AccessSource = Literal["company_wide", "department"]


def _ordered_knowledge_base_items(
    session: Session,
    knowledge_base_ids: set[str],
) -> list[KnowledgeBase]:
    if not knowledge_base_ids:
        return []
    items = session.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.id.in_(knowledge_base_ids))
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
    ).all()
    return list(items)


def user_knowledge_access_summary(session: Session, user: User) -> dict:
    """Return the semantic access breakdown used by the admin user detail.

    Direct rows are deliberately treated as storage facts only.  The API
    exposes them as personal extras after subtracting the current inherited
    set, so an overlapping direct row cannot become a second checkbox source.
    """

    company_ids = set(
        session.scalars(
            select(KnowledgeBase.id).where(KnowledgeBase.is_company_wide.is_(True))
        ).all()
    )
    department_ids: set[str] = set()
    if user.department_id is not None:
        department_ids = set(
            session.scalars(
                select(DepartmentKnowledgeBase.knowledge_base_id).where(
                    DepartmentKnowledgeBase.department_id == user.department_id
                )
            ).all()
        )
    direct_ids = set(
        session.scalars(
            select(UserKnowledgeBaseAccess.knowledge_base_id).where(
                UserKnowledgeBaseAccess.user_id == user.id
            )
        ).all()
    )
    inherited_ids = company_ids | department_ids
    effective_ids = inherited_ids | direct_ids

    if user.role == "admin":
        effective_ids = {
            item.id for item in session.scalars(select(KnowledgeBase)).all()
        }
        inherited_access: list[dict] = []
        personal_extra_access: list[dict] = []
    else:
        inherited_items = _ordered_knowledge_base_items(session, inherited_ids)
        inherited_access = []
        for item in inherited_items:
            sources: list[AccessSource] = []
            if item.id in company_ids:
                sources.append("company_wide")
            if item.id in department_ids:
                sources.append("department")
            inherited_access.append(
                {
                    "knowledge_base_id": item.id,
                    "knowledge_base_name": item.name,
                    "sources": sources,
                }
            )
        personal_extra_access = [
            {
                "knowledge_base_id": item.id,
                "knowledge_base_name": item.name,
            }
            for item in _ordered_knowledge_base_items(session, direct_ids - inherited_ids)
        ]

    return {
        "user_id": user.id,
        "is_admin": user.role == "admin",
        "inherited_access": inherited_access,
        "personal_extra_access": personal_extra_access,
        "department_access_count": len(department_ids),
        "company_wide_access_count": len(company_ids),
        "effective_access_count": len(effective_ids),
    }


def personal_extra_knowledge_base_ids(session: Session, user: User) -> set[str]:
    """Return the current direct rows that are not inherited by ``user``."""

    summary = user_knowledge_access_summary(session, user)
    return {
        item["knowledge_base_id"] for item in summary["personal_extra_access"]
    }


def personal_extra_user_ids_for_knowledge_base(
    session: Session,
    knowledge_base: KnowledgeBase,
) -> list[str]:
    """Return active ordinary users for whom a direct row is truly extra."""

    direct_user_ids = set(
        session.scalars(
            select(UserKnowledgeBaseAccess.user_id).where(
                UserKnowledgeBaseAccess.knowledge_base_id == knowledge_base.id
            )
        ).all()
    )
    users = session.scalars(
        select(User).where(
            User.id.in_(direct_user_ids),
            User.is_active.is_(True),
            User.role == "member",
        )
    ).all() if direct_user_ids else []
    result: list[str] = []
    for user in users:
        summary = user_knowledge_access_summary(session, user)
        if any(
            item["knowledge_base_id"] == knowledge_base.id
            for item in summary["personal_extra_access"]
        ):
            result.append(user.id)
    return result


def can_access_knowledge_base(
    session: Session,
    user: User | None,
    knowledge_base: KnowledgeBase,
) -> bool:
    """Return whether ``user`` can currently read this knowledge base."""

    if user is None or not user.is_active:
        return False
    if user.role == "admin":
        return True
    if knowledge_base.is_company_wide:
        return True
    if user.department_id is not None and session.scalar(
        select(DepartmentKnowledgeBase.id).where(
            DepartmentKnowledgeBase.department_id == user.department_id,
            DepartmentKnowledgeBase.knowledge_base_id == knowledge_base.id,
        )
    ) is not None:
        return True
    return session.scalar(
        select(UserKnowledgeBaseAccess.id).where(
            UserKnowledgeBaseAccess.user_id == user.id,
            UserKnowledgeBaseAccess.knowledge_base_id == knowledge_base.id,
        )
    ) is not None


def list_accessible_knowledge_bases(
    session: Session,
    user: User | None,
) -> list[KnowledgeBase]:
    """Return the current effective KB set with no legacy ACL fallback."""

    if user is None or not user.is_active:
        return []
    query = select(KnowledgeBase)
    if user.role != "admin":
        conditions = [KnowledgeBase.is_company_wide.is_(True)]
        if user.department_id is not None:
            conditions.append(
                KnowledgeBase.id.in_(
                    select(DepartmentKnowledgeBase.knowledge_base_id).where(
                        DepartmentKnowledgeBase.department_id == user.department_id
                    )
                )
            )
        conditions.append(
            KnowledgeBase.id.in_(
                select(UserKnowledgeBaseAccess.knowledge_base_id).where(
                    UserKnowledgeBaseAccess.user_id == user.id
                )
            )
        )
        query = query.where(or_(*conditions))
    return list(
        session.scalars(
            query.order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id)
        ).all()
    )


def require_knowledge_base_access(
    session: Session,
    user: User,
    knowledge_base_id: str,
    permission: Permission = "read",
) -> KnowledgeBase:
    """Re-authorize the current request against the single access model."""

    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    if permission != "read" and user.role != "admin":
        raise ProductError(
            "KNOWLEDGE_BASE_FORBIDDEN",
            "普通用户只能使用知识问答，不能修改知识资料。",
            status_code=403,
        )
    if not can_access_knowledge_base(session, user, knowledge_base):
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    return knowledge_base


def effective_access_ids(session: Session, user: User) -> list[str]:
    return [item.id for item in list_accessible_knowledge_bases(session, user)]


def access_summary_for_knowledge_base(session: Session, knowledge_base: KnowledgeBase) -> dict:
    """Return a read-only, business-facing reverse access summary."""

    departments = session.execute(
        select(DepartmentKnowledgeBase.department_id).where(
            DepartmentKnowledgeBase.knowledge_base_id == knowledge_base.id
        )
    ).scalars().all()
    direct_users = personal_extra_user_ids_for_knowledge_base(session, knowledge_base)

    # Count active ordinary users who receive access from any of the three
    # positive sources.  The admin role is deliberately not included: the
    # summary describes employee coverage, while admins inherently see all KBs.
    accessible_user_count = 0
    for user in session.scalars(
        select(User).where(User.is_active.is_(True), User.role == "member")
    ).all():
        if can_access_knowledge_base(session, user, knowledge_base):
            accessible_user_count += 1

    return {
        "knowledge_base_id": knowledge_base.id,
        "is_company_wide": bool(knowledge_base.is_company_wide),
        "department_ids": list(departments),
        "direct_user_ids": list(direct_users),
        "accessible_user_count": accessible_user_count,
    }
