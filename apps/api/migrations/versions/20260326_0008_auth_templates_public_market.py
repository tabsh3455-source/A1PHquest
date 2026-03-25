"""add staged auth and strategy templates

Revision ID: 20260326_0008
Revises: 20260325_0007
Create Date: 2026-03-26 09:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260326_0008"
down_revision = "20260325_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not _column_exists("users", "pending_totp_secret_encrypted"):
        op.add_column("users", sa.Column("pending_totp_secret_encrypted", sa.Text(), nullable=True))

    if not _column_exists("strategies", "template_key"):
        op.add_column(
            "strategies",
            sa.Column("template_key", sa.String(length=64), nullable=False, server_default="custom"),
        )

    if not _table_exists("pending_registrations"):
        op.create_table(
            "pending_registrations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("email", sa.String(length=128), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("totp_secret_encrypted", sa.Text(), nullable=False),
            sa.Column("registration_token_hash", sa.String(length=128), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("username"),
            sa.UniqueConstraint("email"),
            sa.UniqueConstraint("registration_token_hash"),
        )
        op.create_index("ix_pending_registrations_expires_at", "pending_registrations", ["expires_at"])

    if not _table_exists("user_recovery_codes"):
        op.create_table(
            "user_recovery_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("code_hash", sa.String(length=255), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "code_hash", name="uq_user_recovery_code_hash"),
        )
        op.create_index("ix_user_recovery_codes_user_id", "user_recovery_codes", ["user_id"])

    op.execute(
        sa.text(
            """
            UPDATE strategies
            SET template_key = CASE strategy_type
                WHEN 'grid' THEN 'spot_grid'
                WHEN 'dca' THEN 'dca'
                WHEN 'funding_arbitrage' THEN 'funding_hedge'
                WHEN 'spot_future_arbitrage' THEN 'spot_perp_arbitrage'
                WHEN 'custom' THEN 'custom'
                ELSE COALESCE(template_key, strategy_type)
            END
            """
        )
    )


def downgrade() -> None:
    if _table_exists("user_recovery_codes"):
        op.drop_index("ix_user_recovery_codes_user_id", table_name="user_recovery_codes")
        op.drop_table("user_recovery_codes")
    if _table_exists("pending_registrations"):
        op.drop_index("ix_pending_registrations_expires_at", table_name="pending_registrations")
        op.drop_table("pending_registrations")
    if _column_exists("strategies", "template_key"):
        op.drop_column("strategies", "template_key")
    if _column_exists("users", "pending_totp_secret_encrypted"):
        op.drop_column("users", "pending_totp_secret_encrypted")


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    columns = {column.get("name") for column in inspector.get_columns(table_name)}
    return column_name in columns
