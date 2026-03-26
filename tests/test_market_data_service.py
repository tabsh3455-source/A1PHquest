from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, User
from app.routers.market import get_market_klines
from app.services.market_data import (
    BinanceStreamManager,
    MarketStreamKey,
    MarketDataService,
    OkxStreamManager,
    TradeTick,
)


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _create_account(db: Session, user_id: int, exchange: str = "binance") -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange=exchange,
        account_alias=f"{exchange}-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


class _FakeWsManager:
    def __init__(self) -> None:
        self.events: list[tuple[int, dict]] = []

    async def push_to_user(self, user_id: int, event: dict) -> None:
        self.events.append((user_id, event))


class _FakeMarketClient:
    def __init__(self, history_by_interval: dict[str, list[dict]] | None = None) -> None:
        self.history_by_interval = history_by_interval or {}
        self.history_requests: list[dict] = []

    async def fetch_history(self, **kwargs):
        self.history_requests.append(kwargs)
        interval = str(kwargs["interval"])
        candles = self.history_by_interval.get(interval, [])
        return [dict(item) for item in candles]


class _FakeMarketService:
    async def fetch_history(self, **kwargs):
        assert kwargs["exchange"] == "binance"
        return [
            {"time": 1_710_000_000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 12.0},
            {"time": 1_710_000_060, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.2, "volume": 9.0},
        ]


async def _noop_trade_tick(_: TradeTick) -> None:
    return None


async def _noop_status(*_args) -> None:
    return None


def test_binance_stream_manager_normalizes_trade_message():
    manager = BinanceStreamManager(
        market_type="spot",
        is_testnet=True,
        idle_timeout_seconds=25,
        reconnect_base_seconds=1,
        reconnect_max_seconds=15,
        on_trade_tick=_noop_trade_tick,
        on_status_change=_noop_status,
    )

    ticks = manager._extract_trade_ticks(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "p": "101.25",
            "q": "0.42",
            "T": 1_710_000_123_456,
        }
    )

    assert len(ticks) == 1
    assert ticks[0].exchange == "binance"
    assert ticks[0].symbol == "BTCUSDT"
    assert ticks[0].price == pytest.approx(101.25)
    assert ticks[0].size == pytest.approx(0.42)
    assert ticks[0].is_testnet is True


def test_okx_stream_manager_normalizes_trade_message():
    manager = OkxStreamManager(
        market_type="spot",
        is_testnet=False,
        idle_timeout_seconds=25,
        reconnect_base_seconds=1,
        reconnect_max_seconds=15,
        on_trade_tick=_noop_trade_tick,
        on_status_change=_noop_status,
    )

    ticks = manager._extract_trade_ticks(
        {
            "arg": {"channel": "trades", "instId": "BTC-USDT"},
            "data": [
                {
                    "instId": "BTC-USDT",
                    "px": "43000.2",
                    "sz": "0.010",
                    "ts": "1710000123456",
                }
            ],
        }
    )

    assert len(ticks) == 1
    assert ticks[0].exchange == "okx"
    assert ticks[0].symbol == "BTC-USDT"
    assert ticks[0].price == pytest.approx(43000.2)
    assert ticks[0].size == pytest.approx(0.01)
    assert ticks[0].is_testnet is False


