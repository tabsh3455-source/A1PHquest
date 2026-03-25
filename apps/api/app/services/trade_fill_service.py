from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy.orm import Session

from ..models import ExchangeAccount, TradeFillSnapshot
from ..tenant import with_tenant


class TradeFillService:
    def upsert_fills(
        self,
        db: Session,
        *,
        user_id: int,
        account: ExchangeAccount,
        rows: list[dict[str, Any]],
    ) -> tuple[int, list[TradeFillSnapshot]]:
        existing = with_tenant(db.query(TradeFillSnapshot), TradeFillSnapshot, user_id).filter(
            TradeFillSnapshot.exchange_account_id == account.id
        ).all()
        existing_map = {(item.symbol, item.trade_id): item for item in existing}
        upserted: list[TradeFillSnapshot] = []

        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            trade_id = str(row.get("trade_id", ""))
            if not symbol or not trade_id:
                continue

            record = existing_map.get((symbol, trade_id))
            if not record:
                record = TradeFillSnapshot(
                    user_id=user_id,
                    exchange_account_id=account.id,
                    exchange=account.exchange,
                    symbol=symbol,
                    trade_id=trade_id,
                    order_id=str(row.get("order_id", "")),
                    side=str(row.get("side", "")),
                )
            existing_map[(symbol, trade_id)] = record

            record.symbol = symbol
            record.order_id = str(row.get("order_id", "")) or record.order_id
            record.side = str(row.get("side", "")).upper() or record.side
            record.price = _to_float(row.get("price"))
            record.quantity = _to_float(row.get("quantity"))
            record.quote_quantity = _to_float(row.get("quote_quantity"), record.price * record.quantity)
            record.fee = _to_float(row.get("fee"))
            record.fee_asset = str(row["fee_asset"]).upper() if row.get("fee_asset") else None
            record.is_maker = bool(row.get("is_maker", False))
            record.trade_time = _parse_trade_time(row.get("trade_time"))
            record.raw_json = json.dumps(row.get("raw", row), ensure_ascii=False)

            db.add(record)
            upserted.append(record)
        return len(upserted), upserted

    @staticmethod
    def to_event(fill: TradeFillSnapshot) -> dict[str, Any]:
        return {
            "id": fill.id,
            "exchange_account_id": fill.exchange_account_id,
            "exchange": fill.exchange,
            "symbol": fill.symbol,
            "order_id": fill.order_id,
            "trade_id": fill.trade_id,
            "side": fill.side,
            "price": float(fill.price),
            "quantity": float(fill.quantity),
            "quote_quantity": float(fill.quote_quantity),
            "fee": float(fill.fee),
            "fee_asset": fill.fee_asset,
            "is_maker": fill.is_maker,
            "trade_time": fill.trade_time.isoformat(),
            "updated_at": fill.updated_at.isoformat() if fill.updated_at else None,
        }


def _parse_trade_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)

    if isinstance(value, str) and value:
        stripped = value.strip()
        if stripped.isdigit():
            return _parse_trade_time(int(stripped))
        try:
            return datetime.fromisoformat(stripped.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass

    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
