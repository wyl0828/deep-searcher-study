"""Add ingest pipeline steps (P2-B, aligned with ragent IngestionPipelineNodeDO).

Revision ID: 20260817_0022
Revises: 20260817_0021
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0022"
down_revision = "20260817_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingest_jobs", sa.Column("pipeline_steps", sa.JSON(), nullable=True))
    op.add_column(
        "ingest_jobs",
        sa.Column("pipeline_version", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingest_jobs", "pipeline_version")
    op.drop_column("ingest_jobs", "pipeline_steps")
