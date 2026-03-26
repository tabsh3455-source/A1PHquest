from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..ai_provider_security import normalize_and_validate_provider_base_url
from ..audit import log_audit_event
from ..db import get_db
from ..deps import get_current_verified_user, require_step_up_user
from ..kms import build_kms_provider
from ..models import AiAutopilotDecisionRun, AiAutopilotPolicy, AiProviderCredential, ExchangeAccount, Strategy, User
from ..schemas import (
    AiAutopilotDecisionResponse,
    AiAutopilotPolicyCreateRequest,
    AiAutopilotPolicyResponse,
    AiAutopilotPolicyUpdateRequest,
    AiAutopilotRunRequest,
    AiProviderCreateRequest,
    AiProviderResponse,
    AiProviderUpdateRequest,
)
from ..services.ai_autopilot import AiAutopilotService
from ..services.market_data import normalize_market_symbol
from ..tenant import with_tenant

router = APIRouter(prefix="/api/ai", tags=["ai-autopilot"])
kms = build_kms_provider()


def _get_ai_service(request: Request) -> AiAutopilotService:
    return request.app.state.ai_autopilot_service


@router.get("/providers", response_model=list[AiProviderResponse])
def list_ai_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    providers = (
        with_tenant(db.query(AiProviderCredential), AiProviderCredential, current_user.id)
        .order_by(AiProviderCredential.id.desc())
        .all()
    )
    return [_to_provider_response(item) for item in providers]


@router.post("/providers", response_model=AiProviderResponse, status_code=status.HTTP_201_CREATED)
def create_ai_provider(
    payload: AiProviderCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    normalized_base_url = normalize_and_validate_provider_base_url(payload.base_url)
    provider = AiProviderCredential(
        user_id=current_user.id,
        name=payload.name.strip(),
        provider_type=payload.provider_type,
        base_url=normalized_base_url,
        model_name=payload.model_name.strip(),
        api_key_encrypted=kms.encrypt(payload.api_key.strip()),
        is_active=bool(payload.is_active),
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_provider_create",
        resource="ai_provider",
        resource_id=str(provider.id),
        details={"provider_type": provider.provider_type, "model_name": provider.model_name},
    )
    return _to_provider_response(provider)


@router.put("/providers/{provider_id}", response_model=AiProviderResponse)
def update_ai_provider(
    provider_id: int,
    payload: AiProviderUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    provider = (
        with_tenant(db.query(AiProviderCredential), AiProviderCredential, current_user.id)
        .filter(AiProviderCredential.id == provider_id)
        .first()
    )
    if not provider:
        raise HTTPException(status_code=404, detail="AI provider not found")

    normalized_base_url = normalize_and_validate_provider_base_url(payload.base_url)
    provider.name = payload.name.strip()
    provider.provider_type = payload.provider_type
    provider.base_url = normalized_base_url
    provider.model_name = payload.model_name.strip()
    provider.is_active = bool(payload.is_active)
    if payload.api_key:
        provider.api_key_encrypted = kms.encrypt(payload.api_key.strip())

    db.add(provider)
    db.commit()
    db.refresh(provider)
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_provider_update",
        resource="ai_provider",
        resource_id=str(provider.id),
        details={"provider_type": provider.provider_type, "model_name": provider.model_name},
    )
    return _to_provider_response(provider)


@router.get("/policies", response_model=list[AiAutopilotPolicyResponse])
def list_ai_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    policies = (
        with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, current_user.id)
        .order_by(AiAutopilotPolicy.id.desc())
        .all()
    )
    provider_names = {
        int(item.id): item.name
        for item in with_tenant(db.query(AiProviderCredential), AiProviderCredential, current_user.id).all()
    }
    return [_to_policy_response(item, provider_names=provider_names) for item in policies]


