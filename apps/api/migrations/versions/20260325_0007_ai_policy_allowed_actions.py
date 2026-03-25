"""add ai policy allowed actions

Revision ID: 20260325_0007
Revises: 20260325_0006
Create Date: 2026-03-25 17:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260325_0007"
down_revision = "20260325_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if _column_exists("ai_autopilot_policies", "allowed_actions_json"):
        return
    op.add_column(
        "ai_autopilot_policies",
        sa.Column(
            "allowed_actions_json",
            sa.Text(),
            nullable=False,
            server_default='["activate_strategy", "stop_strategy", "create_strategy_version"]',
        ),
    )


def downgrade() -> None:
    if _column_exists("ai_autopilot_policies", "allowed_actions_json"):
        op.drop_column("ai_autopilot_policies", "allowed_actions_json")


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    columns = {column.get("name") for column in inspector.get_columns(table_name)}
    return column_name in columns
