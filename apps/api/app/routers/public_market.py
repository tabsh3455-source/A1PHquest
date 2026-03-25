from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..schemas import PublicMarketKlineResponse, PublicMarketSymbolsResponse
from ..services.market_data import (
    MarketDataError,
    MarketDataService,
    normalize_market_interval,
    normalize_market_symbol,
    normalize_market_type,
)
from ..services.public_market_catalog import list_public_market_symbols

router = APIRouter(prefix="/api/public/market", tags=["public-market"])


@router.get("/klines", response_model=PublicMarketKlineResponse)
async def get_public_market_klines(
    request: Request,
    exchange: str = Query(min_length=2, max_length=32),
    market_type: str = Query(default="spot"),
    symbol: str = Query(min_length=2, max_length=64),
    interval: str = Query(default="1m"),
    limit: int = Query(default=300, ge=1, le=1000),
):
    service: MarketDataService = request.app.state.market_data_service
    normalized_market_type = normalize_market_type(market_type)
    try:
        candles = await service.fetch_history(
            exchange=exchange,
            market_type=normalized_market_type,
            symbol=symbol,
            interval=interval,
            limit=limit,
            is_testnet=False,
        )
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return PublicMarketKlineResponse(
        exchange=exchange.lower(),
        market_type=normalized_market_type,  # type: ignore[arg-type]
        symbol=normalize_market_symbol(exchange, symbol, normalized_market_type),
        interval=normalize_market_interval(interval),
        candles=candles,
    )


@router.get("/symbols", response_model=PublicMarketSymbolsResponse)
def get_public_market_symbols(
    exchange: str = Query(min_length=2, max_length=32),
    market_type: str = Query(default="spot"),
):
    try:
        return list_public_market_symbols(exchange=exchange, market_type=market_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
