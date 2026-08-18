"""Add v0.6 local-directory connector sync (aligned with ragent schedule DOs).

Revision ID: 20260817_0023
Revises: 20260817_0022
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0023"
down_revision = "20260817_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) documents: connector identity + upload-only partial dedup index.
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("connector_sync_id", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("connector_source", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
        batch_op.drop_constraint("uq_documents_knowledge_base_sha256", type_="unique")
        batch_op.create_index("ix_documents_connector_sync_id", ["connector_sync_id"], unique=False)
        batch_op.create_index("ix_documents_external_id", ["external_id"], unique=False)
        batch_op.create_index("ix_documents_connector_source", ["connector_source"], unique=False)
        batch_op.create_unique_constraint(
            "uq_documents_connector_external",
            ["connector_sync_id", "external_id"],
        )
    op.create_index(
        "uq_documents_knowledge_base_sha256",
        "documents",
        ["knowledge_base_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("connector_sync_id IS NULL"),
        sqlite_where=sa.text("connector_sync_id IS NULL"),
    )

    # 2) ingest_jobs: source identity for connector-enqueued jobs.
    with op.batch_alter_table("ingest_jobs") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("source_metadata", sa.JSON(), nullable=True))

    # 3) schedule + run tables.
    op.create_table(
        "connector_syncs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lock_owner", sa.String(length=80), nullable=True),
        sa.Column("lock_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_connector_syncs_knowledge_base_id",
        "connector_syncs",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_index(
        "ix_connector_syncs_next_run_at",
        "connector_syncs",
        ["next_run_at"],
        unique=False,
    )

    op.create_table(
        "connector_sync_runs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("sync_id", sa.String(length=40), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("enqueue_failed", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sync_id"],
            ["connector_syncs.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_connector_sync_runs_sync_id",
        "connector_sync_runs",
        ["sync_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_connector_sync_runs_sync_id", table_name="connector_sync_runs")
    op.drop_table("connector_sync_runs")
    op.drop_index("ix_connector_syncs_next_run_at", table_name="connector_syncs")
    op.drop_index("ix_connector_syncs_knowledge_base_id", table_name="connector_syncs")
    op.drop_table("connector_syncs")

    with op.batch_alter_table("ingest_jobs") as batch_op:
        batch_op.drop_column("source_metadata")
        batch_op.drop_column("source")

    op.drop_index("uq_documents_knowledge_base_sha256", table_name="documents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("uq_documents_connector_external", type_="unique")
        batch_op.drop_index("ix_documents_connector_source")
        batch_op.drop_index("ix_documents_external_id")
        batch_op.drop_index("ix_documents_connector_sync_id")
        batch_op.drop_column("content_hash")
        batch_op.drop_column("connector_source")
        batch_op.drop_column("external_id")
        batch_op.drop_column("connector_sync_id")
        batch_op.create_unique_constraint(
            "uq_documents_knowledge_base_sha256",
            ["knowledge_base_id", "sha256"],
        )
