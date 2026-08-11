"""Add auditable document and citation temporal metadata.

Revision ID: 20260811_0012
Revises: 20260811_0011
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0012"
down_revision = "20260811_0011"
branch_labels = None
depends_on = None


TEMPORAL_COLUMNS = (
    "published_at",
    "effective_at",
    "superseded_at",
)


def upgrade() -> None:
    for table_name in ("documents", "citations"):
        for column_name in TEMPORAL_COLUMNS:
            op.add_column(table_name, sa.Column(column_name, sa.Date()))
        op.add_column(
            table_name,
            sa.Column("temporal_metadata_source", sa.String(length=32)),
        )


def downgrade() -> None:
    for table_name in ("citations", "documents"):
        op.drop_column(table_name, "temporal_metadata_source")
        for column_name in reversed(TEMPORAL_COLUMNS):
            op.drop_column(table_name, column_name)
