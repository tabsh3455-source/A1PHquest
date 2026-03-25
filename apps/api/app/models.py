from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimeStampedMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )


class User(Base, TimeStampedMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Incremented on logout so previously issued JWTs and step-up tokens become invalid.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ExchangeAccount(Base, TimeStampedMixin):
    __tablename__ = "exchange_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    account_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    passphrase_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_testnet: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship("User")


class Strategy(Base, TimeStampedMixin):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="stopped")
    runtime_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped[User] = relationship("User")


class AiProviderCredential(Base, TimeStampedMixin):
    __tablename__ = "ai_provider_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship("User")


class AiAutopilotPolicy(Base, TimeStampedMixin):
    __tablename__ = "ai_autopilot_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("ai_provider_credentials.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    interval: Mapped[str] = mapped_column(String(16), nullable=False, default="5m")
    strategy_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allowed_actions_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default='["activate_strategy", "stop_strategy", "create_strategy_version"]',
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disabled")
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="dry_run")
    decision_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    minimum_confidence: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0.6)
    max_actions_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_decision_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User")
    provider: Mapped[AiProviderCredential] = relationship("AiProviderCredential")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")


class AiAutopilotDecisionRun(Base):
    __tablename__ = "ai_autopilot_decision_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    policy_id: Mapped[int] = mapped_column(ForeignKey("ai_autopilot_policies.id"), index=True, nullable=False)
    provider_id: Mapped[int] = mapped_column(ForeignKey("ai_provider_credentials.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    trigger_source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="dry_run")
    action: Mapped[str] = mapped_column(String(32), nullable=False, default="hold")
    target_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False, default=0)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    factors_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    raw_response_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    execution_result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)

    user: Mapped[User] = relationship("User")
    policy: Mapped[AiAutopilotPolicy] = relationship("AiAutopilotPolicy")
    provider: Mapped[AiProviderCredential] = relationship("AiProviderCredential")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")
    target_strategy: Mapped[Strategy | None] = relationship("Strategy")


class StrategyRuntime(Base):
    __tablename__ = "strategy_runtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    process_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="stopped")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Runtime event sequence watermark reported by worker-supervisor.
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Highest runtime event sequence that has been written to audit_events.
    last_audited_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Runtime execution counters synced from worker-supervisor observability.
    order_submitted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trade_fill_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    strategy: Mapped[Strategy] = relationship("Strategy")
    user: Mapped[User] = relationship("User")


class RiskRule(Base, TimeStampedMixin):
    __tablename__ = "risk_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)
    max_order_notional: Mapped[float] = mapped_column(Numeric(18, 8), default=0, nullable=False)
    max_daily_loss: Mapped[float] = mapped_column(Numeric(18, 8), default=0, nullable=False)
    max_position_ratio: Mapped[float] = mapped_column(Numeric(8, 4), default=1, nullable=False)
    max_cancel_rate_per_minute: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    circuit_breaker_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship("User")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    user: Mapped[User] = relationship("User")


class UserEventSequence(Base):
    __tablename__ = "user_event_sequences"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # Stores the last allocated user-scoped websocket event sequence so multiple
    # API instances can assign monotonic event_seq values without relying on
    # per-process memory.
    last_event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    user: Mapped[User] = relationship("User")


class UserEvent(Base):
    __tablename__ = "user_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_seq", name="uq_user_event_user_seq"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_user_event_user_dedupe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)

    user: Mapped[User] = relationship("User")


class SystemConfigEntry(Base, TimeStampedMixin):
    __tablename__ = "system_config_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    config_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    updated_by_user: Mapped[User | None] = relationship("User")


class AccountBalanceSnapshot(Base, TimeStampedMixin):
    __tablename__ = "account_balance_snapshots"
    __table_args__ = (UniqueConstraint("exchange_account_id", "asset", name="uq_balance_exchange_account_asset"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    free: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    locked: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)

    user: Mapped[User] = relationship("User")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")


class PositionSnapshot(Base, TimeStampedMixin):
    __tablename__ = "position_snapshots"
    __table_args__ = (
        UniqueConstraint("exchange_account_id", "symbol", "side", name="uq_position_exchange_account_symbol_side"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    entry_price: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    mark_price: Mapped[float | None] = mapped_column(Numeric(28, 12), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(28, 12), nullable=True)

    user: Mapped[User] = relationship("User")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")


class OrderSnapshot(Base, TimeStampedMixin):
    __tablename__ = "order_snapshots"
    __table_args__ = (UniqueConstraint("exchange_account_id", "order_id", name="uq_order_exchange_account_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    quantity: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    filled_quantity: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Numeric(28, 12), nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    user: Mapped[User] = relationship("User")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")


class TradeFillSnapshot(Base, TimeStampedMixin):
    __tablename__ = "trade_fill_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "exchange_account_id",
            "symbol",
            "trade_id",
            name="uq_trade_exchange_account_symbol_trade",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_id: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    quantity: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    quote_quantity: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    fee: Mapped[float] = mapped_column(Numeric(28, 12), nullable=False, default=0)
    fee_asset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trade_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    user: Mapped[User] = relationship("User")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")


class LighterReconcileRecord(Base, TimeStampedMixin):
    __tablename__ = "lighter_reconcile_records"
    __table_args__ = (
        UniqueConstraint(
            "exchange_account_id",
            "operation",
            "request_order_id",
            name="uq_lighter_reconcile_account_operation_request",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    exchange_account_id: Mapped[int] = mapped_column(
        ForeignKey("exchange_accounts.id"),
        index=True,
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    request_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    resolved_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_trade_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    user: Mapped[User] = relationship("User")
    exchange_account: Mapped[ExchangeAccount] = relationship("ExchangeAccount")