def test_market_data_service_rolls_candles_and_pushes_closed_and_live_updates(async_runner):
    ws_manager = _FakeWsManager()
    service = MarketDataService(
        ws_manager=ws_manager,
        market_client=_FakeMarketClient(),
        reconnect_base_seconds=1,
        reconnect_max_seconds=15,
        idle_timeout_seconds=25,
        cache_size=32,
        rest_backfill_limit=16,
    )

    async_runner(
        service.subscribe(
            user_id=11,
            connection_id=22,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=True,
        )
    )

    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=100.0,
                size=0.5,
                ts_ms=1_710_000_000_000,
                is_testnet=True,
            )
        )
    )
    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=101.5,
                size=0.25,
                ts_ms=1_710_000_030_000,
                is_testnet=True,
            )
        )
    )
    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=99.25,
                size=0.1,
                ts_ms=1_710_000_060_000,
                is_testnet=True,
            )
        )
    )

    candle_events = [event for _, event in ws_manager.events if event["type"] == "market_candle"]
    assert len(candle_events) == 4
    assert candle_events[0]["payload"]["candle"]["close"] == pytest.approx(100.0)
    assert candle_events[1]["payload"]["candle"]["close"] == pytest.approx(101.5)
    assert candle_events[2]["payload"]["is_closed"] is True
    assert candle_events[2]["payload"]["candle"]["close"] == pytest.approx(101.5)
    assert candle_events[3]["payload"]["is_closed"] is False
    assert candle_events[3]["payload"]["candle"]["open"] == pytest.approx(99.25)


def test_market_data_service_pushes_candle_updates_to_public_subscribers(async_runner):
    ws_manager = _FakeWsManager()
    service = MarketDataService(
        ws_manager=ws_manager,
        market_client=_FakeMarketClient(),
        reconnect_base_seconds=1,
        reconnect_max_seconds=15,
        idle_timeout_seconds=25,
        cache_size=32,
        rest_backfill_limit=16,
    )

    public_events: list[dict] = []

    async def _public_sender(payload: dict) -> None:
        public_events.append(payload)

    async_runner(service.register_public_connection(9001, _public_sender))
    async_runner(
        service.subscribe_public(
            connection_id=9001,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=False,
        )
    )

    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=100.0,
                size=0.5,
                ts_ms=1_710_000_000_000,
                is_testnet=False,
            )
        )
    )
    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=101.0,
                size=0.3,
                ts_ms=1_710_000_020_000,
                is_testnet=False,
            )
        )
    )

    candle_events = [event for event in public_events if event.get("type") == "market_candle"]
    assert len(candle_events) >= 2
    assert candle_events[-1]["payload"]["symbol"] == "BTCUSDT"
    assert candle_events[-1]["payload"]["interval"] == "1m"
    assert candle_events[-1]["payload"]["candle"]["close"] == pytest.approx(101.0)


def test_fetch_history_uses_warm_cache_before_rest_backfill(async_runner):
    market_client = _FakeMarketClient()
    service = MarketDataService(
        ws_manager=_FakeWsManager(),
        market_client=market_client,
        cache_size=32,
        rest_backfill_limit=16,
    )

    async_runner(
        service.subscribe(
            user_id=1,
            connection_id=2,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=True,
        )
    )
    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=100.0,
                size=0.5,
                ts_ms=1_710_000_000_000,
                is_testnet=True,
            )
        )
    )

    candles = async_runner(
        service.fetch_history(
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            limit=1,
            is_testnet=True,
        )
    )

    assert len(candles) == 1
    assert candles[0]["close"] == pytest.approx(100.0)
    assert market_client.history_requests == []


