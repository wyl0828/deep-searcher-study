"""Add message feedback table (P1-4.2, aligned with ragent MessageFeedbackDO).

Revision ID: 20260817_0021
Revises: 20260817_0020
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0021"
down_revision = "20260817_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_feedback",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("message_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("vote", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "message_id",
            name="uq_message_feedback_user_message",
        ),
        sa.CheckConstraint(
            "vote IS NULL OR vote IN (-1, 1)",
            name="ck_message_feedback_vote",
        ),
    )
    op.create_index(
        "ix_message_feedback_message_id",
        "message_feedback",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_message_feedback_user_id",
        "message_feedback",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_message_feedback_user_id", table_name="message_feedback")
    op.drop_index("ix_message_feedback_message_id", table_name="message_feedback")
    op.drop_table("message_feedback")
