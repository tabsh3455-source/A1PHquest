from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.bootstrap import ensure_bootstrap_admin
from app.models import Base, AuditEvent, User


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_ensure_bootstrap_admin_creates_first_admin(monkeypatch):
    with _build_session() as db:
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_enabled", True)
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_username", "bootstrap-admin")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_email", "bootstrap-admin@example.com")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_password", "StrongBootstrapPass123")

        admin = ensure_bootstrap_admin(db)

        assert admin is not None
        assert admin.role == "admin"
        assert admin.username == "bootstrap-admin"
        assert db.query(User).filter(User.role == "admin").count() == 1
        assert db.query(AuditEvent).filter(AuditEvent.action == "bootstrap_admin_create").count() == 1


def test_ensure_bootstrap_admin_is_idempotent_when_admin_exists(monkeypatch):
    with _build_session() as db:
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_enabled", True)
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_username", "bootstrap-admin")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_email", "bootstrap-admin@example.com")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_password", "StrongBootstrapPass123")

        first = ensure_bootstrap_admin(db)
        second = ensure_bootstrap_admin(db)

        assert first is not None
        assert second is not None
        assert first.id == second.id
        assert db.query(User).filter(User.role == "admin").count() == 1
        assert db.query(AuditEvent).filter(AuditEvent.action == "bootstrap_admin_create").count() == 1


def test_ensure_bootstrap_admin_skips_when_password_missing(monkeypatch):
    with _build_session() as db:
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_enabled", True)
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_username", "bootstrap-admin")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_email", "bootstrap-admin@example.com")
        monkeypatch.setattr("app.bootstrap.settings.bootstrap_admin_password", "")

        admin = ensure_bootstrap_admin(db)

        assert admin is None
        assert db.query(User).count() == 0
