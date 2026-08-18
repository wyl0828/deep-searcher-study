"""Add workspace-level team trust (v0.5.0).

Revision ID: 20260816_0018
Revises: 20260816_0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import select
from sqlalchemy.orm import Session

from frontend.product.models import (
    LEGACY_OWNER_ID,
    KnowledgeBase,
    User,
    Workspace,
    WorkspaceMember,
)

revision = "20260816_0018"
down_revision = "20260816_0017"
branch_labels = None
depends_on = None


def _backfill_workspaces(bind) -> None:
    """Create a personal workspace per user and attach every knowledge base.

    Conversations are intentionally not attached to a workspace: they keep
    their owner_id and inherit the permission domain through the knowledge
    base's workspace. Legacy (unclaimed) knowledge bases go to a workspace
    owned by the first active admin when one exists, otherwise by the real
    legacy owner user row (usr_legacy_owner). Never silently hand legacy data
    to an arbitrary member user.
    """

    session = Session(bind=bind, expire_on_commit=False)
    try:
        users = session.scalars(select(User).where(User.id != LEGACY_OWNER_ID)).all()
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

        personal: dict[str, Workspace] = {}
        for user in users:
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
            personal[user.id] = workspace

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

        knowledge_bases = session.scalars(select(KnowledgeBase)).all()
        for knowledge_base in knowledge_bases:
            if knowledge_base.owner_id == LEGACY_OWNER_ID:
                target = legacy_workspace
            else:
                target = personal.get(knowledge_base.owner_id)
            if target is None:
                target = legacy_workspace
            knowledge_base.workspace_id = target.id

        session.commit()
    finally:
        session.close()


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column(
            "description",
            sa.String(length=200),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "owner_id",
            sa.String(length=40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_workspaces_name"),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"], unique=False)
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_members_workspace_user",
        ),
        sa.CheckConstraint(
            "role IN ('owner','editor','viewer')",
            name="ck_workspace_members_role",
        ),
    )
    op.create_index(
        "ix_workspace_members_workspace_id",
        "workspace_members",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_members_user_id",
        "workspace_members",
        ["user_id"],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.add_column(
            "knowledge_bases",
            sa.Column(
                "workspace_id",
                sa.String(length=40),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    else:
        with op.batch_alter_table("knowledge_bases") as batch_op:
            batch_op.add_column(sa.Column("workspace_id", sa.String(length=40), nullable=True))
    op.create_index(
        "ix_knowledge_bases_workspace_id",
        "knowledge_bases",
        ["workspace_id"],
        unique=False,
    )

    _backfill_workspaces(bind)

    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.alter_column("workspace_id", nullable=False)
        batch_op.drop_constraint("uq_knowledge_bases_owner_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_knowledge_bases_workspace_name",
            ["workspace_id", "name"],
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX uq_workspace_members_single_owner "
            "ON workspace_members (workspace_id) WHERE role = 'owner'"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_workspace_members_single_owner")
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_constraint("uq_knowledge_bases_workspace_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_knowledge_bases_owner_name",
            ["owner_id", "name"],
        )
        batch_op.drop_column("workspace_id")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_index(
        "ix_workspace_members_workspace_id",
        table_name="workspace_members",
    )
    op.drop_table("workspace_members")
    op.drop_index("ix_workspaces_owner_id", table_name="workspaces")
    op.drop_table("workspaces")
