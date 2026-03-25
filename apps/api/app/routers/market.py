from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_verified_user
from ..models import ExchangeAccount, User
from ..schemas import MarketKlineResponse
from ..services.market_data import (
    MarketDataError,
    MarketDataService,
    normalize_market_interval,
    normalize_market_symbol,
    normalize_market_type,
)
from ..tenant import with_tenant

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/klines", response_model=MarketKlineResponse)
async def get_market_klines(
    request: Request,
    exchange_account_id: int = Query(gt=0),
    market_type: str = Query(default="spot"),
    symbol: str = Query(min_length=2, max_length=64),
    interval: str = Query(default="1m"),
    limit: int = Query(default=300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    account = (
        with_tenant(db.query(ExchangeAccount), ExchangeAccount, current_user.id)
        .filter(ExchangeAccount.id == exchange_account_id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=400, detail=f"exchange_account_id {exchange_account_id} not found")
    if account.exchange not in {"binance", "okx"}:
        raise HTTPException(
            status_code=400,
            detail=f"exchange '{account.exchange}' is not supported for market charts",
        )

    service: MarketDataService = request.app.state.market_data_service
    try:
        candles = await service.fetch_history(
            exchange=account.exchange,
            market_type=market_type,
            symbol=symbol,
            interval=interval,
            limit=limit,
            is_testnet=account.is_testnet,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return MarketKlineResponse(
        exchange_account_id=exchange_account_id,
        exchange=account.exchange,
        market_type=normalize_market_type(market_type),  # type: ignore[arg-type]
        symbol=normalize_market_symbol(account.exchange, symbol, market_type),
        interval=normalize_market_interval(interval),
        candles=candles,
    )
