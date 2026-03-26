from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..schemas import (
    ComboGridDcaStrategyConfig,
    DcaStrategyConfig,
    FundingHedgeStrategyConfig,
    FuturesGridStrategyConfig,
    GridStrategyConfig,
    MarketMakingStrategyConfig,
    RebalanceStrategyConfig,
    SignalBotStrategyConfig,
    SpotPerpArbitrageStrategyConfig,
    StrategyTemplateField,
    StrategyTemplateFieldOption,
    StrategyTemplateResponse,
)


@dataclass(frozen=True, slots=True)
class StrategyTemplateSpec:
    template_key: str
    display_name: str
    category: str
    description: str
    execution_status: str
    market_scope: str
    risk_level: str
    runtime_strategy_type: str
    config_model: type[BaseModel]
    fields: list[StrategyTemplateField] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    is_featured: bool = True

    @property
    def live_supported(self) -> bool:
        return self.execution_status == "live_supported"


def _field(
    key: str,
    label: str,
    input_type: str,
    *,
    required: bool = True,
    description: str | None = None,
    default: Any = None,
    min: float | int | None = None,
    max: float | int | None = None,
    step: float | int | None = None,
    precision: int | None = None,
    options: list[tuple[str, str]] | None = None,
) -> StrategyTemplateField:
    return StrategyTemplateField(
        key=key,
        label=label,
        input_type=input_type,  # type: ignore[arg-type]
        required=required,
        description=description,
        default=default,
        min=min,
        max=max,
        step=step,
        precision=precision,
        options=[
            StrategyTemplateFieldOption(label=label_value, value=value)
            for label_value, value in (options or [])
        ],
    )


_COMMON_FIELDS = [
    _field("exchange_account_id", "Exchange Account", "select", description="Bind the strategy to one stored exchange account."),
    _field("symbol", "Symbol", "text", description="Trading pair or instrument ID."),
]


