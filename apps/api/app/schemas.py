from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False


class StepUpRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class StepUpTokenResponse(BaseModel):
    step_up_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RegistrationStartResponse(BaseModel):
    registration_token: str
    otp_secret: str
    otpauth_uri: str
    qr_svg_data_url: str
    expires_at: datetime


class RegistrationCompleteRequest(BaseModel):
    registration_token: str = Field(min_length=16, max_length=255)
    otp_code: str = Field(min_length=6, max_length=6)


class TwoFactorEnrollmentStartResponse(BaseModel):
    otp_secret: str
    otpauth_uri: str
    qr_svg_data_url: str


class TwoFactorEnrollmentCompleteRequest(BaseModel):
    otp_code: str = Field(min_length=6, max_length=6)


class RecoveryCodesResponse(BaseModel):
    recovery_codes: list[str] = Field(default_factory=list, min_length=1)


class AuthFlowResponse(BaseModel):
    user: "UserResponse"
    csrf_token: str
    authenticated: bool = True
    enrollment_required: bool = False
    recovery_codes: list[str] = Field(default_factory=list)


class UserLoginRequest(BaseModel):
    username: str
    password: str
    otp_code: str | None = Field(default=None, min_length=6, max_length=6)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=64)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    role: Literal["user", "admin"]
    is_active: bool
    created_at: datetime


class AuthSessionResponse(BaseModel):
    authenticated: bool = True
    enrollment_required: bool = False
    user: UserResponse
    csrf_token: str


class TOTPSetupResponse(BaseModel):
    otp_secret: str
    otpauth_uri: str


class TOTPSetupRequest(BaseModel):
    current_code: str | None = Field(default=None, min_length=6, max_length=6)


class TOTPVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class ExchangeAccountCreateRequest(BaseModel):
    exchange: Literal["binance", "okx", "lighter"]
    account_alias: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    passphrase: str | None = None
    is_testnet: bool = False


class ExchangeAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange: str
    account_alias: str
    is_testnet: bool
    created_at: datetime


class ExchangeAccountValidateResponse(BaseModel):
    account_id: int
    exchange: str
    validated: bool
    message: str


class ExchangeAccountSyncResponse(BaseModel):
    account_id: int
    exchange: str
    balances_synced: int
    positions_synced: int
    orders_synced: int
    trades_synced: int
    message: str
    synced_at: datetime


class ExchangeConsistencyResponse(BaseModel):
    account_id: int
    checked_at: datetime
    consistent: bool
    total_orders: int
    total_trades: int
    trades_without_order_count: int
    orders_with_fill_but_no_trade_count: int
    trades_without_order_samples: list[str]
    orders_with_fill_but_no_trade_samples: list[str]


class LighterReconcileRecordResponse(BaseModel):
    id: int
    operation: str
    request_order_id: str
    symbol: str
    status: str
    resolved_order_id: str | None
    resolved_trade_id: str | None
    last_sync_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_sync_error: str | None = None
    last_sync_error_code: str | None = None
    sync_error_count: int = 0
    next_retry_at: datetime | None = None
    next_retry_after_seconds: int | None = None
    resolved_match_by: str | None = None
    resolved_match_value: str | None = None
    candidate_order_ids: list[str] = Field(default_factory=list)
    candidate_client_order_ids: list[str] = Field(default_factory=list)


class LighterReconcilePendingResponse(BaseModel):
    account_id: int
    expired_now: int
    expired_pruned_now: int = 0
    status_stats: dict[str, int]
    pending_oldest_age_seconds: int | None = None
    recent_failure_reasons: list[str] = Field(default_factory=list)
    failure_code_stats: dict[str, int] = Field(default_factory=dict)
    retry_due_count: int = 0
    retry_blocked_count: int = 0
    no_retry_hint_count: int = 0
    next_retry_at: datetime | None = None
    records: list[LighterReconcileRecordResponse]


class LighterReconcileRetryResponse(BaseModel):
    account_id: int
    success: bool
    message: str
    pending_before: int
    pending_after: int
    reconciled_now: int
    expired_now: int
    expired_pruned_now: int = 0
    retry_due_before: int = 0
    retry_blocked_before: int = 0
    no_retry_hint_before: int = 0
    balances_synced: int = 0
    positions_synced: int = 0
    orders_synced: int = 0
    trades_synced: int = 0
    synced_at: datetime


class AccountBalanceSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange_account_id: int
    exchange: str
    asset: str
    free: float
    locked: float
    total: float
    updated_at: datetime


class PositionSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange_account_id: int
    exchange: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    mark_price: float | None
    unrealized_pnl: float | None
    updated_at: datetime


class OrderSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange_account_id: int
    exchange: str
    symbol: str
    order_id: str
    client_order_id: str | None
    status: str
    side: str
    order_type: str
    price: float
    quantity: float
    filled_quantity: float
    avg_fill_price: float | None
    updated_at: datetime


class TradeFillSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exchange_account_id: int
    exchange: str
    symbol: str
    order_id: str
    trade_id: str
    side: str
    price: float
    quantity: float
    quote_quantity: float
    fee: float
    fee_asset: str | None
    is_maker: bool
    trade_time: datetime
    updated_at: datetime


class OrderCreateRequest(BaseModel):
    account_id: int
    symbol: str = Field(min_length=2, max_length=64)
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"]
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    reference_price: float | None = Field(default=None, gt=0)
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
    client_order_id: str | None = Field(default=None, min_length=1, max_length=128)
    reduce_only: bool = False
    td_mode: Literal["cash", "cross", "isolated"] = "cash"
    # Deprecated for live risk gating. Kept for backward compatibility and dry-run simulation.
    projected_daily_loss: float = Field(default=0, ge=0)
    projected_position_ratio: float = Field(default=0, ge=0)
    # Exchange-specific passthrough payload.
    # Lighter uses signed transaction fields (`tx_type`, `tx_info`) for /api/v1/sendTx.
    exchange_payload: dict[str, Any] | None = None


class OrderSubmitResponse(BaseModel):
    account_id: int
    exchange: str
    order_id: str
    status: str
    message: str
    synced_snapshot_id: int


class OrderCancelRequest(BaseModel):
    account_id: int
    symbol: str
    client_order_id: str | None = None
    # Exchange-specific passthrough payload.
    # Lighter cancel uses signed transaction fields (`tx_type`, `tx_info`).
    exchange_payload: dict[str, Any] | None = None


class OrderCancelResponse(BaseModel):
    account_id: int
    exchange: str
    order_id: str
    status: str
    message: str


class StrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    template_key: str | None = Field(default=None, min_length=1, max_length=64)
    strategy_type: Literal[
        "grid",
        "futures_grid",
        "dca",
        "combo_grid_dca",
        "funding_arbitrage",
        "spot_future_arbitrage",
        "custom",
    ] | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_template_key(self):
        if not self.template_key and self.strategy_type:
            self.template_key = self.strategy_type
        if not self.template_key:
            raise ValueError("template_key is required")
        return self


class StrategyUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    template_key: str | None = Field(default=None, min_length=1, max_length=64)
    strategy_type: Literal[
        "grid",
        "futures_grid",
        "dca",
        "combo_grid_dca",
        "funding_arbitrage",
        "spot_future_arbitrage",
        "custom",
    ] | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_template_key(self):
        if not self.template_key and self.strategy_type:
            self.template_key = self.strategy_type
        if not self.template_key:
            raise ValueError("template_key is required")
        return self


class StrategyResponse(BaseModel):
    id: int
    name: str
    template_key: str
    template_display_name: str
    category: str
    execution_status: str
    market_scope: str
    risk_level: str
    live_supported: bool = False
    strategy_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: str
    runtime_ref: str | None
    created_at: datetime
    updated_at: datetime


class StrategyRuntimeResponse(BaseModel):
    strategy_id: int
    runtime_ref: str | None
    status: str
    process_id: str | None
    started_at: datetime | None
    stopped_at: datetime | None
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    last_event_seq: int = 0
    order_submitted_count: int = 0
    order_update_count: int = 0
    trade_fill_count: int = 0
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeConsistencyResponse(BaseModel):
    strategy_id: int
    runtime_ref: str | None
    consistent: bool
    checked_at: datetime
    # List of fields that we require to be aligned between DB runtime row and supervisor.
    fields_checked: list[str]
    # Keyed by field name, value holds db/supervisor snapshots for quick troubleshooting.
    mismatches: dict[str, dict[str, str | None]]


class BaseStrategyConfig(BaseModel):
    exchange_account_id: int = Field(gt=0)
    symbol: str = Field(min_length=2, max_length=64)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()


class GridStrategyConfig(BaseStrategyConfig):
    grid_count: int = Field(ge=2, le=1000)
    grid_step_pct: float = Field(gt=0, le=100)
    base_order_size: float = Field(gt=0)
    max_grid_levels: int = Field(ge=2, le=100, default=40)


class DcaStrategyConfig(BaseStrategyConfig):
    cycle_seconds: int = Field(gt=0, le=86_400)
    amount_per_cycle: float = Field(gt=0)
    price_offset_pct: float = Field(ge=0, le=100, default=0.15)
    min_order_volume: float = Field(ge=0, default=0)


