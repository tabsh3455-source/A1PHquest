"""add persistent websocket replay store

Revision ID: 20260324_0004
Revises: 20260324_0003
Create Date: 2026-03-24 14:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260324_0004"
down_revision = "20260324_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _table_exists("user_event_sequences"):
        op.create_table(
            "user_event_sequences",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True, nullable=False),
            sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    if not _table_exists("user_events"):
        op.create_table(
            "user_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("event_seq", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=128), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("dedupe_key", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "event_seq", name="uq_user_event_user_seq"),
            sa.UniqueConstraint("user_id", "dedupe_key", name="uq_user_event_user_dedupe"),
        )
        op.create_index("ix_user_events_user_id", "user_events", ["user_id"], unique=False)
        op.create_index("ix_user_events_created_at", "user_events", ["created_at"], unique=False)


def downgrade() -> None:
    if _table_exists("user_events"):
        _drop_index_if_exists("ix_user_events_created_at", "user_events")
        _drop_index_if_exists("ix_user_events_user_id", "user_events")
        op.drop_table("user_events")
    if _table_exists("user_event_sequences"):
        op.drop_table("user_event_sequences")


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    index_names = {index.get("name") for index in inspector.get_indexes(table_name)}
    if index_name in index_names:
        op.drop_index(index_name, table_name=table_name)
