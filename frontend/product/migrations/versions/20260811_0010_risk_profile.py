"""Persist query risk profiles and claim-level risk checks.

Revision ID: 20260811_0010
Revises: 20260811_0009
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0010"
down_revision = "20260811_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("policy_profile", sa.String(length=32)))
    op.add_column("messages", sa.Column("risk_level", sa.String(length=16)))
    op.add_column("messages", sa.Column("query_type", sa.String(length=32)))
    op.add_column("messages", sa.Column("risk_factors", sa.JSON()))
    op.add_column(
        "answer_claims",
        sa.Column(
            "risk_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_assessed",
        ),
    )
    op.add_column(
        "answer_claims",
        sa.Column("risk_checks", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("answer_claims", "risk_checks")
    op.drop_column("answer_claims", "risk_status")
    op.drop_column("messages", "risk_factors")
    op.drop_column("messages", "query_type")
    op.drop_column("messages", "risk_level")
    op.drop_column("messages", "policy_profile")
