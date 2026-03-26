import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, ExchangeAccount, User
from app.routers import orders as orders_router
from app.schemas import OrderCancelRequest, OrderCreateRequest
from app.services.circuit_breaker import CircuitBreakerResult
from app.services.risk_service import RiskDecision


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


def _create_account(db: Session, user_id: int) -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange="binance",
        account_alias="main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


class _FakeWsManager:
    def __init__(self) -> None:
        self.events: list[tuple[int, dict]] = []

    async def push_to_user(self, user_id: int, event: dict) -> None:
        self.events.append((user_id, event))


class _FakeNotificationService:
    def send_risk_alert(self, user_id: int, reason: str) -> None:
        return None


class _FakeRiskService:
    def __init__(self, *, circuit_breaker_enabled: bool) -> None:
        self.circuit_breaker_enabled = circuit_breaker_enabled

    def evaluate_cancel_rate(self, db: Session, *, user_id: int) -> RiskDecision:
        return RiskDecision(False, "Cancel rate exceeds limit")

    def is_circuit_breaker_enabled(self, db: Session, *, user_id: int) -> bool:
        return self.circuit_breaker_enabled

    def evaluate_rejection_burst(self, db: Session, *, user_id: int) -> RiskDecision:
        # Keep this path no-op so assertions focus on cancel-rate trigger behavior.
        return RiskDecision(True, "Risk rejection burst check passed")


class _FakeCircuitBreakerService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def trigger_user_circuit_breaker(self, db: Session, *, user_id: int, reason: str) -> CircuitBreakerResult:
        self.calls.append((user_id, reason))
        return CircuitBreakerResult(triggered=True, stopped_strategy_ids=[11, 12], errors=[])


def _run_cancel_rejected_flow(
    monkeypatch,
    *,
    circuit_breaker_enabled: bool,
    async_runner,
):
    with _build_session() as db:
        user = _create_user(db, f"risk-cancel-{int(circuit_breaker_enabled)}")
        account = _create_account(db, user.id)
        ws = _FakeWsManager()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))

        fake_breaker = _FakeCircuitBreakerService()
        monkeypatch.setattr(orders_router, "risk_service", _FakeRiskService(circuit_breaker_enabled=circuit_breaker_enabled))
        monkeypatch.setattr(orders_router, "circuit_breaker_service", fake_breaker)
        monkeypatch.setattr(orders_router, "notification_service", _FakeNotificationService())

        with pytest.raises(HTTPException) as exc:
            async_runner(
                orders_router.cancel_order(
                    order_id="ord-1",
                    payload=OrderCancelRequest(account_id=account.id, symbol="BTCUSDT"),
                    request=request,
                    db=db,
                    current_user=user,
                )
            )
        assert exc.value.status_code == 429

        actions = [row.action for row in db.query(AuditEvent).order_by(AuditEvent.id.asc()).all()]
        risk_audit = db.query(AuditEvent).filter(AuditEvent.action == "order_cancel_rejected_risk").first()
        assert risk_audit is not None
        risk_details = json.loads(risk_audit.details_json)
        return actions, risk_details, ws.events, fake_breaker.calls


def test_cancel_rate_rejection_triggers_circuit_breaker_when_enabled(monkeypatch, async_runner):
    actions, risk_details, ws_events, breaker_calls = _run_cancel_rejected_flow(
        monkeypatch,
        circuit_breaker_enabled=True,
        async_runner=async_runner,
    )
    assert "circuit_breaker_trigger" in actions
    assert risk_details["circuit_breaker_triggered"] is True
    assert risk_details["stopped_strategy_ids"] == [11, 12]
    assert breaker_calls
    event_types = [event["type"] for _, event in ws_events]
    assert "risk_blocked" in event_types
    assert "circuit_breaker_triggered" in event_types


def test_cancel_rate_rejection_does_not_trigger_circuit_breaker_when_disabled(monkeypatch, async_runner):
    actions, risk_details, ws_events, breaker_calls = _run_cancel_rejected_flow(
        monkeypatch,
        circuit_breaker_enabled=False,
        async_runner=async_runner,
    )
    assert "circuit_breaker_trigger" not in actions
    assert "circuit_breaker_triggered" not in risk_details
    assert not breaker_calls
    event_types = [event["type"] for _, event in ws_events]
    assert "risk_blocked" in event_types
    assert "circuit_breaker_triggered" not in event_types


def test_submit_order_blocks_when_risk_rule_missing(monkeypatch, async_runner):
    with _build_session() as db:
        user = _create_user(db, "risk-order-missing-rule")
        account = _create_account(db, user.id)
        ws = _FakeWsManager()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))
        monkeypatch.setattr(orders_router, "notification_service", _FakeNotificationService())

        with pytest.raises(HTTPException) as exc:
            async_runner(
                orders_router.submit_order(
                    payload=OrderCreateRequest(
                        account_id=account.id,
                        symbol="BTCUSDT",
                        side="BUY",
                        order_type="MARKET",
                        quantity=0.01,
                        reference_price=100000,
                    ),
                    request=request,
                    db=db,
                    current_user=user,
                )
            )
        assert exc.value.status_code == 403
        assert "risk rule is required" in str(exc.value.detail).lower()
        actions = [row.action for row in db.query(AuditEvent).order_by(AuditEvent.id.asc()).all()]
        assert "order_submit_rejected_risk" in actions
