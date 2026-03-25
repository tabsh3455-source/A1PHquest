from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.deps import require_admin
from app.main import app
from app.models import AuditEvent, Base, LighterReconcileRecord, Strategy, StrategyRuntime, User
from app.routers import ops as ops_router


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_user(db: Session, username: str, *, role: str = "user", active: bool = True) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=role,
        is_active=active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class _FakeWsManager:
    def __init__(self, *, connection_count: int = 0, online_user_count: int = 0):
        self._connection_count = connection_count
        self._online_user_count = online_user_count

    def connection_count(self) -> int:
        return self._connection_count

    def online_user_count(self) -> int:
        return self._online_user_count


def test_admin_ops_metrics_route_requires_admin_dependency():
    route = None
    for candidate in app.routes:
        if isinstance(candidate, APIRoute) and candidate.path == "/api/ops/admin/metrics":
            route = candidate
            break
    assert route is not None
    deps = {dependency.call for dependency in route.dependant.dependencies}
    assert require_admin in deps


def test_admin_ops_metrics_aggregates_cross_user_data():
    with _build_session() as db:
        admin = _create_user(db, "ops-admin", role="admin")
        user_a = _create_user(db, "ops-user-a")
        user_b = _create_user(db, "ops-user-b", active=False)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        strategy_a = Strategy(
            user_id=user_a.id,
            name="grid-a",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-a",
        )
        strategy_b = Strategy(
            user_id=user_b.id,
            name="grid-b",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-b",
        )
        db.add_all([strategy_a, strategy_b])
        db.commit()

        db.add_all(
            [
                StrategyRuntime(strategy_id=strategy_a.id, user_id=user_a.id, status="running"),
                StrategyRuntime(strategy_id=strategy_b.id, user_id=user_b.id, status="failed"),
                LighterReconcileRecord(
                    user_id=user_a.id,
                    exchange_account_id=1,
                    operation="submit",
                    request_order_id="due-1",
                    symbol="BTC-USDC",
                    status="pending",
                    created_at=now - timedelta(minutes=5),
                    raw_json=json.dumps({"next_retry_at": (now - timedelta(seconds=10)).isoformat()}),
                ),
                LighterReconcileRecord(
                    user_id=user_b.id,
                    exchange_account_id=2,
                    operation="submit",
                    request_order_id="blocked-1",
                    symbol="ETH-USDC",
                    status="pending",
                    created_at=now - timedelta(minutes=2),
                    raw_json=json.dumps({"next_retry_at": (now + timedelta(minutes=2)).isoformat()}),
                ),
                AuditEvent(
                    user_id=user_a.id,
                    action="strategy_runtime_error",
                    resource="strategy",
                    resource_id="rt-a",
                    details_json="{}",
                    created_at=now - timedelta(minutes=20),
                ),
                AuditEvent(
                    user_id=user_b.id,
                    action="lighter_reconcile_retry_sync",
                    resource="exchange_account",
                    resource_id="2",
                    details_json='{"success": false}',
                    created_at=now - timedelta(minutes=10),
                ),
            ]
        )
        db.commit()

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=5, online_user_count=3)))
        )
        response = ops_router.get_admin_ops_metrics(request=request, db=db, current_user=admin)

        assert response.total_users == 3
        assert response.active_users == 2
        assert response.ws_connection_count == 5
        assert response.ws_online_user_count == 3
        assert response.strategy_runtime_counts.get("running") == 1
        assert response.strategy_runtime_counts.get("failed") == 1
        assert response.strategy_process_count == 1
        assert response.runtime_status_drift_count == 1
        assert response.lighter_reconcile_status_counts == {"pending": 2, "reconciled": 0, "expired": 0}
        assert response.lighter_reconcile_retry_due_count == 1
        assert response.lighter_reconcile_retry_blocked_count == 1
        assert response.failed_audit_events_last_hour >= 2
        assert response.critical_audit_events_last_hour >= 2
        assert response.audit_action_counts_last_hour.get("strategy_runtime_error", 0) >= 1
        assert response.audit_action_counts_last_hour.get("lighter_reconcile_retry_sync", 0) >= 1
        assert response.top_lighter_pending_users
        assert response.top_lighter_pending_users[0].pending_count >= response.top_lighter_pending_users[-1].pending_count
        assert response.runtime_drift_samples
        assert response.runtime_drift_samples[0].strategy_status != response.runtime_drift_samples[0].runtime_status
        assert response.error_trend_last_hour
        assert any(point.total_events > 0 for point in response.error_trend_last_hour)