_TEMPLATES: dict[str, StrategyTemplateSpec] = {
    "spot_grid": StrategyTemplateSpec(
        template_key="spot_grid",
        display_name="Spot Grid",
        category="range",
        description="Classical spot grid for sideways markets with bounded volatility.",
        execution_status="live_supported",
        market_scope="spot",
        risk_level="medium",
        runtime_strategy_type="grid",
        config_model=GridStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("grid_count", "Grid Count", "number", min=2, max=1000, step=1, default=20),
            _field("grid_step_pct", "Grid Step %", "number", min=0.0001, max=100, step=0.1, precision=4, default=0.4),
            _field("base_order_size", "Base Order Size", "number", min=0.00000001, step=0.0001, precision=8, default=0.001),
            _field("max_grid_levels", "Max Active Levels", "number", required=False, min=2, max=100, step=1, default=40),
        ],
        tags=["3Commas", "Bitsgap", "Pionex"],
    ),
    "futures_grid": StrategyTemplateSpec(
        template_key="futures_grid",
        display_name="Futures Grid",
        category="range",
        description="Leveraged grid variant for perpetual markets with explicit direction bias.",
        execution_status="draft_only",
        market_scope="perp",
        risk_level="high",
        runtime_strategy_type="grid",
        config_model=FuturesGridStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("grid_count", "Grid Count", "number", min=2, max=1000, step=1, default=16),
            _field("grid_step_pct", "Grid Step %", "number", min=0.0001, max=100, step=0.1, precision=4, default=0.5),
            _field("base_order_size", "Base Order Size", "number", min=0.00000001, step=0.0001, precision=8, default=0.001),
            _field("leverage", "Leverage", "number", min=1, max=50, step=1, default=3),
            _field(
                "direction",
                "Direction Bias",
                "select",
                default="neutral",
                options=[("Neutral", "neutral"), ("Long", "long"), ("Short", "short")],
            ),
        ],
        tags=["Pionex", "Perp"],
    ),
    "dca": StrategyTemplateSpec(
        template_key="dca",
        display_name="DCA",
        category="trend",
        description="Periodic accumulation template for trending or long-horizon positioning.",
        execution_status="live_supported",
        market_scope="spot",
        risk_level="low",
        runtime_strategy_type="dca",
        config_model=DcaStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("cycle_seconds", "Cycle Seconds", "number", min=1, max=86400, step=60, default=300),
            _field("amount_per_cycle", "Amount Per Cycle", "number", min=0.00000001, step=0.1, precision=8, default=10),
            _field("price_offset_pct", "Price Offset %", "number", required=False, min=0, max=100, step=0.01, precision=3, default=0.15),
            _field("min_order_volume", "Min Order Volume", "number", required=False, min=0, step=0.0001, precision=8, default=0),
        ],
        tags=["3Commas", "Bitsgap", "Pionex"],
    ),
    "combo_grid_dca": StrategyTemplateSpec(
        template_key="combo_grid_dca",
        display_name="Combo Grid + DCA",
        category="trend",
        description="Hybrid template that accumulates on a schedule while maintaining a range ladder.",
        execution_status="live_supported",
        market_scope="spot",
        risk_level="medium",
        runtime_strategy_type="combo_grid_dca",
        config_model=ComboGridDcaStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("grid_count", "Grid Count", "number", min=2, max=1000, step=1, default=12),
            _field("grid_step_pct", "Grid Step %", "number", min=0.0001, max=100, step=0.1, precision=4, default=0.5),
            _field("base_order_size", "Grid Base Size", "number", min=0.00000001, step=0.0001, precision=8, default=0.001),
            _field("max_grid_levels", "Max Active Levels", "number", required=False, min=2, max=100, step=1, default=40),
            _field("cycle_seconds", "DCA Cycle Seconds", "number", min=1, max=86400, step=60, default=900),
            _field("amount_per_cycle", "DCA Amount", "number", min=0.00000001, step=0.1, precision=8, default=15),
            _field("price_offset_pct", "DCA Price Offset %", "number", required=False, min=0, max=100, step=0.01, precision=3, default=0.15),
            _field("min_order_volume", "Min DCA Volume", "number", required=False, min=0, step=0.0001, precision=8, default=0),
        ],
        tags=["Bitsgap", "COMBO"],
    ),
    "rebalance": StrategyTemplateSpec(
        template_key="rebalance",
        display_name="Rebalance",
        category="portfolio",
        description="Mean-reversion rebalancer that restores a target base-asset ratio once drift widens.",
        execution_status="draft_only",
        market_scope="spot",
        risk_level="low",
        runtime_strategy_type="custom",
        config_model=RebalanceStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("target_base_ratio", "Target Base Ratio", "number", min=0.01, max=0.99, step=0.01, precision=2, default=0.5),
            _field("rebalance_threshold_pct", "Rebalance Threshold %", "number", min=0.01, max=100, step=0.1, precision=2, default=2.5),
            _field("min_order_size", "Minimum Order Size", "number", min=0.00000001, step=0.1, precision=8, default=10),
        ],
        tags=["Pionex", "Portfolio"],
    ),
    "signal_bot": StrategyTemplateSpec(
        template_key="signal_bot",
        display_name="Signal Bot",
        category="signal",
        description="Template for webhook or external signal driven execution with bounded order sizing.",
        execution_status="draft_only",
        market_scope="multi",
        risk_level="medium",
        runtime_strategy_type="custom",
        config_model=SignalBotStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("signal_source", "Signal Source", "text", default="tradingview:webhook"),
            _field(
                "entry_side",
                "Entry Side",
                "select",
                default="both",
                options=[("Both", "both"), ("Buy Only", "buy"), ("Sell Only", "sell")],
            ),
            _field("order_size", "Order Size", "number", min=0.00000001, step=0.1, precision=8, default=10),
        ],
        tags=["3Commas", "Signal"],
    ),
    "spot_perp_arbitrage": StrategyTemplateSpec(
        template_key="spot_perp_arbitrage",
        display_name="Spot-Perp Arbitrage",
        category="arbitrage",
        description="Basis capture template for paired spot and perpetual hedging.",
        execution_status="draft_only",
        market_scope="multi",
        risk_level="medium",
        runtime_strategy_type="spot_future_arbitrage",
        config_model=SpotPerpArbitrageStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("basis_entry_threshold_pct", "Entry Basis %", "number", min=0.01, max=100, step=0.05, precision=2, default=0.5),
            _field("basis_exit_threshold_pct", "Exit Basis %", "number", min=0.01, max=100, step=0.05, precision=2, default=0.15),
            _field("hedge_notional", "Hedge Notional", "number", min=0.00000001, step=1, precision=4, default=100),
        ],
        tags=["Hummingbot", "Arbitrage"],
    ),
    "funding_hedge": StrategyTemplateSpec(
        template_key="funding_hedge",
        display_name="Funding Hedge",
        category="arbitrage",
        description="Funding-rate aware hedge template for carry capture on perpetual venues.",
        execution_status="draft_only",
        market_scope="perp",
        risk_level="medium",
        runtime_strategy_type="funding_arbitrage",
        config_model=FundingHedgeStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("funding_entry_threshold_pct", "Funding Entry %", "number", min=0.001, max=100, step=0.01, precision=3, default=0.05),
            _field("funding_exit_threshold_pct", "Funding Exit %", "number", min=0.001, max=100, step=0.01, precision=3, default=0.02),
            _field("hedge_notional", "Hedge Notional", "number", min=0.00000001, step=1, precision=4, default=100),
        ],
        tags=["Hummingbot", "Funding"],
    ),
    "market_making": StrategyTemplateSpec(
        template_key="market_making",
        display_name="Market Making",
        category="making",
        description="Symmetric quote ladder template with explicit spread and depth controls.",
        execution_status="draft_only",
        market_scope="spot",
        risk_level="high",
        runtime_strategy_type="custom",
        config_model=MarketMakingStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("spread_pct", "Spread %", "number", min=0.001, max=100, step=0.01, precision=3, default=0.2),
            _field("order_size", "Order Size", "number", min=0.00000001, step=0.1, precision=8, default=10),
            _field("levels", "Quote Levels", "number", min=1, max=20, step=1, default=3),
        ],
        tags=["Hummingbot", "Maker"],
    ),
    "custom": StrategyTemplateSpec(
        template_key="custom",
        display_name="Legacy Custom",
        category="legacy",
        description="Compatibility template for legacy custom strategies already stored in the database.",
        execution_status="draft_only",
        market_scope="multi",
        risk_level="high",
        runtime_strategy_type="custom",
        config_model=SignalBotStrategyConfig,
        fields=[
            *_COMMON_FIELDS,
            _field("signal_source", "Strategy Notes", "text", default="legacy"),
            _field("entry_side", "Default Side", "select", default="both", options=[("Both", "both"), ("Buy", "buy"), ("Sell", "sell")]),
            _field("order_size", "Reference Size", "number", min=0.00000001, step=0.1, precision=8, default=10),
        ],
        tags=["Legacy"],
        is_featured=False,
    ),
}


