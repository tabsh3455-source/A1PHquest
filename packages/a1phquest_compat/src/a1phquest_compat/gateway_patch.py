from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GatewayCapability:
    exchange: str
    supports_spot: bool
    supports_futures: bool
    supports_options: bool
    supports_funding_rate: bool


SUPPORTED_GATEWAYS = {
    "binance": GatewayCapability(
        exchange="binance",
        supports_spot=True,
        supports_futures=True,
        supports_options=True,
        supports_funding_rate=True,
    ),
    "okx": GatewayCapability(
        exchange="okx",
        supports_spot=True,
        supports_futures=True,
        supports_options=True,
        supports_funding_rate=True,
    ),
    "lighter": GatewayCapability(
        exchange="lighter",
        supports_spot=True,
        supports_futures=True,
        supports_options=False,
        supports_funding_rate=False,
    ),
}
