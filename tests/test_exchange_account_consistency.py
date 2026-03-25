from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, OrderSnapshot, TradeFillSnapshot, User
from app.routers.exchange_accounts import _build_order_trade_consistency


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_order_trade_consistency_detects_orphans_and_missing_fills():
    with _build_session() as db:
        user = User(
            username="consistency-user",
            email="consistency-user@example.com",
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
            api_key_encrypted="key",
            api_secret_encrypted="secret",
            is_testnet=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)

        db.add(
            OrderSnapshot(
                user_id=user.id,
                exchange_account_id=account.id,
                exchange="binance",
                symbol="BTCUSDT",
                order_id="order-1",
                status="FILLED",
                side="BUY",
                order_type="LIMIT",
                price=100,
                quantity=1,
                filled_quantity=1,
                raw_json="{}",
            )
        )
        db.add(
            TradeFillSnapshot(
                user_id=user.id,
                exchange_account_id=account.id,
                exchange="binance",
                symbol="BTCUSDT",
                order_id="order-orphan",
                trade_id="trade-1",
                side="BUY",
                price=100,
                quantity=1,
                quote_quantity=100,
                fee=0.01,
                is_maker=False,
                raw_json="{}",
            )
        )
        db.commit()

        summary = _build_order_trade_consistency(db, user_id=user.id, account_id=account.id)
        assert summary["consistent"] is False
        assert "order-orphan" in summary["trades_without_order"]
        assert "order-1" in summary["orders_with_fill_but_no_trade"]