_LEGACY_ALIASES = {
    "grid": "spot_grid",
    "dca": "dca",
    "funding_arbitrage": "funding_hedge",
    "spot_future_arbitrage": "spot_perp_arbitrage",
    "custom": "custom",
}


def normalize_template_key(template_key: str) -> str:
    raw = str(template_key or "").strip().lower()
    if not raw:
        raise ValueError("template_key is required")
    return _LEGACY_ALIASES.get(raw, raw)


def get_strategy_template(template_key: str) -> StrategyTemplateSpec:
    normalized = normalize_template_key(template_key)
    try:
        return _TEMPLATES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown strategy template '{template_key}'") from exc


def list_strategy_templates(*, featured_only: bool = True) -> list[StrategyTemplateSpec]:
    templates = list(_TEMPLATES.values())
    if featured_only:
        templates = [item for item in templates if item.is_featured]
    return templates


def validate_strategy_template_config(template_key: str, config: dict[str, Any]) -> tuple[StrategyTemplateSpec, dict[str, Any]]:
    template = get_strategy_template(template_key)
    try:
        normalized = template.config_model.model_validate(config).model_dump()
    except ValidationError as exc:
        raise ValueError(exc.json()) from exc
    return template, normalized


def serialize_strategy_template(template: StrategyTemplateSpec) -> StrategyTemplateResponse:
    return StrategyTemplateResponse(
        template_key=template.template_key,
        display_name=template.display_name,
        category=template.category,
        description=template.description,
        execution_status=template.execution_status,  # type: ignore[arg-type]
        market_scope=template.market_scope,
        risk_level=template.risk_level,  # type: ignore[arg-type]
        runtime_strategy_type=template.runtime_strategy_type,
        fields=template.fields,
        tags=template.tags,
        is_featured=template.is_featured,
    )
