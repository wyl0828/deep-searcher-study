"""Add the single department/company/direct knowledge access model.

The legacy workspace and group tables are intentionally left in place in this
additive migration.  They are no longer consulted by the runtime resolver;
their physical removal belongs to the later local cleanup migration once the
remaining structural foreign keys have been removed.

Revision ID: 20260821_0025
Revises: 20260820_0024
"""

import sqlalchemy as sa
from alembic import op

revision = "20260821_0025"
down_revision = "20260820_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_departments_name"),
    )

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("department_id", sa.String(length=40), nullable=True))
        batch_op.create_index("ix_users_department_id", ["department_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_users_department_id",
            "departments",
            ["department_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_company_wide",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "department_knowledge_bases",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("department_id", sa.String(length=40), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "knowledge_base_id",
            name="uq_department_knowledge_bases_department_kb",
        ),
    )
    op.create_index(
        "ix_department_knowledge_bases_department_id",
        "department_knowledge_bases",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_department_knowledge_bases_knowledge_base_id",
        "department_knowledge_bases",
        ["knowledge_base_id"],
        unique=False,
    )

    op.create_table(
        "user_knowledge_base_access",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "knowledge_base_id",
            name="uq_user_knowledge_base_access_user_kb",
        ),
    )
    op.create_index(
        "ix_user_knowledge_base_access_user_id",
        "user_knowledge_base_access",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_knowledge_base_access_knowledge_base_id",
        "user_knowledge_base_access",
        ["knowledge_base_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_knowledge_base_access_knowledge_base_id",
        table_name="user_knowledge_base_access",
    )
    op.drop_index(
        "ix_user_knowledge_base_access_user_id",
        table_name="user_knowledge_base_access",
    )
    op.drop_table("user_knowledge_base_access")
    op.drop_index(
        "ix_department_knowledge_bases_knowledge_base_id",
        table_name="department_knowledge_bases",
    )
    op.drop_index(
        "ix_department_knowledge_bases_department_id",
        table_name="department_knowledge_bases",
    )
    op.drop_table("department_knowledge_bases")
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_column("is_company_wide")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_department_id", type_="foreignkey")
        batch_op.drop_index("ix_users_department_id")
        batch_op.drop_column("department_id")
    op.drop_table("departments")
