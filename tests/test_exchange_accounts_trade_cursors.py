from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import json

from app.models import Base, ExchangeAccount, OrderSnapshot, Strategy, TradeFillSnapshot, User
from app.routers.exchange_accounts import _build_trade_cursors


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _to_epoch_ms(value: datetime) -> int:
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)


def test_build_trade_cursors_collects_symbol_and_global_hints():
    with _build_session() as db:
        user = User(
            username="cursor-user",
            email="cursor@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        account = ExchangeAccount(
            user_id=user.id,
            exchange="binance",
            account_alias="main",
            api_key_encrypted="enc-key",
            api_secret_encrypted="enc-secret",
            is_testnet=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)

        t1 = datetime(2026, 1, 1, 0, 0, 0)
        t2 = datetime(2026, 1, 1, 1, 0, 0)
        t3 = datetime(2026, 1, 1, 2, 0, 0)

        db.add_all(
            [
                TradeFillSnapshot(
                    user_id=user.id,
                    exchange_account_id=account.id,
                    exchange="binance",
                    symbol="BTCUSDT",
                    order_id="o-1",
                    trade_id="t-1",
                    side="BUY",
                    price=100,
                    quantity=0.1,
                    quote_quantity=10,
                    fee=0.01,
                    is_maker=False,
                    trade_time=t1,
                    raw_json="{}",
                ),
                TradeFillSnapshot(
                    user_id=user.id,
                    exchange_account_id=account.id,
                    exchange="binance",
                    symbol="BTCUSDT",
                    order_id="o-2",
                    trade_id="t-2",
                    side="BUY",
                    price=101,
                    quantity=0.2,
                    quote_quantity=20.2,
                    fee=0.02,
                    is_maker=False,
                    trade_time=t3,
                    raw_json="{}",
                ),
                TradeFillSnapshot(
                    user_id=user.id,
                    exchange_account_id=account.id,
                    exchange="binance",
                    symbol="ETHUSDT",
                    order_id="o-3",
                    trade_id="t-3",
                    side="SELL",
                    price=2000,
                    quantity=0.01,
                    quote_quantity=20,
                    fee=0.01,
                    is_maker=True,
                    trade_time=t2,
                    raw_json="{}",
                ),
            ]
        )
        db.add(
            OrderSnapshot(
                user_id=user.id,
                exchange_account_id=account.id,
                exchange="binance",
                symbol="DOGEUSDT",
                order_id="order-only-symbol",
                status="NEW",
                side="BUY",
                order_type="LIMIT",
                price=0.1,
                quantity=100,
                filled_quantity=0,
                raw_json="{}",
            )
        )
        db.commit()

        cursor = _build_trade_cursors(db, user_id=user.id, account_id=account.id)
        assert set(cursor["symbols"].keys()) == {"BTCUSDT", "ETHUSDT", "DOGEUSDT"}
        assert cursor["symbols"]["BTCUSDT"]["last_trade_time_ms"] == _to_epoch_ms(t3)
        assert cursor["symbols"]["ETHUSDT"]["last_trade_time_ms"] == _to_epoch_ms(t2)
        assert cursor["symbols"]["DOGEUSDT"] == {}
        assert cursor["global"]["last_trade_time_ms"] == _to_epoch_ms(t3)


def test_build_trade_cursors_collects_strategy_symbol_hints_for_same_account():
    with _build_session() as db:
        user = User(
            username="cursor-strategy-user",
            email="cursor-strategy@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        account = ExchangeAccount(
            user_id=user.id,
            exchange="binance",
            account_alias="main",
            api_key_encrypted="enc-key",
            api_secret_encrypted="enc-secret",
            is_testnet=True,
        )
        other_account = ExchangeAccount(
            user_id=user.id,
            exchange="binance",
            account_alias="backup",
            api_key_encrypted="enc-key",
            api_secret_encrypted="enc-secret",
            is_testnet=True,
        )
        db.add_all([account, other_account])
        db.commit()
        db.refresh(account)
        db.refresh(other_account)

        db.add_all(
            [
                Strategy(
                    user_id=user.id,
                    name="grid-a",
                    strategy_type="grid",
                    config_json=json.dumps(
                        {"exchange_account_id": account.id, "symbol": "solusdt"}
                    ),
                    status="stopped",
                ),
                Strategy(
                    user_id=user.id,
                    name="grid-b",
                    strategy_type="grid",
                    config_json=json.dumps(
                        {"exchange_account_id": other_account.id, "symbol": "adausdt"}
                    ),
                    status="stopped",
                ),
            ]
        )
        db.commit()

        cursor = _build_trade_cursors(db, user_id=user.id, account_id=account.id)
        assert "SOLUSDT" in cursor["symbols"]
        assert "ADAUSDT" not in cursor["symbols"]