class FuturesGridStrategyConfig(GridStrategyConfig):
    leverage: int = Field(ge=1, le=50, default=3)
    direction: Literal["long", "short", "neutral"] = "neutral"


class ComboGridDcaStrategyConfig(BaseStrategyConfig):
    grid_count: int = Field(ge=2, le=1000)
    grid_step_pct: float = Field(gt=0, le=100)
    base_order_size: float = Field(gt=0)
    max_grid_levels: int = Field(ge=2, le=100, default=40)
    cycle_seconds: int = Field(gt=0, le=86_400)
    amount_per_cycle: float = Field(gt=0)
    price_offset_pct: float = Field(ge=0, le=100, default=0.15)
    min_order_volume: float = Field(ge=0, default=0)


class RebalanceStrategyConfig(BaseStrategyConfig):
    target_base_ratio: float = Field(gt=0, lt=1)
    rebalance_threshold_pct: float = Field(gt=0, le=100)
    min_order_size: float = Field(gt=0)


class SignalBotStrategyConfig(BaseStrategyConfig):
    signal_source: str = Field(min_length=2, max_length=128)
    entry_side: Literal["buy", "sell", "both"] = "both"
    order_size: float = Field(gt=0)


class SpotPerpArbitrageStrategyConfig(BaseStrategyConfig):
    basis_entry_threshold_pct: float = Field(gt=0, le=100)
    basis_exit_threshold_pct: float = Field(gt=0, le=100)
    hedge_notional: float = Field(gt=0)


class FundingHedgeStrategyConfig(BaseStrategyConfig):
    funding_entry_threshold_pct: float = Field(gt=0, le=100)
    funding_exit_threshold_pct: float = Field(gt=0, le=100)
    hedge_notional: float = Field(gt=0)


class MarketMakingStrategyConfig(BaseStrategyConfig):
    spread_pct: float = Field(gt=0, le=100)
    order_size: float = Field(gt=0)
    levels: int = Field(ge=1, le=20)


class ParametricStrategyConfig(BaseStrategyConfig):
    # Shared shape for non-live builtin strategies that still need account/symbol context.
    params: dict[str, Any] = Field(default_factory=dict)


class GenericStrategyConfig(BaseModel):
    # Non-live strategy types are validated as generic JSON objects.
    # We allow extra keys to preserve user-defined parameters.
    model_config = ConfigDict(extra="allow")
    params: dict[str, Any] = Field(default_factory=dict)


class RiskRuleUpsertRequest(BaseModel):
    max_order_notional: float = Field(ge=0)
    max_daily_loss: float = Field(ge=0)
    max_position_ratio: float = Field(gt=0, le=1)
    max_cancel_rate_per_minute: int = Field(gt=0)
    circuit_breaker_enabled: bool = True


class RiskRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    max_order_notional: float
    max_daily_loss: float
    max_position_ratio: float
    max_cancel_rate_per_minute: int
    circuit_breaker_enabled: bool
    created_at: datetime
    updated_at: datetime


class RiskDryRunCheckRequest(BaseModel):
    order_notional: float = Field(ge=0)
    projected_daily_loss: float = Field(ge=0)
    projected_position_ratio: float = Field(ge=0)


class RiskDryRunCheckResponse(BaseModel):
    allowed: bool
    reason: str
    realized_daily_loss: float = 0.0
    evaluated_daily_loss: float = 0.0


class EventReplayItem(BaseModel):
    type: str
    timestamp: str
    resource_id: str | None = None
    user_id: int
    event_seq: int
    payload: dict[str, Any] = Field(default_factory=dict)


class EventReplayResponse(BaseModel):
    events: list[EventReplayItem]
    next_after_seq: int | None


