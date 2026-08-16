"""Persist versioned knowledge health snapshots.

Revision ID: 20260816_0017
Revises: 20260814_0016
"""

import sqlalchemy as sa
from alembic import op

revision = "20260816_0017"
down_revision = "20260814_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_health_snapshots",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "knowledge_base_id",
            sa.String(length=40),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.String(length=40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("formula_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="complete"),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("data_score", sa.Float(), nullable=True),
        sa.Column("retrieval_score", sa.Float(), nullable=True),
        sa.Column("trust_score", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("deductions", sa.JSON(), nullable=True),
        sa.Column("actions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_knowledge_health_snapshots_knowledge_base_id",
        "knowledge_health_snapshots",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_health_snapshots_owner_id",
        "knowledge_health_snapshots",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_health_snapshots_owner_id", table_name="knowledge_health_snapshots")
    op.drop_index(
        "ix_knowledge_health_snapshots_knowledge_base_id",
        table_name="knowledge_health_snapshots",
    )
    op.drop_table("knowledge_health_snapshots")
