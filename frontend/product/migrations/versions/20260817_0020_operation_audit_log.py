"""Add system-level operation audit log (P1-4.1, aligned with ragent BizChangeLog).

Revision ID: 20260817_0020
Revises: 20260816_0019
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0020"
down_revision = "20260816_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operation_audit_logs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("biz_type", sa.String(length=64), nullable=False),
        sa.Column("biz_id", sa.String(length=64), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("action_desc", sa.String(length=512), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("change_diff", sa.JSON(), nullable=True),
        sa.Column("operator_id", sa.String(length=64), nullable=False),
        sa.Column("operator_name", sa.String(length=128), nullable=True),
        sa.Column("operator_role", sa.String(length=64), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_operation_audit_logs_biz_type",
        "operation_audit_logs",
        ["biz_type"],
        unique=False,
    )
    op.create_index(
        "ix_operation_audit_logs_biz_id",
        "operation_audit_logs",
        ["biz_id"],
        unique=False,
    )
    op.create_index(
        "ix_operation_audit_logs_operator_id",
        "operation_audit_logs",
        ["operator_id"],
        unique=False,
    )
    op.create_index(
        "ix_operation_audit_logs_created_at",
        "operation_audit_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_operation_audit_logs_created_at", table_name="operation_audit_logs")
    op.drop_index("ix_operation_audit_logs_operator_id", table_name="operation_audit_logs")
    op.drop_index("ix_operation_audit_logs_biz_id", table_name="operation_audit_logs")
    op.drop_index("ix_operation_audit_logs_biz_type", table_name="operation_audit_logs")
    op.drop_table("operation_audit_logs")
