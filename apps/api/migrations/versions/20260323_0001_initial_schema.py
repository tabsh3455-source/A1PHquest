"""initial schema baseline

Revision ID: 20260323_0001
Revises:
Create Date: 2026-03-23 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260323_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create the frozen baseline schema as it existed on 2026-03-23.

    This revision intentionally does not import current ORM metadata. Future
    tables/columns must be introduced only by later incremental migrations so
    fresh installs and upgraded installs follow the same schema history.
    """

    if not _table_exists("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("email", sa.String(length=128), nullable=False),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("totp_secret_encrypted", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_users_id", "users", ["id"])
    _create_index_if_missing("ix_users_username", "users", ["username"], unique=True)
    _create_index_if_missing("ix_users_email", "users", ["email"], unique=True)

    if not _table_exists("exchange_accounts"):
        op.create_table(
            "exchange_accounts",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=False),
            sa.Column("account_alias", sa.String(length=64), nullable=False),
            sa.Column("api_key_encrypted", sa.Text(), nullable=False),
            sa.Column("api_secret_encrypted", sa.Text(), nullable=False),
            sa.Column("passphrase_encrypted", sa.Text(), nullable=True),
            sa.Column("is_testnet", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_exchange_accounts_id", "exchange_accounts", ["id"])
    _create_index_if_missing("ix_exchange_accounts_user_id", "exchange_accounts", ["user_id"])

    if not _table_exists("strategies"):
        op.create_table(
            "strategies",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("strategy_type", sa.String(length=64), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="stopped"),
            sa.Column("runtime_ref", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_strategies_id", "strategies", ["id"])
    _create_index_if_missing("ix_strategies_user_id", "strategies", ["user_id"])

    if not _table_exists("strategy_runtimes"):
        op.create_table(
            "strategy_runtimes",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("strategy_id", sa.Integer(), sa.ForeignKey("strategies.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("process_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="stopped"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("stopped_at", sa.DateTime(), nullable=True),
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
        )
    _create_index_if_missing("ix_strategy_runtimes_id", "strategy_runtimes", ["id"])
    _create_index_if_missing("ix_strategy_runtimes_strategy_id", "strategy_runtimes", ["strategy_id"])
    _create_index_if_missing("ix_strategy_runtimes_user_id", "strategy_runtimes", ["user_id"])

    if not _table_exists("risk_rules"):
        op.create_table(
            "risk_rules",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("max_order_notional", sa.Numeric(18, 8), nullable=False, server_default="0"),
            sa.Column("max_daily_loss", sa.Numeric(18, 8), nullable=False, server_default="0"),
            sa.Column("max_position_ratio", sa.Numeric(8, 4), nullable=False, server_default="1"),
            sa.Column("max_cancel_rate_per_minute", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("circuit_breaker_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_risk_rules_id", "risk_rules", ["id"])
    _create_index_if_missing("ix_risk_rules_user_id", "risk_rules", ["user_id"], unique=True)

    if not _table_exists("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("resource", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=128), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _create_index_if_missing("ix_audit_events_id", "audit_events", ["id"])
    _create_index_if_missing("ix_audit_events_user_id", "audit_events", ["user_id"])

    if not _table_exists("account_balance_snapshots"):
        op.create_table(
            "account_balance_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=False),
            sa.Column("asset", sa.String(length=32), nullable=False),
            sa.Column("free", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("locked", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("total", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "exchange_account_id",
                "asset",
                name="uq_balance_exchange_account_asset",
            ),
        )
    _create_index_if_missing("ix_account_balance_snapshots_id", "account_balance_snapshots", ["id"])
    _create_index_if_missing("ix_account_balance_snapshots_user_id", "account_balance_snapshots", ["user_id"])
    _create_index_if_missing(
        "ix_account_balance_snapshots_exchange_account_id",
        "account_balance_snapshots",
        ["exchange_account_id"],
    )

    if not _table_exists("position_snapshots"):
        op.create_table(
            "position_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=False),
            sa.Column("symbol", sa.String(length=64), nullable=False),
            sa.Column("side", sa.String(length=16), nullable=False),
            sa.Column("quantity", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("entry_price", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("mark_price", sa.Numeric(28, 12), nullable=True),
            sa.Column("unrealized_pnl", sa.Numeric(28, 12), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "exchange_account_id",
                "symbol",
                "side",
                name="uq_position_exchange_account_symbol_side",
            ),
        )
    _create_index_if_missing("ix_position_snapshots_id", "position_snapshots", ["id"])
    _create_index_if_missing("ix_position_snapshots_user_id", "position_snapshots", ["user_id"])
    _create_index_if_missing(
        "ix_position_snapshots_exchange_account_id",
        "position_snapshots",
        ["exchange_account_id"],
    )

    if not _table_exists("order_snapshots"):
        op.create_table(
            "order_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=False),
            sa.Column("symbol", sa.String(length=64), nullable=False),
            sa.Column("order_id", sa.String(length=128), nullable=False),
            sa.Column("client_order_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("side", sa.String(length=16), nullable=False),
            sa.Column("order_type", sa.String(length=32), nullable=False),
            sa.Column("price", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("quantity", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("filled_quantity", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("avg_fill_price", sa.Numeric(28, 12), nullable=True),
            sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "exchange_account_id",
                "order_id",
                name="uq_order_exchange_account_order",
            ),
        )
    _create_index_if_missing("ix_order_snapshots_id", "order_snapshots", ["id"])
    _create_index_if_missing("ix_order_snapshots_user_id", "order_snapshots", ["user_id"])
    _create_index_if_missing(
        "ix_order_snapshots_exchange_account_id",
        "order_snapshots",
        ["exchange_account_id"],
    )

    if not _table_exists("trade_fill_snapshots"):
        op.create_table(
            "trade_fill_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
            sa.Column("exchange", sa.String(length=32), nullable=False),
            sa.Column("symbol", sa.String(length=64), nullable=False),
            sa.Column("order_id", sa.String(length=128), nullable=False),
            sa.Column("trade_id", sa.String(length=128), nullable=False),
            sa.Column("side", sa.String(length=16), nullable=False),
            sa.Column("price", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("quantity", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("quote_quantity", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("fee", sa.Numeric(28, 12), nullable=False, server_default="0"),
            sa.Column("fee_asset", sa.String(length=32), nullable=True),
            sa.Column("is_maker", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("trade_time", sa.DateTime(), nullable=False),
            sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "exchange_account_id",
                "symbol",
                "trade_id",
                name="uq_trade_exchange_account_symbol_trade",
            ),
        )
    _create_index_if_missing("ix_trade_fill_snapshots_id", "trade_fill_snapshots", ["id"])
    _create_index_if_missing("ix_trade_fill_snapshots_user_id", "trade_fill_snapshots", ["user_id"])
    _create_index_if_missing(
        "ix_trade_fill_snapshots_exchange_account_id",
        "trade_fill_snapshots",
        ["exchange_account_id"],
    )

    if not _table_exists("lighter_reconcile_records"):
        op.create_table(
            "lighter_reconcile_records",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("exchange_account_id", sa.Integer(), sa.ForeignKey("exchange_accounts.id"), nullable=False),
            sa.Column("operation", sa.String(length=16), nullable=False),
            sa.Column("request_order_id", sa.String(length=128), nullable=False),
            sa.Column("symbol", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("resolved_order_id", sa.String(length=128), nullable=True),
            sa.Column("resolved_trade_id", sa.String(length=128), nullable=True),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "exchange_account_id",
                "operation",
                "request_order_id",
                name="uq_lighter_reconcile_account_operation_request",
            ),
        )
    _create_index_if_missing("ix_lighter_reconcile_records_id", "lighter_reconcile_records", ["id"])
    _create_index_if_missing("ix_lighter_reconcile_records_user_id", "lighter_reconcile_records", ["user_id"])
    _create_index_if_missing(
        "ix_lighter_reconcile_records_exchange_account_id",
        "lighter_reconcile_records",
        ["exchange_account_id"],
    )


def downgrade() -> None:
    for index_name, table_name in [
        ("ix_lighter_reconcile_records_exchange_account_id", "lighter_reconcile_records"),
        ("ix_lighter_reconcile_records_user_id", "lighter_reconcile_records"),
        ("ix_lighter_reconcile_records_id", "lighter_reconcile_records"),
        ("ix_trade_fill_snapshots_exchange_account_id", "trade_fill_snapshots"),
        ("ix_trade_fill_snapshots_user_id", "trade_fill_snapshots"),
        ("ix_trade_fill_snapshots_id", "trade_fill_snapshots"),
        ("ix_order_snapshots_exchange_account_id", "order_snapshots"),
        ("ix_order_snapshots_user_id", "order_snapshots"),
        ("ix_order_snapshots_id", "order_snapshots"),
        ("ix_position_snapshots_exchange_account_id", "position_snapshots"),
        ("ix_position_snapshots_user_id", "position_snapshots"),
        ("ix_position_snapshots_id", "position_snapshots"),
        ("ix_account_balance_snapshots_exchange_account_id", "account_balance_snapshots"),
        ("ix_account_balance_snapshots_user_id", "account_balance_snapshots"),
        ("ix_account_balance_snapshots_id", "account_balance_snapshots"),
        ("ix_audit_events_user_id", "audit_events"),
        ("ix_audit_events_id", "audit_events"),
        ("ix_risk_rules_user_id", "risk_rules"),
        ("ix_risk_rules_id", "risk_rules"),
        ("ix_strategy_runtimes_user_id", "strategy_runtimes"),
        ("ix_strategy_runtimes_strategy_id", "strategy_runtimes"),
        ("ix_strategy_runtimes_id", "strategy_runtimes"),
        ("ix_strategies_user_id", "strategies"),
        ("ix_strategies_id", "strategies"),
        ("ix_exchange_accounts_user_id", "exchange_accounts"),
        ("ix_exchange_accounts_id", "exchange_accounts"),
        ("ix_users_email", "users"),
        ("ix_users_username", "users"),
        ("ix_users_id", "users"),
    ]:
        _drop_index_if_exists(index_name, table_name)

    for table_name in [
        "lighter_reconcile_records",
        "trade_fill_snapshots",
        "order_snapshots",
        "position_snapshots",
        "account_balance_snapshots",
        "audit_events",
        "risk_rules",
        "strategy_runtimes",
        "strategies",
        "exchange_accounts",
        "users",
    ]:
        if _table_exists(table_name):
            op.drop_table(table_name)


def _table_exists(table_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return table_name in inspector.get_table_names()


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], unique: bool = False) -> None:
    if not _table_exists(table_name):
        return
    inspector = sa.inspect(op.get_bind())
    index_names = {index.get("name") for index in inspector.get_indexes(table_name)}
    if index_name not in index_names:
        op.create_index(index_name, table_name, columns, unique=unique)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if not _table_exists(table_name):
        return
    inspector = sa.inspect(op.get_bind())
    index_names = {index.get("name") for index in inspector.get_indexes(table_name)}
    if index_name in index_names:
        op.drop_index(index_name, table_name=table_name)
