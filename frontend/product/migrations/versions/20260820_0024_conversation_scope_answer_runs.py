"""Add conversation scope policies and durable answer execution facts.

Revision ID: 20260820_0024
Revises: 20260817_0023
"""

import sqlalchemy as sa
from alembic import op

revision = "20260820_0024"
down_revision = "20260817_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column(
            "knowledge_base_id",
            existing_type=sa.String(length=40),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "scope_mode",
                sa.String(length=16),
                nullable=False,
                server_default="fixed",
            )
        )

    op.create_table(
        "answer_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("question_message_id", sa.String(length=40), nullable=False),
        sa.Column("answer_message_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=160), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.JSON(), nullable=True),
        sa.Column("query_scope_snapshot", sa.JSON(), nullable=False),
        sa.Column("stage_results", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["answer_message_id"],
            ["messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_answer_runs_conversation_id",
        "answer_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_answer_runs_question_message_id",
        "answer_runs",
        ["question_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_answer_runs_answer_message_id",
        "answer_runs",
        ["answer_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_answer_runs_answer_message_id", table_name="answer_runs")
    op.drop_index("ix_answer_runs_question_message_id", table_name="answer_runs")
    op.drop_index("ix_answer_runs_conversation_id", table_name="answer_runs")
    op.drop_table("answer_runs")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("scope_mode")
        batch_op.alter_column(
            "knowledge_base_id",
            existing_type=sa.String(length=40),
            nullable=False,
        )
