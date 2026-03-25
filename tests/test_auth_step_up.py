from fastapi import HTTPException
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.models import Base, User
from app.routers import auth
from app.schemas import StepUpRequest
from app.security import decode_step_up_token, hash_password


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _build_request(ip: str = "127.0.0.1", user_agent: str = "pytest-agent"):
    return SimpleNamespace(headers={"user-agent": user_agent}, client=SimpleNamespace(host=ip))


def test_step_up_endpoint_returns_short_lived_token():
    with _build_session() as db:
        secret = pyotp.random_base32()
        user = User(
            username="stepup-user",
            email="stepup-user@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=auth.kms.encrypt(secret),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        code = pyotp.TOTP(secret).now()
        response = auth.step_up_2fa(StepUpRequest(code=code), request=_build_request(), current_user=user, db=db)
        payload = decode_step_up_token(response.step_up_token)
        assert payload["sub"] == str(user.id)
        assert payload["token_use"] == "step_up"
        assert response.expires_in_seconds > 0


def test_step_up_endpoint_rejects_invalid_code():
    with _build_session() as db:
        secret = pyotp.random_base32()
        user = User(
            username="stepup-user-bad",
            email="stepup-user-bad@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=auth.kms.encrypt(secret),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        try:
            auth.step_up_2fa(StepUpRequest(code="000000"), request=_build_request(), current_user=user, db=db)
            raise AssertionError("Expected HTTPException for invalid code")
        except HTTPException as exc:
            assert exc.status_code == 401
