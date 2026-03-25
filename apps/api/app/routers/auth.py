from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..config import get_settings
from ..db import get_db
from ..deps import get_access_token, get_current_user, get_current_user_optional, get_current_verified_user
from ..kms import build_kms_provider
from ..models import AuditEvent, PendingRegistration, User, UserRecoveryCode
from ..schemas import (
    AuthFlowResponse,
    AuthSessionResponse,
    RegistrationCompleteRequest,
    RegistrationStartResponse,
    RecoveryCodesResponse,
    StepUpRequest,
    StepUpTokenResponse,
    TwoFactorEnrollmentCompleteRequest,
    TwoFactorEnrollmentStartResponse,
    TOTPSetupRequest,
    TOTPSetupResponse,
    TOTPVerifyRequest,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from ..security import (
    build_totp_uri,
    build_qr_svg_data_url,
    create_access_token,
    create_step_up_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_opaque_token,
    hash_password,
    hash_recovery_code,
    verify_password,
    verify_recovery_code,
    verify_totp,
)
from ..services.notifications import NotificationService

router = APIRouter(prefix="/api/auth", tags=["auth"])
kms = build_kms_provider()
settings = get_settings()
notification_service = NotificationService()


@dataclass(slots=True)
class LoginRiskAssessment:
    score: int
    flags: list[str]


@dataclass(slots=True)
class _RateLimitState:
    failures: deque[datetime]
    lock_until: datetime | None = None


class _InMemoryRateLimiter:
    """
    Small in-process limiter for auth brute-force resistance.

    Limits are best-effort and intentionally simple; they provide immediate
    protection against repeated online guessing without adding DB write overhead
    on every request.
    """

    def __init__(self, *, max_keys: int = 20_000) -> None:
        self._max_keys = max_keys
        self._states: dict[str, _RateLimitState] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    def check_blocked(
        self,
        *,
        key: str,
        window_seconds: int,
    ) -> int | None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            state = self._states.get(key)
            if not state:
                return None
            self._prune_failures(state, now=now, window_seconds=window_seconds)
            if state.lock_until and state.lock_until > now:
                return max(int((state.lock_until - now).total_seconds()), 1)
            if state.lock_until and state.lock_until <= now:
                state.lock_until = None
            if not state.failures and state.lock_until is None:
                self._states.pop(key, None)
            return None

    def register_failure(
        self,
        *,
        key: str,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
    ) -> int | None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            state = self._states.setdefault(key, _RateLimitState(failures=deque()))
            self._prune_failures(state, now=now, window_seconds=window_seconds)
            state.failures.append(now)
            if len(state.failures) >= max(max_attempts, 1):
                state.lock_until = now + timedelta(seconds=max(lockout_seconds, 1))
                state.failures.clear()
                self._trim_if_needed()
                return max(int((state.lock_until - now).total_seconds()), 1)
            self._trim_if_needed()
        return None

    def register_success(self, *, key: str) -> None:
        with self._lock:
            self._states.pop(key, None)

    @staticmethod
    def _prune_failures(state: _RateLimitState, *, now: datetime, window_seconds: int) -> None:
        cutoff = now - timedelta(seconds=max(window_seconds, 1))
        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()

    def _trim_if_needed(self) -> None:
        if len(self._states) <= self._max_keys:
            return
        for key, state in list(self._states.items()):
            if not state.failures and state.lock_until is None:
                self._states.pop(key, None)
            if len(self._states) <= self._max_keys:
                return
        while len(self._states) > self._max_keys:
            first_key = next(iter(self._states))
            self._states.pop(first_key, None)


login_rate_limiter = _InMemoryRateLimiter()
step_up_rate_limiter = _InMemoryRateLimiter()


@router.post("/register", response_model=RegistrationStartResponse, status_code=status.HTTP_201_CREATED)
@router.post("/register/start", response_model=RegistrationStartResponse, status_code=status.HTTP_201_CREATED)
def register_start(payload: UserRegisterRequest, db: Session = Depends(get_db)):
    normalized_username, normalized_email = _normalize_registration_identity(payload)
    _purge_expired_pending_registrations(db)
    _ensure_registration_available(db=db, username=normalized_username, email=normalized_email)

    token = secrets.token_urlsafe(32)
    secret = generate_totp_secret()
    pending = PendingRegistration(
        username=normalized_username,
        email=normalized_email,
        password_hash=hash_password(payload.password),
        totp_secret_encrypted=kms.encrypt(secret),
        registration_token_hash=hash_opaque_token(token),
        expires_at=_utcnow() + timedelta(minutes=settings.registration_token_expire_minutes),
    )
    db.add(pending)
    db.commit()

    otpauth_uri = build_totp_uri(secret, normalized_username)
    return RegistrationStartResponse(
        registration_token=token,
        otp_secret=secret,
        otpauth_uri=otpauth_uri,
        qr_svg_data_url=build_qr_svg_data_url(otpauth_uri),
        expires_at=pending.expires_at,
    )


@router.post("/register/complete", response_model=AuthFlowResponse, status_code=status.HTTP_201_CREATED)
def register_complete(
    payload: RegistrationCompleteRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    _purge_expired_pending_registrations(db)
    pending = _get_pending_registration(db, payload.registration_token)
    secret = kms.decrypt(pending.totp_secret_encrypted)
    if not verify_totp(secret, payload.otp_code):
        raise HTTPException(status_code=400, detail="Invalid Google Authenticator code")

    _ensure_registration_available(
        db=db,
        username=pending.username,
        email=pending.email,
        exclude_pending_id=pending.id,
    )
    user = User(
        username=pending.username,
        email=pending.email,
        password_hash=pending.password_hash,
        role="user",
        is_active=True,
        totp_secret_encrypted=pending.totp_secret_encrypted,
        pending_totp_secret_encrypted=None,
    )
    db.add(user)
    db.flush()
    recovery_codes = _replace_recovery_codes(db, user_id=user.id)
    db.delete(pending)
    db.commit()
    db.refresh(user)

    log_audit_event(db, user_id=user.id, action="register", resource="user", resource_id=str(user.id))
    token = _issue_access_token(user=user, twofa_pending=False)
    return _build_auth_flow_response(
        user=user,
        access_token=token,
        response=response,
        enrollment_required=False,
        recovery_codes=recovery_codes,
    )


@router.post("/login", response_model=AuthSessionResponse)
def login(
    payload: UserLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    normalized_username = payload.username.strip()
    client_ip, client_ip_source = _extract_client_ip(request)
    login_limit_key = _build_rate_limit_key(
        scope="login",
        principal=normalized_username.lower(),
        client_ip=client_ip,
    )
    blocked_seconds = login_rate_limiter.check_blocked(
        key=login_limit_key,
        window_seconds=settings.auth_login_window_seconds,
    )
    if blocked_seconds:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {blocked_seconds} seconds.",
        )

    user = db.query(User).filter(User.username == normalized_username, User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        login_rate_limiter.register_failure(
            key=login_limit_key,
            max_attempts=settings.auth_login_max_attempts,
            window_seconds=settings.auth_login_window_seconds,
            lockout_seconds=settings.auth_login_lockout_seconds,
        )
        if user:
            log_audit_event(
                db,
                user_id=user.id,
                action="login_failed",
                resource="session",
                details={"client_ip": client_ip, "client_ip_source": client_ip_source},
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    requires_2fa = bool(user.totp_secret_encrypted)
    enrollment_required = not requires_2fa
    if enrollment_required:
        token = _issue_access_token(user=user, twofa_pending=True)
    else:
        _verify_login_second_factor(db=db, user=user, payload=payload, login_limit_key=login_limit_key, client_ip=client_ip, client_ip_source=client_ip_source)
        token = _issue_access_token(user=user, twofa_pending=False)
    login_rate_limiter.register_success(key=login_limit_key)
    user_agent = request.headers.get("user-agent", "")
    client_geo_country, client_geo_source = _extract_geo_country(request)
    risk = _assess_login_risk(
        db,
        user=user,
        client_ip=client_ip,
        user_agent=user_agent,
        client_geo_country=client_geo_country,
    )
    log_audit_event(
        db,
        user_id=user.id,
        action="login",
        resource="session",
        details={
            "requires_2fa": requires_2fa,
            "otp_verified": requires_2fa,
            "client_ip": client_ip,
            "client_ip_source": client_ip_source,
            "user_agent": user_agent,
            "client_geo_country": client_geo_country,
            "client_geo_source": client_geo_source,
            "risk_score": risk.score,
            "anomaly_flags": risk.flags,
        },
    )
    if risk.score >= settings.login_anomaly_alert_threshold and risk.flags:
        should_send_alert, suppressed_reason, sent_alerts_last_hour = _should_send_login_anomaly_alert(
            db,
            user_id=user.id,
        )
        message = f"score={risk.score},flags={','.join(risk.flags)}"
        if should_send_alert:
            delivery = notification_service.send_security_alert(user.id, "login_anomaly", message)
            if not isinstance(delivery, dict):
                # Preserve backward compatibility for mocks/fakes that still return None.
                delivery = {"result": "unknown"}
            alert_sent = any(str(value).strip().lower() == "sent" for value in delivery.values())
        else:
            # Suppression signal is persisted so operators can trace why alert was not emitted.
            delivery = {"suppressed": suppressed_reason or "policy"}
            alert_sent = False
        log_audit_event(
            db,
            user_id=user.id,
            action="login_anomaly",
            resource="session",
            details={
                "client_ip": client_ip,
                "user_agent": user_agent,
                "client_geo_country": client_geo_country,
                "risk_score": risk.score,
                "flags": risk.flags,
                "delivery": delivery,
                "alert_sent": alert_sent,
                "suppressed_reason": suppressed_reason,
                "alerts_sent_last_hour_before": sent_alerts_last_hour,
                "cooldown_seconds": int(settings.login_anomaly_alert_cooldown_seconds),
                "max_alerts_per_hour": int(settings.login_anomaly_max_alerts_per_hour),
            },
        )
    return _build_authenticated_session_response(
        user=user,
        access_token=token,
        response=response,
        enrollment_required=enrollment_required,
    )


@router.post("/2fa/enroll/start", response_model=TwoFactorEnrollmentStartResponse)
def start_2fa_enrollment(
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA is already configured")

    secret = generate_totp_secret()
    current_user.pending_totp_secret_encrypted = kms.encrypt(secret)
    db.add(current_user)
    db.commit()

    otpauth_uri = build_totp_uri(secret, current_user.username)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return TwoFactorEnrollmentStartResponse(
        otp_secret=secret,
        otpauth_uri=otpauth_uri,
        qr_svg_data_url=build_qr_svg_data_url(otpauth_uri),
    )


@router.post("/2fa/enroll/complete", response_model=AuthFlowResponse)
def complete_2fa_enrollment(
    payload: TwoFactorEnrollmentCompleteRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA is already configured")
    if not current_user.pending_totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA enrollment has not been started")

    secret = kms.decrypt(current_user.pending_totp_secret_encrypted)
    if not verify_totp(secret, payload.otp_code):
        raise HTTPException(status_code=400, detail="Invalid Google Authenticator code")

    current_user.totp_secret_encrypted = current_user.pending_totp_secret_encrypted
    current_user.pending_totp_secret_encrypted = None
    db.add(current_user)
    recovery_codes = _replace_recovery_codes(db, user_id=current_user.id)
    db.commit()
    db.refresh(current_user)

    token = _issue_access_token(user=current_user, twofa_pending=False)
    log_audit_event(db, user_id=current_user.id, action="2fa_enroll_complete", resource="user")
    return _build_auth_flow_response(
        user=current_user,
        access_token=token,
        response=response,
        enrollment_required=False,
        recovery_codes=recovery_codes,
    )


@router.post("/2fa/setup", response_model=TOTPSetupResponse)
def setup_2fa(
    payload: TOTPSetupRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.totp_secret_encrypted:
        if not payload.current_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current Google Authenticator code is required to rotate 2FA",
            )
        current_secret = kms.decrypt(current_user.totp_secret_encrypted)
        if not verify_totp(current_secret, payload.current_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid current Google Authenticator code",
            )

    secret = generate_totp_secret()
    encrypted = kms.encrypt(secret)
    current_user.totp_secret_encrypted = encrypted
    db.add(current_user)
    db.commit()

    log_audit_event(
        db,
        user_id=current_user.id,
        action="2fa_setup",
        resource="user",
        details={"rotated": bool(payload.current_code)},
    )
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return TOTPSetupResponse(otp_secret=secret, otpauth_uri=build_totp_uri(secret, current_user.username))


@router.post("/2fa/verify", response_model=AuthSessionResponse)
def verify_2fa(
    payload: TOTPVerifyRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA is not configured")

    secret = kms.decrypt(current_user.totp_secret_encrypted)
    if not verify_totp(secret, payload.code):
        raise HTTPException(status_code=400, detail="Invalid 2FA code")

    token = create_access_token(
        str(current_user.id),
        {
            "role": current_user.role,
            "twofa_pending": False,
            "token_version": int(current_user.token_version or 0),
        },
    )
    log_audit_event(db, user_id=current_user.id, action="2fa_verify", resource="session")
    return _build_authenticated_session_response(user=current_user, access_token=token, response=response)


@router.get("/session", response_model=AuthSessionResponse)
def get_auth_session(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
    token: str = Depends(get_access_token),
):
    existing_csrf_token = str(request.cookies.get(settings.csrf_cookie_name) or "").strip()
    csrf_token = existing_csrf_token or _generate_csrf_token()
    _set_auth_cookies(response=response, access_token=token, csrf_token=csrf_token)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return AuthSessionResponse(
        user=current_user,
        csrf_token=csrf_token,
        authenticated=True,
        enrollment_required=not bool(current_user.totp_secret_encrypted),
    )


@router.post("/2fa/step-up", response_model=StepUpTokenResponse)
def step_up_2fa(
    payload: StepUpRequest,
    request: Request,
    current_user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
    response: Response = None,
):
    client_ip = _extract_client_ip(request)[0]
    step_up_limit_key = _build_rate_limit_key(
        scope="step_up",
        principal=str(current_user.id),
        client_ip=client_ip,
    )
    blocked_seconds = step_up_rate_limiter.check_blocked(
        key=step_up_limit_key,
        window_seconds=settings.auth_step_up_window_seconds,
    )
    if blocked_seconds:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many step-up attempts. Try again in {blocked_seconds} seconds.",
        )

    if not current_user.totp_secret_encrypted:
        raise HTTPException(status_code=400, detail="2FA is not configured")
    secret = kms.decrypt(current_user.totp_secret_encrypted)
    if not verify_totp(secret, payload.code):
        step_up_rate_limiter.register_failure(
            key=step_up_limit_key,
            max_attempts=settings.auth_step_up_max_attempts,
            window_seconds=settings.auth_step_up_window_seconds,
            lockout_seconds=settings.auth_step_up_lockout_seconds,
        )
        log_audit_event(
            db,
            user_id=current_user.id,
            action="2fa_step_up_failed",
            resource="session",
            details={"client_ip": client_ip, "reason": "invalid_code"},
        )
        raise HTTPException(status_code=401, detail="Invalid Google Authenticator code")

    step_up_rate_limiter.register_success(key=step_up_limit_key)
    token = create_step_up_token(
        str(current_user.id),
        purpose="high_risk",
        token_version=int(current_user.token_version or 0),
    )
    log_audit_event(
        db,
        user_id=current_user.id,
        action="2fa_step_up",
        resource="session",
        details={"expires_in_seconds": settings.step_up_token_expire_minutes * 60},
    )
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return StepUpTokenResponse(
        step_up_token=token,
        expires_in_seconds=settings.step_up_token_expire_minutes * 60,
    )


@router.post("/logout")
def logout(
    response: Response,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    # Revoking by monotonically increasing token_version invalidates all previously
    # issued access and step-up JWTs without requiring server-side token storage.
    if current_user is not None:
        current_user.token_version = int(current_user.token_version or 0) + 1
        db.add(current_user)
        log_audit_event(db, user_id=current_user.id, action="logout", resource="session")
        db.commit()
    _clear_auth_cookies(response=response)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return {"message": "Logged out"}


def _build_auth_flow_response(
    *,
    user: User,
    access_token: str,
    response: Response | None,
    enrollment_required: bool,
    recovery_codes: list[str] | None = None,
) -> AuthFlowResponse:
    base = _build_authenticated_session_response(
        user=user,
        access_token=access_token,
        response=response,
        enrollment_required=enrollment_required,
    )
    return AuthFlowResponse(
        user=base.user,
        csrf_token=base.csrf_token,
        authenticated=base.authenticated,
        enrollment_required=base.enrollment_required,
        recovery_codes=recovery_codes or [],
    )


def _issue_access_token(*, user: User, twofa_pending: bool) -> str:
    return create_access_token(
        str(user.id),
        {
            "role": user.role,
            "twofa_pending": bool(twofa_pending),
            "token_version": int(user.token_version or 0),
        },
    )


def _verify_login_second_factor(
    *,
    db: Session,
    user: User,
    payload: UserLoginRequest,
    login_limit_key: str,
    client_ip: str,
    client_ip_source: str,
) -> None:
    if payload.recovery_code:
        if _consume_recovery_code(db=db, user=user, recovery_code=payload.recovery_code):
            return
        login_rate_limiter.register_failure(
            key=login_limit_key,
            max_attempts=settings.auth_login_max_attempts,
            window_seconds=settings.auth_login_window_seconds,
            lockout_seconds=settings.auth_login_lockout_seconds,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid recovery code")

    if not payload.otp_code:
        login_rate_limiter.register_failure(
            key=login_limit_key,
            max_attempts=settings.auth_login_max_attempts,
            window_seconds=settings.auth_login_window_seconds,
            lockout_seconds=settings.auth_login_lockout_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Authenticator code is required",
        )

    secret = kms.decrypt(user.totp_secret_encrypted or "")
    if verify_totp(secret, payload.otp_code):
        return

    login_rate_limiter.register_failure(
        key=login_limit_key,
        max_attempts=settings.auth_login_max_attempts,
        window_seconds=settings.auth_login_window_seconds,
        lockout_seconds=settings.auth_login_lockout_seconds,
    )
    log_audit_event(
        db,
        user_id=user.id,
        action="login_failed",
        resource="session",
        details={"client_ip": client_ip, "client_ip_source": client_ip_source, "reason": "invalid_otp"},
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Google Authenticator code",
    )


def _consume_recovery_code(*, db: Session, user: User, recovery_code: str) -> bool:
    normalized = recovery_code.strip().upper()
    if not normalized:
        return False

    candidates = (
        db.query(UserRecoveryCode)
        .filter(
            UserRecoveryCode.user_id == user.id,
            UserRecoveryCode.used_at.is_(None),
        )
        .all()
    )
    for item in candidates:
        if not verify_recovery_code(normalized, item.code_hash):
            continue
        item.used_at = _utcnow()
        db.add(item)
        db.commit()
        log_audit_event(db, user_id=user.id, action="recovery_code_used", resource="session")
        return True
    return False


def _replace_recovery_codes(db: Session, *, user_id: int) -> list[str]:
    db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id == user_id).delete()
    raw_codes = generate_recovery_codes()
    for raw_code in raw_codes:
        db.add(
            UserRecoveryCode(
                user_id=user_id,
                code_hash=hash_recovery_code(raw_code),
            )
        )
    db.flush()
    return raw_codes


def _normalize_registration_identity(payload: UserRegisterRequest) -> tuple[str, str]:
    normalized_username = payload.username.strip()
    normalized_email = str(payload.email).strip().lower()
    if len(normalized_username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 non-space characters")
    return normalized_username, normalized_email


def _purge_expired_pending_registrations(db: Session) -> None:
    db.query(PendingRegistration).filter(PendingRegistration.expires_at < _utcnow()).delete()
    db.commit()


def _ensure_registration_available(
    *,
    db: Session,
    username: str,
    email: str,
    exclude_pending_id: int | None = None,
) -> None:
    existing_user = (
        db.query(User)
        .filter(or_(User.username == username, User.email == email))
        .first()
    )
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already exists")

    existing_pending = (
        db.query(PendingRegistration)
        .filter(or_(PendingRegistration.username == username, PendingRegistration.email == email))
        .first()
    )
    if existing_pending and existing_pending.id != exclude_pending_id:
        raise HTTPException(
            status_code=409,
            detail="Registration is already in progress for that username or email",
        )


def _get_pending_registration(db: Session, token: str) -> PendingRegistration:
    token_hash = hash_opaque_token(token.strip())
    pending = (
        db.query(PendingRegistration)
        .filter(PendingRegistration.registration_token_hash == token_hash)
        .first()
    )
    if not pending or pending.expires_at < _utcnow():
        raise HTTPException(status_code=400, detail="Registration token is invalid or expired")
    return pending


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _build_authenticated_session_response(
    *,
    user: User,
    access_token: str,
    response: Response | None,
    enrollment_required: bool = False,
) -> AuthSessionResponse:
    csrf_token = _generate_csrf_token()
    _set_auth_cookies(response=response, access_token=access_token, csrf_token=csrf_token)
    if response is not None:
        response.headers["Cache-Control"] = "no-store"
    return AuthSessionResponse(
        user=user,
        csrf_token=csrf_token,
        authenticated=True,
        enrollment_required=enrollment_required,
    )


def _set_auth_cookies(*, response: Response | None, access_token: str, csrf_token: str) -> None:
    if response is None:
        return

    cookie_domain = _cookie_domain()
    max_age_seconds = int(settings.access_token_expire_minutes) * 60
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=access_token,
        max_age=max_age_seconds,
        expires=max_age_seconds,
        path=settings.auth_cookie_path,
        domain=cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age_seconds,
        expires=max_age_seconds,
        path=settings.auth_cookie_path,
        domain=cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite=settings.auth_cookie_samesite,
    )


def _clear_auth_cookies(*, response: Response | None) -> None:
    if response is None:
        return

    cookie_domain = _cookie_domain()
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path=settings.auth_cookie_path,
        domain=cookie_domain,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path=settings.auth_cookie_path,
        domain=cookie_domain,
    )


def _cookie_domain() -> str | None:
    normalized = str(settings.auth_cookie_domain or "").strip()
    return normalized or None


def _generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# Backward-compatible symbol exports used by older tests and internal callers.
register = register_start


def _extract_client_ip(request: Request) -> tuple[str, str]:
    if settings.trust_proxy_headers:
        # Prefer headers overwritten by the trusted reverse proxy. Only fall back
        # to X-Forwarded-For when the deployment cannot provide a dedicated client-IP
        # header, and then use the last hop appended by the proxy instead of the
        # attacker-controlled first hop.
        for key in ("cf-connecting-ip", "x-real-ip", "x-client-ip"):
            value = request.headers.get(key)
            if value:
                return value.strip(), key
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
            if parts:
                return parts[-1], "x-forwarded-for"
    if request.client:
        return request.client.host, "request.client.host"
    return "", "unknown"


def _extract_geo_country(request: Request) -> tuple[str, str]:
    """
    Resolve country signal from common reverse proxy headers.

    We intentionally do not hard-fail when headers are absent because local/dev
    environments and direct service access may not provide geo metadata.
    """
    if not settings.trust_proxy_headers:
        return "", "proxy_headers_disabled"

    for key in (
        "x-geo-country",
        "x-country-code",
        "x-geoip-country",
        "x-forwarded-country",
        "x-amzn-geo-country",
        "cf-ipcountry",
        "x-vercel-ip-country",
        "x-appengine-country",
    ):
        value = request.headers.get(key)
        if value:
            return value.strip().upper(), key
    return "", "missing"


def _assess_login_risk(
    db: Session,
    *,
    user: User,
    client_ip: str,
    user_agent: str,
    client_geo_country: str,
) -> LoginRiskAssessment:
    """
    Detect simple login anomalies from previous successful login audit snapshot.

    Current implementation is intentionally lightweight and deterministic:
    - ip_changed: previous login ip differs from current ip
    - user_agent_changed: previous login ua differs from current ua
    """
    previous_login = (
        db.query(AuditEvent)
        .filter(AuditEvent.user_id == user.id, AuditEvent.action == "login")
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    if not previous_login:
        return LoginRiskAssessment(score=0, flags=[])

    try:
        details = json.loads(previous_login.details_json or "{}")
    except json.JSONDecodeError:
        details = {}

    flags: list[str] = []
    score = 0
    previous_ip = str(details.get("client_ip") or "")
    previous_ua = str(details.get("user_agent") or "")
    previous_geo = str(details.get("client_geo_country") or "").upper()
    if previous_ip and client_ip and previous_ip != client_ip:
        flags.append("ip_changed")
        score += 40
    if previous_ua and user_agent and previous_ua != user_agent:
        flags.append("user_agent_changed")
        score += 25
    if previous_geo and client_geo_country and previous_geo != client_geo_country:
        flags.append("geo_changed")
        score += 35
    if previous_geo and not client_geo_country:
        flags.append("geo_signal_missing")
        score += 10
    return LoginRiskAssessment(score=score, flags=flags)


def _should_send_login_anomaly_alert(db: Session, *, user_id: int) -> tuple[bool, str | None, int]:
    """
    Decide whether login anomaly notification should be emitted now.

    The policy combines:
    - per-hour cap to avoid alert storms
    - cooldown window between sent alerts for same user
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    max_alerts_per_hour = max(int(settings.login_anomaly_max_alerts_per_hour), 1)
    cooldown_seconds = max(int(settings.login_anomaly_alert_cooldown_seconds), 0)
    since = now - timedelta(hours=1)

    rows = (
        db.query(AuditEvent.created_at, AuditEvent.details_json)
        .filter(
            AuditEvent.user_id == user_id,
            AuditEvent.action == "login_anomaly",
            AuditEvent.created_at >= since,
        )
        .order_by(AuditEvent.created_at.desc())
        .all()
    )
    sent_count = 0
    latest_sent_at: datetime | None = None
    for created_at, details_json in rows:
        details = _safe_load_json(details_json)
        # Legacy records without `alert_sent` are treated as sent for conservative suppression.
        alert_sent = bool(details.get("alert_sent", True))
        if not alert_sent:
            continue
        sent_count += 1
        if latest_sent_at is None:
            latest_sent_at = created_at

    if sent_count >= max_alerts_per_hour:
        return False, "hourly_limit", sent_count

    if latest_sent_at and cooldown_seconds > 0:
        elapsed = (now - latest_sent_at).total_seconds()
        if elapsed < cooldown_seconds:
            return False, "cooldown", sent_count

    return True, None, sent_count


def _safe_load_json(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _build_rate_limit_key(*, scope: str, principal: str, client_ip: str) -> str:
    return f"{scope}:{principal or 'unknown'}:{client_ip or 'unknown'}"
