from __future__ import annotations

from typing import Any
import json

from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..models import ExchangeAccount, Strategy, StrategyRuntime
from ..services.strategy_supervisor import RuntimeState, StrategySupervisorClient
from ..tenant import with_tenant


class StrategyRuntimeControlError(RuntimeError):
    """Raised when an internal strategy runtime operation cannot be completed."""


class StrategyRuntimeControlService:
    def __init__(self, supervisor: StrategySupervisorClient | None = None) -> None:
        self._supervisor = supervisor or StrategySupervisorClient()

    def start_strategy(
        self,
        *,
        db: Session,
        user_id: int,
        strategy: Strategy,
        reason: str,
    ) -> RuntimeState:
        config = _safe_load_json(strategy.config_json)
        _validate_live_runtime_strategy(strategy=strategy, config=config, db=db, user_id=user_id)

        state = self._supervisor.start_strategy(
            user_id=user_id,
            strategy_id=strategy.id,
            strategy_type=strategy.strategy_type,
            config_json=strategy.config_json,
        )
        strategy.runtime_ref = state.runtime_ref
        strategy.status = state.status
        db.add(strategy)
        _upsert_runtime(db, user_id, strategy.id, state)
        log_audit_event(
            db,
            user_id=user_id,
            action="strategy_start",
            resource="strategy",
            resource_id=str(strategy.id),
            details={
                "runtime_ref": state.runtime_ref,
                "status": state.status,
                "reason": reason,
                "trigger": "ai_autopilot",
            },
        )
        return state

    def stop_strategy(
        self,
        *,
        db: Session,
        user_id: int,
        strategy: Strategy,
        reason: str,
    ) -> RuntimeState | None:
        if strategy.status not in {"starting", "running", "stopping"} or not strategy.runtime_ref:
            return None

        state = self._supervisor.stop_strategy(strategy.runtime_ref)
        strategy.status = state.status
        db.add(strategy)
        _upsert_runtime(db, user_id, strategy.id, state)
        log_audit_event(
            db,
            user_id=user_id,
            action="strategy_stop",
            resource="strategy",
            resource_id=str(strategy.id),
            details={
                "runtime_ref": strategy.runtime_ref,
                "status": state.status,
                "reason": reason,
                "trigger": "ai_autopilot",
            },
        )
        return state

    def list_running_candidates(
        self,
        *,
        db: Session,
        user_id: int,
        strategy_ids: list[int],
    ) -> list[Strategy]:
        if not strategy_ids:
            return []
        return (
            with_tenant(db.query(Strategy), Strategy, user_id)
            .filter(
                Strategy.id.in_(strategy_ids),
                Strategy.status.in_(("starting", "running", "stopping")),
            )
            .all()
        )

    def get_runtime_state(self, runtime_ref: str) -> RuntimeState:
        return self._supervisor.get_runtime(runtime_ref)


def _validate_live_runtime_strategy(
    *,
    strategy: Strategy,
    config: dict[str, Any],
    db: Session,
    user_id: int,
) -> None:
    if strategy.strategy_type not in {"grid", "dca"}:
        raise StrategyRuntimeControlError(
            f"strategy_type '{strategy.strategy_type}' is not enabled for live runtime"
        )

    exchange_account_id = int(config.get("exchange_account_id") or 0)
    if exchange_account_id <= 0:
        raise StrategyRuntimeControlError("strategy config is missing exchange_account_id")

    account = (
        with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id)
        .filter(ExchangeAccount.id == exchange_account_id)
        .first()
    )
    if not account:
        raise StrategyRuntimeControlError(f"exchange_account_id {exchange_account_id} not found")
    if account.exchange not in {"binance", "okx"}:
        raise StrategyRuntimeControlError(
            f"exchange '{account.exchange}' is not supported for live runtime"
        )


def _safe_load_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise StrategyRuntimeControlError(f"invalid strategy config_json: {exc}") from exc
    if not isinstance(value, dict):
        raise StrategyRuntimeControlError("invalid strategy config_json: expected object")
    return value


def _upsert_runtime(db: Session, user_id: int, strategy_id: int, state: RuntimeState) -> StrategyRuntime:
    runtime = (
        with_tenant(db.query(StrategyRuntime), StrategyRuntime, user_id)
        .filter(StrategyRuntime.strategy_id == strategy_id)
        .first()
    )
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
