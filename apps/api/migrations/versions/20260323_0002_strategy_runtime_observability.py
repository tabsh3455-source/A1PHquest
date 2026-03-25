"""add strategy runtime observability fields

Revision ID: 20260323_0002
Revises: 20260323_0001
Create Date: 2026-03-23 20:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260323_0002"
down_revision = "20260323_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column_if_missing(
        "strategy_runtimes",
        sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "strategy_runtimes",
        sa.Column("last_audited_event_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "strategy_runtimes",
        sa.Column("order_submitted_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "strategy_runtimes",
        sa.Column("order_update_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        "strategy_runtimes",
        sa.Column("trade_fill_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    _drop_column_if_exists("strategy_runtimes", "trade_fill_count")
    _drop_column_if_exists("strategy_runtimes", "order_update_count")
    _drop_column_if_exists("strategy_runtimes", "order_submitted_count")
    _drop_column_if_exists("strategy_runtimes", "last_audited_event_seq")
    _drop_column_if_exists("strategy_runtimes", "last_event_seq")


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if _column_exists(table_name, str(column.name)):
        return
    op.add_column(table_name, column)


def _drop_column_if_exists(table_name: str, column_name: str) -> None:
    if not _column_exists(table_name, column_name):
        return
    op.drop_column(table_name, column_name)


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))
