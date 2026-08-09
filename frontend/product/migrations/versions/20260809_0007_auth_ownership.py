"""Add local users, sessions and workspace ownership.

Revision ID: 20260809_0007
Revises: 20260809_0006
Create Date: 2026-08-09
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0007"
down_revision: Union[str, Sequence[str], None] = "20260809_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_OWNER_ID = "usr_legacy_owner"
NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    now = datetime.now(timezone.utc)
    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("username", sa.String),
        sa.column("display_name", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("role", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        users,
        [
            {
                "id": LEGACY_OWNER_ID,
                "username": "__legacy__",
                "display_name": "待接管的旧数据",
                "password_hash": "disabled",
                "role": "system_pending",
                "is_active": False,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(40), nullable=True))
    op.execute(
        sa.text("UPDATE knowledge_bases SET owner_id = :owner_id").bindparams(
            owner_id=LEGACY_OWNER_ID
        )
    )
    with op.batch_alter_table("knowledge_bases", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.String(40), nullable=False)
        batch_op.drop_constraint("uq_knowledge_bases_name", type_="unique")
        batch_op.create_foreign_key(
            "fk_knowledge_bases_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_knowledge_bases_owner_id", ["owner_id"])
        batch_op.create_unique_constraint("uq_knowledge_bases_owner_name", ["owner_id", "name"])

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(40), nullable=True))
    op.execute(
        "UPDATE conversations SET owner_id = "
        "(SELECT owner_id FROM knowledge_bases "
        "WHERE knowledge_bases.id = conversations.knowledge_base_id)"
    )
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column("owner_id", existing_type=sa.String(40), nullable=False)
        batch_op.create_foreign_key(
            "fk_conversations_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_conversations_owner_id", ["owner_id"])


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_index("ix_conversations_owner_id")
        batch_op.drop_constraint("fk_conversations_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_constraint("uq_knowledge_bases_owner_name", type_="unique")
        batch_op.create_unique_constraint("uq_knowledge_bases_name", ["name"])
        batch_op.drop_index("ix_knowledge_bases_owner_id")
        batch_op.drop_constraint("fk_knowledge_bases_owner_id_users", type_="foreignkey")
        batch_op.drop_column("owner_id")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("users")
