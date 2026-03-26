import pytest
from app.services.strategy_templates import get_strategy_template, validate_strategy_template_config


def test_combo_grid_dca_template_is_live_supported():
    template = get_strategy_template("combo_grid_dca")
    assert template.live_supported is True
    assert template.runtime_strategy_type == "combo_grid_dca"
    assert template.execution_status == "live_supported"


def test_futures_grid_template_is_live_supported():
    template = get_strategy_template("futures_grid")
    assert template.live_supported is True
    assert template.runtime_strategy_type == "futures_grid"
    assert template.execution_status == "live_supported"


def test_futures_grid_config_validation_accepts_direction_and_leverage():
    _, config = validate_strategy_template_config(
        "futures_grid",
        {
            "exchange_account_id": 3,
            "symbol": "btcusdt",
            "grid_count": 12,
            "grid_step_pct": 0.6,
            "base_order_size": 0.002,
            "leverage": 5,
            "direction": "short",
        },
    )
    assert config["symbol"] == "BTCUSDT"
    assert config["leverage"] == 5
    assert config["direction"] == "short"


def test_futures_grid_config_validation_rejects_invalid_leverage():
    with pytest.raises(ValueError):
        validate_strategy_template_config(
            "futures_grid",
            {
                "exchange_account_id": 3,
                "symbol": "BTCUSDT",
                "grid_count": 12,
                "grid_step_pct": 0.6,
                "base_order_size": 0.002,
                "leverage": 0,
                "direction": "neutral",
            },
        )


def test_combo_grid_dca_config_validation_preserves_advanced_runtime_fields():
    template, config = validate_strategy_template_config(
        "combo_grid_dca",
        {
            "exchange_account_id": 7,
            "symbol": "btcusdt",
            "grid_count": 12,
            "grid_step_pct": 0.5,
            "base_order_size": 0.001,
            "max_grid_levels": 16,
            "cycle_seconds": 900,
            "amount_per_cycle": 25,
            "price_offset_pct": 0.2,
            "min_order_volume": 0.0003,
        },
    )
    assert template.template_key == "combo_grid_dca"
    assert config["symbol"] == "BTCUSDT"
    assert config["max_grid_levels"] == 16
    assert config["price_offset_pct"] == 0.2
    assert config["min_order_volume"] == 0.0003


def test_dca_template_validation_accepts_price_offset_and_min_order_volume():
    _, config = validate_strategy_template_config(
        "dca",
        {
            "exchange_account_id": 9,
            "symbol": "ethusdt",
            "cycle_seconds": 600,
            "amount_per_cycle": 15,
            "price_offset_pct": 0.1,
            "min_order_volume": 0.002,
        },
    )
    assert config["symbol"] == "ETHUSDT"
    assert config["price_offset_pct"] == 0.1
    assert config["min_order_volume"] == 0.002
