from __future__ import annotations

from ..schemas import PublicMarketSymbolItem, PublicMarketSymbolsResponse
from .market_data import normalize_market_exchange

_CATALOG: dict[tuple[str, str], list[dict[str, object]]] = {
    ("binance", "spot"): [
        {"symbol": "BTCUSDT", "label": "BTC / USDT", "is_default": True},
        {"symbol": "ETHUSDT", "label": "ETH / USDT"},
        {"symbol": "SOLUSDT", "label": "SOL / USDT"},
        {"symbol": "BNBUSDT", "label": "BNB / USDT"},
        {"symbol": "XRPUSDT", "label": "XRP / USDT"},
    ],
    ("binance", "perp"): [
        {"symbol": "BTCUSDT", "label": "BTCUSDT Perp", "is_default": True},
        {"symbol": "ETHUSDT", "label": "ETHUSDT Perp"},
        {"symbol": "SOLUSDT", "label": "SOLUSDT Perp"},
        {"symbol": "XRPUSDT", "label": "XRPUSDT Perp"},
    ],
    ("okx", "spot"): [
        {"symbol": "BTC-USDT", "label": "BTC / USDT", "is_default": True},
        {"symbol": "ETH-USDT", "label": "ETH / USDT"},
        {"symbol": "SOL-USDT", "label": "SOL / USDT"},
        {"symbol": "TON-USDT", "label": "TON / USDT"},
    ],
    ("okx", "perp"): [
        {"symbol": "BTC-USDT-SWAP", "label": "BTC Perp", "is_default": True},
        {"symbol": "ETH-USDT-SWAP", "label": "ETH Perp"},
        {"symbol": "SOL-USDT-SWAP", "label": "SOL Perp"},
        {"symbol": "TON-USDT-SWAP", "label": "TON Perp"},
    ],
}


def list_public_market_symbols(*, exchange: str, market_type: str) -> PublicMarketSymbolsResponse:
    normalized_exchange = normalize_market_exchange(exchange)
    normalized_market_type = str(market_type or "").strip().lower() or "spot"
    if normalized_market_type not in {"spot", "perp"}:
        raise ValueError("market_type must be one of: spot, perp")

    items = [
        PublicMarketSymbolItem(
            exchange=normalized_exchange,
            market_type=normalized_market_type,  # type: ignore[arg-type]
            symbol=str(entry["symbol"]),
            label=str(entry["label"]),
            is_default=bool(entry.get("is_default")),
        )
        for entry in _CATALOG.get((normalized_exchange, normalized_market_type), [])
    ]
    return PublicMarketSymbolsResponse(
        exchange=normalized_exchange,
        market_type=normalized_market_type,  # type: ignore[arg-type]
        symbols=items,
    )
