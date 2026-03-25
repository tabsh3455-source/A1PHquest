from __future__ import annotations

from fastapi import Cookie, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .security import STEP_UP_PURPOSE_HIGH_RISK, decode_access_token, decode_step_up_token

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_access_token_optional(
    bearer_token: str | None = Depends(oauth2_scheme),
    cookie_token: str | None = Cookie(default=None, alias=settings.auth_cookie_name),
) -> str | None:
    if bearer_token:
        return bearer_token
    normalized_cookie_token = str(cookie_token or "").strip()
    return normalized_cookie_token or None


def get_access_token(token: str | None = Depends(get_access_token_optional)) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return token


def authenticate_access_token_user(
    *,
    db: Session,
    token: str,
    require_verified: bool = False,
    invalid_status: int = status.HTTP_401_UNAUTHORIZED,
) -> User:
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=invalid_status, detail="Invalid authentication") from exc

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=invalid_status, detail="User not found")
    _enforce_token_version(user=user, payload=payload, invalid_status=invalid_status)
    if require_verified and payload.get("twofa_pending"):
        detail = "2FA enrollment required" if not user.totp_secret_encrypted else "2FA verification required"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return user


def get_current_user(db: Session = Depends(get_db), token: str = Depends(get_access_token)) -> User:
    return authenticate_access_token_user(db=db, token=token)


def get_current_user_optional(
    db: Session = Depends(get_db),
    token: str | None = Depends(get_access_token_optional),
) -> User | None:
    if not token:
        return None
    try:
        return authenticate_access_token_user(db=db, token=token)
    except HTTPException:
        return None


def get_current_verified_user(
    db: Session = Depends(get_db), token: str = Depends(get_access_token)
) -> User:
    return authenticate_access_token_user(db=db, token=token, require_verified=True)


def require_2fa_user(user: User = Depends(get_current_verified_user)) -> User:
    """
    Enforce that high-risk operations can only be executed by users with 2FA enabled.

    Authentication tokens for users with configured 2FA are already OTP-verified at login.
    This guard adds an explicit policy that sensitive endpoints require 2FA enrollment.
    """
    if not user.totp_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="2FA setup required for this operation",
        )
    return user


def require_step_up_user(
    user: User = Depends(require_2fa_user),
    step_up_token: str | None = Header(default=None, alias="X-StepUp-Token"),
) -> User:
    """
    Enforce short-lived step-up proof for high-risk operations.

    Client must call /api/auth/2fa/step-up first, then pass token via header:
    X-StepUp-Token: <token>
    """
    if not step_up_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Step-up token required for this operation",
        )
    try:
        payload = decode_step_up_token(step_up_token)
        token_user_id = int(payload["sub"])
        token_purpose = str(payload.get("purpose") or "")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid step-up token") from exc
    if token_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Step-up token user mismatch")
    if token_purpose != STEP_UP_PURPOSE_HIGH_RISK:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Step-up token purpose mismatch")
    _enforce_token_version(user=user, payload=payload, invalid_status=status.HTTP_403_FORBIDDEN)
    return user


def _enforce_token_version(
    *,
    user: User,
    payload: dict,
    invalid_status: int = status.HTTP_401_UNAUTHORIZED,
) -> None:
    current_version = int(getattr(user, "token_version", 0) or 0)
    token_version = payload.get("token_version")
    normalized_token_version = int(token_version) if token_version is not None else 0
    if normalized_token_version != current_version:
        raise HTTPException(status_code=invalid_status, detail="Session has been revoked")
