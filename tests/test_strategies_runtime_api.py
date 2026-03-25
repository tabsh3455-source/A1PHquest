from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, ExchangeAccount, Strategy, StrategyRuntime, User
from app.routers import strategies as strategies_router
from app.services.strategy_supervisor import RuntimeState, StrategySupervisorUnavailableError


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


def _create_account(db: Session, user_id: int, exchange: str = "binance") -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange=exchange,
        account_alias=f"{exchange}-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
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


class _UnavailableSupervisor:
    def start_strategy(self, **_: object):
        raise StrategySupervisorUnavailableError("worker-supervisor unavailable: connection refused")


class _RunningSupervisor:
    def start_strategy(self, **_: object):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return RuntimeState(
            runtime_ref="rt-success-1",
            status="running",
            process_id="proc-1",
            started_at=now,
            last_heartbeat=now,
            last_event_seq=2,
            order_submitted_count=1,
            order_update_count=1,
            trade_fill_count=1,
            recent_events=[
                {
                    "seq": 1,
                    "type": "order_submitted",
                    "timestamp": now.isoformat(),
                    "payload": {"order_id": "ord-1"},
                },
                {
                    "seq": 2,
                    "type": "trade_filled",
                    "timestamp": now.isoformat(),
                    "payload": {"trade_id": "tr-1"},
                },
            ],
        )

    def get_runtime(self, runtime_ref: str):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return RuntimeState(
            runtime_ref=runtime_ref,
            status="running",
            process_id="proc-1",
            started_at=now,
            last_heartbeat=now,
            last_event_seq=1,
            recent_events=[
                {
                    "seq": 1,
                    "type": "strategy_triggered",
                    "timestamp": now.isoformat(),
                    "payload": {"strategy_name": "grid-live-success"},
                }
            ],
        )


def test_grid_strategy_config_validation_enforces_required_fields_and_symbol_normalization():
    _, valid = strategies_router._validate_strategy_config(
        "grid",
        {
            "exchange_account_id": 1,
            "symbol": " btcusdt ",
            "grid_count": 10,
            "grid_step_pct": 0.5,
            "base_order_size": 0.01,
        },
    )
    assert valid["symbol"] == "BTCUSDT"

    with pytest.raises(HTTPException) as exc:
        strategies_router._validate_strategy_config(
            "grid",
            {
                "exchange_account_id": 1,
                "symbol": "BTCUSDT",
                "grid_count": 10,
                "grid_step_pct": 0.5,
            },
        )
    assert exc.value.status_code == 400


def test_dca_strategy_config_validation_blocks_invalid_boundaries():
    with pytest.raises(HTTPException) as exc:
        strategies_router._validate_strategy_config(
            "dca",
            {
                "exchange_account_id": 1,
                "symbol": "ETHUSDT",
                "cycle_seconds": 0,
                "amount_per_cycle": 100,
            },
        )
    assert exc.value.status_code == 400


def test_non_live_builtin_strategy_config_still_requires_account_and_symbol():
    _, valid = strategies_router._validate_strategy_config(
        "funding_arbitrage",
        {
            "exchange_account_id": 9,
            "symbol": " eth-usdt-swap ",
            "funding_entry_threshold_pct": 0.2,
            "funding_exit_threshold_pct": 0.05,
            "hedge_notional": 250,
        },
    )
    assert valid["symbol"] == "ETH-USDT-SWAP"

    with pytest.raises(HTTPException) as exc:
        strategies_router._validate_strategy_config(
            "spot_future_arbitrage",
            {"params": {"window": 5}},
        )
    assert exc.value.status_code == 400


def test_create_strategy_returns_normalized_config_payload():
    with _build_session() as db:
        user = _create_user(db, "strategy-create-user")

        response = strategies_router.create_strategy(
            payload=strategies_router.StrategyCreateRequest(
                name="grid-alpha",
                strategy_type="grid",
                config={
                    "exchange_account_id": 7,
                    "symbol": " btcusdt ",
                    "grid_count": 12,
                    "grid_step_pct": 0.6,
                    "base_order_size": 0.003,
                },
            ),
            db=db,
            current_user=user,
        )

        assert response.name == "grid-alpha"
        assert response.strategy_type == "grid"
        assert response.config["symbol"] == "BTCUSDT"
        assert response.config["grid_count"] == 12


