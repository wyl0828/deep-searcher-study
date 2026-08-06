"""Add bounded upload metadata and durable ingest job fields.

Revision ID: 20260801_0005
Revises: 20260801_0004
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260801_0005"
down_revision: Union[str, Sequence[str], None] = "20260801_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute("UPDATE ingest_jobs SET available_at = CURRENT_TIMESTAMP WHERE available_at IS NULL")
    op.add_column("ingest_jobs", sa.Column("lease_owner", sa.String(80)))
    op.add_column("ingest_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ingest_jobs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column("ingest_jobs", sa.Column("error_code", sa.String(64)))
    op.add_column("ingest_jobs", sa.Column("error_message", sa.String(300)))
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(64), primary_key=True),
        sa.Column("worker_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_column("ingest_jobs", "error_message")
    op.drop_column("ingest_jobs", "error_code")
    op.drop_column("ingest_jobs", "max_retries")
    op.drop_column("ingest_jobs", "retry_count")
    op.drop_column("ingest_jobs", "lease_expires_at")
    op.drop_column("ingest_jobs", "lease_owner")
    op.drop_column("ingest_jobs", "available_at")
    op.drop_column("documents", "page_count")
