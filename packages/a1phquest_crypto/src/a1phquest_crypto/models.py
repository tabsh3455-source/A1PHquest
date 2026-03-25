from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class PositionMode(str, Enum):
    one_way = "one_way"
    hedge = "hedge"


class FundingRateSnapshot(BaseModel):
    exchange: str
    symbol: str
    funding_rate: float
    next_funding_time: datetime


class MarkPriceSnapshot(BaseModel):
    exchange: str
    symbol: str
    mark_price: float
    index_price: float
    timestamp: datetime


class InstrumentContext(BaseModel):
    exchange: str
    symbol: str
    leverage: int = Field(ge=1, le=125)
    margin_mode: str = Field(pattern="^(isolated|cross)$")
    position_mode: PositionMode
    is_24x7: bool = True