def test_update_strategy_replaces_strategy_fields_when_stopped():
    with _build_session() as db:
        user = _create_user(db, "strategy-update-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-original",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": 5,
                    "symbol": "BTCUSDT",
                    "grid_count": 10,
                    "grid_step_pct": 0.4,
                    "base_order_size": 0.001,
                }
            ),
            status="stopped",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        response = strategies_router.update_strategy(
            strategy_id=strategy.id,
            payload=strategies_router.StrategyUpdateRequest(
                name="dca-next",
                strategy_type="dca",
                config={
                    "exchange_account_id": 6,
                    "symbol": " ethusdt ",
                    "cycle_seconds": 900,
                    "amount_per_cycle": 25,
                },
            ),
            db=db,
            current_user=user,
        )

        db.refresh(strategy)
        assert response.name == "dca-next"
        assert response.strategy_type == "dca"
        assert response.config["symbol"] == "ETHUSDT"
        assert strategy.name == "dca-next"
        assert strategy.strategy_type == "dca"
        assert json.loads(strategy.config_json)["cycle_seconds"] == 900
        audit_actions = [item.action for item in db.query(AuditEvent).all()]
        assert "strategy_update" in audit_actions


def test_update_strategy_rejects_running_strategy():
    with _build_session() as db:
        user = _create_user(db, "strategy-update-running-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-live",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": 5,
                    "symbol": "BTCUSDT",
                    "grid_count": 10,
                    "grid_step_pct": 0.4,
                    "base_order_size": 0.001,
                }
            ),
            status="running",
            runtime_ref="rt-55",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        with pytest.raises(HTTPException) as exc:
            strategies_router.update_strategy(
                strategy_id=strategy.id,
                payload=strategies_router.StrategyUpdateRequest(
                    name="grid-live-edit",
                    strategy_type="grid",
                    config={
                        "exchange_account_id": 5,
                        "symbol": "BTCUSDT",
                        "grid_count": 11,
                        "grid_step_pct": 0.5,
                        "base_order_size": 0.002,
                    },
                ),
                db=db,
                current_user=user,
            )
        assert exc.value.status_code == 409


def test_get_runtime_includes_last_heartbeat_and_last_error_fields():
    with _build_session() as db:
        user = _create_user(db, "runtime-fields-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-runtime",
            strategy_type="grid",
            config_json="{}",
            status="failed",
            runtime_ref="rt-100",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
        runtime = StrategyRuntime(
            strategy_id=strategy.id,
            user_id=user.id,
            process_id="proc-100",
            status="failed",
            started_at=heartbeat,
            stopped_at=heartbeat,
            last_heartbeat=heartbeat,
            last_error="gateway bootstrap failed",
        )
        db.add(runtime)
        db.commit()

        response = strategies_router.get_runtime(strategy.id, db=db, current_user=user)
        assert response.last_heartbeat == heartbeat
        assert response.last_error == "gateway bootstrap failed"


def test_start_strategy_returns_503_when_supervisor_unavailable_and_no_fake_running(monkeypatch, async_runner):
    with _build_session() as db:
        user = _create_user(db, "runtime-503-user")
        account = _create_account(db, user.id, exchange="binance")
        strategy = Strategy(
            user_id=user.id,
            name="grid-live",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": account.id,
                    "symbol": "BTCUSDT",
                    "grid_count": 8,
                    "grid_step_pct": 0.8,
                    "base_order_size": 0.002,
                }
            ),
            status="stopped",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        ws = _FakeWsManager()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))
        monkeypatch.setattr(strategies_router, "supervisor", _UnavailableSupervisor())

        with pytest.raises(HTTPException) as exc:
            async_runner(
                strategies_router.start_strategy(
                    strategy_id=strategy.id,
                    request=request,
                    db=db,
                    current_user=user,
                )
            )
        assert exc.value.status_code == 503

        db.refresh(strategy)
        assert strategy.status == "stopped"
        runtime_row = db.query(StrategyRuntime).filter(StrategyRuntime.strategy_id == strategy.id).first()
        assert runtime_row is None
        assert any(event["type"] == "strategy_runtime_error" for _, event in ws.events)


