"""Add external Web source fields to citations.

Revision ID: 20260801_0004
Revises: 20260731_0003
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0004"
down_revision: Union[str, Sequence[str], None] = "20260731_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "citations",
        sa.Column("source_type", sa.String(32), nullable=False, server_default="knowledge_base"),
    )
    op.add_column("citations", sa.Column("source_url", sa.String(2048)))
    op.add_column("citations", sa.Column("source_domain", sa.String(253)))
    op.add_column(
        "citations",
        sa.Column("trusted", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("citations", "trusted")
    op.drop_column("citations", "source_domain")
    op.drop_column("citations", "source_url")
    op.drop_column("citations", "source_type")
