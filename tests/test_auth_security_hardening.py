from fastapi import HTTPException, Response
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.models import Base, User
from app.routers import auth
from app.schemas import StepUpRequest, TOTPSetupRequest, UserLoginRequest
from app.security import hash_password


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _build_request(ip: str = "127.0.0.1", user_agent: str = "pytest-agent"):
    return SimpleNamespace(headers={"user-agent": user_agent}, client=SimpleNamespace(host=ip))


def _login(db: Session, *, username: str, password: str, request=None, otp_code: str | None = None):
    return auth.login(
        UserLoginRequest(username=username, password=password, otp_code=otp_code),
        request or _build_request(),
        Response(),
        db,
    )


def test_setup_2fa_rotation_requires_current_code():
    with _build_session() as db:
        secret = pyotp.random_base32()
        user = User(
            username="rotate-user",
            email="rotate-user@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=auth.kms.encrypt(secret),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        try:
            auth.setup_2fa(TOTPSetupRequest(), response=Response(), current_user=user, db=db)
            raise AssertionError("Expected HTTPException for missing current_code")
        except HTTPException as exc:
            assert exc.status_code == 400

        try:
            auth.setup_2fa(
                TOTPSetupRequest(current_code="000000"),
                response=Response(),
                current_user=user,
                db=db,
            )
            raise AssertionError("Expected HTTPException for invalid current_code")
        except HTTPException as exc:
            assert exc.status_code == 401

        valid_code = pyotp.TOTP(secret).now()
        response = auth.setup_2fa(
            TOTPSetupRequest(current_code=valid_code),
            response=Response(),
            current_user=user,
            db=db,
        )
        assert response.otp_secret
        assert response.otpauth_uri.startswith("otpauth://")


def test_login_rate_limit_blocks_repeated_failed_attempts():
    with _build_session() as db:
        user = User(
            username="limit-user",
            email="limit-user@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()

        auth.login_rate_limiter.clear()
        original = (
            auth.settings.auth_login_max_attempts,
            auth.settings.auth_login_window_seconds,
            auth.settings.auth_login_lockout_seconds,
        )
        auth.settings.auth_login_max_attempts = 2
        auth.settings.auth_login_window_seconds = 600
        auth.settings.auth_login_lockout_seconds = 60
        try:
            for _ in range(2):
                try:
                    _login(db, username="limit-user", password="wrong-password")
                    raise AssertionError("Expected HTTPException for invalid credentials")
                except HTTPException as exc:
                    assert exc.status_code == 401

            try:
                _login(db, username="limit-user", password="wrong-password")
                raise AssertionError("Expected HTTPException for login rate limit")
            except HTTPException as exc:
                assert exc.status_code == 429
        finally:
            (
                auth.settings.auth_login_max_attempts,
                auth.settings.auth_login_window_seconds,
                auth.settings.auth_login_lockout_seconds,
            ) = original
            auth.login_rate_limiter.clear()


def test_step_up_rate_limit_blocks_repeated_invalid_codes():
    with _build_session() as db:
        secret = pyotp.random_base32()
        user = User(
            username="stepup-limit-user",
            email="stepup-limit-user@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=auth.kms.encrypt(secret),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        auth.step_up_rate_limiter.clear()
        original = (
            auth.settings.auth_step_up_max_attempts,
            auth.settings.auth_step_up_window_seconds,
            auth.settings.auth_step_up_lockout_seconds,
        )
        auth.settings.auth_step_up_max_attempts = 2
        auth.settings.auth_step_up_window_seconds = 600
        auth.settings.auth_step_up_lockout_seconds = 60
        try:
            for _ in range(2):
                try:
                    auth.step_up_2fa(
                        StepUpRequest(code="000000"),
                        request=_build_request(),
                        current_user=user,
                        db=db,
                    )
                    raise AssertionError("Expected HTTPException for invalid step-up code")
                except HTTPException as exc:
                    assert exc.status_code == 401

            try:
                auth.step_up_2fa(
                    StepUpRequest(code="000000"),
                    request=_build_request(),
                    current_user=user,
                    db=db,
                )
                raise AssertionError("Expected HTTPException for step-up rate limit")
            except HTTPException as exc:
                assert exc.status_code == 429
        finally:
            (
                auth.settings.auth_step_up_max_attempts,
                auth.settings.auth_step_up_window_seconds,
                auth.settings.auth_step_up_lockout_seconds,
            ) = original
            auth.step_up_rate_limiter.clear()
