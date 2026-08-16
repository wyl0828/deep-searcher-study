"""Workspace-level access control for the product workspace (roadmap v0.5).

Authorization is always evaluated against the current WorkspaceMember state at
the start of each request. A conversation created in the past is never a
permission credential: every request that reads a knowledge base re-checks
membership before touching Cache, Retriever or collection names. Permissions
are expressed as sets (not string comparison): viewer = read, editor = read +
write, owner = read + write + admin.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from frontend.product.errors import ProductError
from frontend.product.models import (
    LEGACY_OWNER_ID,
    KnowledgeBase,
    User,
    Workspace,
    WorkspaceMember,
)

Permission = Literal["read", "write", "admin"]

PERMISSIONS: dict[str, set[str]] = {
    "viewer": {"read"},
    "editor": {"read", "write"},
    "owner": {"read", "write", "admin"},
}

VALID_ROLES = frozenset({"owner", "editor", "viewer"})
ADDABLE_ROLES = frozenset({"editor", "viewer"})


def user_workspace_role(
    session: Session,
    user_id: str,
    workspace_id: str,
) -> WorkspaceMember | None:
    """Return the member row for the current state, or None."""
    return session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )


def get_workspace(session: Session, workspace_id: str) -> Workspace | None:
    return session.get(Workspace, workspace_id)


def _require_member(
    session: Session,
    user: User,
    workspace_id: str,
) -> WorkspaceMember:
    member = user_workspace_role(session, user.id, workspace_id)
    if member is None:
        raise ProductError(
            "WORKSPACE_NOT_FOUND",
            "没有找到这个工作区。",
            status_code=404,
        )
    return member


def require_workspace_access(
    session: Session,
    user: User,
    workspace_id: str,
    permission: Permission,
) -> WorkspaceMember:
    """Authorize workspace-level operations (create KB, member management)."""
    member = _require_member(session, user, workspace_id)
    if permission not in PERMISSIONS[member.role]:
        raise ProductError(
            "WORKSPACE_FORBIDDEN",
            "没有足够的权限执行该操作。",
            status_code=403,
        )
    return member


def require_accessible_knowledge_base(
    session: Session,
    user: User,
    knowledge_base_id: str,
    permission: Permission = "read",
) -> KnowledgeBase:
    """Realtime authorization for any knowledge-base access.

    The membership check is joined with the KB lookup so that a missing KB and
    a KB in another workspace are indistinguishable (both 404). Members with an
    insufficient role get a 403.
    """

    knowledge_base = session.scalar(
        select(KnowledgeBase)
        .join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == KnowledgeBase.workspace_id,
        )
        .where(
            KnowledgeBase.id == knowledge_base_id,
            WorkspaceMember.user_id == user.id,
        )
    )
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    member = user_workspace_role(session, user.id, knowledge_base.workspace_id)
    if member is None or permission not in PERMISSIONS[member.role]:
        raise ProductError(
            "KNOWLEDGE_BASE_FORBIDDEN",
            "没有足够的权限执行该操作。",
            status_code=403,
        )
    return knowledge_base


def workspace_response(
    session: Session,
    workspace: Workspace,
    *,
    role: str,
) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "description": workspace.description,
        "owner_id": workspace.owner_id,
        "role": role,
        "member_count": len(
            session.scalars(
                select(WorkspaceMember.id).where(
                    WorkspaceMember.workspace_id == workspace.id
                )
            ).all()
        ),
        "knowledge_base_count": len(
            session.scalars(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.workspace_id == workspace.id
                )
            ).all()
        ),
        "created_at": workspace.created_at,
    }


def list_workspaces(session: Session, user_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user_id)
        .order_by(Workspace.created_at, Workspace.id)
    ).all()
    return [
        workspace_response(session, workspace, role=role)
        for workspace, role in rows
    ]


def create_workspace(
    session: Session,
    *,
    name: str,
    description: str,
    owner_id: str,
) -> Workspace:
    workspace = Workspace(
        name=name.strip(),
        description=description.strip(),
        owner_id=owner_id,
    )
    session.add(workspace)
    session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=owner_id,
            role="owner",
        )
    )
    session.commit()
    session.refresh(workspace)
    return workspace


def list_workspace_members(
    session: Session,
    workspace_id: str,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at, WorkspaceMember.id)
    ).all()
    return [
        {
            "user_id": member.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "role": member.role,
        }
        for member, user in rows
    ]


def add_workspace_member(
    session: Session,
    workspace: Workspace,
    user_id: str,
    role: str,
) -> WorkspaceMember:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "WORKSPACE_ROLE_INVALID",
            "只能添加 editor 或 viewer 成员。",
            status_code=400,
        )
    existing = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if existing is not None:
        raise ProductError(
            "WORKSPACE_MEMBER_EXISTS",
            "该用户已经是工作区成员。",
            status_code=409,
        )
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user_id,
        role=role,
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


def set_member_role(
    session: Session,
    workspace: Workspace,
    target_user_id: str,
    role: str,
) -> WorkspaceMember:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "WORKSPACE_ROLE_INVALID",
            "角色只能为 editor 或 viewer。",
            status_code=400,
        )
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == target_user_id,
        )
    )
    if member is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if member.role == "owner":
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能修改工作区 owner 的角色。",
            status_code=400,
        )
    member.role = role
    session.commit()
    session.refresh(member)
    return member


def remove_workspace_member(
    session: Session,
    workspace: Workspace,
    target_user_id: str,
) -> None:
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.user_id == target_user_id,
        )
    )
    if member is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if member.role == "owner":
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能移除工作区 owner。",
            status_code=400,
        )
    session.delete(member)
    session.commit()


def ensure_personal_workspaces(session: Session) -> int:
    """Idempotently attach knowledge bases to personal/legacy workspaces.

    Used by the SQLite local upgrade path. Creates a personal workspace for
    every user that does not already own one, then attaches any knowledge base
    without a workspace to its owner's personal workspace (legacy data goes to
    the legacy workspace). Returns the number of workspaces created.
    """

    created = 0
    users = session.scalars(
        select(User).where(User.id != LEGACY_OWNER_ID)
    ).all()
    personal: dict[str, Workspace] = {}
    for user in users:
        workspace = session.scalar(
            select(Workspace).where(
                Workspace.owner_id == user.id,
                Workspace.name == user.username,
            )
        )
        if workspace is None:
            workspace = Workspace(
                name=user.username,
                description="个人工作区",
                owner_id=user.id,
            )
            session.add(workspace)
            session.flush()
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                )
            )
            created += 1
        personal[user.id] = workspace

    legacy_workspace = session.scalar(
        select(Workspace).where(Workspace.name == "__legacy__")
    )
    if legacy_workspace is None:
        admin = session.scalars(
            select(User)
            .where(
                User.id != LEGACY_OWNER_ID,
                User.role == "admin",
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .limit(1)
        ).first()
        legacy_owner_id = admin.id if admin is not None else LEGACY_OWNER_ID
        legacy_workspace = Workspace(
            name="__legacy__",
            description="待接管的旧数据",
            owner_id=legacy_owner_id,
        )
        session.add(legacy_workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=legacy_workspace.id,
                user_id=legacy_owner_id,
                role="owner",
            )
        )
        created += 1

    for knowledge_base in session.scalars(
        select(KnowledgeBase).where(KnowledgeBase.workspace_id.is_(None))
    ).all():
        if knowledge_base.owner_id == LEGACY_OWNER_ID:
            knowledge_base.workspace_id = legacy_workspace.id
        else:
            workspace = personal.get(knowledge_base.owner_id)
            knowledge_base.workspace_id = (
                workspace.id if workspace is not None else legacy_workspace.id
            )

    session.commit()
    return created
