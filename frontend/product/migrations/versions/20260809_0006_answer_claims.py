"""Add claim-level grounding records.

Revision ID: 20260809_0006
Revises: 20260801_0005
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0006"
down_revision: Union[str, Sequence[str], None] = "20260801_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "answer_claims",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(40),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("support_status", sa.String(32), nullable=False),
        sa.Column("citation_indices", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("message_id", "index", name="uq_answer_claims_message_index"),
    )
    op.create_index("ix_answer_claims_message_id", "answer_claims", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_answer_claims_message_id", table_name="answer_claims")
    op.drop_table("answer_claims")
