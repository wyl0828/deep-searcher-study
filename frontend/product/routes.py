from __future__ import annotations

import json
from datetime import date, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, or_, select
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
    AnswerRun,
    ConnectorSync,
    ConnectorSyncRun,
    Conversation,
    Department,
    DepartmentKnowledgeBase,
    Document,
    GroupMember,
    IngestJob,
    KnowledgeBase,
    KnowledgeBaseMember,
    KnowledgeHealthSnapshot,
    MemberGroup,
    Message,
    MessageFeedback,
    User,
    UserKnowledgeBaseAccess,
    UserSession,
    Workspace,
    WorkspaceMember,
    utcnow,
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
    ConnectorSyncCreate,
    ConversationCreate,
    DepartmentCreate,
    DepartmentUpdate,
    DocumentGovernanceUpdate,
    DocumentTemporalUpdate,
    GroupMemberAdd,
    HealthActionsRun,
    KnowledgeBaseCreate,
    KnowledgeBaseMemberAdd,
    KnowledgeBaseMemberRoleUpdate,
    MemberGroupCreate,
    MemberGroupRoleUpdate,
    MessageCreate,
    MessageFeedbackCreate,
    MessageResponse,
    UserCreate,
    UserUpdate,
    KnowledgeAccessReplace,
    WorkspaceCreate,
    WorkspaceMemberAdd,
    WorkspaceMemberRoleUpdate,
)
from frontend.product.services import documents as document_service
from frontend.product.services.access import (
    add_group_member,
    add_kb_member,
    add_workspace_member,
    create_group,
    create_workspace,
    delete_group,
    effective_kb_role,
    get_group,
    get_workspace,
    list_group_members,
    list_groups,
    list_kb_members,
    list_workspace_members,
    list_workspaces,
    remove_group_member,
    remove_kb_member,
    remove_workspace_member,
    require_workspace_access,
    set_group_role,
    set_kb_member_role,
    set_member_role,
    workspace_response,
)
from frontend.product.services.authorization import (
    access_summary_for_knowledge_base,
    list_accessible_knowledge_bases,
    personal_extra_knowledge_base_ids,
    require_knowledge_base_access as require_accessible_knowledge_base,
    user_knowledge_access_summary,
)
from frontend.product.services.audit import page_audit_logs, record_operation
from frontend.product.services.connector_sync import (
    compute_next_run,
    validate_cron,
)
from frontend.product.services.conversations import stream_message_events, submit_message
from frontend.product.services.query_scope import (
    resolve_conversation_scope,
    resolve_user_query_scope,
)
from frontend.product.services.dashboard import (
    overview as dashboard_overview,
)
from frontend.product.services.dashboard import (
    trends as dashboard_trends,
)
from frontend.product.services.documents import (
    create_document_from_upload,
    delete_document,
    retry_document,
    update_document_governance_metadata,
    update_document_temporal_metadata,
)
from frontend.product.services.feedback import (
    cancel_message_feedback,
    get_feedback_map,
    submit_message_feedback,
)
from frontend.product.services.knowledge_bases import (
    delete_knowledge_base,
    reindex_knowledge_base,
)
from frontend.product.services.knowledge_health import (
    assemble_health_payload,
    create_health_snapshot,
    get_health_level,
    health_snapshot_response,
    health_trend_payload,
    latest_health_snapshot,
    list_health_snapshots,
    run_health_actions,
)

router = APIRouter(prefix="/api")


def _accessible_document(
    session: Session,
    user: User,
    document_id: str,
    permission: str = "read",
) -> Document:
    document = session.scalar(
        select(Document)
        .join(Document.knowledge_base)
        .where(
            Document.id == document_id,
        )
    )
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
    require_accessible_knowledge_base(
        session,
        user,
        document.knowledge_base_id,
        permission,
    )
    return document


def _admin_workspace(session: Session, workspace_id: str) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise ProductError("WORKSPACE_NOT_FOUND", "没有找到这个工作区。", status_code=404)
    return workspace


def _admin_knowledge_base(session: Session, knowledge_base_id: str) -> KnowledgeBase:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            "没有找到这个知识库。",
            status_code=404,
        )
    return knowledge_base


