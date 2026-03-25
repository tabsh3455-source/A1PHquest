from types import SimpleNamespace
import json

from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, User
from app.routers import auth
from app.schemas import UserLoginRequest
from app.security import hash_password


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _build_request(*, ip: str, ua: str, country: str | None = None, extra_headers: dict[str, str] | None = None):
    headers = {"user-agent": ua}
    if country:
        headers["x-geo-country"] = country
    if extra_headers:
        headers.update(extra_headers)
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=ip))


def _login(db: Session, *, username: str, password: str, request) -> None:
    auth.login(
        UserLoginRequest(username=username, password=password),
        request,
        Response(),
        db,
    )


class _FakeNotificationService:
    def __init__(self) -> None:
        self.alerts: list[tuple[int, str, str]] = []

    def send_security_alert(self, user_id: int, title: str, details: str) -> dict[str, str]:
        self.alerts.append((user_id, title, details))
        return {"telegram": "sent", "email": "sent"}


def test_login_anomaly_scoring_triggers_alert_when_threshold_reached(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-a",
            email="risk-user-a@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)

        _login(db, username="risk-user-a", password="StrongPass123!", request=_build_request(ip="1.1.1.1", ua="ua-a", country="CN"))
        _login(db, username="risk-user-a", password="StrongPass123!", request=_build_request(ip="2.2.2.2", ua="ua-b", country="US"))

        assert len(fake_notification.alerts) == 1
        row = db.query(AuditEvent).filter(AuditEvent.action == "login_anomaly").first()
        assert row is not None
        details = json.loads(row.details_json)
        assert details["delivery"]["telegram"] == "sent"
        assert details["delivery"]["email"] == "sent"


def test_login_anomaly_scoring_no_alert_for_low_risk_change(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-b",
            email="risk-user-b@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)

        _login(db, username="risk-user-b", password="StrongPass123!", request=_build_request(ip="3.3.3.3", ua="ua-a", country="CN"))
        _login(db, username="risk-user-b", password="StrongPass123!", request=_build_request(ip="3.3.3.3", ua="ua-b", country="CN"))

        assert fake_notification.alerts == []
        anomaly_rows = db.query(AuditEvent).filter(AuditEvent.action == "login_anomaly").count()
        assert anomaly_rows == 0


def test_login_uses_proxy_headers_for_ip_and_geo_signals(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-c",
            email="risk-user-c@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)

        _login(
            db,
            username="risk-user-c",
            password="StrongPass123!",
            request=_build_request(
                ip="10.0.0.10",
                ua="ua-c",
                extra_headers={
                    "cf-connecting-ip": "198.51.100.10",
                    "cf-ipcountry": "SG",
                },
            ),
        )
        row = db.query(AuditEvent).filter(AuditEvent.action == "login").order_by(AuditEvent.id.desc()).first()
        assert row is not None
        details = json.loads(row.details_json)
        assert details["client_ip"] == "198.51.100.10"
        assert details["client_ip_source"] == "cf-connecting-ip"
        assert details["client_geo_country"] == "SG"
        assert details["client_geo_source"] == "cf-ipcountry"


def test_login_prefers_x_real_ip_over_spoofed_forwarded_for(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-proxy",
            email="risk-user-proxy@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)

        _login(
            db,
            username="risk-user-proxy",
            password="StrongPass123!",
            request=_build_request(
                ip="10.0.0.10",
                ua="ua-proxy",
                extra_headers={
                    "x-forwarded-for": "203.0.113.9, 198.51.100.77",
                    "x-real-ip": "198.51.100.77",
                },
            ),
        )
        row = db.query(AuditEvent).filter(AuditEvent.action == "login").order_by(AuditEvent.id.desc()).first()
        assert row is not None
        details = json.loads(row.details_json)
        assert details["client_ip"] == "198.51.100.77"
        assert details["client_ip_source"] == "x-real-ip"


def test_login_anomaly_alert_is_suppressed_by_cooldown(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-d",
            email="risk-user-d@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)
        monkeypatch.setattr(auth.settings, "login_anomaly_alert_cooldown_seconds", 3600)
        monkeypatch.setattr(auth.settings, "login_anomaly_max_alerts_per_hour", 10)

        _login(db, username="risk-user-d", password="StrongPass123!", request=_build_request(ip="11.11.11.1", ua="ua-d1", country="CN"))
        _login(db, username="risk-user-d", password="StrongPass123!", request=_build_request(ip="11.11.11.2", ua="ua-d2", country="US"))
        _login(db, username="risk-user-d", password="StrongPass123!", request=_build_request(ip="11.11.11.3", ua="ua-d3", country="SG"))

        assert len(fake_notification.alerts) == 1
        rows = (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "login_anomaly")
            .order_by(AuditEvent.id.asc())
            .all()
        )
        assert len(rows) == 2
        first = json.loads(rows[0].details_json)
        second = json.loads(rows[1].details_json)
        assert first["alert_sent"] is True
        assert second["alert_sent"] is False
        assert second["suppressed_reason"] == "cooldown"
        assert second["delivery"]["suppressed"] == "cooldown"


def test_login_anomaly_alert_is_suppressed_by_hourly_limit(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
    with _build_session() as db:
        user = User(
            username="risk-user-e",
            email="risk-user-e@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)
        monkeypatch.setattr(auth.settings, "login_anomaly_alert_cooldown_seconds", 0)
        monkeypatch.setattr(auth.settings, "login_anomaly_max_alerts_per_hour", 1)

        _login(db, username="risk-user-e", password="StrongPass123!", request=_build_request(ip="21.21.21.1", ua="ua-e1", country="CN"))
        _login(db, username="risk-user-e", password="StrongPass123!", request=_build_request(ip="21.21.21.2", ua="ua-e2", country="US"))
        _login(db, username="risk-user-e", password="StrongPass123!", request=_build_request(ip="21.21.21.3", ua="ua-e3", country="JP"))

        assert len(fake_notification.alerts) == 1
        rows = (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "login_anomaly")
            .order_by(AuditEvent.id.asc())
            .all()
        )
        assert len(rows) == 2
        second = json.loads(rows[1].details_json)
        assert second["alert_sent"] is False
        assert second["suppressed_reason"] == "hourly_limit"
        assert second["delivery"]["suppressed"] == "hourly_limit"


def test_login_ignores_untrusted_proxy_headers_by_default(monkeypatch):
    monkeypatch.setattr(auth.settings, "trust_proxy_headers", False)
    with _build_session() as db:
        user = User(
            username="risk-user-f",
            email="risk-user-f@example.com",
            password_hash=hash_password("StrongPass123!"),
            role="user",
            is_active=True,
            totp_secret_encrypted=None,
        )
        db.add(user)
        db.commit()

        fake_notification = _FakeNotificationService()
        monkeypatch.setattr(auth, "notification_service", fake_notification)

        _login(
            db,
            username="risk-user-f",
            password="StrongPass123!",
            request=_build_request(
                ip="10.0.0.10",
                ua="ua-f",
                extra_headers={
                    "cf-connecting-ip": "198.51.100.10",
                    "cf-ipcountry": "SG",
                },
            ),
        )
        row = db.query(AuditEvent).filter(AuditEvent.action == "login").order_by(AuditEvent.id.desc()).first()
        assert row is not None
        details = json.loads(row.details_json)
        assert details["client_ip"] == "10.0.0.10"
        assert details["client_ip_source"] == "request.client.host"
        assert details["client_geo_country"] == ""
        assert details["client_geo_source"] == "proxy_headers_disabled"
