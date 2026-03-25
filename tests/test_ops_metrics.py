from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, LighterReconcileRecord, Strategy, StrategyRuntime, User
from app.routers import ops as ops_router


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


class _FakeWsManager:
    def __init__(self, *, connection_count: int = 0, online_user_count: int = 0):
        self._connection_count = connection_count
        self._online_user_count = online_user_count

    def connection_count(self) -> int:
        return self._connection_count

    def online_user_count(self) -> int:
        return self._online_user_count


def test_ops_metrics_includes_lighter_reconcile_counts():
    with _build_session() as db:
        user = _create_user(db, "ops-metrics-user")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        strategy_running = Strategy(
            user_id=user.id,
            name="grid-a",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="ref-a",
        )
        strategy_stopped = Strategy(
            user_id=user.id,
            name="grid-b",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="ref-b",
        )
        db.add_all([strategy_running, strategy_stopped])
        db.commit()

        db.add_all(
            [
                StrategyRuntime(strategy_id=strategy_running.id, user_id=user.id, status="running"),
                StrategyRuntime(strategy_id=strategy_stopped.id, user_id=user.id, status="stopped"),
                LighterReconcileRecord(
                    user_id=user.id,
                    exchange_account_id=11,
                    operation="submit",
                    request_order_id="due-1",
                    symbol="BTC-USDC",
                    status="pending",
                    raw_json=json.dumps({"next_retry_at": (now - timedelta(seconds=5)).isoformat()}),
                ),
                LighterReconcileRecord(
                    user_id=user.id,
                    exchange_account_id=11,
                    operation="submit",
                    request_order_id="blocked-1",
                    symbol="ETH-USDC",
                    status="pending",
                    raw_json=json.dumps({"next_retry_at": (now + timedelta(seconds=120)).isoformat()}),
                ),
                LighterReconcileRecord(
                    user_id=user.id,
                    exchange_account_id=11,
                    operation="submit",
                    request_order_id="expired-1",
                    symbol="SOL-USDC",
                    status="expired",
                    raw_json="{}",
                ),
                AuditEvent(
                    user_id=user.id,
                    action="strategy_runtime_error",
                    resource="strategy",
                    resource_id="1",
                    details_json="{}",
                ),
            ]
        )
        db.commit()

        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=3, online_user_count=2))))
        response = ops_router.get_ops_metrics(request=request, db=db, current_user=user)

        assert response.ws_connection_count == 3
        assert response.ws_online_user_count == 2
        assert response.strategy_runtime_counts["running"] == 1
        assert response.strategy_runtime_counts["stopped"] == 1
        assert response.strategy_process_count == 1
        assert response.runtime_status_drift_count == 1
        assert response.lighter_reconcile_status_counts == {"pending": 2, "reconciled": 0, "expired": 1}
        assert response.lighter_reconcile_retry_due_count == 1
        assert response.lighter_reconcile_retry_blocked_count == 1
        assert response.lighter_pending_oldest_age_seconds is not None
        assert response.lighter_pending_oldest_age_seconds >= 0
        assert response.audit_action_counts_last_hour.get("strategy_runtime_error", 0) >= 1
        assert response.critical_audit_events_last_hour >= 1


def test_ops_metrics_counts_failed_lighter_reconcile_retry_sync_as_failed_and_critical():
    with _build_session() as db:
        user = _create_user(db, "ops-metrics-retry-failed-user")
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="lighter_reconcile_retry_sync",
                    resource="exchange_account",
                    resource_id="12",
                    details_json='{"success": false, "message":"upstream timeout"}',
                ),
                AuditEvent(
                    user_id=user.id,
                    action="lighter_reconcile_retry_sync",
                    resource="exchange_account",
                    resource_id="12",
                    details_json='{"success": true, "message":"ok"}',
                ),
            ]
        )
        db.commit()

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=0, online_user_count=0)))
        )
        response = ops_router.get_ops_metrics(request=request, db=db, current_user=user)
        assert response.total_audit_events_last_hour >= 2
        assert response.failed_audit_events_last_hour >= 1
        assert response.critical_audit_events_last_hour >= 1
        assert response.audit_action_counts_last_hour.get("lighter_reconcile_retry_sync", 0) >= 2


def test_ops_metrics_emits_alert_items_when_thresholds_exceeded(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "ops-metrics-alert-user")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        strategy = Strategy(
            user_id=user.id,
            name="grid-alert",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="ref-alert",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        db.add_all(
            [
                StrategyRuntime(strategy_id=strategy.id, user_id=user.id, status="failed"),
                LighterReconcileRecord(
                    user_id=user.id,
                    exchange_account_id=21,
                    operation="submit",
                    request_order_id="pending-alert-1",
                    symbol="BTC-USDC",
                    status="pending",
                    raw_json=json.dumps({"next_retry_at": (now + timedelta(minutes=5)).isoformat()}),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="strategy_runtime_error",
                    resource="strategy",
                    resource_id="ref-alert",
                    details_json="{}",
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
            app=SimpleNamespace(state=SimpleNamespace(ws_manager=_FakeWsManager(connection_count=0, online_user_count=0)))
        )
        response = ops_router.get_ops_metrics(request=request, db=db, current_user=user)
        codes = {item.code for item in response.alert_items}
        assert "failed_audit_rate_high" in codes
        assert "runtime_status_drift_detected" in codes
        assert "lighter_pending_backlog_high" in codes
        assert "lighter_retry_blocked_high" in codes
        assert "critical_audit_events_high" in codes