def test_admin_ops_metrics_error_trend_uses_fixed_time_buckets():
    with _build_session() as db:
        admin = _create_user(db, "ops-admin-bucket", role="admin")
        user = _create_user(db, "ops-user-bucket")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="strategy_runtime_error",
                    resource="strategy",
                    resource_id="rt-1",
                    details_json="{}",
                    created_at=now - timedelta(minutes=50),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="order_submit_failed",
                    resource="order",
                    resource_id="o-1",
                    details_json="{}",
                    created_at=now - timedelta(minutes=3),
                ),
            ]
        )
        db.commit()

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=0, online_user_count=0)))
        )
        response = ops_router.get_admin_ops_metrics(request=request, db=db, current_user=admin)
        assert len(response.error_trend_last_hour) >= 12
        assert len(response.error_trend_last_hour) <= 13

        # Buckets are monotonic and can be consumed directly as a chart x-axis.
        bucket_times = [item.bucket_start for item in response.error_trend_last_hour]
        assert bucket_times == sorted(bucket_times)
        total_from_trend = sum(item.total_events for item in response.error_trend_last_hour)
        failed_from_trend = sum(item.failed_events for item in response.error_trend_last_hour)
        assert total_from_trend == response.total_audit_events_last_hour
        assert failed_from_trend == response.failed_audit_events_last_hour


def test_admin_ops_metrics_emits_alert_items_when_thresholds_exceeded(monkeypatch):
    with _build_session() as db:
        admin = _create_user(db, "ops-admin-alert", role="admin")
        user = _create_user(db, "ops-user-alert")
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        strategy = Strategy(
            user_id=user.id,
            name="grid-alert-admin",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-admin-alert",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        db.add_all(
            [
                StrategyRuntime(strategy_id=strategy.id, user_id=user.id, status="failed"),
                LighterReconcileRecord(
                    user_id=user.id,
                    exchange_account_id=91,
                    operation="submit",
                    request_order_id="blocked-alert",
                    symbol="ETH-USDC",
                    status="pending",
                    raw_json=json.dumps({"next_retry_at": (now + timedelta(minutes=3)).isoformat()}),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="strategy_runtime_error",
                    resource="strategy",
                    resource_id="rt-admin-alert",
                    details_json="{}",
                    created_at=now - timedelta(minutes=5),
                ),
            ]
        )
        db.commit()

        monkeypatch.setattr(ops_router.settings, "ops_alert_failed_audit_rate_threshold", 0.1)
        monkeypatch.setattr(ops_router.settings, "ops_alert_runtime_drift_count_threshold", 1)
        monkeypatch.setattr(ops_router.settings, "ops_alert_lighter_pending_threshold", 1)
        monkeypatch.setattr(ops_router.settings, "ops_alert_lighter_retry_blocked_threshold", 1)
        monkeypatch.setattr(ops_router.settings, "ops_alert_critical_audit_events_threshold", 1)

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=1, online_user_count=1)))
        )
        response = ops_router.get_admin_ops_metrics(request=request, db=db, current_user=admin)
        codes = {item.code for item in response.alert_items}
        assert "failed_audit_rate_high" in codes
        assert "runtime_status_drift_detected" in codes
        assert "lighter_pending_backlog_high" in codes
        assert "lighter_retry_blocked_high" in codes
        assert "critical_audit_events_high" in codes
