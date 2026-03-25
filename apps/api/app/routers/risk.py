from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..deps import get_current_verified_user, require_step_up_user
from ..models import RiskRule, User
from ..schemas import (
    RiskDryRunCheckRequest,
    RiskDryRunCheckResponse,
    RiskRuleResponse,
    RiskRuleUpsertRequest,
)
from ..services.notifications import NotificationService
from ..services.risk_service import RiskService
from ..tenant import with_tenant

router = APIRouter(prefix="/api/risk-rules", tags=["risk-rules"])
notification_service = NotificationService()
risk_service = RiskService()


@router.put("", response_model=RiskRuleResponse)
def upsert_risk_rule(
    payload: RiskRuleUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    rule = with_tenant(db.query(RiskRule), RiskRule, current_user.id).first()
    if not rule:
        rule = RiskRule(user_id=current_user.id)

    rule.max_order_notional = payload.max_order_notional
    rule.max_daily_loss = payload.max_daily_loss
    rule.max_position_ratio = payload.max_position_ratio
    rule.max_cancel_rate_per_minute = payload.max_cancel_rate_per_minute
    rule.circuit_breaker_enabled = payload.circuit_breaker_enabled
    db.add(rule)
    db.commit()
    db.refresh(rule)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="risk_rule_upsert",
        resource="risk_rule",
        resource_id=str(rule.id),
    )
    return rule


@router.get("", response_model=RiskRuleResponse)
def get_risk_rule(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    rule = with_tenant(db.query(RiskRule), RiskRule, current_user.id).first()
    if not rule:
        rule = RiskRule(
            user_id=current_user.id,
            max_order_notional=0,
            max_daily_loss=0,
            max_position_ratio=1,
            max_cancel_rate_per_minute=60,
            circuit_breaker_enabled=True,
        )
        db.add(rule)
        db.commit()
        db.refresh(rule)
    return rule


@router.post("/dry-run-check", response_model=RiskDryRunCheckResponse)
def dry_run_check(
    payload: RiskDryRunCheckRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    decision = risk_service.evaluate_order(
        db,
        user_id=current_user.id,
        order_notional=payload.order_notional,
        projected_daily_loss=payload.projected_daily_loss,
        projected_position_ratio=payload.projected_position_ratio,
    )
    if not decision.allowed:
        notification_service.send_risk_alert(current_user.id, decision.reason)
    return RiskDryRunCheckResponse(
        allowed=decision.allowed,
        reason=decision.reason,
        realized_daily_loss=decision.realized_daily_loss,
        evaluated_daily_loss=decision.evaluated_daily_loss,
    )
