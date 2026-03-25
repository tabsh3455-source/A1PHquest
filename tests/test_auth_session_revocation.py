from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.deps import authenticate_access_token_user, get_current_user, require_step_up_user
from app.models import Base, User
from app.routers import auth
from app.security import create_access_token, create_step_up_token, hash_password


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_logout_revokes_existing_access_token():
    with _build_session() as db:
        user = User(
            username="logout-user",
            email="logout-user@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted="enc-secret",
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(str(user.id), {"role": user.role, "twofa_pending": False, "token_version": 0})
        assert get_current_user(db=db, token=token).id == user.id

        auth.logout(current_user=user, db=db)

        try:
            get_current_user(db=db, token=token)
            raise AssertionError("Expected revoked token to be rejected")
        except HTTPException as exc:
            assert exc.status_code == 401


def test_logout_revokes_existing_step_up_token():
    user = User(
        id=7,
        username="logout-step-up",
        email="logout-step-up@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc-secret",
        token_version=1,
    )
    token = create_step_up_token(str(user.id), token_version=1)
    assert require_step_up_user(user=user, step_up_token=token) is user

    user.token_version = 2
    try:
        require_step_up_user(user=user, step_up_token=token)
        raise AssertionError("Expected step-up token version mismatch")
    except HTTPException as exc:
        assert exc.status_code == 403


def test_authenticated_access_token_user_rejects_revoked_token():
    with _build_session() as db:
        user = User(
            username="logout-ws",
            email="logout-ws@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted="enc-secret",
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(str(user.id), {"role": user.role, "twofa_pending": False, "token_version": 0})
        assert authenticate_access_token_user(db=db, token=token, require_verified=True).id == user.id

        auth.logout(current_user=user, db=db)

        try:
            authenticate_access_token_user(db=db, token=token, require_verified=True)
            raise AssertionError("Expected revoked token to be rejected")
        except HTTPException as exc:
            assert exc.status_code == 401


def test_authenticated_access_token_user_blocks_2fa_pending_tokens_when_verified_required():
    with _build_session() as db:
        user = User(
            username="ws-2fa",
            email="ws-2fa@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted="enc-secret",
            token_version=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        token = create_access_token(str(user.id), {"role": user.role, "twofa_pending": True, "token_version": 0})

        try:
            authenticate_access_token_user(db=db, token=token, require_verified=True)
            raise AssertionError("Expected 2FA-pending token to be rejected for verified access")
        except HTTPException as exc:
            assert exc.status_code == 403
