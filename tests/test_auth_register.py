from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.routers import auth
from app.schemas import UserRegisterRequest


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_register_creates_user_with_normalized_identity():
    with _build_session() as db:
        response = auth.register(
            UserRegisterRequest(
                username="  trader-one  ",
                email="Trader.One@Example.com",
                password="StrongPass123!",
            ),
            db=db,
        )

        assert response.username == "trader-one"
        assert response.email == "trader.one@example.com"
        stored = db.query(User).filter(User.username == "trader-one").one()
        assert stored.role == "user"


def test_register_rejects_duplicate_username_or_email_case_insensitive():
    with _build_session() as db:
        auth.register(
            UserRegisterRequest(
                username="trader-one",
                email="trader.one@example.com",
                password="StrongPass123!",
            ),
            db=db,
        )

        try:
            auth.register(
                UserRegisterRequest(
                    username="trader-one",
                    email="TRADER.ONE@example.com",
                    password="AnotherStrongPass123!",
                ),
                db=db,
            )
            raise AssertionError("Expected duplicate registration to fail")
        except HTTPException as exc:
            assert exc.status_code == 400
            assert "already exists" in str(exc.detail)
