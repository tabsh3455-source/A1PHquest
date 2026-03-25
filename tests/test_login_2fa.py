from fastapi import HTTPException, Response
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.models import Base, User
from app.routers import auth
from app.schemas import UserLoginRequest
from app.security import hash_password


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _build_request(ip: str = "127.0.0.1", user_agent: str = "pytest-agent"):
    return SimpleNamespace(headers={"user-agent": user_agent}, client=SimpleNamespace(host=ip))


def test_login_requires_google_authenticator_code_when_2fa_enabled():
    with _build_session() as db:
        otp_secret = pyotp.random_base32()
        user = User(
            username="alice",
            email="alice@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=auth.kms.encrypt(otp_secret),
        )
        db.add(user)
        db.commit()

        try:
            auth.login(
                UserLoginRequest(username="alice", password="StrongPass123!"),
                _build_request(),
                db,
            )
            raise AssertionError("Expected HTTPException for missing otp_code")
        except HTTPException as exc:
            assert exc.status_code == 400

        try:
            auth.login(
                UserLoginRequest(username="alice", password="StrongPass123!", otp_code="000000"),
                _build_request(),
                db,
            )
            raise AssertionError("Expected HTTPException for invalid otp_code")
        except HTTPException as exc:
            assert exc.status_code == 401

        valid_code = pyotp.TOTP(otp_secret).now()
        raw_response = Response()
        response = auth.login(
            UserLoginRequest(username="alice", password="StrongPass123!", otp_code=valid_code),
            _build_request(),
            db,
            raw_response,
        )
        assert response.authenticated is True
        assert response.user.username == "alice"
        assert response.csrf_token
        set_cookie_headers = raw_response.headers.getlist("set-cookie")
        assert any(auth.settings.auth_cookie_name in header and "HttpOnly" in header for header in set_cookie_headers)
        assert any(auth.settings.csrf_cookie_name in header for header in set_cookie_headers)
