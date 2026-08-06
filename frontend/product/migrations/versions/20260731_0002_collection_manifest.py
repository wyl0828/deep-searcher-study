"""Persist the active vector collection manifest.

Revision ID: 20260731_0002
Revises: 20260724_0001
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0002"
down_revision: Union[str, Sequence[str], None] = "20260724_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column("index_manifest", sa.Text(), nullable=True),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "index_previous_collection",
            sa.String(length=64),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("knowledge_bases", "index_previous_collection")
    op.drop_column("knowledge_bases", "index_manifest")