class MarketKlineItem(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class MarketKlineResponse(BaseModel):
    exchange_account_id: int
    exchange: str
    market_type: Literal["spot", "perp"] = "spot"
    symbol: str
    interval: str
    candles: list[MarketKlineItem] = Field(default_factory=list)


class PublicMarketKlineResponse(BaseModel):
    exchange: str
    market_type: Literal["spot", "perp"]
    symbol: str
    interval: str
    candles: list[MarketKlineItem] = Field(default_factory=list)


class PublicMarketSymbolItem(BaseModel):
    exchange: str
    market_type: Literal["spot", "perp"]
    symbol: str
    label: str
    is_default: bool = False


class PublicMarketSymbolsResponse(BaseModel):
    exchange: str
    market_type: Literal["spot", "perp"]
    symbols: list[PublicMarketSymbolItem] = Field(default_factory=list)


class StrategyTemplateFieldOption(BaseModel):
    label: str
    value: str


class StrategyTemplateField(BaseModel):
    key: str
    label: str
    input_type: Literal["text", "number", "select", "switch"]
    required: bool = True
    description: str | None = None
    default: Any = None
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None
    precision: int | None = None
    options: list[StrategyTemplateFieldOption] = Field(default_factory=list)


class StrategyTemplateResponse(BaseModel):
    template_key: str
    display_name: str
    category: str
    description: str
    execution_status: Literal["live_supported", "draft_only"]
    market_scope: str
    risk_level: Literal["low", "medium", "high"]
    runtime_strategy_type: str
    fields: list[StrategyTemplateField] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_featured: bool = False


class MarketDataConfigRequest(BaseModel):
    market_ws_reconnect_base_seconds: float = Field(ge=0.5, le=30)
    market_ws_reconnect_max_seconds: float = Field(ge=1, le=120)
    market_ws_idle_timeout_seconds: float = Field(ge=5, le=120)
    market_candle_cache_size: int = Field(ge=100, le=5000)
    market_rest_backfill_limit: int = Field(ge=10, le=2000)

    @model_validator(mode="after")
    def validate_relationships(self):
        if self.market_ws_reconnect_max_seconds < self.market_ws_reconnect_base_seconds:
            raise ValueError("Reconnect max seconds must be greater than or equal to reconnect base seconds")
        if self.market_rest_backfill_limit > self.market_candle_cache_size:
            raise ValueError("REST backfill limit cannot be larger than candle cache size")
        return self


class MarketDataConfigResponse(MarketDataConfigRequest):
    has_overrides: bool = False
    updated_at: datetime | None = None
    updated_by_user_id: int | None = None
    default_values: dict[str, float | int] = Field(default_factory=dict)


class AiProviderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=1, max_length=512)
    is_active: bool = True


class AiProviderUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider_type: Literal["openai_compatible"] = "openai_compatible"
    base_url: str = Field(min_length=1, max_length=255)
    model_name: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)
    is_active: bool = True


class AiProviderResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: str
    model_name: str
    is_active: bool
    has_api_key: bool = True
    created_at: datetime
    updated_at: datetime


AiAutopilotAllowedAction = Literal["activate_strategy", "stop_strategy", "create_strategy_version"]
_AI_AUTOPILOT_ALLOWED_ACTION_ORDER = {
    "activate_strategy": 0,
    "stop_strategy": 1,
    "create_strategy_version": 2,
}


class AiAutopilotPolicyBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider_id: int = Field(gt=0)
    exchange_account_id: int = Field(gt=0)
    symbol: str = Field(min_length=2, max_length=64)
    interval: Literal["1m", "5m", "15m", "1h"] = "5m"
    strategy_ids: list[int] = Field(min_length=1, max_length=50)
    allowed_actions: list[AiAutopilotAllowedAction] = Field(
        default_factory=lambda: ["activate_strategy", "stop_strategy", "create_strategy_version"],
        min_length=1,
        max_length=3,
    )
    execution_mode: Literal["dry_run", "auto"] = "dry_run"
    decision_interval_seconds: int = Field(ge=30, le=3600)
    minimum_confidence: float = Field(ge=0, le=1)
    max_actions_per_hour: int = Field(ge=1, le=120)
    custom_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("symbol")
    @classmethod
    def normalize_policy_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("strategy_ids")
    @classmethod
    def normalize_strategy_ids(cls, value: list[int]) -> list[int]:
        normalized = sorted({int(item) for item in value if int(item) > 0})
        if not normalized:
            raise ValueError("At least one strategy id is required")
        return normalized

    @field_validator("allowed_actions")
    @classmethod
    def normalize_allowed_actions(cls, value: list[AiAutopilotAllowedAction]) -> list[AiAutopilotAllowedAction]:
        normalized = sorted(
            {str(item) for item in value if str(item).strip()},
            key=lambda item: _AI_AUTOPILOT_ALLOWED_ACTION_ORDER.get(str(item), 99),
        )
        if not normalized:
            raise ValueError("At least one allowed AI action is required")
        return normalized


class AiAutopilotPolicyCreateRequest(AiAutopilotPolicyBase):
    status: Literal["disabled", "enabled"] = "disabled"


class AiAutopilotPolicyUpdateRequest(AiAutopilotPolicyBase):
    status: Literal["disabled", "enabled"] = "disabled"


