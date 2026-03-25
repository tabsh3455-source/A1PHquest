import json
from types import SimpleNamespace

from app.routers import ws as ws_router
from app.services.market_data import MarketStreamKey


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class _FakeMarketDataService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status_calls: list[tuple[int, MarketStreamKey]] = []

    async def subscribe(self, **kwargs):
        self.calls.append(("subscribe", kwargs))
        return MarketStreamKey(
            exchange=kwargs["exchange"],
            market_type=kwargs.get("market_type", "spot"),
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=bool(kwargs["is_testnet"]),
        )

    async def unsubscribe(self, **kwargs):
        self.calls.append(("unsubscribe", kwargs))
        return MarketStreamKey(
            exchange=kwargs["exchange"],
            market_type=kwargs.get("market_type", "spot"),
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=bool(kwargs["is_testnet"]),
        )

    async def send_subscription_status(self, user_id: int, key: MarketStreamKey):
        self.status_calls.append((user_id, key))


def test_handle_ws_message_subscribes_market_stream_and_returns_ack(async_runner, monkeypatch):
    websocket = _FakeWebSocket()
    market_data = _FakeMarketDataService()
    monkeypatch.setattr(
        ws_router,
        "_get_owned_exchange_account",
        lambda **kwargs: SimpleNamespace(id=9, exchange="binance", is_testnet=True),
    )

    async_runner(
        ws_router._handle_ws_message(
            websocket=websocket,
            user_id=5,
            connection_id=99,
            market_data=market_data,
            raw_message=json.dumps(
                {
                    "action": "subscribe_market",
                    "exchange_account_id": 9,
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                }
            ),
        )
    )

    assert market_data.calls[0][0] == "subscribe"
    assert websocket.messages[-1]["type"] == "market_subscription_ack"
    assert websocket.messages[-1]["symbol"] == "BTCUSDT"
    assert market_data.status_calls[0][0] == 5
    assert market_data.status_calls[0][1].symbol == "BTCUSDT"


def test_handle_ws_message_rejects_unknown_account(async_runner):
    websocket = _FakeWebSocket()
    market_data = _FakeMarketDataService()

    async_runner(
        ws_router._handle_ws_message(
            websocket=websocket,
            user_id=5,
            connection_id=99,
            market_data=market_data,
            raw_message=json.dumps(
                {
                    "action": "subscribe_market",
                    "exchange_account_id": 0,
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                }
            ),
        )
    )

    assert websocket.messages[-1]["type"] == "market_subscription_error"
    assert market_data.calls == []
