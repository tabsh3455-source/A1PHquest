from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import SystemConfigEntry
from ..schemas import MarketDataConfigRequest, MarketDataConfigResponse

settings = get_settings()
MARKET_DATA_CONFIG_KEY = "market_data"


def build_default_market_data_config() -> dict[str, float | int]:
    return {
        "market_ws_reconnect_base_seconds": float(settings.market_ws_reconnect_base_seconds),
        "market_ws_reconnect_max_seconds": float(settings.market_ws_reconnect_max_seconds),
        "market_ws_idle_timeout_seconds": float(settings.market_ws_idle_timeout_seconds),
        "market_candle_cache_size": int(settings.market_candle_cache_size),
        "market_rest_backfill_limit": int(settings.market_rest_backfill_limit),
    }


def get_market_data_config_response(db: Session) -> MarketDataConfigResponse:
    defaults = build_default_market_data_config()
    entry = _get_system_config_entry(db, MARKET_DATA_CONFIG_KEY)
    if not entry:
        return MarketDataConfigResponse(
            **defaults,
            has_overrides=False,
            default_values=defaults,
        )

    raw_value = _safe_load_json(entry.value_json)
    normalized = MarketDataConfigRequest(**{**defaults, **raw_value})
    return MarketDataConfigResponse(
        **normalized.model_dump(),
        has_overrides=True,
        updated_at=entry.updated_at,
        updated_by_user_id=entry.updated_by_user_id,
        default_values=defaults,
    )


def get_market_data_config_values(db: Session) -> dict[str, float | int]:
    response = get_market_data_config_response(db)
    return {
        "market_ws_reconnect_base_seconds": response.market_ws_reconnect_base_seconds,
        "market_ws_reconnect_max_seconds": response.market_ws_reconnect_max_seconds,
        "market_ws_idle_timeout_seconds": response.market_ws_idle_timeout_seconds,
        "market_candle_cache_size": response.market_candle_cache_size,
        "market_rest_backfill_limit": response.market_rest_backfill_limit,
    }


def upsert_market_data_config(
    db: Session,
    *,
    payload: MarketDataConfigRequest,
    user_id: int,
) -> MarketDataConfigResponse:
    entry = _get_system_config_entry(db, MARKET_DATA_CONFIG_KEY)
    if not entry:
        entry = SystemConfigEntry(config_key=MARKET_DATA_CONFIG_KEY)

    entry.value_json = json.dumps(payload.model_dump(), ensure_ascii=False)
    entry.updated_by_user_id = user_id
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return get_market_data_config_response(db)


def reset_market_data_config(db: Session) -> MarketDataConfigResponse:
    entry = _get_system_config_entry(db, MARKET_DATA_CONFIG_KEY)
    if entry:
        db.delete(entry)
        db.commit()
    return get_market_data_config_response(db)


def _get_system_config_entry(db: Session, config_key: str) -> SystemConfigEntry | None:
    return db.query(SystemConfigEntry).filter(SystemConfigEntry.config_key == config_key).first()


def _safe_load_json(value_json: str) -> dict[str, Any]:
    try:
        payload = json.loads(value_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
