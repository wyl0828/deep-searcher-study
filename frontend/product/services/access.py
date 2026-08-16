"""Workspace/group/KB access control (roadmap v0.5 + v0.5.1).

Authorization is always evaluated in real time at the start of each user-initiated
request: WorkspaceMember -> Group roles -> KnowledgeBase override. Already queued
async mutations run with the system identity and are not re-authorized in the
consumer. ACL data is DB-enforced (composite FKs + cascade) plus service checks
plus row locks so four invariants hold under concurrency:

1. Workspace owner never has KnowledgeBaseMember / GroupMember records.
2. Non-WorkspaceMember never has KnowledgeBaseMember / GroupMember records.
3. KnowledgeBaseMember.user and KnowledgeBase must belong to the same workspace.
4. ACL mutations lock the target WorkspaceMember row (FOR UPDATE) first.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from frontend.product.errors import ProductError
from frontend.product.models import (
    LEGACY_OWNER_ID,
    GroupMember,
    KnowledgeBase,
    KnowledgeBaseMember,
    MemberGroup,
    User,
    Workspace,
    WorkspaceMember,
)
from frontend.product.services.audit import audit_operation

Permission = Literal["read", "write", "admin"]

OWNER = "owner"
EDITOR = "editor"
VIEWER = "viewer"

PERMISSIONS: dict[str, set[str]] = {
    VIEWER: {"read"},
    EDITOR: {"read", "write"},
    OWNER: {"read", "write", "admin"},
}
ROLE_RANK: dict[str, int] = {VIEWER: 10, EDITOR: 20}

VALID_ROLES = frozenset({OWNER, EDITOR, VIEWER})
ADDABLE_ROLES = frozenset({EDITOR, VIEWER})


def _audit_member_snapshot(
    session: Session,
    workspace_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Snapshot a workspace membership row for the operation audit (before-state)."""
    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        return None
    return {"user_id": member.user_id, "role": member.role}


