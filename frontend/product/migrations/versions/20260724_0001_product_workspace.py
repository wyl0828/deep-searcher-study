"""Create product workspace tables.

Revision ID: 20260724_0001
Revises:
Create Date: 2026-07-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("collection_name", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_name"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "sha256",
            name="uq_documents_knowledge_base_sha256",
        ),
    )
    op.create_index(
        op.f("ix_documents_knowledge_base_id"),
        "documents",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("document_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ingest_jobs_document_id"),
        "ingest_jobs",
        ["document_id"],
        unique=False,
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_knowledge_base_id"),
        "conversations",
        ["knowledge_base_id"],
        unique=False,
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("conversation_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("answer_state", sa.String(length=32), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_messages_conversation_id"),
        "messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("message_id", sa.String(length=40), nullable=False),
        sa.Column("document_id", sa.String(length=40), nullable=True),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("supported", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_citations_document_id"),
        "citations",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_citations_message_id"),
        "citations",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_citations_message_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_document_id"), table_name="citations")
    op.drop_table("citations")
    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_conversations_knowledge_base_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_index(op.f("ix_ingest_jobs_document_id"), table_name="ingest_jobs")
    op.drop_table("ingest_jobs")
    op.drop_index(op.f("ix_documents_knowledge_base_id"), table_name="documents")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
