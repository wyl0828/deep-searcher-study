"""Persist versioned Trust Trace provenance identity.

Revision ID: 20260811_0011
Revises: 20260811_0010
"""

import sqlalchemy as sa
from alembic import op

revision = "20260811_0011"
down_revision = "20260811_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("provenance_contract_version", sa.Integer()))
    op.add_column("messages", sa.Column("provenance_digest", sa.String(length=71)))
    op.create_index(
        "ix_messages_provenance_digest",
        "messages",
        ["provenance_digest"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_provenance_digest", table_name="messages")
    op.drop_column("messages", "provenance_digest")
    op.drop_column("messages", "provenance_contract_version")
