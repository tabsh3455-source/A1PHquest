from fastapi import HTTPException

from app.deps import require_2fa_user, require_admin_step_up_user, require_step_up_user
from app.models import User
from app.security import create_step_up_token


def test_require_2fa_user_blocks_when_secret_missing():
    user = User(
        id=1,
        username="no2fa",
        email="no2fa@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted=None,
    )
    try:
        require_2fa_user(user)
        raise AssertionError("Expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "2FA setup required" in str(exc.detail)


def test_require_2fa_user_allows_when_secret_exists():
    user = User(
        id=2,
        username="with2fa",
        email="with2fa@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc-secret",
    )
    assert require_2fa_user(user) is user


def test_require_step_up_user_allows_valid_token():
    user = User(
        id=3,
        username="stepup",
        email="stepup@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc-secret",
    )
    token = create_step_up_token(str(user.id))
    assert require_step_up_user(user=user, step_up_token=token) is user


def test_require_step_up_user_blocks_missing_or_mismatched_token():
    user = User(
        id=5,
        username="stepup-miss",
        email="stepup-miss@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc-secret",
    )
    try:
        require_step_up_user(user=user, step_up_token=None)
        raise AssertionError("Expected HTTPException for missing step-up token")
    except HTTPException as exc:
        assert exc.status_code == 403

    mismatch = create_step_up_token("6")
    try:
        require_step_up_user(user=user, step_up_token=mismatch)
        raise AssertionError("Expected HTTPException for token user mismatch")
    except HTTPException as exc:
        assert exc.status_code == 403

    wrong_purpose = create_step_up_token(str(user.id), purpose="read_only")
    try:
        require_step_up_user(user=user, step_up_token=wrong_purpose)
        raise AssertionError("Expected HTTPException for token purpose mismatch")
    except HTTPException as exc:
        assert exc.status_code == 403


def test_require_admin_step_up_user_blocks_non_admin():
    user = User(
        id=7,
        username="non-admin-stepup",
        email="non-admin-stepup@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc-secret",
    )
    try:
        require_admin_step_up_user(user=user)
        raise AssertionError("Expected HTTPException for non-admin user")
    except HTTPException as exc:
        assert exc.status_code == 403
        assert "Admin role required" in str(exc.detail)
