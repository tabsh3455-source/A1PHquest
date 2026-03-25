from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, Strategy, StrategyRuntime, User
from app.routers.ops import get_ops_metrics
from app.ws_manager import WsManager


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_ops_metrics_returns_runtime_and_ws_counts(async_runner):
    with _build_session() as db:
        user = User(
            username="ops-user",
            email="ops-user@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            user_id=user.id,
            name="grid",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-ops-1",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        runtime = StrategyRuntime(
            strategy_id=strategy.id,
            user_id=user.id,
            process_id="proc-1",
            status="running",
        )
        db.add(runtime)
        db.add(
            AuditEvent(
                user_id=user.id,
                action="strategy_start_failed",
                resource="strategy",
                resource_id=strategy.runtime_ref,
                details_json="{}",
            )
        )
        db.add(
            AuditEvent(
                user_id=user.id,
                action="login_anomaly",
                resource="session",
                resource_id=None,
                details_json="{}",
            )
        )
        db.commit()

        ws_manager = WsManager(backend="memory")
        async_runner(ws_manager.push_to_user(user.id, {"type": "runtime"}))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws_manager)))
        result = get_ops_metrics(request=request, db=db, current_user=user)
        assert result.ws_connection_count == 0
        assert result.ws_online_user_count == 0
        assert result.strategy_runtime_counts.get("running") == 1
        assert result.strategy_process_count == 1
        assert result.total_audit_events_last_hour >= 2
        assert result.failed_audit_events_last_hour >= 1
        assert result.failed_audit_event_rate_last_hour > 0
        assert result.critical_audit_events_last_hour >= 1
