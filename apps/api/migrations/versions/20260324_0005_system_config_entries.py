"""add system config entries store

Revision ID: 20260324_0005
Revises: 20260324_0004
Create Date: 2026-03-24 19:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260324_0005"
down_revision = "20260324_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if _table_exists("system_config_entries"):
        return

    op.create_table(
        "system_config_entries",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("config_key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("config_key", name="uq_system_config_entries_config_key"),
    )
    op.create_index(
        "ix_system_config_entries_config_key",
        "system_config_entries",
        ["config_key"],
        unique=True,
    )


def downgrade() -> None:
    if not _table_exists("system_config_entries"):
        return
    _drop_index_if_exists("ix_system_config_entries_config_key", "system_config_entries")
    op.drop_table("system_config_entries")


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    index_names = {index.get("name") for index in inspector.get_indexes(table_name)}
    if index_name in index_names:
        op.drop_index(index_name, table_name=table_name)
