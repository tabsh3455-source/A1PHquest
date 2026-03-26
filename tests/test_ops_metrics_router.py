from datetime import datetime, timezone
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, Strategy, StrategyRuntime, User
from app.routers.ops import get_futures_grid_audit, get_ops_metrics
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


def test_get_futures_grid_audit_returns_profile_and_grid_seed_trace():
    with _build_session() as db:
        user = User(
            username="ops-futures-user",
            email="ops-futures-user@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            user_id=user.id,
            name="futures-grid-live",
            template_key="futures_grid",
            strategy_type="futures_grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-futures-1",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        db.add(
            StrategyRuntime(
                strategy_id=strategy.id,
                user_id=user.id,
                status="running",
            )
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-futures-1",
                            "event_seq": 2,
                            "event_type": "futures_grid_profile",
                            "timestamp": now_iso,
                            "payload": {"direction": "short", "leverage": 8},
                        },
                        ensure_ascii=False,
                    ),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-futures-1",
                            "event_seq": 3,
                            "event_type": "grid_seeded",
                            "timestamp": now_iso,
                            "payload": {
                                "planned_order_count": 5,
                                "buy_levels": 0,
                                "sell_levels": 5,
                            },
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        db.commit()

        response = get_futures_grid_audit(limit=20, db=db, current_user=user)
        assert len(response.runtimes) == 1
        item = response.runtimes[0]
        assert item.strategy_id == strategy.id
        assert item.runtime_ref == "rt-futures-1"
        assert item.direction == "short"
        assert item.leverage == 8
        assert item.planned_order_count == 5
        assert item.buy_levels == 0
        assert item.sell_levels == 5
        assert item.action_level == "ok"
        assert item.audit_flags == []
        assert "No action needed" in item.suggested_action


def test_get_futures_grid_audit_ignores_trace_from_stale_runtime_ref():
    with _build_session() as db:
        user = User(
            username="ops-futures-stale-user",
            email="ops-futures-stale-user@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            user_id=user.id,
            name="futures-grid-switch",
            template_key="futures_grid",
            strategy_type="futures_grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-current",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        db.add(
            StrategyRuntime(
                strategy_id=strategy.id,
                user_id=user.id,
                status="running",
            )
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-stale",
                            "event_seq": 10,
                            "event_type": "futures_grid_profile",
                            "timestamp": now_iso,
                            "payload": {"direction": "short", "leverage": 20},
                        },
                        ensure_ascii=False,
                    ),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-current",
                            "event_seq": 1,
                            "event_type": "futures_grid_profile",
                            "timestamp": now_iso,
                            "payload": {"direction": "long", "leverage": 3},
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        db.commit()

        response = get_futures_grid_audit(limit=20, db=db, current_user=user)
        assert len(response.runtimes) == 1
        item = response.runtimes[0]
        assert item.runtime_ref == "rt-current"
        assert item.direction == "long"
        assert item.leverage == 3
        assert item.action_level == "warning"
        assert "grid_seed_trace_missing" in item.audit_flags


def test_get_futures_grid_audit_marks_direction_seed_mismatch_as_critical():
    with _build_session() as db:
        user = User(
            username="ops-futures-mismatch-user",
            email="ops-futures-mismatch-user@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            user_id=user.id,
            name="futures-grid-mismatch",
            template_key="futures_grid",
            strategy_type="futures_grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-mismatch",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        db.add(
            StrategyRuntime(
                strategy_id=strategy.id,
                user_id=user.id,
                status="running",
            )
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-mismatch",
                            "event_seq": 1,
                            "event_type": "futures_grid_profile",
                            "timestamp": now_iso,
                            "payload": {"direction": "long", "leverage": 4},
                        },
                        ensure_ascii=False,
                    ),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="runtime_trace",
                    resource="strategy",
                    resource_id=str(strategy.id),
                    details_json=json.dumps(
                        {
                            "runtime_ref": "rt-mismatch",
                            "event_seq": 2,
                            "event_type": "grid_seeded",
                            "timestamp": now_iso,
                            "payload": {"planned_order_count": 4, "buy_levels": 1, "sell_levels": 3},
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        db.commit()

        response = get_futures_grid_audit(limit=20, db=db, current_user=user)
        assert len(response.runtimes) == 1
        item = response.runtimes[0]
        assert item.action_level == "critical"
        assert "direction_seed_mismatch_long" in item.audit_flags
        assert "Pause this strategy" in item.suggested_action
