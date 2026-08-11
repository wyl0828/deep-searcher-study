"""Persist claim-level citation spans.

Revision ID: 20260811_0009
Revises: 20260810_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0009"
down_revision = "20260810_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "answer_claims",
        sa.Column("citation_spans", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("answer_claims", "citation_spans")
