"""Add per-KB overrides and member groups (1.1/1.2).

Revision ID: 20260816_0019
Revises: 20260816_0018
"""

import sqlalchemy as sa
from alembic import op

revision = "20260816_0019"
down_revision = "20260816_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite unique on knowledge_bases so KnowledgeBaseMember can reference
    # (workspace_id, knowledge_base_id).
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.create_unique_constraint(
            "uq_knowledge_bases_workspace_id",
            ["workspace_id", "id"],
        )

    op.create_table(
        "knowledge_base_members",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "user_id",
            name="uq_kb_members_kb_user",
        ),
        sa.CheckConstraint(
            "role IN ('editor','viewer')",
            name="ck_kb_members_role",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_kb_members_workspace_user",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "knowledge_base_id"],
            ["knowledge_bases.workspace_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_kb_members_workspace_kb",
        ),
    )
    op.create_index(
        "ix_knowledge_base_members_kb_user",
        "knowledge_base_members",
        ["knowledge_base_id", "user_id"],
        unique=True,
    )

    op.create_table(
        "member_groups",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=40),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_member_groups_workspace_name",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_member_groups_workspace_id",
        ),
        sa.CheckConstraint(
            "role IN ('editor','viewer')",
            name="ck_member_groups_role",
        ),
    )
    op.create_index(
        "ix_member_groups_workspace_id",
        "member_groups",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "group_members",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("group_id", sa.String(length=40), nullable=False),
        sa.Column("workspace_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "group_id",
            "user_id",
            name="uq_group_members_group_user",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            ondelete="CASCADE",
            name="fk_group_members_workspace_user",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "group_id"],
            ["member_groups.workspace_id", "member_groups.id"],
            ondelete="CASCADE",
            name="fk_group_members_workspace_group",
        ),
    )
    op.create_index(
        "ix_group_members_workspace_user",
        "group_members",
        ["workspace_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_group_members_workspace_user", table_name="group_members")
    op.drop_table("group_members")
    op.drop_index("ix_member_groups_workspace_id", table_name="member_groups")
    op.drop_table("member_groups")
    op.drop_index(
        "ix_knowledge_base_members_kb_user",
        table_name="knowledge_base_members",
    )
    op.drop_table("knowledge_base_members")
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_constraint("uq_knowledge_bases_workspace_id", type_="unique")
