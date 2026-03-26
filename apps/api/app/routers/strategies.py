from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..deps import get_current_verified_user, require_step_up_user
from ..events import build_ws_event
from ..models import AuditEvent, ExchangeAccount, Strategy, StrategyRuntime, User
from ..schemas import (
    RuntimeConsistencyResponse,
    StrategyCreateRequest,
    StrategyResponse,
    StrategyRuntimeResponse,
    StrategyUpdateRequest,
)
from ..services.strategy_templates import (
    StrategyTemplateSpec,
    get_strategy_template,
    validate_strategy_template_config,
)
from ..services.strategy_supervisor import (
    RuntimeState,
    StrategySupervisorClient,
    StrategySupervisorError,
    StrategySupervisorUnavailableError,
)
from ..services.risk_service import RiskService
from ..tenant import with_tenant
from ..ws_manager import WsManager

router = APIRouter(prefix="/api/strategies", tags=["strategies"])
supervisor = StrategySupervisorClient()
risk_service = RiskService()


def _get_ws_manager(request: Request) -> WsManager:
    return request.app.state.ws_manager


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    template, config = _validate_strategy_config(payload.template_key or payload.strategy_type or "", payload.config)
    strategy = Strategy(
        user_id=current_user.id,
        name=payload.name,
        template_key=template.template_key,
        strategy_type=template.runtime_strategy_type,
        config_json=json.dumps(config, ensure_ascii=False),
        status="stopped",
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    log_audit_event(
        db,
        user_id=current_user.id,
        action="strategy_create",
        resource="strategy",
        resource_id=str(strategy.id),
        details={"strategy_type": strategy.strategy_type, "template_key": strategy.template_key},
    )
    return _to_strategy_response(strategy)


@router.get("", response_model=list[StrategyResponse])
def list_strategies(db: Session = Depends(get_db), current_user: User = Depends(get_current_verified_user)):
    strategies = (
        with_tenant(db.query(Strategy), Strategy, current_user.id)
        .order_by(Strategy.id.desc())
        .all()
    )
    return [_to_strategy_response(strategy) for strategy in strategies]


@router.put("/{strategy_id}", response_model=StrategyResponse)
def update_strategy(
    strategy_id: int,
    payload: StrategyUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    strategy = with_tenant(db.query(Strategy), Strategy, current_user.id).filter(
        Strategy.id == strategy_id
    ).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if strategy.status in {"starting", "running", "stopping"}:
        raise HTTPException(status_code=409, detail="Stop the strategy before editing it")

    template, config = _validate_strategy_config(payload.template_key or payload.strategy_type or "", payload.config)
    previous_type = strategy.strategy_type
    previous_template_key = strategy.template_key
    strategy.name = payload.name
    strategy.template_key = template.template_key
    strategy.strategy_type = template.runtime_strategy_type
    strategy.config_json = json.dumps(config, ensure_ascii=False)
    db.add(strategy)
    db.commit()
    db.refresh(strategy)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="strategy_update",
        resource="strategy",
        resource_id=str(strategy.id),
        details={
            "previous_strategy_type": previous_type,
            "previous_template_key": previous_template_key,
            "strategy_type": strategy.strategy_type,
            "template_key": strategy.template_key,
        },
    )
    return _to_strategy_response(strategy)


@router.post("/{strategy_id}/start", response_model=StrategyRuntimeResponse)
async def start_strategy(
    strategy_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    strategy = with_tenant(db.query(Strategy), Strategy, current_user.id).filter(
        Strategy.id == strategy_id
    ).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if strategy.status in {"running", "starting", "stopping"} and strategy.runtime_ref:
        # Idempotent start: if runtime is already active, return current runtime snapshot.
        try:
            runtime_state = supervisor.get_runtime(strategy.runtime_ref)
            strategy.status = runtime_state.status
            db.add(strategy)
            runtime = _upsert_runtime(db, current_user.id, strategy.id, runtime_state)
            new_events = _persist_runtime_audit_events(
                db=db,
                user_id=current_user.id,
                strategy=strategy,
                runtime=runtime,
                state=runtime_state,
            )
            db.commit()
            db.refresh(runtime)
            await _emit_runtime_trace_events(
                ws=_get_ws_manager(request),
                user_id=current_user.id,
                strategy_id=strategy.id,
                runtime_ref=strategy.runtime_ref,
                events=new_events,
            )
            return _to_runtime_response(
                strategy,
                runtime,
                recent_events=_normalize_runtime_events(runtime_state.recent_events),
            )
        except StrategySupervisorUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except StrategySupervisorError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    template = _resolve_strategy_template_for_strategy(strategy)
    config = _validate_existing_strategy(strategy, template)
    if not template.live_supported or strategy.strategy_type not in {"grid", "futures_grid", "dca", "combo_grid_dca"}:
        raise HTTPException(
            status_code=400,
            detail=f"template '{template.template_key}' is not enabled for live runtime yet",
        )

    exchange_account_id = int(config["exchange_account_id"])
    exchange_account = _get_owned_exchange_account(db, current_user.id, exchange_account_id)
    if exchange_account.exchange not in {"binance", "okx"}:
        raise HTTPException(
            status_code=400,
            detail=f"exchange '{exchange_account.exchange}' is not supported for live runtime",
        )
    if not risk_service.has_configured_rule(db, user_id=current_user.id):
        raise HTTPException(
            status_code=403,
            detail="Risk rule is required before starting live strategies",
        )

    try:
        runtime_state = supervisor.start_strategy(
            user_id=current_user.id,
            strategy_id=strategy.id,
            strategy_type=strategy.strategy_type,
            config_json=strategy.config_json,
        )
    except StrategySupervisorUnavailableError as exc:
        await _emit_runtime_error(
            db=db,
            ws=_get_ws_manager(request),
            user=current_user,
            strategy=strategy,
            action="strategy_start_failed",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StrategySupervisorError as exc:
        await _emit_runtime_error(
            db=db,
            ws=_get_ws_manager(request),
            user=current_user,
            strategy=strategy,
            action="strategy_start_failed",
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    strategy.runtime_ref = runtime_state.runtime_ref
    strategy.status = runtime_state.status
    db.add(strategy)
    runtime = _upsert_runtime(db, current_user.id, strategy.id, runtime_state)
    new_events = _persist_runtime_audit_events(
        db=db,
        user_id=current_user.id,
        strategy=strategy,
        runtime=runtime,
        state=runtime_state,
    )
    db.commit()
    db.refresh(runtime)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="strategy_start",
        resource="strategy",
        resource_id=str(strategy.id),
        details={
            "runtime_ref": runtime_state.runtime_ref,
            "status": runtime_state.status,
            "exchange_account_id": exchange_account_id,
            "exchange": exchange_account.exchange,
        },
    )
    ws = _get_ws_manager(request)
    runtime_ref = strategy.runtime_ref or runtime_state.runtime_ref
    await _emit_runtime_trace_events(
        ws=ws,
        user_id=current_user.id,
        strategy_id=strategy.id,
        runtime_ref=runtime_ref,
        events=new_events,
    )
    await ws.push_to_user(
        current_user.id,
        build_ws_event(
            event_type="strategy_runtime_update",
            resource_id=runtime_ref,
            dedupe_key=f"runtime_update:{runtime_ref}:{runtime.status}:{runtime.last_event_seq}",
            payload={
                "strategy_id": strategy.id,
                "runtime_ref": runtime_ref,
                "status": runtime.status,
                "process_id": runtime.process_id,
                "started_at": runtime.started_at.isoformat() if runtime.started_at else None,
                "stopped_at": runtime.stopped_at.isoformat() if runtime.stopped_at else None,
                "last_heartbeat": runtime.last_heartbeat.isoformat() if runtime.last_heartbeat else None,
                "last_error": runtime.last_error,
            },
        ),
    )
    if runtime.last_error:
        await ws.push_to_user(
            current_user.id,
            build_ws_event(
                event_type="strategy_runtime_error",
                resource_id=runtime_ref,
                dedupe_key=f"runtime_error:{runtime_ref}:{runtime.last_error}",
                payload={
                    "strategy_id": strategy.id,
                    "runtime_ref": runtime_ref,
                    "error": runtime.last_error,
                },
            ),
        )
    return _to_runtime_response(
        strategy,
        runtime,
        recent_events=_normalize_runtime_events(runtime_state.recent_events),
    )


@router.post("/{strategy_id}/stop", response_model=StrategyRuntimeResponse)
async def stop_strategy(
    strategy_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    strategy = with_tenant(db.query(Strategy), Strategy, current_user.id).filter(
        Strategy.id == strategy_id
    ).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    if strategy.status not in {"starting", "running", "stopping"}:
        runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, current_user.id).filter(
            StrategyRuntime.strategy_id == strategy.id
        ).first()
        if runtime:
            return _to_runtime_response(strategy, runtime)
        raise HTTPException(status_code=409, detail="Strategy is not running")
    if not strategy.runtime_ref:
        runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, current_user.id).filter(
            StrategyRuntime.strategy_id == strategy.id
        ).first()
        if runtime:
            return _to_runtime_response(strategy, runtime)
        raise HTTPException(status_code=409, detail="Strategy has no runtime_ref")

    try:
        runtime_state = supervisor.stop_strategy(strategy.runtime_ref)
    except StrategySupervisorUnavailableError as exc:
        await _emit_runtime_error(
            db=db,
            ws=_get_ws_manager(request),
            user=current_user,
            strategy=strategy,
            action="strategy_stop_failed",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StrategySupervisorError as exc:
        await _emit_runtime_error(
            db=db,
            ws=_get_ws_manager(request),
            user=current_user,
            strategy=strategy,
            action="strategy_stop_failed",
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    strategy.status = runtime_state.status
    db.add(strategy)
    runtime = _upsert_runtime(db, current_user.id, strategy.id, runtime_state)
    new_events = _persist_runtime_audit_events(
        db=db,
        user_id=current_user.id,
        strategy=strategy,
        runtime=runtime,
        state=runtime_state,
    )
    db.commit()
    db.refresh(runtime)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="strategy_stop",
        resource="strategy",
        resource_id=str(strategy.id),
        details={
            "runtime_ref": strategy.runtime_ref,
            "status": runtime.status,
        },
    )
    ws = _get_ws_manager(request)
    runtime_ref = strategy.runtime_ref or runtime_state.runtime_ref
    await _emit_runtime_trace_events(
        ws=ws,
        user_id=current_user.id,
        strategy_id=strategy.id,
        runtime_ref=runtime_ref,
        events=new_events,
    )
    await ws.push_to_user(
        current_user.id,
        build_ws_event(
            event_type="strategy_runtime_update",
            resource_id=runtime_ref,
            dedupe_key=f"runtime_update:{runtime_ref}:{runtime.status}:{runtime.last_event_seq}",
            payload={
                "strategy_id": strategy.id,
                "runtime_ref": runtime_ref,
                "status": runtime.status,
                "process_id": runtime.process_id,
                "started_at": runtime.started_at.isoformat() if runtime.started_at else None,
                "stopped_at": runtime.stopped_at.isoformat() if runtime.stopped_at else None,
                "last_heartbeat": runtime.last_heartbeat.isoformat() if runtime.last_heartbeat else None,
                "last_error": runtime.last_error,
            },
        ),
    )
    if runtime.last_error:
        await ws.push_to_user(
            current_user.id,
            build_ws_event(
                event_type="strategy_runtime_error",
                resource_id=runtime_ref,
                dedupe_key=f"runtime_error:{runtime_ref}:{runtime.last_error}",
                payload={
                    "strategy_id": strategy.id,
                    "runtime_ref": runtime_ref,
                    "error": runtime.last_error,
                },
            ),
        )
    return _to_runtime_response(
        strategy,
        runtime,
        recent_events=_normalize_runtime_events(runtime_state.recent_events),
    )


@router.get("/{strategy_id}/runtime", response_model=StrategyRuntimeResponse)
def get_runtime(
    strategy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    strategy = with_tenant(db.query(Strategy), Strategy, current_user.id).filter(
        Strategy.id == strategy_id
    ).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, current_user.id).filter(
        StrategyRuntime.strategy_id == strategy.id
    ).first()
    if not runtime:
        return StrategyRuntimeResponse(
            strategy_id=strategy.id,
            runtime_ref=strategy.runtime_ref,
            status=strategy.status,
            process_id=None,
            started_at=None,
            stopped_at=None,
            last_heartbeat=None,
            last_error=None,
            last_event_seq=0,
            order_submitted_count=0,
            order_update_count=0,
            trade_fill_count=0,
            recent_events=[],
        )

    recent_events: list[dict[str, Any]] = []
    if strategy.runtime_ref and runtime.status in {"starting", "running", "stopping"}:
        try:
            runtime_state = supervisor.get_runtime(strategy.runtime_ref)
            strategy.status = runtime_state.status
            db.add(strategy)
            runtime = _upsert_runtime(db, current_user.id, strategy.id, runtime_state)
            _persist_runtime_audit_events(
                db=db,
                user_id=current_user.id,
                strategy=strategy,
                runtime=runtime,
                state=runtime_state,
            )
            db.commit()
            db.refresh(runtime)
            recent_events = _normalize_runtime_events(runtime_state.recent_events)
        except StrategySupervisorError:
            # For runtime query we keep cached status when supervisor is temporarily unavailable.
            pass

    return _to_runtime_response(strategy, runtime, recent_events=recent_events)


@router.get("/{strategy_id}/runtime/consistency", response_model=RuntimeConsistencyResponse)
def check_runtime_consistency(
    strategy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """Compare runtime observability fields in DB with worker-supervisor runtime view."""
    strategy = with_tenant(db.query(Strategy), Strategy, current_user.id).filter(
        Strategy.id == strategy_id
    ).first()
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")

    runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, current_user.id).filter(
        StrategyRuntime.strategy_id == strategy.id
    ).first()
    if not strategy.runtime_ref or not runtime:
        return RuntimeConsistencyResponse(
            strategy_id=strategy.id,
            runtime_ref=strategy.runtime_ref,
            consistent=False,
            checked_at=_utcnow(),
            fields_checked=[
                "status",
                "last_heartbeat",
                "last_error",
                "last_event_seq",
                "order_submitted_count",
                "order_update_count",
                "trade_fill_count",
            ],
            mismatches={
                "runtime": {
                    "db": "missing_runtime_row_or_ref",
                    "supervisor": "not_checked",
                }
            },
        )

    try:
        supervisor_state = supervisor.get_runtime(strategy.runtime_ref)
    except StrategySupervisorUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StrategySupervisorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Refresh runtime observability row with the latest supervisor snapshot before
    # checking consistency. This avoids false negatives caused by async heartbeat lag
    # between worker process updates and API-side persistence.
    strategy.status = supervisor_state.status
    db.add(strategy)
    runtime = _upsert_runtime(db, current_user.id, strategy.id, supervisor_state)
    _persist_runtime_audit_events(
        db=db,
        user_id=current_user.id,
        strategy=strategy,
        runtime=runtime,
        state=supervisor_state,
    )
    db.commit()
    db.refresh(runtime)

    mismatches = _build_runtime_mismatches(runtime, supervisor_state)
    return RuntimeConsistencyResponse(
        strategy_id=strategy.id,
        runtime_ref=strategy.runtime_ref,
        consistent=not mismatches,
        checked_at=_utcnow(),
        fields_checked=[
            "status",
            "last_heartbeat",
            "last_error",
            "last_event_seq",
            "order_submitted_count",
            "order_update_count",
            "trade_fill_count",
        ],
        mismatches=mismatches,
    )


def _validate_strategy_config(template_key: str, config: dict[str, Any]) -> tuple[StrategyTemplateSpec, dict[str, Any]]:
    try:
        return validate_strategy_template_config(template_key, config)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"invalid strategy config: {exc}",
        ) from exc


def _resolve_strategy_template_for_strategy(strategy: Strategy) -> StrategyTemplateSpec:
    candidate_key = str(strategy.template_key or "").strip()
    if not candidate_key or (
        candidate_key == "custom"
        and strategy.strategy_type in {"grid", "futures_grid", "dca", "combo_grid_dca", "funding_arbitrage", "spot_future_arbitrage"}
    ):
        candidate_key = strategy.strategy_type
    try:
        return get_strategy_template(candidate_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_existing_strategy(strategy: Strategy, template: StrategyTemplateSpec) -> dict[str, Any]:
    config = _safe_load_json(strategy.config_json)
    _, normalized = _validate_strategy_config(template.template_key, config)
    return normalized


def _safe_load_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid strategy config_json: {exc}") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="invalid strategy config_json: expected object")
    return value


def _get_owned_exchange_account(db: Session, user_id: int, account_id: int) -> ExchangeAccount:
    account = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id).filter(
        ExchangeAccount.id == account_id
    ).first()
    if not account:
        raise HTTPException(status_code=400, detail=f"exchange_account_id {account_id} not found")
    return account


def _upsert_runtime(db: Session, user_id: int, strategy_id: int, state: RuntimeState) -> StrategyRuntime:
    runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, user_id).filter(
        StrategyRuntime.strategy_id == strategy_id
    ).first()
    if not runtime:
        runtime = StrategyRuntime(strategy_id=strategy_id, user_id=user_id)
    runtime.process_id = state.process_id
    runtime.status = state.status
    runtime.started_at = state.started_at
    runtime.stopped_at = state.stopped_at
    runtime.last_heartbeat = state.last_heartbeat
    runtime.last_error = state.last_error
    runtime.last_event_seq = max(int(state.last_event_seq or 0), 0)
    runtime.order_submitted_count = max(int(state.order_submitted_count or 0), 0)
    runtime.order_update_count = max(int(state.order_update_count or 0), 0)
    runtime.trade_fill_count = max(int(state.trade_fill_count or 0), 0)
    db.add(runtime)
    return runtime


def _to_runtime_response(
    strategy: Strategy,
    runtime: StrategyRuntime,
    *,
    recent_events: list[dict[str, Any]] | None = None,
) -> StrategyRuntimeResponse:
    return StrategyRuntimeResponse(
        strategy_id=strategy.id,
        runtime_ref=strategy.runtime_ref,
        status=runtime.status,
        process_id=runtime.process_id,
        started_at=runtime.started_at,
        stopped_at=runtime.stopped_at,
        last_heartbeat=runtime.last_heartbeat,
        last_error=runtime.last_error,
        last_event_seq=runtime.last_event_seq,
        order_submitted_count=runtime.order_submitted_count,
        order_update_count=runtime.order_update_count,
        trade_fill_count=runtime.trade_fill_count,
        recent_events=recent_events or [],
    )


def _to_strategy_response(strategy: Strategy) -> StrategyResponse:
    template = _resolve_strategy_template_for_strategy(strategy)
    return StrategyResponse(
        id=strategy.id,
        name=strategy.name,
        template_key=template.template_key,
        template_display_name=template.display_name,
        category=template.category,
        execution_status=template.execution_status,
        market_scope=template.market_scope,
        risk_level=template.risk_level,
        live_supported=template.live_supported,
        strategy_type=strategy.strategy_type,
        config=_safe_load_json(strategy.config_json),
        status=strategy.status,
        runtime_ref=strategy.runtime_ref,
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
    )


def _persist_runtime_audit_events(
    *,
    db: Session,
    user_id: int,
    strategy: Strategy,
    runtime: StrategyRuntime,
    state: RuntimeState,
) -> list[dict[str, Any]]:
    events = _normalize_runtime_events(state.recent_events)
    if not events:
        return []

    events.sort(key=lambda item: int(item.get("seq", 0)))
    current_watermark = int(runtime.last_audited_event_seq or 0)
    first_seq = int(events[0].get("seq", 0))
    if first_seq > current_watermark + 1:
        db.add(
            AuditEvent(
                user_id=user_id,
                action="runtime_event_gap_detected",
                resource="strategy",
                resource_id=str(strategy.id),
                details_json=json.dumps(
                    {
                        "runtime_ref": strategy.runtime_ref,
                        "expected_next_seq": current_watermark + 1,
                        "first_seen_seq": first_seq,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    new_events: list[dict[str, Any]] = []
    for item in events:
        seq = int(item.get("seq", 0))
        if seq <= current_watermark:
            continue
        event_type = str(item.get("type", "strategy_runtime_trace"))
        payload = item.get("payload")
        normalized_payload = payload if isinstance(payload, dict) else {}
        db.add(
            AuditEvent(
                user_id=user_id,
                action=_runtime_event_audit_action(event_type),
                resource="strategy",
                resource_id=str(strategy.id),
                details_json=json.dumps(
                    {
                        "runtime_ref": strategy.runtime_ref,
                        "event_seq": seq,
                        "event_type": event_type,
                        "timestamp": item.get("timestamp"),
                        "payload": normalized_payload,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        current_watermark = seq
        new_events.append(item)

    runtime.last_audited_event_seq = current_watermark
    db.add(runtime)
    return new_events


def _normalize_runtime_events(events: list[dict] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return normalized
    for row in events:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "seq": int(row.get("seq") or 0),
                "type": str(row.get("type") or "strategy_runtime_trace"),
                "timestamp": row.get("timestamp"),
                "payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
            }
        )
    return normalized


def _runtime_event_audit_action(event_type: str) -> str:
    mapping = {
        "order_submitted": "runtime_order_submitted",
        "order_status_update": "runtime_order_status_update",
        "trade_filled": "runtime_trade_filled",
        "strategy_triggered": "runtime_strategy_triggered",
        "strategy_stopped": "runtime_strategy_stopped",
        "strategy_runtime_error": "runtime_strategy_error",
        "runtime_failed": "runtime_failed",
        "runtime_stopped": "runtime_stopped",
    }
    return mapping.get(event_type, "runtime_trace")


async def _emit_runtime_trace_events(
    *,
    ws: WsManager,
    user_id: int,
    strategy_id: int,
    runtime_ref: str | None,
    events: list[dict[str, Any]],
) -> None:
    if not events:
        return
    for event in events:
        event_type = str(event.get("type") or "strategy_runtime_trace")
        payload = event.get("payload")
        normalized_payload = payload if isinstance(payload, dict) else {}
        event_payload = {
            "strategy_id": strategy_id,
            "runtime_ref": runtime_ref,
            "event_seq": int(event.get("seq") or 0),
            "event_type": event_type,
            "event_timestamp": event.get("timestamp"),
            **normalized_payload,
        }
        await ws.push_to_user(
            user_id,
            build_ws_event(
                event_type="strategy_runtime_trace",
                resource_id=runtime_ref,
                dedupe_key=f"runtime_trace:{runtime_ref}:{event_payload['event_seq']}:{event_type}",
                payload=event_payload,
            ),
        )
        if event_type == "trade_filled":
            await ws.push_to_user(
                user_id,
                build_ws_event(
                    event_type="trade_filled",
                    resource_id=runtime_ref,
                    dedupe_key=f"runtime_trade_filled:{runtime_ref}:{event_payload['event_seq']}",
                    payload=event_payload,
                ),
            )
        elif event_type == "order_submitted":
            await ws.push_to_user(
                user_id,
                build_ws_event(
                    event_type="order_submitted",
                    resource_id=runtime_ref,
                    dedupe_key=f"runtime_order_submitted:{runtime_ref}:{event_payload['event_seq']}",
                    payload=event_payload,
                ),
            )


async def _emit_runtime_error(
    *,
    db: Session,
    ws: WsManager,
    user: User,
    strategy: Strategy,
    action: str,
    error: str,
) -> None:
    log_audit_event(
        db,
        user_id=user.id,
        action=action,
        resource="strategy",
        resource_id=str(strategy.id),
        details={"runtime_ref": strategy.runtime_ref, "error": error},
    )
    await ws.push_to_user(
        user.id,
        build_ws_event(
            event_type="strategy_runtime_error",
            resource_id=strategy.runtime_ref,
            dedupe_key=f"runtime_error:{strategy.runtime_ref}:{error}",
            payload={
                "strategy_id": strategy.id,
                "runtime_ref": strategy.runtime_ref,
                "error": error,
            },
        ),
    )


def _build_runtime_mismatches(runtime: StrategyRuntime, state: RuntimeState) -> dict[str, dict[str, str | None]]:
    """Return mismatched runtime observability fields between DB row and supervisor."""
    mismatches: dict[str, dict[str, str | None]] = {}
    if runtime.status != state.status:
        mismatches["status"] = {"db": runtime.status, "supervisor": state.status}
    if (runtime.last_error or None) != (state.last_error or None):
        mismatches["last_error"] = {
            "db": runtime.last_error,
            "supervisor": state.last_error,
        }
    if not _datetime_close(runtime.last_heartbeat, state.last_heartbeat):
        mismatches["last_heartbeat"] = {
            "db": runtime.last_heartbeat.isoformat() if runtime.last_heartbeat else None,
            "supervisor": state.last_heartbeat.isoformat() if state.last_heartbeat else None,
        }
    if int(runtime.last_event_seq or 0) != int(state.last_event_seq or 0):
        mismatches["last_event_seq"] = {
            "db": str(runtime.last_event_seq),
            "supervisor": str(state.last_event_seq),
        }
    if int(runtime.order_submitted_count or 0) != int(state.order_submitted_count or 0):
        mismatches["order_submitted_count"] = {
            "db": str(runtime.order_submitted_count),
            "supervisor": str(state.order_submitted_count),
        }
    if int(runtime.order_update_count or 0) != int(state.order_update_count or 0):
        mismatches["order_update_count"] = {
            "db": str(runtime.order_update_count),
            "supervisor": str(state.order_update_count),
        }
    if int(runtime.trade_fill_count or 0) != int(state.trade_fill_count or 0):
        mismatches["trade_fill_count"] = {
            "db": str(runtime.trade_fill_count),
            "supervisor": str(state.trade_fill_count),
        }
    return mismatches


def _datetime_close(left: datetime | None, right: datetime | None, *, tolerance_seconds: int = 10) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return abs((left - right).total_seconds()) <= tolerance_seconds


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
