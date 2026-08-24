"""Persist Product answer routing and orchestration facts.

Revision ID: 20260824_0027
Revises: 20260821_0026
"""

import sqlalchemy as sa
from alembic import op

revision = "20260824_0027"
down_revision = "20260821_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("answer_mode", sa.String(length=16), nullable=True))

    with op.batch_alter_table("answer_runs") as batch_op:
        batch_op.add_column(sa.Column("answer_mode", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("routing_decision", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("current_stage", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("failure_code", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("effective_risk_level", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("effective_risk_factors", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("answer_runs") as batch_op:
        batch_op.drop_column("effective_risk_factors")
        batch_op.drop_column("effective_risk_level")
        batch_op.drop_column("failure_code")
        batch_op.drop_column("current_stage")
        batch_op.drop_column("routing_decision")
        batch_op.drop_column("answer_mode")

    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("answer_mode")
