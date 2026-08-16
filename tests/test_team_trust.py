from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from frontend.product.db import Base
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
from frontend.product.repositories import create_knowledge_base
from frontend.product.services.access import (
    add_group_member,
    add_kb_member,
    add_workspace_member,
    create_group,
    create_workspace,
    effective_kb_role,
    effective_workspace_role,
    get_group,
    promote_member_to_owner,
    ensure_personal_workspaces,
    remove_group_member,
    remove_kb_member,
    remove_workspace_member,
    require_accessible_knowledge_base,
    require_workspace_access,
    set_kb_member_role,
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


# ---- 1.1 per-KB override ----


def _setup_shared(session):
    owner = make_user(session, "owner", role="admin")
    member = make_user(session, "member")
    workspace = make_personal_workspace(session, owner)
    add_workspace_member(session, workspace, member.id, "viewer")
    knowledge_base = make_kb(session, owner, workspace)
    return owner, member, workspace, knowledge_base


def test_kb_override_upgrades_and_downgrades():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, member, workspace, knowledge_base = _setup_shared(session)
        # baseline: member is workspace viewer -> read only
        require_accessible_knowledge_base(session, member, knowledge_base.id, "read")
        with pytest.raises(ProductError):
            require_accessible_knowledge_base(session, member, knowledge_base.id, "write")
        # upgrade via KB override -> editor
        add_kb_member(session, knowledge_base, member.id, "editor")
        require_accessible_knowledge_base(session, member, knowledge_base.id, "write")
        # downgrade via KB override -> viewer
        set_kb_member_role(session, knowledge_base, member.id, "viewer")
        with pytest.raises(ProductError):
            require_accessible_knowledge_base(session, member, knowledge_base.id, "write")
        # remove override -> falls back to workspace viewer
        remove_kb_member(session, knowledge_base, member.id)
        with pytest.raises(ProductError):
            require_accessible_knowledge_base(session, member, knowledge_base.id, "write")


def test_kb_override_owner_not_allowed():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, _, workspace, knowledge_base = _setup_shared(session)
        with pytest.raises(ProductError) as exc:
            add_kb_member(session, knowledge_base, owner.id, "viewer")
        assert exc.value.status_code == 400


def test_kb_override_non_workspace_member_rejected():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, _, workspace, knowledge_base = _setup_shared(session)
        stranger = make_user(session, "stranger")
        with pytest.raises(ProductError) as exc:
            add_kb_member(session, knowledge_base, stranger.id, "editor")
        assert exc.value.status_code == 404


def test_kb_override_cross_workspace_db_rejected():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner_a = make_user(session, "ownera")
        owner_b = make_user(session, "ownerb")
        user_b = make_user(session, "userb")
        ws_a = make_personal_workspace(session, owner_a)
        ws_b = make_personal_workspace(session, owner_b)
        add_workspace_member(session, ws_b, user_b.id, "viewer")
        kb_a = make_kb(session, owner_a, ws_a)
        with pytest.raises(Exception):  # composite FK rejects cross-workspace
            bad = KnowledgeBaseMember(
                knowledge_base_id=kb_a.id,
                workspace_id=ws_b.id,
                user_id=user_b.id,
                role="editor",
            )
            session.add(bad)
            session.flush()


def test_kb_override_applies_immediately():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, member, _, knowledge_base = _setup_shared(session)
        assert effective_kb_role(session, member.id, knowledge_base) == "viewer"
        add_kb_member(session, knowledge_base, member.id, "editor")
        assert effective_kb_role(session, member.id, knowledge_base) == "editor"


# ---- 1.2 member groups ----


def test_group_role_boosts_effective_workspace_role():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, member, workspace, knowledge_base = _setup_shared(session)
        assert effective_workspace_role(session, member.id, workspace.id) == "viewer"
        group = create_group(session, workspace, name="editors", role="editor")
        add_group_member(session, group, member.id)
        assert effective_workspace_role(session, member.id, workspace.id) == "editor"
        require_accessible_knowledge_base(session, member, knowledge_base.id, "write")


def test_group_cannot_grant_admin():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, member, workspace, _ = _setup_shared(session)
        group = create_group(session, workspace, name="editors", role="editor")
        add_group_member(session, group, member.id)
        with pytest.raises(ProductError) as exc:
            require_workspace_access(session, member, workspace.id, "admin")
        assert exc.value.status_code == 403


def test_group_member_owner_not_allowed():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, _, workspace, _ = _setup_shared(session)
        group = create_group(session, workspace, name="editors", role="editor")
        with pytest.raises(ProductError) as exc:
            add_group_member(session, group, owner.id)
        assert exc.value.status_code == 400


def test_group_member_requires_workspace_membership():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, _, workspace, _ = _setup_shared(session)
        stranger = make_user(session, "stranger")
        group = create_group(session, workspace, name="editors", role="editor")
        with pytest.raises(ProductError) as exc:
            add_group_member(session, group, stranger.id)
        assert exc.value.status_code == 404


def test_stale_group_member_has_no_access():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, member, workspace, _ = _setup_shared(session)
        group = create_group(session, workspace, name="editors", role="editor")
        add_group_member(session, group, member.id)
        # remove the workspace member directly; the group member must be gone
        remove_workspace_member(session, workspace, member.id)
        assert effective_workspace_role(session, member.id, workspace.id) is None
        assert (
            session.query(GroupMember)
            .filter_by(group_id=group.id, user_id=member.id)
            .count()
            == 0
        )


def test_remove_workspace_member_cleans_kb_override():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, member, workspace, knowledge_base = _setup_shared(session)
        add_kb_member(session, knowledge_base, member.id, "editor")
        assert (
            session.query(KnowledgeBaseMember)
            .filter_by(knowledge_base_id=knowledge_base.id, user_id=member.id)
            .count()
            == 1
        )
        remove_workspace_member(session, workspace, member.id)
        assert (
            session.query(KnowledgeBaseMember)
            .filter_by(knowledge_base_id=knowledge_base.id, user_id=member.id)
            .count()
            == 0
        )


def test_promote_to_owner_cleans_acls():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        owner, member, workspace, knowledge_base = _setup_shared(session)
        add_kb_member(session, knowledge_base, member.id, "editor")
        group = create_group(session, workspace, name="editors", role="editor")
        add_group_member(session, group, member.id)
        promote_member_to_owner(session, workspace, member.id)
        assert (
            session.query(KnowledgeBaseMember)
            .filter_by(knowledge_base_id=knowledge_base.id, user_id=member.id)
            .count()
            == 0
        )
        assert (
            session.query(GroupMember)
            .filter_by(group_id=group.id, user_id=member.id)
            .count()
            == 0
        )
        member_row = session.query(WorkspaceMember).filter_by(
            workspace_id=workspace.id, user_id=member.id
        ).one()
        assert member_row.role == "owner"
