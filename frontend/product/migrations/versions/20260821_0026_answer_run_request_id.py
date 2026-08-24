"""Persist the HTTP request identifier on durable answer runs.

Revision ID: 20260821_0026
Revises: 20260821_0025
"""

import sqlalchemy as sa
from alembic import op

revision = "20260821_0026"
down_revision = "20260821_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("answer_runs") as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.String(length=128), nullable=True))
        batch_op.create_index("ix_answer_runs_request_id", ["request_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("answer_runs") as batch_op:
        batch_op.drop_index("ix_answer_runs_request_id")
        batch_op.drop_column("request_id")
