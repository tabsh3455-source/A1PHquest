from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.routers import risk as risk_router
from app.schemas import RiskDryRunCheckRequest


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_user(db: Session, username: str) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_get_risk_rule_returns_404_when_not_configured():
    with _build_session() as db:
        user = _create_user(db, "risk-router-404")
        try:
            risk_router.get_risk_rule(db=db, current_user=user)
            raise AssertionError("Expected risk router to return 404 for missing risk rule")
        except HTTPException as exc:
            assert exc.status_code == 404
            assert "not configured" in str(exc.detail).lower()


def test_dry_run_check_keeps_working_without_risk_rule():
    with _build_session() as db:
        user = _create_user(db, "risk-router-dry-run")
        response = risk_router.dry_run_check(
            payload=RiskDryRunCheckRequest(
                order_notional=100,
                projected_daily_loss=0,
                projected_position_ratio=0.2,
            ),
            db=db,
            current_user=user,
        )
        assert response.allowed is True