@router.post("/policies", response_model=AiAutopilotPolicyResponse, status_code=status.HTTP_201_CREATED)
def create_ai_policy(
    payload: AiAutopilotPolicyCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    _validate_policy_scope(
        db=db,
        user_id=current_user.id,
        provider_id=payload.provider_id,
        exchange_account_id=payload.exchange_account_id,
        symbol=payload.symbol,
        strategy_ids=payload.strategy_ids,
    )
    policy = AiAutopilotPolicy(
        user_id=current_user.id,
        provider_id=payload.provider_id,
        exchange_account_id=payload.exchange_account_id,
        name=payload.name.strip(),
        symbol=payload.symbol.strip().upper(),
        interval=payload.interval,
        strategy_ids_json=json.dumps(payload.strategy_ids, ensure_ascii=False),
        allowed_actions_json=json.dumps(payload.allowed_actions, ensure_ascii=False),
        status=payload.status,
        execution_mode=payload.execution_mode,
        decision_interval_seconds=int(payload.decision_interval_seconds),
        minimum_confidence=float(payload.minimum_confidence),
        max_actions_per_hour=int(payload.max_actions_per_hour),
        custom_prompt=(payload.custom_prompt or "").strip() or None,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    provider_names = {payload.provider_id: _get_provider_name(db, current_user.id, payload.provider_id)}
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_policy_create",
        resource="ai_policy",
        resource_id=str(policy.id),
        details={"execution_mode": policy.execution_mode, "status": policy.status},
    )
    return _to_policy_response(policy, provider_names=provider_names)


@router.put("/policies/{policy_id}", response_model=AiAutopilotPolicyResponse)
def update_ai_policy(
    policy_id: int,
    payload: AiAutopilotPolicyUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    policy = (
        with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, current_user.id)
        .filter(AiAutopilotPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="AI policy not found")

    _validate_policy_scope(
        db=db,
        user_id=current_user.id,
        provider_id=payload.provider_id,
        exchange_account_id=payload.exchange_account_id,
        symbol=payload.symbol,
        strategy_ids=payload.strategy_ids,
    )
    policy.provider_id = payload.provider_id
    policy.exchange_account_id = payload.exchange_account_id
    policy.name = payload.name.strip()
    policy.symbol = payload.symbol.strip().upper()
    policy.interval = payload.interval
    policy.strategy_ids_json = json.dumps(payload.strategy_ids, ensure_ascii=False)
    policy.allowed_actions_json = json.dumps(payload.allowed_actions, ensure_ascii=False)
    policy.status = payload.status
    policy.execution_mode = payload.execution_mode
    policy.decision_interval_seconds = int(payload.decision_interval_seconds)
    policy.minimum_confidence = float(payload.minimum_confidence)
    policy.max_actions_per_hour = int(payload.max_actions_per_hour)
    policy.custom_prompt = (payload.custom_prompt or "").strip() or None

    db.add(policy)
    db.commit()
    db.refresh(policy)
    provider_names = {payload.provider_id: _get_provider_name(db, current_user.id, payload.provider_id)}
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_policy_update",
        resource="ai_policy",
        resource_id=str(policy.id),
        details={"execution_mode": policy.execution_mode, "status": policy.status},
    )
    return _to_policy_response(policy, provider_names=provider_names)


@router.post("/policies/{policy_id}/enable", response_model=AiAutopilotPolicyResponse)
def enable_ai_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    policy = (
        with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, current_user.id)
        .filter(AiAutopilotPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="AI policy not found")
    policy.status = "enabled"
    db.add(policy)
    db.commit()
    db.refresh(policy)
    provider_names = {policy.provider_id: _get_provider_name(db, current_user.id, policy.provider_id)}
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_policy_enable",
        resource="ai_policy",
        resource_id=str(policy.id),
        details={"execution_mode": policy.execution_mode},
    )
    return _to_policy_response(policy, provider_names=provider_names)


@router.post("/policies/{policy_id}/disable", response_model=AiAutopilotPolicyResponse)
def disable_ai_policy(
    policy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    policy = (
        with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, current_user.id)
        .filter(AiAutopilotPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="AI policy not found")
    policy.status = "disabled"
    db.add(policy)
    db.commit()
    db.refresh(policy)
    provider_names = {policy.provider_id: _get_provider_name(db, current_user.id, policy.provider_id)}
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_policy_disable",
        resource="ai_policy",
        resource_id=str(policy.id),
        details={"execution_mode": policy.execution_mode},
    )
    return _to_policy_response(policy, provider_names=provider_names)


