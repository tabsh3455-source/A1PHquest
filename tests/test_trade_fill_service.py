from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, TradeFillSnapshot, User
from app.services.trade_fill_service import TradeFillService


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_user_and_account(db: Session) -> tuple[User, ExchangeAccount]:
    user = User(
        username="trade-user",
        email="trade-user@example.com",
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
        api_key_encrypted="k",
        api_secret_encrypted="s",
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return user, account


def test_upsert_fills_is_idempotent_and_updates_existing():
    service = TradeFillService()
    with _build_session() as db:
        user, account = _create_user_and_account(db)

        count_1, rows_1 = service.upsert_fills(
            db,
            user_id=user.id,
            account=account,
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "order_id": "11",
                    "trade_id": "1001",
                    "side": "BUY",
                    "price": 100,
                    "quantity": 0.01,
                    "fee": 0.001,
                    "fee_asset": "BNB",
                    "trade_time": 1710000000000,
                }
            ],
        )
        db.commit()
        for row in rows_1:
            db.refresh(row)

        count_2, rows_2 = service.upsert_fills(
            db,
            user_id=user.id,
            account=account,
            rows=[
                {
                    "symbol": "BTCUSDT",
                    "order_id": "11",
                    "trade_id": "1001",
                    "side": "BUY",
                    "price": 101,
                    "quantity": 0.02,
                    "fee": 0.002,
                    "fee_asset": "BNB",
                    "trade_time": "2026-03-22T12:34:56Z",
                }
            ],
        )
        db.commit()
        for row in rows_2:
            db.refresh(row)

        result = db.query(TradeFillSnapshot).all()

        assert count_1 == 1
        assert count_2 == 1
        assert len(result) == 1
        assert float(rows_2[0].price) == 101.0
        assert float(rows_2[0].quantity) == 0.02
        assert rows_2[0].trade_time.year == 2026
