from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Strategy, StrategyRuntime, User
from app.services.circuit_breaker import CircuitBreakerService
from app.services.strategy_supervisor import RuntimeState


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


class _FakeSupervisor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def stop_strategy(self, runtime_ref: str) -> RuntimeState:
        self.calls.append(runtime_ref)
        return RuntimeState(
            runtime_ref=runtime_ref,
            process_id=f"proc-{runtime_ref}" if runtime_ref else None,
            status="stopped",
            started_at=None,
            stopped_at=_utcnow(),
        )


def test_circuit_breaker_stops_all_running_strategies():
    with _build_session() as db:
        user = _create_user(db, "breaker-user")
        running_a = Strategy(
            user_id=user.id,
            name="grid-a",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-1",
        )
        running_b = Strategy(
            user_id=user.id,
            name="grid-b",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-2",
        )
        stopped = Strategy(
            user_id=user.id,
            name="idle-c",
            strategy_type="grid",
            config_json="{}",
            status="stopped",
            runtime_ref="rt-3",
        )
        db.add_all([running_a, running_b, stopped])
        db.commit()
        db.refresh(running_a)
        db.refresh(running_b)
        db.refresh(stopped)

        db.add(
            StrategyRuntime(
                strategy_id=running_a.id,
                user_id=user.id,
                process_id="proc-1",
                status="running",
                started_at=_utcnow(),
                stopped_at=None,
            )
        )
        db.commit()

        fake_supervisor = _FakeSupervisor()
        service = CircuitBreakerService(supervisor=fake_supervisor)
        result = service.trigger_user_circuit_breaker(
            db,
            user_id=user.id,
            reason="Projected daily loss exceeds threshold",
        )

        db.refresh(running_a)
        db.refresh(running_b)
        db.refresh(stopped)
        runtime_rows = db.query(StrategyRuntime).filter(StrategyRuntime.user_id == user.id).all()

        assert result.triggered
        assert set(result.stopped_strategy_ids) == {running_a.id, running_b.id}
        assert not result.errors
        assert running_a.status == "stopped"
        assert running_b.status == "stopped"
        assert stopped.status == "stopped"
        assert len(fake_supervisor.calls) == 2
        assert len(runtime_rows) == 2
        assert all(runtime.status == "stopped" for runtime in runtime_rows)


def test_circuit_breaker_no_running_strategy_returns_not_triggered():
    with _build_session() as db:
        user = _create_user(db, "no-running-user")
        db.add(
            Strategy(
                user_id=user.id,
                name="idle",
                strategy_type="grid",
                config_json="{}",
                status="stopped",
                runtime_ref="rt-9",
            )
        )
        db.commit()

        service = CircuitBreakerService(supervisor=_FakeSupervisor())
        result = service.trigger_user_circuit_breaker(
            db,
            user_id=user.id,
            reason="Projected daily loss exceeds threshold",
        )
        assert not result.triggered
        assert result.stopped_strategy_ids == []


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
