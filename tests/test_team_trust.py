from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import Base
from frontend.product.errors import ProductError
from frontend.product.models import (
    LEGACY_OWNER_ID,
    KnowledgeBase,
    User,
    Workspace,
    WorkspaceMember,
)
from frontend.product.repositories import create_knowledge_base
from frontend.product.services.access import (
    add_workspace_member,
    create_workspace,
    ensure_personal_workspaces,
    remove_workspace_member,
    require_accessible_knowledge_base,
    require_workspace_access,
    set_member_role,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as store:
        yield store
    engine.dispose()


def make_user(session: Session, username: str, role: str = "member") -> User:
    user = User(
        username=username,
        display_name=username,
        password_hash="x" * 60,
        role=role,
    )
    session.add(user)
    session.commit()
    return user


def make_personal_workspace(session: Session, user: User) -> Workspace:
    workspace = create_workspace(
        session,
        name=user.username,
        description="",
        owner_id=user.id,
    )
    return workspace


def make_kb(session: Session, user: User, workspace: Workspace) -> KnowledgeBase:
    return create_knowledge_base(
        session,
        name="kb",
        description="",
        workspace_id=workspace.id,
        owner_id=user.id,
    )


def test_permission_matrix():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner", role="admin")
        editor = make_user(session, "editor")
        viewer = make_user(session, "viewer")
        workspace = make_personal_workspace(session, owner)
        add_workspace_member(session, workspace, editor.id, "editor")
        add_workspace_member(session, workspace, viewer.id, "viewer")
        knowledge_base = make_kb(session, owner, workspace)

        # viewer: read ok, write/admin denied
        require_accessible_knowledge_base(session, viewer, knowledge_base.id, "read")
        with pytest.raises(ProductError) as exc:
            require_accessible_knowledge_base(session, viewer, knowledge_base.id, "write")
        assert exc.value.status_code == 403

        # editor: read/write ok, admin denied
        require_accessible_knowledge_base(session, editor, knowledge_base.id, "read")
        require_accessible_knowledge_base(session, editor, knowledge_base.id, "write")
        with pytest.raises(ProductError) as exc:
            require_accessible_knowledge_base(session, editor, knowledge_base.id, "admin")
        assert exc.value.status_code == 403

        # owner: all ok
        require_accessible_knowledge_base(session, owner, knowledge_base.id, "admin")


def test_non_member_and_missing_kb_are_indistinguishable():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        stranger = make_user(session, "stranger")
        workspace = make_personal_workspace(session, owner)
        knowledge_base = make_kb(session, owner, workspace)

        def code(kb_id: str):
            try:
                require_accessible_knowledge_base(session, stranger, kb_id, "read")
            except ProductError as exc:
                return exc.status_code
            return None

        missing = code("kb_does_not_exist")
        other_workspace = code(knowledge_base.id)
        assert missing == 404
        assert other_workspace == 404


def test_workspace_access_requires_admin_for_members():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        editor = make_user(session, "editor")
        workspace = make_personal_workspace(session, owner)
        add_workspace_member(session, workspace, editor.id, "editor")

        require_workspace_access(session, owner, workspace.id, "admin")
        with pytest.raises(ProductError) as exc:
            require_workspace_access(session, editor, workspace.id, "admin")
        assert exc.value.status_code == 403


def test_add_member_only_editor_or_viewer():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        other = make_user(session, "other")
        workspace = make_personal_workspace(session, owner)
        with pytest.raises(ProductError) as exc:
            add_workspace_member(session, workspace, other.id, "owner")
        assert exc.value.status_code == 400


def test_owner_cannot_be_patched_or_removed():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        workspace = make_personal_workspace(session, owner)
        with pytest.raises(ProductError) as exc:
            set_member_role(session, workspace, owner.id, "editor")
        assert exc.value.status_code == 400
        with pytest.raises(ProductError) as exc:
            remove_workspace_member(session, workspace, owner.id)
        assert exc.value.status_code == 400


def test_revocation_blocks_subsequent_access():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        member = make_user(session, "member")
        workspace = make_personal_workspace(session, owner)
        add_workspace_member(session, workspace, member.id, "viewer")
        knowledge_base = make_kb(session, owner, workspace)
        require_accessible_knowledge_base(session, member, knowledge_base.id, "read")

        remove_workspace_member(session, workspace, member.id)
        with pytest.raises(ProductError) as exc:
            require_accessible_knowledge_base(session, member, knowledge_base.id, "read")
        assert exc.value.status_code == 404


def test_downgrade_editor_to_viewer_blocks_write():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner = make_user(session, "owner")
        member = make_user(session, "member")
        workspace = make_personal_workspace(session, owner)
        add_workspace_member(session, workspace, member.id, "editor")
        knowledge_base = make_kb(session, owner, workspace)
        require_accessible_knowledge_base(session, member, knowledge_base.id, "write")

        set_member_role(session, workspace, member.id, "viewer")
        require_accessible_knowledge_base(session, member, knowledge_base.id, "read")
        with pytest.raises(ProductError) as exc:
            require_accessible_knowledge_base(session, member, knowledge_base.id, "write")
        assert exc.value.status_code == 403


def test_create_workspace_creates_owner_membership_in_one_transaction():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        user = make_user(session, "alice")
        workspace = create_workspace(
            session,
            name="alice",
            description="",
            owner_id=user.id,
        )
        members = session.query(WorkspaceMember).filter_by(
            workspace_id=workspace.id
        ).all()
        assert len(members) == 1
        assert members[0].user_id == user.id
        assert members[0].role == "owner"
        assert workspace.owner_id == user.id


def test_ensure_personal_workspaces_idempotent_and_complete():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        admin = make_user(session, "admin", role="admin")
        user_b = make_user(session, "userb")
        workspace = make_personal_workspace(session, admin)
        make_kb(session, admin, workspace)

        created_first = ensure_personal_workspaces(session)
        created_second = ensure_personal_workspaces(session)
        assert created_first >= 2  # admin + userb (+ legacy workspace)
        assert created_second == 0  # idempotent

        knowledge_bases = session.query(KnowledgeBase).all()
        assert all(kb.workspace_id is not None for kb in knowledge_bases)
        # every user owns exactly one personal workspace
        for user in (admin, user_b):
            personal = session.query(Workspace).filter_by(
                owner_id=user.id,
                name=user.username,
            ).one()
            assert personal is not None