def _audit_kb_member_snapshot(
    session: Session,
    knowledge_base_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Snapshot a KB override row for the operation audit (before-state)."""
    member = session.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )
    if member is None:
        return None
    return {
        "knowledge_base_id": member.knowledge_base_id,
        "user_id": member.user_id,
        "role": member.role,
    }


def _lock_workspace_member(
    session: Session,
    workspace_id: str,
    user_id: str,
) -> WorkspaceMember | None:
    """Serialization point for every ACL mutation (invariant 4)."""
    return session.scalar(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
        .with_for_update()
    )


def user_workspace_role(
    session: Session,
    user_id: str,
    workspace_id: str,
) -> WorkspaceMember | None:
    """Return the current personal membership row, or None."""
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


def _group_roles(session: Session, workspace_id: str, user_id: str) -> list[str]:
    return list(
        session.scalars(
            select(MemberGroup.role)
            .join(GroupMember, GroupMember.group_id == MemberGroup.id)
            .where(
                GroupMember.workspace_id == workspace_id,
                GroupMember.user_id == user_id,
                MemberGroup.workspace_id == workspace_id,
            )
        ).all()
    )


def effective_workspace_role(
    session: Session,
    user_id: str,
    workspace_id: str,
) -> str | None:
    """Owner short-circuits; otherwise max(personal, group roles)."""
    member = user_workspace_role(session, user_id, workspace_id)
    if member is None:
        return None
    if member.role == OWNER:
        return OWNER
    rank = ROLE_RANK.get(member.role, ROLE_RANK[VIEWER])
    for role in _group_roles(session, workspace_id, user_id):
        rank = max(rank, ROLE_RANK.get(role, 0))
    return EDITOR if rank >= ROLE_RANK[EDITOR] else VIEWER


def effective_kb_role(
    session: Session,
    user_id: str,
    knowledge_base: KnowledgeBase,
) -> str | None:
    """KB override wins; otherwise effective workspace role. Owner is never
    overridden (the API refuses to create an override for the owner)."""
    override = session.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base.id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )
    if override is not None:
        return override.role
    return effective_workspace_role(session, user_id, knowledge_base.workspace_id)


def has_workspace_admin(session: Session, user_id: str, workspace_id: str) -> bool:
    member = user_workspace_role(session, user_id, workspace_id)
    return member is not None and member.role == OWNER


def require_workspace_access(
    session: Session,
    user: User,
    workspace_id: str,
    permission: Permission,
) -> WorkspaceMember:
    """Authorize workspace-level operations.

    admin only comes from the personal owner role; read/write use the effective
    workspace role (personal + groups).
    """
    member = _require_member(session, user, workspace_id)
    if permission == "admin":
        allowed = member.role == OWNER
    else:
        effective = effective_workspace_role(session, user.id, workspace_id)
        allowed = effective is not None and permission in PERMISSIONS[effective]
    if not allowed:
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

    The membership check is joined with the KB lookup so a missing KB and a KB
    in another workspace are indistinguishable (both 404). read/write use the
    effective KB role; admin only from the workspace owner.
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
    if permission == "admin":
        allowed = has_workspace_admin(session, user.id, knowledge_base.workspace_id)
    else:
        effective = effective_kb_role(session, user.id, knowledge_base)
        allowed = effective is not None and permission in PERMISSIONS[effective]
    if not allowed:
        raise ProductError(
            "KNOWLEDGE_BASE_FORBIDDEN",
            "没有足够的权限执行该操作。",
            status_code=403,
        )
    return knowledge_base


# ---- workspaces ----


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
                select(WorkspaceMember.id).where(WorkspaceMember.workspace_id == workspace.id)
            ).all()
        ),
        "knowledge_base_count": len(
            session.scalars(
                select(KnowledgeBase.id).where(KnowledgeBase.workspace_id == workspace.id)
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
    return [workspace_response(session, workspace, role=role) for workspace, role in rows]


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
            role=OWNER,
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


def _delete_user_acls(session: Session, workspace_id: str, user_id: str) -> None:
    """Remove KB overrides and group memberships for one user in one workspace."""
    session.execute(
        delete(KnowledgeBaseMember).where(
            KnowledgeBaseMember.workspace_id == workspace_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )
    session.execute(
        delete(GroupMember).where(
            GroupMember.workspace_id == workspace_id,
            GroupMember.user_id == user_id,
        )
    )


@audit_operation(
    biz_type="workspace_member",
    operation_type="ADD_WORKSPACE_MEMBER",
    action_desc=lambda hook: f"添加工作区成员 {hook.arg('user_id')} 为 {hook.arg('role')} 角色",
    biz_id="workspace",
)
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


@audit_operation(
    biz_type="workspace_member",
    operation_type="SET_MEMBER_ROLE",
    action_desc=lambda hook: f"修改工作区成员 {hook.arg('target_user_id')} 角色为 {hook.arg('role')}",
    biz_id="workspace",
    before=lambda hook: _audit_member_snapshot(
        hook.arg("session"), hook.arg("workspace").id, hook.arg("target_user_id")
    ),
)
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
    locked = _lock_workspace_member(session, workspace.id, target_user_id)
    if locked is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if locked.role == OWNER:
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能修改工作区 owner 的角色。",
            status_code=400,
        )
    locked.role = role
    session.commit()
    session.refresh(locked)
    return locked


@audit_operation(
    biz_type="workspace_member",
    operation_type="PROMOTE_MEMBER_TO_OWNER",
    action_desc=lambda hook: f"提升工作区成员 {hook.arg('target_user_id')} 为 owner",
    biz_id="workspace",
    before=lambda hook: _audit_member_snapshot(
        hook.arg("session"), hook.arg("workspace").id, hook.arg("target_user_id")
    ),
)
def promote_member_to_owner(
    session: Session,
    workspace: Workspace,
    target_user_id: str,
) -> WorkspaceMember:
    """Promote a member to owner after clearing its ACL records (invariant 1).

    Not exposed through the v0.5.1 API (ownership transfer is out of scope);
    kept as the canonical path future ownership transfer must use.
    """
    locked = _lock_workspace_member(session, workspace.id, target_user_id)
    if locked is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if locked.role != OWNER:
        _delete_user_acls(session, workspace.id, target_user_id)
    locked.role = OWNER
    session.commit()
    session.refresh(locked)
    return locked


@audit_operation(
    biz_type="workspace_member",
    operation_type="REMOVE_WORKSPACE_MEMBER",
    action_desc=lambda hook: f"移除工作区成员 {hook.arg('target_user_id')}",
    biz_id="workspace",
    before=lambda hook: _audit_member_snapshot(
        hook.arg("session"), hook.arg("workspace").id, hook.arg("target_user_id")
    ),
)
def remove_workspace_member(
    session: Session,
    workspace: Workspace,
    target_user_id: str,
) -> None:
    locked = _lock_workspace_member(session, workspace.id, target_user_id)
    if locked is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if locked.role == OWNER:
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能移除工作区 owner。",
            status_code=400,
        )
    _delete_user_acls(session, workspace.id, target_user_id)
    session.delete(locked)
    session.commit()


# ---- knowledge-base members (1.1) ----


def _require_kb_member(session: Session, knowledge_base_id: str, user_id: str):
    return session.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == knowledge_base_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )


def list_kb_members(
    session: Session,
    knowledge_base: KnowledgeBase,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(KnowledgeBaseMember, User)
        .join(User, User.id == KnowledgeBaseMember.user_id)
        .where(KnowledgeBaseMember.knowledge_base_id == knowledge_base.id)
        .order_by(KnowledgeBaseMember.created_at, KnowledgeBaseMember.id)
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


@audit_operation(
    biz_type="kb_member",
    operation_type="ADD_KB_MEMBER",
    action_desc=lambda hook: f"为知识库 {hook.arg('knowledge_base').name} 添加成员 {hook.arg('target_user_id')} 为 {hook.arg('role')} 角色",
    biz_id="knowledge_base",
)
def add_kb_member(
    session: Session,
    knowledge_base: KnowledgeBase,
    target_user_id: str,
    role: str,
) -> KnowledgeBaseMember:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "KB_ROLE_INVALID",
            "知识库覆盖角色只能为 editor 或 viewer。",
            status_code=400,
        )
    locked = _lock_workspace_member(
        session,
        knowledge_base.workspace_id,
        target_user_id,
    )
    if locked is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if locked.role == OWNER:
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能为工作区 owner 设置知识库覆盖。",
            status_code=400,
        )
    existing = _require_kb_member(session, knowledge_base.id, target_user_id)
    if existing is not None:
        raise ProductError(
            "KB_MEMBER_EXISTS",
            "该用户已有知识库覆盖。",
            status_code=409,
        )
    member = KnowledgeBaseMember(
        knowledge_base_id=knowledge_base.id,
        workspace_id=knowledge_base.workspace_id,
        user_id=target_user_id,
        role=role,
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


@audit_operation(
    biz_type="kb_member",
    operation_type="SET_KB_MEMBER_ROLE",
    action_desc=lambda hook: f"修改知识库 {hook.arg('knowledge_base').name} 成员 {hook.arg('target_user_id')} 角色为 {hook.arg('role')}",
    biz_id="knowledge_base",
    before=lambda hook: _audit_kb_member_snapshot(
        hook.arg("session"), hook.arg("knowledge_base").id, hook.arg("target_user_id")
    ),
)
def set_kb_member_role(
    session: Session,
    knowledge_base: KnowledgeBase,
    target_user_id: str,
    role: str,
) -> KnowledgeBaseMember:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "KB_ROLE_INVALID",
            "知识库覆盖角色只能为 editor 或 viewer。",
            status_code=400,
        )
    locked = _lock_workspace_member(
        session,
        knowledge_base.workspace_id,
        target_user_id,
    )
    if locked is None or locked.role == OWNER:
        raise ProductError(
            "KB_MEMBER_NOT_FOUND",
            "该用户不是可覆盖的工作区成员。",
            status_code=404,
        )
    member = _require_kb_member(session, knowledge_base.id, target_user_id)
    if member is None:
        raise ProductError(
            "KB_MEMBER_NOT_FOUND",
            "该用户没有知识库覆盖。",
            status_code=404,
        )
    member.role = role
    session.commit()
    session.refresh(member)
    return member


@audit_operation(
    biz_type="kb_member",
    operation_type="REMOVE_KB_MEMBER",
    action_desc=lambda hook: f"移除知识库 {hook.arg('knowledge_base').name} 成员 {hook.arg('target_user_id')}",
    biz_id="knowledge_base",
    before=lambda hook: _audit_kb_member_snapshot(
        hook.arg("session"), hook.arg("knowledge_base").id, hook.arg("target_user_id")
    ),
)
def remove_kb_member(
    session: Session,
    knowledge_base: KnowledgeBase,
    target_user_id: str,
) -> None:
    _lock_workspace_member(session, knowledge_base.workspace_id, target_user_id)
    member = _require_kb_member(session, knowledge_base.id, target_user_id)
    if member is None:
        raise ProductError(
            "KB_MEMBER_NOT_FOUND",
            "该用户没有知识库覆盖。",
            status_code=404,
        )
    session.delete(member)
    session.commit()


# ---- member groups (1.2) ----


def list_groups(session: Session, workspace_id: str) -> list[dict[str, Any]]:
    groups = session.scalars(
        select(MemberGroup)
        .where(MemberGroup.workspace_id == workspace_id)
        .order_by(MemberGroup.created_at, MemberGroup.id)
    ).all()
    return [
        {
            "id": group.id,
            "name": group.name,
            "role": group.role,
            "member_count": len(
                session.scalars(
                    select(GroupMember.id).where(GroupMember.group_id == group.id)
                ).all()
            ),
        }
        for group in groups
    ]


def get_group(session: Session, workspace_id: str, group_id: str) -> MemberGroup:
    group = session.scalar(
        select(MemberGroup).where(
            MemberGroup.id == group_id,
            MemberGroup.workspace_id == workspace_id,
        )
    )
    if group is None:
        raise ProductError(
            "GROUP_NOT_FOUND",
            "没有找到这个成员组。",
            status_code=404,
        )
    return group


@audit_operation(
    biz_type="member_group",
    operation_type="CREATE_GROUP",
    action_desc=lambda hook: f"创建成员组 {hook.arg('name')}",
    biz_id=lambda hook: getattr(hook.result, "id", None),
)
def create_group(
    session: Session,
    workspace: Workspace,
    *,
    name: str,
    role: str,
) -> MemberGroup:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "GROUP_ROLE_INVALID",
            "成员组角色只能为 editor 或 viewer。",
            status_code=400,
        )
    group = MemberGroup(
        workspace_id=workspace.id,
        name=name.strip(),
        role=role,
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def set_group_role(
    session: Session,
    group: MemberGroup,
    role: str,
) -> MemberGroup:
    if role not in ADDABLE_ROLES:
        raise ProductError(
            "GROUP_ROLE_INVALID",
            "成员组角色只能为 editor 或 viewer。",
            status_code=400,
        )
    group.role = role
    session.commit()
    session.refresh(group)
    return group


@audit_operation(
    biz_type="member_group",
    operation_type="DELETE_GROUP",
    action_desc=lambda hook: f"删除成员组 {hook.arg('group').name}",
    biz_id="group",
    before=lambda hook: {
        "group_id": hook.arg("group").id,
        "name": hook.arg("group").name,
        "role": hook.arg("group").role,
    },
)
def delete_group(session: Session, group: MemberGroup) -> None:
    session.delete(group)
    session.commit()


def list_group_members(session: Session, group: MemberGroup) -> list[dict[str, Any]]:
    rows = session.execute(
        select(GroupMember, User)
        .join(User, User.id == GroupMember.user_id)
        .where(GroupMember.group_id == group.id)
        .order_by(GroupMember.created_at, GroupMember.id)
    ).all()
    return [
        {
            "user_id": member.user_id,
            "username": user.username,
            "display_name": user.display_name,
        }
        for member, user in rows
    ]


@audit_operation(
    biz_type="member_group",
    operation_type="ADD_GROUP_MEMBER",
    action_desc=lambda hook: f"将成员 {hook.arg('target_user_id')} 加入成员组 {hook.arg('group').name}",
    biz_id="group",
)
def add_group_member(
    session: Session,
    group: MemberGroup,
    target_user_id: str,
) -> GroupMember:
    locked = _lock_workspace_member(session, group.workspace_id, target_user_id)
    if locked is None:
        raise ProductError(
            "WORKSPACE_MEMBER_NOT_FOUND",
            "该用户不是工作区成员。",
            status_code=404,
        )
    if locked.role == OWNER:
        raise ProductError(
            "OWNER_ROLE_FIXED",
            "不能把工作区 owner 加入成员组。",
            status_code=400,
        )
    existing = session.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.user_id == target_user_id,
        )
    )
    if existing is not None:
        raise ProductError(
            "GROUP_MEMBER_EXISTS",
            "该用户已在成员组中。",
            status_code=409,
        )
    member = GroupMember(
        group_id=group.id,
        workspace_id=group.workspace_id,
        user_id=target_user_id,
    )
    session.add(member)
    session.commit()
    session.refresh(member)
    return member


def remove_group_member(
    session: Session,
    group: MemberGroup,
    target_user_id: str,
) -> None:
    _lock_workspace_member(session, group.workspace_id, target_user_id)
    member = session.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.user_id == target_user_id,
        )
    )
    if member is None:
        raise ProductError(
            "GROUP_MEMBER_NOT_FOUND",
            "该用户不在成员组中。",
            status_code=404,
        )
    session.delete(member)
    session.commit()


# ---- migration/ensure helpers ----


def ensure_personal_workspaces(session: Session) -> int:
    """Idempotently attach knowledge bases to personal/legacy workspaces."""
    created = 0
    users = session.scalars(select(User).where(User.id != LEGACY_OWNER_ID)).all()
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
                    role=OWNER,
                )
            )
            created += 1
        personal[user.id] = workspace

    legacy_workspace = session.scalar(select(Workspace).where(Workspace.name == "__legacy__"))
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
                role=OWNER,
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
