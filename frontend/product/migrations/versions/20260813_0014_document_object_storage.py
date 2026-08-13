"""Add document object storage identity.

Revision ID: 20260813_0014
Revises: 20260811_0013
"""

import sqlalchemy as sa
from alembic import op

revision = "20260813_0014"
down_revision = "20260811_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("storage_type", sa.String(length=16), nullable=False, server_default="local"),
    )
    op.add_column("documents", sa.Column("storage_bucket", sa.String(length=255)))
    op.add_column("documents", sa.Column("storage_key", sa.Text()))
    op.execute("UPDATE documents SET storage_key = storage_path WHERE storage_key IS NULL")


def downgrade() -> None:
    op.drop_column("documents", "storage_key")
    op.drop_column("documents", "storage_bucket")
    op.drop_column("documents", "storage_type")
