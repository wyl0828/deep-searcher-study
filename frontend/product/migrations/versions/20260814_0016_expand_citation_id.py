"""Expand citation IDs for the citation UUID prefix.

Revision ID: 20260814_0016
Revises: 20260813_0015
"""

import sqlalchemy as sa
from alembic import op

revision = "20260814_0016"
down_revision = "20260813_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("citations") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.String(length=40),
            type_=sa.String(length=48),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("citations") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.String(length=48),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
