"""Add explicit document version-family identity.

Revision ID: 20260811_0013
Revises: 20260811_0012
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0013"
down_revision = "20260811_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("documents", "citations"):
        op.add_column(table_name, sa.Column("version_family", sa.String(length=128)))
        op.add_column(table_name, sa.Column("version_family_source", sa.String(length=32)))
    op.create_index("ix_documents_version_family", "documents", ["version_family"])


def downgrade() -> None:
    op.drop_index("ix_documents_version_family", table_name="documents")
    for table_name in ("citations", "documents"):
        op.drop_column(table_name, "version_family_source")
        op.drop_column(table_name, "version_family")