@router.post("/policies/{policy_id}/run", response_model=AiAutopilotDecisionResponse)
async def run_ai_policy_once(
    policy_id: int,
    payload: AiAutopilotRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    policy = (
        with_tenant(db.query(AiAutopilotPolicy), AiAutopilotPolicy, current_user.id)
        .filter(AiAutopilotPolicy.id == policy_id)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="AI policy not found")

    run = await _get_ai_service(request).run_policy_once(
        policy_id=policy.id,
        trigger_source="manual",
        dry_run_override=payload.dry_run_override,
    )
    db.expire_all()
    fresh = db.query(AiAutopilotDecisionRun).filter(AiAutopilotDecisionRun.id == run.id).first()
    if not fresh:
        raise HTTPException(status_code=500, detail="AI decision run was not persisted")
    log_audit_event(
        db,
        user_id=current_user.id,
        action="ai_policy_run",
        resource="ai_policy",
        resource_id=str(policy.id),
        details={"decision_run_id": fresh.id, "status": fresh.status, "action": fresh.action},
    )
    return _to_decision_response(fresh)


@router.get("/decisions", response_model=list[AiAutopilotDecisionResponse])
def list_ai_decisions(
    policy_id: int | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    normalized_limit = max(min(int(limit), 100), 1)
    query = with_tenant(db.query(AiAutopilotDecisionRun), AiAutopilotDecisionRun, current_user.id)
    if policy_id is not None:
        query = query.filter(AiAutopilotDecisionRun.policy_id == int(policy_id))
    rows = query.order_by(AiAutopilotDecisionRun.id.desc()).limit(normalized_limit).all()
    return [_to_decision_response(item) for item in rows]


def _validate_policy_scope(
    *,
    db: Session,
    user_id: int,
    provider_id: int,
    exchange_account_id: int,
    symbol: str,
    strategy_ids: list[int],
) -> None:
    provider = (
        with_tenant(db.query(AiProviderCredential), AiProviderCredential, user_id)
        .filter(AiProviderCredential.id == provider_id)
        .first()
    )
    if not provider:
        raise HTTPException(status_code=400, detail=f"provider_id {provider_id} not found")

    account = (
        with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id)
        .filter(ExchangeAccount.id == exchange_account_id)
        .first()
    )
    if not account:
        raise HTTPException(status_code=400, detail=f"exchange_account_id {exchange_account_id} not found")

    normalized_symbol = normalize_market_symbol(account.exchange, symbol)
    strategies = (
        with_tenant(db.query(Strategy), Strategy, user_id)
        .filter(Strategy.id.in_(strategy_ids))
        .all()
    )
    if len(strategies) != len(strategy_ids):
        raise HTTPException(status_code=400, detail="One or more strategy_ids were not found")

    for strategy in strategies:
        config = _safe_load_json_dict(strategy.config_json)
        config_symbol = normalize_market_symbol(account.exchange, str(config.get("symbol") or ""))
        if int(config.get("exchange_account_id") or 0) != int(exchange_account_id) or config_symbol != normalized_symbol:
            raise HTTPException(
                status_code=400,
                detail=f"strategy {strategy.id} does not match the selected account/symbol scope",
            )
        if strategy.strategy_type not in {"grid", "dca", "combo_grid_dca"}:
            raise HTTPException(
                status_code=400,
                detail=f"strategy {strategy.id} uses unsupported type '{strategy.strategy_type}'",
            )


def _to_provider_response(provider: AiProviderCredential) -> AiProviderResponse:
    return AiProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        model_name=provider.model_name,
        is_active=provider.is_active,
        has_api_key=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _to_policy_response(
    policy: AiAutopilotPolicy,
    *,
    provider_names: dict[int, str],
) -> AiAutopilotPolicyResponse:
    return AiAutopilotPolicyResponse(
        id=policy.id,
        name=policy.name,
        provider_id=policy.provider_id,
        provider_name=provider_names.get(int(policy.provider_id), f"Provider {policy.provider_id}"),
        exchange_account_id=policy.exchange_account_id,
        symbol=policy.symbol,
        interval=policy.interval,
        strategy_ids=_safe_load_json_list(policy.strategy_ids_json),
        allowed_actions=_safe_load_action_list(policy.allowed_actions_json),
        execution_mode=policy.execution_mode,
        status=policy.status,
        decision_interval_seconds=int(policy.decision_interval_seconds),
        minimum_confidence=float(policy.minimum_confidence or 0),
        max_actions_per_hour=int(policy.max_actions_per_hour),
        custom_prompt=policy.custom_prompt,
        last_run_at=policy.last_run_at,
        last_decision_at=policy.last_decision_at,
        last_error=policy.last_error,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _to_decision_response(row: AiAutopilotDecisionRun) -> AiAutopilotDecisionResponse:
    return AiAutopilotDecisionResponse(
        id=row.id,
        policy_id=row.policy_id,
        provider_id=row.provider_id,
        exchange_account_id=row.exchange_account_id,
        trigger_source=row.trigger_source,
        status=row.status,
        action=row.action,
        target_strategy_id=row.target_strategy_id,
        confidence=float(row.confidence or 0),
        rationale=row.rationale,
        factors=_safe_load_json_dict(row.factors_json),
        context=_safe_load_json_dict(row.context_json),
        raw_response=_safe_load_json_dict(row.raw_response_json),
        execution_result=_safe_load_json_dict(row.execution_result_json),
        created_at=row.created_at,
    )


def _get_provider_name(db: Session, user_id: int, provider_id: int) -> str:
    provider = (
        with_tenant(db.query(AiProviderCredential), AiProviderCredential, user_id)
        .filter(AiProviderCredential.id == provider_id)
        .first()
    )
    return provider.name if provider else f"Provider {provider_id}"


def _safe_load_json_dict(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _safe_load_json_list(raw: str) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return sorted({int(item) for item in value if int(item) > 0})


def _safe_load_action_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    allowed = {"activate_strategy", "stop_strategy", "create_strategy_version"}
    normalized = [str(item) for item in value if str(item) in allowed]
    order = {"activate_strategy": 0, "stop_strategy": 1, "create_strategy_version": 2}
    return sorted(set(normalized), key=lambda item: order.get(item, 99))