def test_fetch_history_backfills_cold_cache(async_runner):
    market_client = _FakeMarketClient(
        history_by_interval={
            "1m": [
                {"time": 1_710_000_000, "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0, "volume": 10.0},
                {"time": 1_710_000_060, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 11.0},
            ]
        }
    )
    service = MarketDataService(
        ws_manager=_FakeWsManager(),
        market_client=market_client,
        cache_size=32,
        rest_backfill_limit=16,
    )

    candles = async_runner(
        service.fetch_history(
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            limit=2,
            is_testnet=True,
        )
    )

    assert len(candles) == 2
    assert candles[-1]["close"] == pytest.approx(101.0)
    assert len(market_client.history_requests) == 1

    cached = async_runner(
        service.fetch_history(
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            limit=2,
            is_testnet=True,
        )
    )
    assert len(cached) == 2
    assert len(market_client.history_requests) == 1


def test_market_data_service_pushes_stream_status_changes(async_runner):
    ws_manager = _FakeWsManager()
    service = MarketDataService(
        ws_manager=ws_manager,
        market_client=_FakeMarketClient(),
        cache_size=32,
        rest_backfill_limit=16,
    )
    key = async_runner(
        service.subscribe(
            user_id=7,
            connection_id=8,
            exchange="okx",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=False,
        )
    )

    async_runner(service.update_symbol_status(key.symbol_key, "stale"))
    async_runner(service.update_symbol_status(key.symbol_key, "reconnecting"))

    status_events = [event for _, event in ws_manager.events if event["type"] == "market_stream_status"]
    assert [event["payload"]["status"] for event in status_events] == ["stale", "reconnecting"]
    assert all(event["payload"]["symbol"] == "BTC-USDT" for event in status_events)


def test_market_data_service_unsubscribe_connection_releases_stream(async_runner):
    service = MarketDataService(
        ws_manager=_FakeWsManager(),
        market_client=_FakeMarketClient(),
        cache_size=32,
        rest_backfill_limit=16,
    )

    key = async_runner(
        service.subscribe(
            user_id=7,
            connection_id=8,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="5m",
            is_testnet=True,
        )
    )
    assert key in service._stream_subscribers

    async_runner(service.unsubscribe_connection(7, 8))
    assert key not in service._stream_subscribers


def test_market_data_service_apply_runtime_config_updates_runtime_values(async_runner):
    service = MarketDataService(
        ws_manager=_FakeWsManager(),
        market_client=_FakeMarketClient(),
        cache_size=64,
        rest_backfill_limit=32,
    )

    async_runner(
        service.subscribe(
            user_id=1,
            connection_id=2,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            interval="1m",
            is_testnet=True,
        )
    )
    async_runner(
        service.ingest_trade_tick(
            TradeTick(
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                price=100.0,
                size=0.1,
                ts_ms=1_710_000_000_000,
                is_testnet=True,
            )
        )
    )

    updated = async_runner(
        service.apply_runtime_config(
            {
                "market_ws_reconnect_base_seconds": 2,
                "market_ws_reconnect_max_seconds": 8,
                "market_ws_idle_timeout_seconds": 12,
                "market_candle_cache_size": 10,
                "market_rest_backfill_limit": 8,
            }
        )
    )

    assert updated["market_ws_reconnect_base_seconds"] == pytest.approx(2.0)
    assert service.current_runtime_config()["market_candle_cache_size"] == 10
    assert service.current_runtime_config()["market_rest_backfill_limit"] == 8


def test_market_history_route_returns_candles_for_owned_account(async_runner):
    with _build_session() as db:
        user = _create_user(db, "market-user")
        account = _create_account(db, user.id, exchange="binance")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data_service=_FakeMarketService())))

        response = async_runner(
            get_market_klines(
                request=request,
                exchange_account_id=account.id,
                market_type="spot",
                symbol=" btcusdt ",
                interval="1m",
                limit=50,
                db=db,
                current_user=user,
            )
        )

        assert response.exchange == "binance"
        assert response.symbol == "BTCUSDT"
        assert len(response.candles) == 2
        assert response.candles[-1].close == pytest.approx(101.2)


def test_market_history_route_blocks_unsupported_exchange(async_runner):
    with _build_session() as db:
        user = _create_user(db, "market-user-2")
        account = _create_account(db, user.id, exchange="lighter")
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data_service=_FakeMarketService())))

        with pytest.raises(HTTPException) as exc:
            async_runner(
                get_market_klines(
                    request=request,
                    exchange_account_id=account.id,
                    market_type="spot",
                    symbol="BTCUSDT",
                    interval="1m",
                    limit=50,
                    db=db,
                    current_user=user,
                )
            )
        assert "not supported for market charts" in str(exc.value.detail)
