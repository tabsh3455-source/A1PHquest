from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..deps import require_admin, require_admin_step_up_user
from ..models import User
from ..schemas import MarketDataConfigRequest, MarketDataConfigResponse
from ..services.market_data import MarketDataService
from ..services.system_config import (
    get_market_data_config_response,
    reset_market_data_config,
    upsert_market_data_config,
)

router = APIRouter(prefix="/api/system-config", tags=["system-config"])


@router.get("/market-data", response_model=MarketDataConfigResponse)
def get_market_data_config(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    del current_user
    return get_market_data_config_response(db)


@router.put("/market-data", response_model=MarketDataConfigResponse)
async def update_market_data_config(
    payload: MarketDataConfigRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_step_up_user),
):
    response = upsert_market_data_config(db, payload=payload, user_id=current_user.id)
    market_data_service: MarketDataService = request.app.state.market_data_service
    await market_data_service.apply_runtime_config(payload.model_dump())
    log_audit_event(
        db,
        user_id=current_user.id,
        action="market_data_config_update",
        resource="system_config",
        resource_id="market_data",
        details=payload.model_dump(),
    )
    return response


@router.delete("/market-data", response_model=MarketDataConfigResponse)
async def reset_market_data_config_route(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_step_up_user),
):
    response = reset_market_data_config(db)
    market_data_service: MarketDataService = request.app.state.market_data_service
    await market_data_service.apply_runtime_config(
        {
            "market_ws_reconnect_base_seconds": response.market_ws_reconnect_base_seconds,
            "market_ws_reconnect_max_seconds": response.market_ws_reconnect_max_seconds,
            "market_ws_idle_timeout_seconds": response.market_ws_idle_timeout_seconds,
            "market_candle_cache_size": response.market_candle_cache_size,
            "market_rest_backfill_limit": response.market_rest_backfill_limit,
        }
    )
    log_audit_event(
        db,
        user_id=current_user.id,
        action="market_data_config_reset",
        resource="system_config",
        resource_id="market_data",
        details={"reset_to_defaults": True},
    )
    return response
