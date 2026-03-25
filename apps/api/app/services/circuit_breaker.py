from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Strategy, StrategyRuntime
from ..tenant import with_tenant
from .strategy_supervisor import StrategySupervisorClient


@dataclass(slots=True)
class CircuitBreakerResult:
    triggered: bool
    stopped_strategy_ids: list[int]
    errors: list[str]


class CircuitBreakerService:
    def __init__(self, supervisor: StrategySupervisorClient | None = None) -> None:
        self.supervisor = supervisor or StrategySupervisorClient()

    def trigger_user_circuit_breaker(
        self,
        db: Session,
        *,
        user_id: int,
        reason: str,
    ) -> CircuitBreakerResult:
        running_strategies = with_tenant(db.query(Strategy), Strategy, user_id).filter(
            Strategy.status == "running"
        ).all()
        if not running_strategies:
            return CircuitBreakerResult(triggered=False, stopped_strategy_ids=[], errors=[])

        stopped_ids: list[int] = []
        errors: list[str] = []

        for strategy in running_strategies:
            try:
                runtime_state = self.supervisor.stop_strategy(strategy.runtime_ref or "")
                strategy.status = "stopped"
                db.add(strategy)

                runtime = with_tenant(db.query(StrategyRuntime), StrategyRuntime, user_id).filter(
                    StrategyRuntime.strategy_id == strategy.id
                ).first()
                if not runtime:
                    runtime = StrategyRuntime(strategy_id=strategy.id, user_id=user_id)
                runtime.process_id = runtime_state.process_id
                runtime.status = "stopped"
                runtime.started_at = runtime.started_at or runtime_state.started_at
                runtime.stopped_at = runtime_state.stopped_at or datetime.now(timezone.utc).replace(
                    tzinfo=None
                )
                runtime.last_heartbeat = runtime_state.last_heartbeat or runtime.stopped_at
                runtime.last_error = runtime_state.last_error
                db.add(runtime)
                stopped_ids.append(strategy.id)
            except Exception as exc:
                errors.append(f"strategy_id={strategy.id}: {exc}")

        if stopped_ids:
            db.commit()
        return CircuitBreakerResult(triggered=bool(stopped_ids), stopped_strategy_ids=stopped_ids, errors=errors)
