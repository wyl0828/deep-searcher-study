"""Add page, section and layout locators to citations.

Revision ID: 20260731_0003
Revises: 20260731_0002
Create Date: 2026-07-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0003"
down_revision: Union[str, Sequence[str], None] = "20260731_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("section_title", sa.String(255)))
    op.add_column("citations", sa.Column("section_path", sa.JSON()))
    op.add_column("citations", sa.Column("char_start", sa.Integer()))
    op.add_column("citations", sa.Column("char_end", sa.Integer()))
    op.add_column("citations", sa.Column("bbox", sa.JSON()))
    op.add_column("citations", sa.Column("location_id", sa.String(64)))
    op.add_column("citations", sa.Column("source_locator", sa.String(128)))
    op.add_column("citations", sa.Column("parser_version", sa.String(128)))
    op.add_column("citations", sa.Column("extraction_method", sa.String(32)))


def downgrade() -> None:
    op.drop_column("citations", "extraction_method")
    op.drop_column("citations", "parser_version")
    op.drop_column("citations", "source_locator")
    op.drop_column("citations", "location_id")
    op.drop_column("citations", "bbox")
    op.drop_column("citations", "char_end")
    op.drop_column("citations", "char_start")
    op.drop_column("citations", "section_path")
    op.drop_column("citations", "section_title")
