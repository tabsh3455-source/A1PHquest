from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .audit import log_audit_event
from .config import get_settings
from .db import SessionLocal
from .models import User
from .security import hash_password

settings = get_settings()
logger = logging.getLogger(__name__)


def ensure_bootstrap_admin(db: Session | None = None) -> User | None:
    """
    Create the first administrator account from deployment config if needed.

    This keeps one-click deployments usable without a separate manual bootstrap
    step while remaining idempotent for subsequent restarts.
    """
    if not settings.bootstrap_admin_enabled:
        return None

    username = str(settings.bootstrap_admin_username or "").strip()
    email = str(settings.bootstrap_admin_email or "").strip().lower()
    password = str(settings.bootstrap_admin_password or "")

    if not username or not email or not password:
        logger.info("Bootstrap admin skipped because BOOTSTRAP_ADMIN_* is incomplete.")
        return None

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        existing_admin = db.query(User).filter(User.role == "admin").order_by(User.id.asc()).first()
        if existing_admin:
            return existing_admin

        username_conflict = db.query(User).filter(User.username == username).first()
        if username_conflict:
            logger.warning(
                "Bootstrap admin skipped because username '%s' already exists and is not an admin.",
                username,
            )
            return None

        email_conflict = db.query(User).filter(User.email == email).first()
        if email_conflict:
            logger.warning(
                "Bootstrap admin skipped because email '%s' already exists and is not an admin.",
                email,
            )
            return None

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        log_audit_event(
            db,
            user_id=user.id,
            action="bootstrap_admin_create",
            resource="user",
            resource_id=str(user.id),
            details={"username": user.username, "email": user.email},
        )
        logger.info("Bootstrapped initial admin account '%s'.", user.username)
        return user
    finally:
        if own_session:
            db.close()
