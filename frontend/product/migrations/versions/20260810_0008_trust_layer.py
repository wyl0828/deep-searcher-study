"""Add versioned Trust Layer decision fields.

Revision ID: 20260810_0008
Revises: 20260809_0007
"""

import sqlalchemy as sa
from alembic import op

revision = "20260810_0008"
down_revision = "20260809_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("trust_contract_version", sa.Integer()))
    op.add_column("messages", sa.Column("trust_status", sa.String(length=32)))
    op.add_column("messages", sa.Column("safety_status", sa.String(length=24)))
    op.add_column("messages", sa.Column("policy_action", sa.String(length=24)))
    op.add_column("messages", sa.Column("policy_reason_codes", sa.JSON()))
    op.add_column("messages", sa.Column("trust_details", sa.JSON()))
    op.add_column(
        "answer_claims",
        sa.Column(
            "structural_support_status",
            sa.String(length=32),
            nullable=False,
            server_default="unsupported",
        ),
    )
    op.add_column(
        "answer_claims",
        sa.Column(
            "citation_status",
            sa.String(length=24),
            nullable=False,
            server_default="missing",
        ),
    )
    op.add_column(
        "answer_claims",
        sa.Column(
            "entailment_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_checked",
        ),
    )
    op.add_column(
        "answer_claims",
        sa.Column(
            "consistency_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_checked",
        ),
    )
    op.add_column(
        "answer_claims",
        sa.Column("consistency_checks", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("answer_claims", sa.Column("confidence", sa.Float()))
    op.add_column(
        "answer_claims",
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("answer_claims", "reason_codes")
    op.drop_column("answer_claims", "confidence")
    op.drop_column("answer_claims", "consistency_checks")
    op.drop_column("answer_claims", "consistency_status")
    op.drop_column("answer_claims", "entailment_status")
    op.drop_column("answer_claims", "citation_status")
    op.drop_column("answer_claims", "structural_support_status")
    op.drop_column("messages", "policy_reason_codes")
    op.drop_column("messages", "trust_details")
    op.drop_column("messages", "policy_action")
    op.drop_column("messages", "safety_status")
    op.drop_column("messages", "trust_status")
    op.drop_column("messages", "trust_contract_version")