def test_start_strategy_emits_runtime_update_with_strategy_runtime_ref(monkeypatch, async_runner):
    with _build_session() as db:
        user = _create_user(db, "runtime-success-user")
        account = _create_account(db, user.id, exchange="binance")
        strategy = Strategy(
            user_id=user.id,
            name="grid-live-success",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": account.id,
                    "symbol": "BTCUSDT",
                    "grid_count": 8,
                    "grid_step_pct": 0.8,
                    "base_order_size": 0.002,
                }
            ),
            status="stopped",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        ws = _FakeWsManager()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))
        monkeypatch.setattr(strategies_router, "supervisor", _RunningSupervisor())

        response = async_runner(
            strategies_router.start_strategy(
                strategy_id=strategy.id,
                request=request,
                db=db,
                current_user=user,
            )
        )

        assert response.runtime_ref == "rt-success-1"
        assert response.status == "running"
        assert response.last_event_seq == 2
        assert response.order_submitted_count == 1
        assert response.order_update_count == 1
        assert response.trade_fill_count == 1
        assert len(response.recent_events) == 2
        assert ws.events
        event_types = [event["type"] for _, event in ws.events]
        assert "strategy_runtime_update" in event_types
        assert "strategy_runtime_trace" in event_types
        assert "trade_filled" in event_types
        assert "order_submitted" in event_types
        # P13 ordering contract: runtime execution events must be replayable as
        # order_submitted -> trade_filled -> strategy_runtime_update.
        assert event_types.index("order_submitted") < event_types.index("trade_filled")
        assert event_types.index("trade_filled") < event_types.index("strategy_runtime_update")

        runtime_row = db.query(StrategyRuntime).filter(StrategyRuntime.strategy_id == strategy.id).first()
        assert runtime_row is not None
        assert runtime_row.last_event_seq == 2
        assert runtime_row.last_audited_event_seq == 2
        audit_actions = [item.action for item in db.query(AuditEvent).all()]
        assert "runtime_order_submitted" in audit_actions
        assert "runtime_trade_filled" in audit_actions


def test_runtime_consistency_endpoint_reports_missing_runtime_row():
    with _build_session() as db:
        user = _create_user(db, "consistency-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-consistency",
            strategy_type="grid",
            config_json="{}",
            status="stopped",
            runtime_ref=None,
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        response = strategies_router.check_runtime_consistency(strategy.id, db=db, current_user=user)
        assert response.consistent is False
        assert "runtime" in response.mismatches


def test_runtime_consistency_endpoint_syncs_runtime_row_before_compare(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "consistency-sync-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-consistency-sync",
            strategy_type="grid",
            config_json="{}",
            status="running",
            runtime_ref="rt-sync-1",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)

        stale_heartbeat = datetime(2026, 3, 23, 12, 0, 0)
        runtime_row = StrategyRuntime(
            strategy_id=strategy.id,
            user_id=user.id,
            process_id="proc-old",
            status="running",
            last_heartbeat=stale_heartbeat,
            last_event_seq=0,
            order_submitted_count=0,
            order_update_count=0,
            trade_fill_count=0,
        )
        db.add(runtime_row)
        db.commit()

        monkeypatch.setattr(strategies_router, "supervisor", _RunningSupervisor())
        response = strategies_router.check_runtime_consistency(strategy.id, db=db, current_user=user)
        assert response.consistent is True

        refreshed = db.query(StrategyRuntime).filter(StrategyRuntime.strategy_id == strategy.id).first()
        assert refreshed is not None
        assert refreshed.last_event_seq == 1
        assert refreshed.last_heartbeat is not None
        assert refreshed.last_heartbeat != stale_heartbeat


def test_start_strategy_is_idempotent_when_runtime_already_active(monkeypatch, async_runner):
    with _build_session() as db:
        user = _create_user(db, "runtime-idempotent-user")
        strategy = Strategy(
            user_id=user.id,
            name="grid-running",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": 1,
                    "symbol": "BTCUSDT",
                    "grid_count": 8,
                    "grid_step_pct": 0.8,
                    "base_order_size": 0.002,
                }
            ),
            status="running",
            runtime_ref="rt-running-1",
        )
        runtime_row = StrategyRuntime(
            strategy_id=1,
            user_id=user.id,
            process_id="proc-running",
            status="running",
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        runtime_row.strategy_id = strategy.id
        db.add(runtime_row)
        db.commit()

        ws = _FakeWsManager()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws)))
        monkeypatch.setattr(strategies_router, "supervisor", _RunningSupervisor())
        response = async_runner(
            strategies_router.start_strategy(
                strategy_id=strategy.id,
                request=request,
                db=db,
                current_user=user,
            )
        )
        assert response.status == "running"
        assert response.runtime_ref == "rt-running-1"
