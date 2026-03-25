from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, User
from app.tenant import with_tenant


def test_tenant_query_filters_records():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        user_1 = User(username="u1", email="u1@example.com", password_hash="x", role="user", is_active=True)
        user_2 = User(username="u2", email="u2@example.com", password_hash="x", role="user", is_active=True)
        db.add_all([user_1, user_2])
        db.commit()
        db.refresh(user_1)
        db.refresh(user_2)

        db.add_all(
            [
                ExchangeAccount(
                    user_id=user_1.id,
                    exchange="binance",
                    account_alias="u1-main",
                    api_key_encrypted="a",
                    api_secret_encrypted="b",
                    is_testnet=True,
                ),
                ExchangeAccount(
                    user_id=user_2.id,
                    exchange="okx",
                    account_alias="u2-main",
                    api_key_encrypted="c",
                    api_secret_encrypted="d",
                    is_testnet=True,
                ),
            ]
        )
        db.commit()

        user_1_accounts = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_1.id).all()
        user_2_accounts = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_2.id).all()

        assert len(user_1_accounts) == 1
        assert len(user_2_accounts) == 1
        assert user_1_accounts[0].account_alias == "u1-main"
        assert user_2_accounts[0].account_alias == "u2-main"