def _accessible_ingest_job(
    session: Session,
    user: User,
    job_id: str,
) -> IngestJob:
    job = session.scalar(select(IngestJob).join(IngestJob.document).where(IngestJob.id == job_id))
    if job is None:
        raise ProductError(
            "INGEST_JOB_NOT_FOUND",
            "没有找到这个文档处理任务。",
            status_code=404,
        )
    require_accessible_knowledge_base(
        session,
        user,
        job.document.knowledge_base_id,
        "read",
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


@router.get("/me/query-scope")
def current_query_scope(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    """Return the live, user-facing knowledge availability contract."""
    return resolve_user_query_scope(session, user).payload


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


def _department_response(session: Session, department: Department) -> dict:
    return {
        "id": department.id,
        "name": department.name,
        "user_count": int(
            session.scalar(
                select(func.count(User.id)).where(User.department_id == department.id)
            )
            or 0
        ),
        "knowledge_base_count": int(
            session.scalar(
                select(func.count(DepartmentKnowledgeBase.id)).where(
                    DepartmentKnowledgeBase.department_id == department.id
                )
            )
            or 0
        ),
        "created_at": department.created_at,
        "updated_at": department.updated_at,
    }


def _admin_user_response(session: Session, user: User) -> dict:
    item = user_response(user)
    item["accessible_knowledge_base_count"] = len(list_accessible_knowledge_bases(session, user))
    item["last_activity_at"] = session.scalar(
        select(func.max(UserSession.last_seen_at)).where(UserSession.user_id == user.id)
    )
    return item


@router.get("/admin/departments")
def list_departments(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    departments = session.scalars(
        select(Department).order_by(Department.name, Department.id)
    ).all()
    unassigned_count = int(
        session.scalar(
            select(func.count(User.id)).where(
                User.id != LEGACY_OWNER_ID,
                User.department_id.is_(None),
            )
        )
        or 0
    )
    return {
        "items": [_department_response(session, department) for department in departments],
        "unassigned": {
            "id": None,
            "name": "未分部门",
            "user_count": unassigned_count,
            "knowledge_base_count": 0,
        },
    }


@router.post("/admin/departments", status_code=201)
def add_department(
    payload: DepartmentCreate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    name = payload.name.strip()
    if session.scalar(select(Department.id).where(Department.name == name)) is not None:
        raise ProductError("DEPARTMENT_EXISTS", "这个部门已经存在。", status_code=409)
    department = Department(name=name)
    session.add(department)
    session.flush()
    item = _department_response(session, department)
    record_operation(
        session,
        biz_type="department",
        biz_id=department.id,
        operation_type="CREATE_DEPARTMENT",
        action_desc=f"创建部门 {department.name}",
        after=item,
    )
    return {"department": item}


@router.patch("/admin/departments/{department_id}")
def rename_department(
    department_id: str,
    payload: DepartmentUpdate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    department = session.get(Department, department_id)
    if department is None:
        raise ProductError("DEPARTMENT_NOT_FOUND", "没有找到这个部门。", status_code=404)
    name = payload.name.strip()
    duplicate = session.scalar(
        select(Department.id).where(Department.name == name, Department.id != department.id)
    )
    if duplicate is not None:
        raise ProductError("DEPARTMENT_EXISTS", "这个部门已经存在。", status_code=409)
    before = _department_response(session, department)
    department.name = name
    session.flush()
    after = _department_response(session, department)
    record_operation(
        session,
        biz_type="department",
        biz_id=department.id,
        operation_type="RENAME_DEPARTMENT",
        action_desc=f"重命名部门为 {department.name}",
        before=before,
        after=after,
    )
    return {"department": after}


def _department_users_query(department_id: str | None):
    query = select(User).where(User.id != LEGACY_OWNER_ID)
    if department_id is None:
        return query.where(User.department_id.is_(None))
    return query.where(User.department_id == department_id)


@router.get("/admin/departments/unassigned/users")
def list_unassigned_department_users(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    users = session.scalars(
        _department_users_query(None).order_by(User.created_at, User.username)
    ).all()
    return {"items": [_admin_user_response(session, user) for user in users]}


@router.get("/admin/departments/{department_id}/users")
def list_department_users(
    department_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    department = session.get(Department, department_id)
    if department is None:
        raise ProductError("DEPARTMENT_NOT_FOUND", "没有找到这个部门。", status_code=404)
    users = session.scalars(
        _department_users_query(department_id).order_by(User.created_at, User.username)
    ).all()
    return {
        "department": _department_response(session, department),
        "items": [_admin_user_response(session, user) for user in users],
    }


@router.get("/admin/users")
def list_users(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
    q: str | None = Query(None),
    department_id: str | None = Query(None),
    role: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    query = select(User).outerjoin(Department).where(User.id != LEGACY_OWNER_ID)
    if q:
        needle = f"%{q.strip()}%"
        query = query.where(
            or_(
                User.username.ilike(needle),
                User.display_name.ilike(needle),
                Department.name.ilike(needle),
            )
        )
    if department_id == "unassigned":
        query = query.where(User.department_id.is_(None))
    elif department_id:
        query = query.where(User.department_id == department_id)
    if role in {"admin", "member"}:
        query = query.where(User.role == role)
    if status == "active":
        query = query.where(User.is_active.is_(True))
    elif status == "inactive":
        query = query.where(User.is_active.is_(False))
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    users = session.scalars(
        query.order_by(User.created_at, User.username)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_admin_user_response(session, user) for user in users],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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
            department_id=payload.department_id,
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
    record_operation(
        session,
        biz_type="user",
        biz_id=user.id,
        operation_type="CREATE_USER",
        action_desc=f"创建用户 {user.username}（{user.display_name}）",
        before=None,
        after=user_response(user),
    )
    return {"user": user_response(user)}


@router.patch("/admin/users/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdate,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(User, user_id)
    if user is None or user.id == LEGACY_OWNER_ID:
        raise ProductError("USER_NOT_FOUND", "没有找到这个用户。", status_code=404)
    before = _admin_user_response(session, user)
    next_role = payload.role if payload.role is not None else user.role
    next_active = payload.is_active if payload.is_active is not None else user.is_active
    if user.role == "admin" and (next_role != "admin" or not next_active):
        active_admin_count = int(
            session.scalar(
                select(func.count(User.id)).where(
                    User.role == "admin", User.is_active.is_(True)
                )
            )
            or 0
        )
        if active_admin_count <= 1:
            raise ProductError(
                "LAST_ADMIN_REQUIRED",
                "不能停用或降级最后一个管理员。",
                status_code=409,
            )
    if payload.department_id is not None and session.get(Department, payload.department_id) is None:
        raise ProductError("DEPARTMENT_NOT_FOUND", "没有找到这个部门。", status_code=404)
    old_department_id = user.department_id
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.role is not None:
        user.role = payload.role
    if payload.department_id is not None or payload.department_id is None and "department_id" in payload.model_fields_set:
        user.department_id = payload.department_id
    if payload.is_active is not None:
        user.is_active = payload.is_active
    session.flush()
    after = _admin_user_response(session, user)
    operation_type = "CHANGE_USER_DEPARTMENT" if old_department_id != user.department_id else "UPDATE_USER"
    action = (
        f"调整用户 {user.username} 的部门"
        if operation_type == "CHANGE_USER_DEPARTMENT"
        else f"修改用户 {user.username}"
    )
    record_operation(
        session,
        biz_type="user",
        biz_id=user.id,
        operation_type=operation_type,
        action_desc=action,
        before=before,
        after=after,
    )
    return {"user": after}


def _validate_knowledge_base_ids(session: Session, ids: list[str]) -> list[KnowledgeBase]:
    unique_ids = list(dict.fromkeys(ids))
    items = session.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(unique_ids))).all()
    if len(items) != len(unique_ids):
        found = {item.id for item in items}
        missing = next(item for item in unique_ids if item not in found)
        raise ProductError(
            "KNOWLEDGE_BASE_NOT_FOUND",
            f"没有找到知识库 {missing}。",
            status_code=404,
        )
    return items


def _knowledge_base_snapshots(
    session: Session,
    knowledge_base_ids: list[str] | set[str],
) -> list[dict[str, str]]:
    """Build the stable, name-bearing permission audit snapshot."""

    unique_ids = list(dict.fromkeys(knowledge_base_ids))
    if not unique_ids:
        return []
    items = session.scalars(
        select(KnowledgeBase).where(KnowledgeBase.id.in_(unique_ids))
    ).all()
    names = {item.id: item.name for item in items}
    return [
        {
            "knowledge_base_id": knowledge_base_id,
            "knowledge_base_name": names[knowledge_base_id],
        }
        for knowledge_base_id in sorted(unique_ids)
        if knowledge_base_id in names
    ]


@router.get("/admin/knowledge-access/company-wide")
def get_company_wide_access(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    ids = session.scalars(
        select(KnowledgeBase.id).where(KnowledgeBase.is_company_wide.is_(True))
    ).all()
    return {"knowledge_base_ids": list(ids)}


@router.put("/admin/knowledge-access/company-wide")
def set_company_wide_access(
    payload: KnowledgeAccessReplace,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    items = _validate_knowledge_base_ids(session, payload.knowledge_base_ids)
    selected = {item.id for item in items}
    before = list(
        session.scalars(select(KnowledgeBase.id).where(KnowledgeBase.is_company_wide.is_(True))).all()
    )
    all_items = session.scalars(select(KnowledgeBase)).all()
    for knowledge_base in all_items:
        knowledge_base.is_company_wide = knowledge_base.id in selected
    session.flush()
    after = sorted(selected)
    record_operation(
        session,
        biz_type="knowledge_access",
        biz_id="company-wide",
        operation_type="SET_COMPANY_WIDE_ACCESS",
        action_desc="修改全员知识访问",
        before=_knowledge_base_snapshots(session, before),
        after=_knowledge_base_snapshots(session, after),
    )
    return {"knowledge_base_ids": after}


@router.get("/admin/departments/{department_id}/knowledge-access")
def get_department_knowledge_access(
    department_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    if session.get(Department, department_id) is None:
        raise ProductError("DEPARTMENT_NOT_FOUND", "没有找到这个部门。", status_code=404)
    ids = session.scalars(
        select(DepartmentKnowledgeBase.knowledge_base_id).where(
            DepartmentKnowledgeBase.department_id == department_id
        )
    ).all()
    return {"department_id": department_id, "knowledge_base_ids": list(ids)}


@router.put("/admin/departments/{department_id}/knowledge-access")
def set_department_knowledge_access(
    department_id: str,
    payload: KnowledgeAccessReplace,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    department = session.get(Department, department_id)
    if department is None:
        raise ProductError("DEPARTMENT_NOT_FOUND", "没有找到这个部门。", status_code=404)
    items = _validate_knowledge_base_ids(session, payload.knowledge_base_ids)
    selected = {item.id for item in items}
    before = list(
        session.scalars(
            select(DepartmentKnowledgeBase.knowledge_base_id).where(
                DepartmentKnowledgeBase.department_id == department_id
            )
        ).all()
    )
    session.execute(
        delete(DepartmentKnowledgeBase).where(
            DepartmentKnowledgeBase.department_id == department_id
        )
    )
    for knowledge_base_id in selected:
        session.add(
            DepartmentKnowledgeBase(
                department_id=department_id,
                knowledge_base_id=knowledge_base_id,
            )
        )
    session.flush()
    record_operation(
        session,
        biz_type="department",
        biz_id=department_id,
        operation_type="SET_DEPARTMENT_KB_ACCESS",
        action_desc=f"修改{department.name}知识访问",
        before=_knowledge_base_snapshots(session, before),
        after=_knowledge_base_snapshots(session, sorted(selected)),
    )
    return {"department_id": department_id, "knowledge_base_ids": sorted(selected)}


@router.get("/admin/users/{user_id}/knowledge-access")
def get_user_knowledge_access(
    user_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(User, user_id)
    if user is None or user.id == LEGACY_OWNER_ID:
        raise ProductError("USER_NOT_FOUND", "没有找到这个用户。", status_code=404)
    return user_knowledge_access_summary(session, user)


@router.put("/admin/users/{user_id}/knowledge-access")
def set_user_knowledge_access(
    user_id: str,
    payload: KnowledgeAccessReplace,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    user = session.get(User, user_id)
    if user is None or user.id == LEGACY_OWNER_ID:
        raise ProductError("USER_NOT_FOUND", "没有找到这个用户。", status_code=404)
    if user.role == "admin":
        raise ProductError(
            "ADMIN_ACCESS_NOT_EDITABLE",
            "管理员拥有全量管理访问，不配置个人额外知识。",
            status_code=409,
        )
    items = _validate_knowledge_base_ids(session, payload.knowledge_base_ids)
    requested_personal = {item.id for item in items}
    inherited_ids = {
        item["knowledge_base_id"]
        for item in user_knowledge_access_summary(session, user)["inherited_access"]
    }
    selected = requested_personal - inherited_ids
    before = personal_extra_knowledge_base_ids(session, user)
    session.execute(delete(UserKnowledgeBaseAccess).where(UserKnowledgeBaseAccess.user_id == user_id))
    for knowledge_base_id in selected:
        session.add(
            UserKnowledgeBaseAccess(user_id=user_id, knowledge_base_id=knowledge_base_id)
        )
    session.flush()
    record_operation(
        session,
        biz_type="user",
        biz_id=user_id,
        operation_type="SET_USER_KB_ACCESS",
        action_desc=f"修改用户 {user.username} 额外知识访问",
        before=_knowledge_base_snapshots(session, before),
        after=_knowledge_base_snapshots(session, sorted(selected)),
    )
    return {
        "user_id": user_id,
        "personal_extra_access": [
            item
            for item in user_knowledge_access_summary(session, user)["personal_extra_access"]
            if item["knowledge_base_id"] in selected
        ],
    }


@router.get("/admin/knowledge-bases")
def admin_knowledge_bases(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Return the admin-visible knowledge inventory, independent of ACL membership."""
    items = list_knowledge_bases(session)
    snapshots = session.scalars(
        select(KnowledgeHealthSnapshot).order_by(
            KnowledgeHealthSnapshot.created_at.desc(),
            KnowledgeHealthSnapshot.id.desc(),
        )
    ).all()
    latest_by_knowledge_base: dict[str, KnowledgeHealthSnapshot] = {}
    for snapshot in snapshots:
        latest_by_knowledge_base.setdefault(snapshot.knowledge_base_id, snapshot)

    for item in items:
        snapshot = latest_by_knowledge_base.get(item["id"])
        item["health_status"] = (
            None
            if snapshot is None
            else "partial"
            if snapshot.status == "partial"
            else get_health_level(snapshot.overall_score)
        )
        item["health_score"] = snapshot.overall_score if snapshot is not None else None
        item["health_updated_at"] = snapshot.created_at if snapshot is not None else None
    return {"items": items}


@router.get("/admin/knowledge-bases/{knowledge_base_id}/documents")
def admin_knowledge_base_documents(
    knowledge_base_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError("KNOWLEDGE_BASE_NOT_FOUND", "没有找到这个知识库。", status_code=404)
    items = session.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc())
    ).all()
    return {"items": [admin_document_response(session, document) for document in items]}


@router.get("/admin/documents/{document_id}")
def admin_document_detail(
    document_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
    return admin_document_response(session, document)


@router.get("/admin/knowledge-bases/{knowledge_base_id}/permissions")
def admin_knowledge_base_permissions(
    knowledge_base_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Return a read-only reverse view of the single access model."""
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    summary = access_summary_for_knowledge_base(session, knowledge_base)
    departments = session.scalars(
        select(Department).where(Department.id.in_(summary["department_ids"]))
    ).all()
    users = session.scalars(
        select(User).where(User.id.in_(summary["direct_user_ids"]))
    ).all()
    return {
        "knowledge_base_id": knowledge_base.id,
        "is_company_wide": summary["is_company_wide"],
        "departments": [
            {"id": department.id, "name": department.name} for department in departments
        ],
        "direct_users": [
            {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
            }
            for user in users
        ],
        "accessible_user_count": summary["accessible_user_count"],
        # Keep the old endpoint URL as a compatibility alias, but its payload
        # no longer exposes Workspace/Group/ACL sources.
    }


@router.get("/admin/knowledge-bases/{knowledge_base_id}/access-summary")
def admin_knowledge_base_access_summary(
    knowledge_base_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    summary = access_summary_for_knowledge_base(session, knowledge_base)
    departments = session.scalars(
        select(Department).where(Department.id.in_(summary["department_ids"]))
    ).all()
    users = session.scalars(
        select(User).where(User.id.in_(summary["direct_user_ids"]))
    ).all()
    return {
        "is_company_wide": summary["is_company_wide"],
        "departments": [
            {"id": department.id, "name": department.name} for department in departments
        ],
        "direct_users": [
            {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
            }
            for user in users
        ],
        "accessible_user_count": summary["accessible_user_count"],
    }


@router.get("/admin/knowledge-bases/{knowledge_base_id}/health")
def admin_knowledge_base_health(
    knowledge_base_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None:
        raise ProductError("KNOWLEDGE_BASE_NOT_FOUND", "没有找到这个知识库。", status_code=404)
    latest = latest_health_snapshot(session, knowledge_base.id)
    return {
        "snapshot": health_snapshot_response(latest) if latest is not None else None,
        "current": assemble_health_payload(session, knowledge_base),
    }


@router.get("/admin/ingest-jobs")
def admin_ingest_jobs(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    knowledge_base_id: str | None = Query(None),
) -> dict:
    """Admin-wide ingestion queue view; the UI never infers jobs from KPI zeros."""
    conditions = []
    if status:
        conditions.append(IngestJob.status == status)
    if knowledge_base_id:
        conditions.append(Document.knowledge_base_id == knowledge_base_id)

    count_query = (
        select(func.count(IngestJob.id))
        .select_from(IngestJob)
        .join(Document, Document.id == IngestJob.document_id)
    )
    if conditions:
        count_query = count_query.where(*conditions)
    total = int(session.scalar(count_query) or 0)

    query = (
        select(IngestJob, Document, KnowledgeBase)
        .join(Document, Document.id == IngestJob.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .order_by(IngestJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if conditions:
        query = query.where(*conditions)

    items = []
    for job, document, knowledge_base in session.execute(query).all():
        item = ingest_job_response(job)
        item.update(
            {
                "document": document_response(document),
                "knowledge_base": {
                    "id": knowledge_base.id,
                    "name": knowledge_base.name,
                },
            }
        )
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("/admin/documents/{document_id}/retry", status_code=202)
def admin_retry_document(
    document_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
    job = retry_document(session, document)
    return {"document": document_response(document), "job": ingest_job_response(job)}


@router.get("/admin/runs")
def admin_runs(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    knowledge_base_id: str | None = Query(None),
    status: str | None = Query(None),
    trust_status: str | None = Query(None),
    risk_level: str | None = Query(None),
    feedback: str | None = Query(None),
) -> dict:
    """Admin read model for persisted answer evidence and trust decisions.

    The message table does not persist the transient routing or stage stream,
    so this endpoint deliberately exposes only durable message fields.
    """
    conditions = [Message.role == "assistant"]
    if status:
        conditions.append(Message.status == status)
    if trust_status:
        conditions.append(Message.trust_status == trust_status)
    if risk_level:
        conditions.append(Message.risk_level == risk_level)
    if feedback == "negative":
        conditions.append(
            Message.id.in_(
                select(MessageFeedback.message_id).where(
                    MessageFeedback.cancelled.is_(False),
                    MessageFeedback.vote == -1,
                )
            )
        )
    elif feedback == "positive":
        conditions.append(
            Message.id.in_(
                select(MessageFeedback.message_id).where(
                    MessageFeedback.cancelled.is_(False),
                    MessageFeedback.vote == 1,
                )
            )
        )
    elif feedback == "commented":
        conditions.append(
            Message.id.in_(
                select(MessageFeedback.message_id).where(
                    MessageFeedback.cancelled.is_(False),
                    MessageFeedback.comment.is_not(None),
                    MessageFeedback.comment != "",
                )
            )
        )

    # Auto-scope conversations deliberately keep ``knowledge_base_id`` null.
    # A SQL filter on the legacy conversation anchor would therefore hide valid
    # multi-KB runs.  When a KB filter is requested, filter the persisted
    # execution snapshot after loading the durable read model instead.
    if knowledge_base_id:
        query = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
            .options(*admin_run_query_options())
            .order_by(Message.created_at.desc(), Message.id.desc())
        )
        all_items = [
            admin_run_response(session, message)
            for message in session.scalars(query).all()
        ]
        scoped_items = [
            item
            for item in all_items
            if knowledge_base_id in item.get("scope", {}).get("knowledge_base_ids", [])
        ]
        total = len(scoped_items)
        start = (page - 1) * page_size
        items = scoped_items[start : start + page_size]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    total = int(
        session.scalar(
            select(func.count(Message.id))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(*conditions)
        )
        or 0
    )
    query = (
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(*conditions)
        .options(*admin_run_query_options())
        .order_by(Message.created_at.desc(), Message.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [admin_run_response(session, message) for message in session.scalars(query).all()]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/admin/runs/{message_id}")
def admin_run_detail(
    message_id: str,
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    message = session.scalar(
        select(Message)
        .where(Message.id == message_id, Message.role == "assistant")
        .options(*admin_run_query_options())
    )
    if message is None:
        raise ProductError("MESSAGE_NOT_FOUND", "没有找到这个回答运行记录。", status_code=404)
    return admin_run_response(session, message)


@router.get("/admin/audit-logs")
def audit_logs(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    biz_type: str | None = Query(None),
    biz_id: str | None = Query(None),
    operation_type: str | None = Query(None),
    operator_id: str | None = Query(None),
    operator_name: str | None = Query(None),
    success: bool | None = Query(None),
    begin_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
) -> dict:
    """Admin-only operation audit page (analog of ragent BizChangeLogController)."""
    return page_audit_logs(
        session,
        page=page,
        page_size=page_size,
        biz_type=biz_type,
        biz_id=biz_id,
        operation_type=operation_type,
        operator_id=operator_id,
        operator_name=operator_name,
        success=success,
        begin_time=begin_time,
        end_time=end_time,
    )


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
        "is_company_wide": knowledge_base.is_company_wide,
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


def admin_document_response(session: Session, document: Document) -> dict:
    """Document detail plus the latest real processing job, if one exists."""
    latest_job = session.scalar(
        select(IngestJob)
        .where(IngestJob.document_id == document.id)
        .order_by(IngestJob.created_at.desc(), IngestJob.id.desc())
        .limit(1)
    )
    data = document_response(document)
    data["processing"] = ingest_job_response(latest_job) if latest_job is not None else None
    return data


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


def message_response(message: Message, feedback: dict | None = None) -> dict:
    data = MessageResponse.model_validate(message).model_dump(mode="json")
    data["feedback"] = feedback
    return data


def admin_run_response(session: Session, message: Message) -> dict:
    conversation = message.conversation
    knowledge_base = conversation.knowledge_base
    owner = conversation.owner
    feedback = [item for item in message.feedback_records if not item.cancelled]
    answer_run = max(
        message.answer_runs,
        key=lambda item: (item.created_at, item.id),
        default=None,
    )
    question = (
        answer_run.question_message.content
        if answer_run is not None
        else next(
            (
                candidate.content
                for candidate in reversed(conversation.messages)
                if candidate.role == "user" and candidate.created_at <= message.created_at
            ),
            None,
        )
    )
    snapshot = (
        answer_run.query_scope_snapshot
        if answer_run is not None
        else {
            "schema_version": 1,
            "mode": conversation.scope_mode or "fixed",
            "knowledge_base_ids": [knowledge_base.id] if knowledge_base is not None else [],
            "collection_names": (
                [knowledge_base.collection_name] if knowledge_base is not None else []
            ),
            "resolved_at": None,
            "status_counts": None,
        }
    )
    scope_ids = snapshot.get("knowledge_base_ids") if isinstance(snapshot, dict) else []
    scope_ids = scope_ids if isinstance(scope_ids, list) else []
    scope_knowledge_bases = session.scalars(
        select(KnowledgeBase).where(KnowledgeBase.id.in_(scope_ids))
    ).all() if scope_ids else []
    return {
        "question": question,
        "message": message_response(message),
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
        },
        "knowledge_base": (
            {"id": knowledge_base.id, "name": knowledge_base.name}
            if knowledge_base is not None
            else None
        ),
        "scope": {
            **snapshot,
            "knowledge_bases": [
                {"id": item.id, "name": item.name} for item in scope_knowledge_bases
            ],
        },
        "answer_run": (
            {
                "id": answer_run.id,
                "status": answer_run.status,
                "request_id": answer_run.request_id,
                "answer_mode": answer_run.answer_mode,
                "routing_decision": answer_run.routing_decision,
                "current_stage": answer_run.current_stage,
                "failure_code": answer_run.failure_code,
                "effective_risk_level": answer_run.effective_risk_level,
                "effective_risk_factors": answer_run.effective_risk_factors,
                "started_at": answer_run.started_at,
                "finished_at": answer_run.finished_at,
                "total_latency_ms": answer_run.total_latency_ms,
                "provider": answer_run.provider,
                "model": answer_run.model,
                "attempts": answer_run.attempts,
                "stage_results": answer_run.stage_results,
            }
            if answer_run is not None
            else None
        ),
        "owner": {
            "id": owner.id,
            "username": owner.username,
            "display_name": owner.display_name,
        },
        "feedback": {
            "positive": sum(item.vote == 1 for item in feedback),
            "negative": sum(item.vote == -1 for item in feedback),
            "comment_count": sum(bool(item.comment) for item in feedback),
        },
        "feedback_items": [
            {
                "vote": item.vote,
                "reason": item.reason,
                "comment": item.comment,
                "created_at": item.created_at,
            }
            for item in sorted(
                feedback,
                key=lambda feedback_item: feedback_item.created_at,
                reverse=True,
            )
        ],
    }


def admin_run_query_options():
    return (
        selectinload(Message.conversation).selectinload(Conversation.knowledge_base),
        selectinload(Message.conversation).selectinload(Conversation.owner),
        selectinload(Message.conversation).selectinload(Conversation.messages),
        selectinload(Message.answer_runs).selectinload(AnswerRun.question_message),
        selectinload(Message.citations),
        selectinload(Message.claims),
        selectinload(Message.feedback_records),
    )


def _sse_message(envelope: dict) -> str:
    event_name = str(envelope.get("event") or "message")
    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.get("/workspaces")
def workspaces(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    return {"items": list_workspaces(session, user.id)}


@router.post("/workspaces", status_code=201)
def add_workspace(
    request: WorkspaceCreate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    try:
        workspace = create_workspace(
            session,
            name=request.name,
            description=request.description,
            owner_id=user.id,
        )
    except IntegrityError as exc:
        session.rollback()
        raise ProductError(
            "WORKSPACE_NAME_EXISTS",
            "已经存在同名工作区。",
            status_code=409,
        ) from exc
    return workspace_response(session, workspace, role="owner")


@router.get("/workspaces/{workspace_id}/members")
def workspace_members(
    workspace_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_workspace(session, workspace_id)
    return {"items": list_workspace_members(session, workspace_id)}


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_workspace_member_route(
    workspace_id: str,
    payload: WorkspaceMemberAdd,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    workspace = _admin_workspace(session, workspace_id)
    target = session.scalar(
        select(User).where(User.username == payload.username.strip().casefold())
    )
    if target is None:
        raise ProductError(
            "USER_NOT_FOUND",
            "没有找到这个用户。",
            status_code=404,
        )
    member = add_workspace_member(session, workspace, target.id, payload.role)
    return {
        "member": {
            "user_id": member.user_id,
            "username": target.username,
            "display_name": target.display_name,
            "role": member.role,
        }
    }


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
def update_workspace_member_role(
    workspace_id: str,
    user_id: str,
    payload: WorkspaceMemberRoleUpdate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    workspace = _admin_workspace(session, workspace_id)
    member = set_member_role(session, workspace, user_id, payload.role)
    target = session.get(User, user_id)
    return {
        "member": {
            "user_id": member.user_id,
            "username": target.username if target is not None else user_id,
            "display_name": target.display_name if target is not None else user_id,
            "role": member.role,
        }
    }


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def delete_workspace_member_route(
    workspace_id: str,
    user_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    workspace = _admin_workspace(session, workspace_id)
    remove_workspace_member(session, workspace, user_id)
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/groups")
def workspace_groups(
    workspace_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_workspace(session, workspace_id)
    return {"items": list_groups(session, workspace_id)}


@router.post("/workspaces/{workspace_id}/groups", status_code=201)
def add_workspace_group(
    workspace_id: str,
    payload: MemberGroupCreate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    workspace = _admin_workspace(session, workspace_id)
    group = create_group(
        session,
        workspace,
        name=payload.name,
        role=payload.role,
    )
    return {"group": {"id": group.id, "name": group.name, "role": group.role}}


@router.get("/workspaces/{workspace_id}/groups/{group_id}")
def workspace_group_detail(
    workspace_id: str,
    group_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_workspace(session, workspace_id)
    group = get_group(session, workspace_id, group_id)
    return {
        "group": {"id": group.id, "name": group.name, "role": group.role},
        "members": list_group_members(session, group),
    }


@router.patch("/workspaces/{workspace_id}/groups/{group_id}")
def update_workspace_group_role(
    workspace_id: str,
    group_id: str,
    payload: MemberGroupRoleUpdate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    group = get_group(session, workspace_id, group_id)
    group = set_group_role(session, group, payload.role)
    return {"group": {"id": group.id, "name": group.name, "role": group.role}}


@router.delete("/workspaces/{workspace_id}/groups/{group_id}", status_code=204)
def delete_workspace_group_route(
    workspace_id: str,
    group_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    group = get_group(session, workspace_id, group_id)
    delete_group(session, group)
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/groups/{group_id}/members", status_code=201)
def add_workspace_group_member_route(
    workspace_id: str,
    group_id: str,
    payload: GroupMemberAdd,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    group = get_group(session, workspace_id, group_id)
    target = session.scalar(
        select(User).where(User.username == payload.username.strip().casefold())
    )
    if target is None:
        raise ProductError(
            "USER_NOT_FOUND",
            "没有找到这个用户。",
            status_code=404,
        )
    add_group_member(session, group, target.id)
    return {"member": {"user_id": target.id, "username": target.username}}


@router.delete(
    "/workspaces/{workspace_id}/groups/{group_id}/members/{user_id}",
    status_code=204,
)
def delete_workspace_group_member_route(
    workspace_id: str,
    group_id: str,
    user_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    group = get_group(session, workspace_id, group_id)
    remove_group_member(session, group, user_id)
    return Response(status_code=204)


@router.get("/knowledge-bases")
def knowledge_bases(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    return {"items": list_knowledge_bases(session, user.id)}


@router.post("/knowledge-bases", status_code=201)
def add_knowledge_base(
    request: KnowledgeBaseCreate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    workspace_id = request.workspace_id
    if workspace_id is None:
        from frontend.product.services.access import ensure_personal_workspaces

        ensure_personal_workspaces(session)
        workspace = session.scalar(
            select(Workspace).where(
                Workspace.owner_id == user.id,
                Workspace.name == user.username,
            )
        )
        if workspace is None:
            raise ProductError(
                "WORKSPACE_MISSING",
                "请先创建工作区。",
                status_code=400,
            )
        workspace_id = workspace.id
    _admin_workspace(session, workspace_id)
    try:
        knowledge_base = create_knowledge_base(
            session,
            name=request.name,
            description=request.description,
            workspace_id=workspace_id,
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
    knowledge_base = require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    return knowledge_base_detail(session, knowledge_base)


@router.put("/knowledge-bases/{knowledge_base_id}/current")
def select_knowledge_base(
    knowledge_base_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_knowledge_base(session, knowledge_base_id)
    updated = set_current_knowledge_base(session, knowledge_base_id)
    if updated is None:
        raise ProductError("KNOWLEDGE_BASE_NOT_FOUND", "没有找到这个知识库。", status_code=404)
    return knowledge_base_detail(session, updated)


@router.delete("/knowledge-bases/{knowledge_base_id}")
async def remove_knowledge_base(
    knowledge_base_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    deleted_name = knowledge_base.name
    deleted_collection = knowledge_base.collection_name
    next_current = await delete_knowledge_base(session, knowledge_base)
    record_operation(
        session,
        biz_type="knowledge_base",
        biz_id=knowledge_base_id,
        operation_type="DELETE_KNOWLEDGE_BASE",
        action_desc=f"删除知识库 {deleted_name}",
        before={"name": deleted_name, "collection_name": deleted_collection},
        after=None,
    )
    return {
        "deleted_id": knowledge_base_id,
        "current_knowledge_base_id": next_current.id if next_current else None,
    }


@router.post("/knowledge-bases/{knowledge_base_id}/reindex")
async def reindex_knowledge_base_route(
    knowledge_base_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    await reindex_knowledge_base(session, knowledge_base)
    session.refresh(knowledge_base)
    return knowledge_base_detail(session, knowledge_base)


@router.get("/knowledge-bases/{knowledge_base_id}/health")
def knowledge_health(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    latest = latest_health_snapshot(session, knowledge_base.id)
    current = assemble_health_payload(session, knowledge_base)
    return {
        "snapshot": health_snapshot_response(latest) if latest is not None else None,
        "current": current,
    }


@router.post("/knowledge-bases/{knowledge_base_id}/health/snapshot", status_code=201)
def create_knowledge_health_snapshot_route(
    knowledge_base_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    snapshot, previous, change = create_health_snapshot(
        session,
        knowledge_base,
        user.id,
    )
    return {
        "snapshot": health_snapshot_response(snapshot),
        "previous": (health_snapshot_response(previous) if previous is not None else None),
        "change": change,
    }


@router.get("/knowledge-bases/{knowledge_base_id}/health/history")
def knowledge_health_history(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    items = list_health_snapshots(session, knowledge_base_id)
    return {"items": [health_snapshot_response(item) for item in items]}


@router.get("/knowledge-bases/{knowledge_base_id}/health/trend")
def knowledge_health_trend(
    knowledge_base_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    snapshots = list_health_snapshots(session, knowledge_base_id, limit=limit)
    return health_trend_payload(snapshots)


@router.post("/knowledge-bases/{knowledge_base_id}/health/actions/run")
async def run_knowledge_health_actions_route(
    knowledge_base_id: str,
    payload: HealthActionsRun,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    result = await run_health_actions(session, knowledge_base, user.id, payload.actions)
    record_operation(
        session,
        biz_type="knowledge_health",
        biz_id=knowledge_base.id,
        operation_type="RUN_HEALTH_ACTIONS",
        action_desc=f"执行知识健康建议操作：{','.join(payload.actions)}",
        before=None,
        after=result,
    )
    return result


@router.get("/knowledge-bases/{knowledge_base_id}/members")
def knowledge_base_members(
    knowledge_base_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    return {"items": list_kb_members(session, knowledge_base)}


@router.post("/knowledge-bases/{knowledge_base_id}/members", status_code=201)
def add_knowledge_base_member_route(
    knowledge_base_id: str,
    payload: KnowledgeBaseMemberAdd,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    target = session.scalar(
        select(User).where(User.username == payload.username.strip().casefold())
    )
    if target is None:
        raise ProductError(
            "USER_NOT_FOUND",
            "没有找到这个用户。",
            status_code=404,
        )
    member = add_kb_member(session, knowledge_base, target.id, payload.role)
    return {
        "member": {
            "user_id": member.user_id,
            "username": target.username,
            "display_name": target.display_name,
            "role": member.role,
        }
    }


@router.patch("/knowledge-bases/{knowledge_base_id}/members/{user_id}")
def update_knowledge_base_member_role(
    knowledge_base_id: str,
    user_id: str,
    payload: KnowledgeBaseMemberRoleUpdate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    member = set_kb_member_role(session, knowledge_base, user_id, payload.role)
    target = session.get(User, user_id)
    return {
        "member": {
            "user_id": member.user_id,
            "username": target.username if target is not None else user_id,
            "display_name": target.display_name if target is not None else user_id,
            "role": member.role,
        }
    }


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/members/{user_id}",
    status_code=204,
)
def remove_knowledge_base_member_route(
    knowledge_base_id: str,
    user_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
    remove_kb_member(session, knowledge_base, user_id)
    return Response(status_code=204)


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
def documents(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
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
    document = _accessible_document(session, user, document_id, "read")
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
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    knowledge_base = _admin_knowledge_base(session, knowledge_base_id)
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
    document = _accessible_document(session, user, document_id, "read")
    return document_response(document)


@router.patch("/documents/{document_id}/temporal-metadata", status_code=202)
def update_document_temporal_metadata_route(
    document_id: str,
    payload: DocumentTemporalUpdate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
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
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
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
    job = _accessible_ingest_job(session, user, job_id)
    return ingest_job_response(job)


@router.delete("/documents/{document_id}", status_code=204)
async def remove_document(
    document_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
    await delete_document(session, document)
    return Response(status_code=204)


@router.post("/documents/{document_id}/retry", status_code=202)
def retry_failed_document(
    document_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    document = session.get(Document, document_id)
    if document is None:
        raise ProductError("DOCUMENT_NOT_FOUND", "没有找到这个文档。", status_code=404)
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
                "knowledge_base_name": (
                    conversation.knowledge_base.name
                    if conversation.knowledge_base is not None
                    else None
                ),
                "scope_mode": conversation.scope_mode,
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
    knowledge_base = None
    if request.scope_mode == "fixed":
        assert request.knowledge_base_id is not None
        knowledge_base = require_accessible_knowledge_base(
            session, user, request.knowledge_base_id, "read"
        )
    conversation = Conversation(
        owner_id=user.id,
        knowledge_base_id=knowledge_base.id if knowledge_base is not None else None,
        scope_mode=request.scope_mode or ("fixed" if knowledge_base is not None else "auto"),
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return {
        "id": conversation.id,
        "knowledge_base_id": conversation.knowledge_base_id,
        "scope_mode": conversation.scope_mode,
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
    feedback_map = get_feedback_map(
        session,
        user_id=user.id,
        message_ids=[message.id for message in conversation.messages],
    )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "scope_mode": conversation.scope_mode,
        "knowledge_base": (
            knowledge_base_detail(session, conversation.knowledge_base)
            if conversation.knowledge_base is not None
            else None
        ),
        "messages": [
            message_response(message, feedback_map.get(message.id))
            for message in conversation.messages
        ],
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
    scope = resolve_conversation_scope(session, user, conversation)
    user_message, assistant_message = await submit_message(
        session,
        conversation=conversation,
        scope=scope,
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
    scope = resolve_conversation_scope(session, user, conversation)
    if not payload.content.strip():
        raise ProductError("MESSAGE_EMPTY", "请输入你想了解的问题。")

    async def relay_events():
        async for envelope in stream_message_events(
            session,
            conversation=conversation,
            scope=scope,
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


def _feedback_target(
    session: Session,
    *,
    conversation_id: str,
    message_id: str,
    user: User,
) -> Message:
    """Resolve and validate the feedback target (loadAssistantMessage equivalent).

    Error semantics are fixed: an unknown or foreign conversation is a 404, an
    unknown message inside the conversation is a 404 (conversation is resolved
    first so message existence is never probed across conversations), and only
    assistant messages are feedback targets (400).
    """
    conversation = get_conversation(session, conversation_id, user.id)
    if conversation is None:
        raise ProductError(
            "CONVERSATION_NOT_FOUND",
            "没有找到这个对话。",
            status_code=404,
        )
    if conversation.scope_mode == "fixed" and conversation.knowledge_base_id:
        require_accessible_knowledge_base(
            session,
            user,
            conversation.knowledge_base_id,
            "read",
        )
    message = next(
        (candidate for candidate in conversation.messages if candidate.id == message_id),
        None,
    )
    if message is None:
        raise ProductError(
            "MESSAGE_NOT_FOUND",
            "没有找到这条消息。",
            status_code=404,
        )
    if message.role != "assistant":
        raise ProductError(
            "INVALID_FEEDBACK_TARGET",
            "仅支持对助手消息反馈。",
            status_code=400,
        )
    return message


@router.post("/conversations/{conversation_id}/messages/{message_id}/feedback")
def submit_message_feedback_route(
    conversation_id: str,
    message_id: str,
    payload: MessageFeedbackCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    message = _feedback_target(
        session,
        conversation_id=conversation_id,
        message_id=message_id,
        user=user,
    )
    feedback = submit_message_feedback(
        session,
        user_id=user.id,
        message_id=message.id,
        vote=payload.vote,
        reason=payload.reason,
        comment=payload.comment,
    )
    session.commit()
    return {
        "feedback": {
            "vote": feedback.vote,
            "cancelled": feedback.cancelled,
        }
    }


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    status_code=204,
)
def cancel_message_feedback_route(
    conversation_id: str,
    message_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    message = _feedback_target(
        session,
        conversation_id=conversation_id,
        message_id=message_id,
        user=user,
    )
    cancel_message_feedback(session, user_id=user.id, message_id=message.id)
    session.commit()
    return Response(status_code=204)


# ---- v0.6 connector sync API ----


@router.post("/knowledge-bases/{knowledge_base_id}/connector-syncs", status_code=201)
def create_connector_sync_route(
    knowledge_base_id: str,
    payload: ConnectorSyncCreate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_knowledge_base(session, knowledge_base_id)
    validate_cron(payload.cron)
    sync = ConnectorSync(
        knowledge_base_id=knowledge_base_id,
        source_type=payload.source_type,
        config=payload.config,
        cron=payload.cron,
        status="active",
        next_run_at=compute_next_run(payload.cron, utcnow()),
    )
    session.add(sync)
    session.commit()
    session.refresh(sync)
    return {
        "id": sync.id,
        "source_type": sync.source_type,
        "cron": sync.cron,
        "status": sync.status,
        "next_run_at": sync.next_run_at,
    }


@router.get("/knowledge-bases/{knowledge_base_id}/connector-syncs")
def list_connector_syncs_route(
    knowledge_base_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    items = session.scalars(
        select(ConnectorSync).where(ConnectorSync.knowledge_base_id == knowledge_base_id)
    ).all()
    return {
        "items": [
            {
                "id": sync.id,
                "source_type": sync.source_type,
                "cron": sync.cron,
                "status": sync.status,
                "next_run_at": sync.next_run_at,
            }
            for sync in items
        ],
    }


@router.post("/knowledge-bases/{knowledge_base_id}/connector-syncs/{sync_id}/trigger")
def trigger_connector_sync_route(
    knowledge_base_id: str,
    sync_id: str,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    _admin_knowledge_base(session, knowledge_base_id)
    sync = session.get(ConnectorSync, sync_id)
    if sync is None or sync.knowledge_base_id != knowledge_base_id:
        raise ProductError("CONNECTOR_SYNC_NOT_FOUND", "没有找到这个连接器同步。", status_code=404)
    sync.next_run_at = utcnow()
    session.commit()
    return {"triggered": True, "id": sync.id}


@router.get("/knowledge-bases/{knowledge_base_id}/connector-syncs/{sync_id}/runs")
def connector_sync_runs_route(
    knowledge_base_id: str,
    sync_id: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> dict:
    require_accessible_knowledge_base(session, user, knowledge_base_id, "read")
    sync = session.get(ConnectorSync, sync_id)
    if sync is None or sync.knowledge_base_id != knowledge_base_id:
        raise ProductError("CONNECTOR_SYNC_NOT_FOUND", "没有找到这个连接器同步。", status_code=404)
    runs = session.scalars(
        select(ConnectorSyncRun)
        .where(ConnectorSyncRun.sync_id == sync_id)
        .order_by(ConnectorSyncRun.created_at.desc())
        .limit(50)
    ).all()
    return {
        "items": [
            {
                "id": run.id,
                "status": run.status,
                "scheduled_for": run.scheduled_for,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "added": run.added,
                "updated": run.updated,
                "deleted": run.deleted,
                "skipped": run.skipped,
                "enqueue_failed": run.enqueue_failed,
                "error_message": run.error_message,
            }
            for run in runs
        ],
    }


# ---- P4 admin dashboard ----


@router.get("/admin/dashboard/overview")
def admin_dashboard_overview_route(
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    return dashboard_overview(session)


@router.get("/admin/dashboard/trends")
def admin_dashboard_trends_route(
    days: int = Query(7),
    _admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    if days not in (7, 30):
        raise ProductError(
            "DASHBOARD_INVALID_DAYS",
            "days 必须是 7 或 30。",
            status_code=422,
        )
    return dashboard_trends(session, days=days)
