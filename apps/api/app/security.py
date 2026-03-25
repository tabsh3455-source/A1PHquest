from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import io
import os
import secrets
from typing import Any

import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext
import pyotp
import qrcode
from qrcode.image.svg import SvgImage

from .config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()
PBKDF2_ITERATIONS = 600_000
ACCESS_TOKEN_USE = "access"  # nosec B105
STEP_UP_TOKEN_USE = "step_up"  # nosec B105
STEP_UP_PURPOSE_HIGH_RISK = "high_risk"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        derived.hex(),
    )


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("pbkdf2_sha256$"):
        _, iterations, salt_hex, digest_hex = password_hash.split("$", maxsplit=3)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(derived, expected)

    # Backward compatibility for existing bcrypt hashes.
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    issued_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "token_use": ACCESS_TOKEN_USE,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_step_up_token(
    subject: str,
    *,
    purpose: str = STEP_UP_PURPOSE_HIGH_RISK,
    token_version: int | None = None,
) -> str:
    """
    Create a short-lived token for high-risk operation authorization.

    This token is intentionally separate from access token scope so sensitive
    operations can require fresh Google Authenticator verification.
    """
    issued_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "token_use": STEP_UP_TOKEN_USE,
        "purpose": purpose,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.step_up_token_expire_minutes),
    }
    if token_version is not None:
        payload["token_version"] = int(token_version)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_jwt(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except InvalidTokenError as exc:
        raise ValueError("Invalid token") from exc


def decode_access_token(token: str) -> dict[str, Any]:
    payload = _decode_jwt(token)
    # Backward-compatible default: tokens without `token_use` are treated as
    # legacy access tokens; any explicit non-access token is rejected.
    token_use = str(payload.get("token_use") or ACCESS_TOKEN_USE)
    if token_use != ACCESS_TOKEN_USE:
        raise ValueError("Invalid access token")
    return payload


def decode_step_up_token(token: str) -> dict[str, Any]:
    payload = _decode_jwt(token)
    if payload.get("token_use") != STEP_UP_TOKEN_USE:
        raise ValueError("Invalid step-up token")
    return payload


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def build_totp_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="A1phquest")


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def build_qr_svg_data_url(value: str) -> str:
    image = qrcode.make(value, image_factory=SvgImage)
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def generate_recovery_codes(*, count: int = 8) -> list[str]:
    codes: list[str] = []
    for _ in range(max(count, 1)):
        block = secrets.token_hex(4).upper()
        codes.append(f"AQ-{block[:4]}-{block[4:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    return hash_password(code.strip().upper())


def verify_recovery_code(code: str, code_hash: str) -> bool:
    return verify_password(code.strip().upper(), code_hash)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
