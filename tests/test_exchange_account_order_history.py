from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, OrderSnapshot, User
from app.routers.exchange_accounts import _upsert_orders


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


def _create_account(db: Session, user_id: int) -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange="binance",
        account_alias="main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_upsert_orders_preserves_terminal_history_when_latest_sync_has_no_open_orders():
    with _build_session() as db:
        user = _create_user(db, "history-user")
        account = _create_account(db, user.id)
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
        db.commit()

        synced = _upsert_orders(db=db, user_id=user.id, account=account, rows=[])
        db.commit()

        assert synced == 0
        remaining = db.query(OrderSnapshot).filter(OrderSnapshot.exchange_account_id == account.id).all()
        assert len(remaining) == 1
        assert remaining[0].status == "FILLED"