class AiAutopilotPolicyResponse(BaseModel):
    id: int
    name: str
    provider_id: int
    provider_name: str
    exchange_account_id: int
    symbol: str
    interval: str
    strategy_ids: list[int] = Field(default_factory=list)
    allowed_actions: list[AiAutopilotAllowedAction] = Field(default_factory=list)
    execution_mode: str
    status: str
    decision_interval_seconds: int
    minimum_confidence: float
    max_actions_per_hour: int
    custom_prompt: str | None = None
    last_run_at: datetime | None = None
    last_decision_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AiAutopilotRunRequest(BaseModel):
    dry_run_override: bool | None = None


class AiAutopilotDecisionResponse(BaseModel):
    id: int
    policy_id: int
    provider_id: int
    exchange_account_id: int
    trigger_source: str
    status: str
    action: str
    target_strategy_id: int | None = None
    confidence: float
    rationale: str | None = None
    factors: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    execution_result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class OpsAlertItem(BaseModel):
    code: str
    severity: Literal["warning", "critical"]
    metric: str
    value: float
    threshold: float
    message: str


class OpsMetricsResponse(BaseModel):
    checked_at: datetime
    ws_connection_count: int
    ws_online_user_count: int
    strategy_runtime_counts: dict[str, int]
    strategy_process_count: int
    runtime_status_drift_count: int = 0
    lighter_reconcile_status_counts: dict[str, int] = Field(default_factory=dict)
    lighter_reconcile_retry_due_count: int = 0
    lighter_reconcile_retry_blocked_count: int = 0
    lighter_pending_oldest_age_seconds: int | None = None
    total_audit_events_last_hour: int
    failed_audit_events_last_hour: int
    failed_audit_event_rate_last_hour: float
    critical_audit_events_last_hour: int
    audit_action_counts_last_hour: dict[str, int] = Field(default_factory=dict)
    alert_items: list[OpsAlertItem] = Field(default_factory=list)


class OpsTopBacklogUser(BaseModel):
    user_id: int
    pending_count: int
    retry_due_count: int
    retry_blocked_count: int
    oldest_pending_age_seconds: int | None = None


class OpsErrorTrendPoint(BaseModel):
    bucket_start: datetime
    total_events: int
    failed_events: int
    critical_events: int


class OpsRuntimeDriftSample(BaseModel):
    user_id: int
    strategy_id: int
    strategy_name: str
    strategy_type: str
    strategy_status: str
    runtime_status: str
    runtime_ref: str | None = None
    process_id: str | None = None
    last_heartbeat: datetime | None = None
    last_error: str | None = None


class OpsFuturesGridRuntimeAudit(BaseModel):
    strategy_id: int
    strategy_name: str
    strategy_status: str
    runtime_status: str | None = None
    runtime_ref: str | None = None
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    direction: Literal["neutral", "long", "short"] | None = None
    leverage: float | None = None
    profile_event_seq: int | None = None
    profile_timestamp: datetime | None = None
    grid_seeded_event_seq: int | None = None
    grid_seeded_timestamp: datetime | None = None
    planned_order_count: int | None = None
    buy_levels: int | None = None
    sell_levels: int | None = None
    action_level: Literal["ok", "warning", "critical"] = "ok"
    audit_flags: list[str] = Field(default_factory=list)
    suggested_action: str = "No action needed."


class OpsFuturesGridAuditResponse(BaseModel):
    checked_at: datetime
    runtimes: list[OpsFuturesGridRuntimeAudit] = Field(default_factory=list)


class AdminOpsMetricsResponse(BaseModel):
    checked_at: datetime
    total_users: int
    active_users: int
    ws_connection_count: int
    ws_online_user_count: int
    strategy_runtime_counts: dict[str, int]
    strategy_process_count: int
    runtime_status_drift_count: int = 0
    lighter_reconcile_status_counts: dict[str, int] = Field(default_factory=dict)
    lighter_reconcile_retry_due_count: int = 0
    lighter_reconcile_retry_blocked_count: int = 0
    total_audit_events_last_hour: int
    failed_audit_events_last_hour: int
    failed_audit_event_rate_last_hour: float
    critical_audit_events_last_hour: int
    audit_action_counts_last_hour: dict[str, int] = Field(default_factory=dict)
    alert_items: list[OpsAlertItem] = Field(default_factory=list)
    top_lighter_pending_users: list[OpsTopBacklogUser] = Field(default_factory=list)
    error_trend_last_hour: list[OpsErrorTrendPoint] = Field(default_factory=list)
    runtime_drift_samples: list[OpsRuntimeDriftSample] = Field(default_factory=list)
