from __future__ import annotations

from datetime import datetime, timezone
from random import uniform

from .models import FundingRateSnapshot, InstrumentContext, MarkPriceSnapshot, PositionMode


class CryptoFeatureService:
    """Domain service for crypto-only trading context."""

    def build_default_context(self, exchange: str, symbol: str) -> InstrumentContext:
        return InstrumentContext(
            exchange=exchange,
            symbol=symbol,
            leverage=1,
            margin_mode="cross",
            position_mode=PositionMode.one_way,
            is_24x7=True,
        )

    def mock_funding_rate(self, exchange: str, symbol: str) -> FundingRateSnapshot:
        return FundingRateSnapshot(
            exchange=exchange,
            symbol=symbol,
            funding_rate=round(uniform(-0.001, 0.001), 8),
            next_funding_time=datetime.now(timezone.utc),
        )

    def mock_mark_price(self, exchange: str, symbol: str, base: float = 100.0) -> MarkPriceSnapshot:
        mark = round(base * (1 + uniform(-0.01, 0.01)), 8)
        return MarkPriceSnapshot(
            exchange=exchange,
            symbol=symbol,
            mark_price=mark,
            index_price=round(base, 8),
            timestamp=datetime.now(timezone.utc),
        )

