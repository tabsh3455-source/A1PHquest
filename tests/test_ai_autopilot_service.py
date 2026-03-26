from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import AiAutopilotDecisionRun, AiAutopilotPolicy, AiProviderCredential, Base, ExchangeAccount, RiskRule, Strategy, User
from app.services.ai_autopilot import AiAutopilotService
from app.services.strategy_supervisor import RuntimeState


def _build_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def _seed_policy_graph(db: Session) -> tuple[User, AiProviderCredential, ExchangeAccount, Strategy, Strategy, AiAutopilotPolicy]:
    user = User(
        username="ai-user",
        email="ai-user@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    provider = AiProviderCredential(
        user_id=user.id,
        name="primary-provider",
        provider_type="openai_compatible",
        base_url="https://api.example.test/v1",
        model_name="gpt-test",
        api_key_encrypted="encrypted",
        is_active=True,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)

    account = ExchangeAccount(
        user_id=user.id,
        exchange="binance",
        account_alias="main",
        api_key_encrypted="a",
        api_secret_encrypted="b",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    strategy_grid = Strategy(
        user_id=user.id,
        name="grid-alpha",
        strategy_type="grid",
        config_json=json.dumps(
            {
                "exchange_account_id": account.id,
                "symbol": "BTCUSDT",
                "grid_count": 12,
                "grid_step_pct": 0.5,
                "base_order_size": 20,
            }
        ),
        status="stopped",
    )
    strategy_dca = Strategy(
        user_id=user.id,
        name="dca-beta",
        strategy_type="dca",
        config_json=json.dumps(
            {
                "exchange_account_id": account.id,
                "symbol": "BTCUSDT",
                "cycle_seconds": 300,
                "amount_per_cycle": 25,
            }
        ),
        status="stopped",
    )
    db.add_all([strategy_grid, strategy_dca])
    db.commit()
    db.refresh(strategy_grid)
    db.refresh(strategy_dca)

    policy = AiAutopilotPolicy(
        user_id=user.id,
        provider_id=provider.id,
        exchange_account_id=account.id,
        name="btc-autopilot",
        symbol="BTCUSDT",
        interval="5m",
        strategy_ids_json=json.dumps([strategy_grid.id, strategy_dca.id]),
        allowed_actions_json=json.dumps(["activate_strategy", "stop_strategy", "create_strategy_version"]),
        status="enabled",
        execution_mode="dry_run",
        decision_interval_seconds=300,
        minimum_confidence=0.6,
        max_actions_per_hour=4,
        custom_prompt=None,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return user, provider, account, strategy_grid, strategy_dca, policy


def _add_risk_rule(db: Session, *, user_id: int) -> None:
    db.add(
        RiskRule(
            user_id=user_id,
            max_order_notional=1000,
            max_daily_loss=300,
            max_position_ratio=0.8,
            max_cancel_rate_per_minute=60,
            circuit_breaker_enabled=True,
        )
    )
    db.commit()


class _FakeMarketDataService:
    async def fetch_history(self, **kwargs):
        base_time = 1_710_000_000
        candles = []
        price = 100.0
        for index in range(60):
            candles.append(
                {
                    "time": base_time + index * 300,
                    "open": price,
                    "high": price + 2,
                    "low": price - 1,
                    "close": price + 1,
                    "volume": 10 + index,
                }
            )
            price += 1
        return candles


class _FakeWsManager:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def push_to_user(self, user_id: int, event: dict) -> None:
        self.events.append({"user_id": user_id, "event": event})


class _FakeRuntimeControl:
    def __init__(self) -> None:
        self.started: list[int] = []
        self.stopped: list[int] = []

    def list_running_candidates(self, *, db: Session, user_id: int, strategy_ids: list[int]):
        return (
            db.query(Strategy)
            .filter(Strategy.id.in_(strategy_ids), Strategy.status.in_(("starting", "running", "stopping")))
            .all()
        )

    def start_strategy(self, *, db: Session, user_id: int, strategy: Strategy, reason: str):
        self.started.append(strategy.id)
        strategy.status = "running"
        strategy.runtime_ref = f"runtime-{strategy.id}"
        db.add(strategy)
        return RuntimeState(
            runtime_ref=strategy.runtime_ref,
            status="running",
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

    def stop_strategy(self, *, db: Session, user_id: int, strategy: Strategy, reason: str):
        self.stopped.append(strategy.id)
        strategy.status = "stopped"
        db.add(strategy)
        return RuntimeState(runtime_ref=strategy.runtime_ref or f"runtime-{strategy.id}", status="stopped")


def test_ai_autopilot_manual_dry_run_persists_decision(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        _, _, _, strategy_grid, _, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        policy_id = policy.id

    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = _FakeRuntimeControl()

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "activate_strategy",
            "target_strategy_id": strategy_grid_id,
            "confidence": 0.81,
            "rationale": "Grid fits the current market regime",
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=True,
        )
    )

    assert decision.status == "dry_run"
    assert decision.action == "activate_strategy"
    with session_factory() as verify_db:
        stored = verify_db.query(AiAutopilotDecisionRun).filter(AiAutopilotDecisionRun.id == decision.id).first()
        assert stored is not None
        assert stored.status == "dry_run"


def test_ai_autopilot_auto_activation_executes_selected_strategy(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user, _, _, strategy_grid, strategy_dca, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        strategy_dca_id = strategy_dca.id
        policy_id = policy.id
        policy.execution_mode = "auto"
        strategy_grid.status = "running"
        strategy_grid.runtime_ref = "runtime-grid"
        db.add_all([policy, strategy_grid])
        db.commit()
        _add_risk_rule(db, user_id=int(user.id))

    fake_runtime_control = _FakeRuntimeControl()
    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = fake_runtime_control

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "activate_strategy",
            "target_strategy_id": strategy_dca_id,
            "confidence": 0.9,
            "rationale": "Trend conditions favor DCA over grid",
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=False,
        )
    )

    assert decision.status == "executed"
    assert fake_runtime_control.stopped == [strategy_grid_id]
    assert fake_runtime_control.started == [strategy_dca_id]
    with session_factory() as verify_db:
        refreshed = verify_db.query(Strategy).filter(Strategy.id == strategy_dca_id).first()
        assert refreshed is not None
        assert refreshed.status == "running"


def test_ai_autopilot_dry_run_create_strategy_version_returns_preview(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        _, _, _, strategy_grid, _, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        policy_id = policy.id

    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = _FakeRuntimeControl()

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "create_strategy_version",
            "target_strategy_id": strategy_grid_id,
            "confidence": 0.88,
            "rationale": "Widen the grid while volatility expands",
            "parameter_overrides": {
                "grid_count": 16,
                "grid_step_pct": 0.8,
            },
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=True,
        )
    )

    assert decision.status == "dry_run"
    assert decision.action == "create_strategy_version"
    assert decision.target_strategy_id == strategy_grid_id
    execution_result = json.loads(decision.execution_result_json)
    assert execution_result["requested_action"] == "create_strategy_version"
    assert execution_result["parameter_overrides"]["grid_count"] == 16
    assert execution_result["proposed_strategy"]["changed_fields"]["grid_step_pct"] == 0.8
    with session_factory() as verify_db:
        strategies = verify_db.query(Strategy).all()
        assert len(strategies) == 2


def test_ai_autopilot_auto_create_strategy_version_generates_and_starts_new_strategy(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user, _, _, strategy_grid, strategy_dca, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        strategy_dca_id = strategy_dca.id
        policy_id = policy.id
        policy.execution_mode = "auto"
        strategy_grid.status = "running"
        strategy_grid.runtime_ref = "runtime-grid"
        db.add_all([policy, strategy_grid])
        db.commit()
        _add_risk_rule(db, user_id=int(user.id))

    class _CommitAwareRuntimeControl(_FakeRuntimeControl):
        def start_strategy(self, *, db: Session, user_id: int, strategy: Strategy, reason: str):
            with session_factory() as verify_db:
                visible = verify_db.query(Strategy).filter(Strategy.id == strategy.id).first()
                assert visible is not None
                assert visible.name == strategy.name
            return super().start_strategy(db=db, user_id=user_id, strategy=strategy, reason=reason)

    fake_runtime_control = _CommitAwareRuntimeControl()
    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = fake_runtime_control

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "create_strategy_version",
            "target_strategy_id": strategy_grid_id,
            "confidence": 0.91,
            "rationale": "Increase spacing and size for higher volatility",
            "parameter_overrides": {
                "grid_step_pct": 1.0,
                "base_order_size": 30,
            },
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=False,
        )
    )

    assert decision.status == "executed"
    execution_result = json.loads(decision.execution_result_json)
    generated_strategy_id = int(execution_result["generated_strategy_id"])
    assert decision.target_strategy_id == generated_strategy_id
    assert fake_runtime_control.stopped == [strategy_grid_id]
    assert fake_runtime_control.started == [generated_strategy_id]

    with session_factory() as verify_db:
        generated = verify_db.query(Strategy).filter(Strategy.id == generated_strategy_id).first()
        assert generated is not None
        assert generated.status == "running"
        generated_config = json.loads(generated.config_json)
        assert generated_config["grid_step_pct"] == 1.0
        assert generated_config["base_order_size"] == 30

        refreshed_policy = verify_db.query(AiAutopilotPolicy).filter(AiAutopilotPolicy.id == policy_id).first()
        assert refreshed_policy is not None
        strategy_ids = json.loads(refreshed_policy.strategy_ids_json)
        assert strategy_grid_id in strategy_ids
        assert strategy_dca_id in strategy_ids
        assert generated_strategy_id in strategy_ids


def test_ai_autopilot_auto_mode_is_blocked_without_risk_rule(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        _, _, _, strategy_grid, _, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        policy_id = policy.id
        policy.execution_mode = "auto"
        db.add(policy)
        db.commit()

    fake_runtime_control = _FakeRuntimeControl()
    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = fake_runtime_control

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "activate_strategy",
            "target_strategy_id": strategy_grid_id,
            "confidence": 0.9,
            "rationale": "switch to grid",
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=False,
        )
    )

    assert decision.status == "blocked"
    execution_result = json.loads(decision.execution_result_json)
    assert "risk rule is required" in str(execution_result.get("blocked_reason", "")).lower()
    assert fake_runtime_control.started == []


def test_ai_autopilot_blocks_disabled_action_from_policy(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user, _, _, strategy_grid, _, policy = _seed_policy_graph(db)
        strategy_grid_id = strategy_grid.id
        policy_id = policy.id
        policy.allowed_actions_json = json.dumps(["activate_strategy"])
        db.add(policy)
        db.commit()
        _add_risk_rule(db, user_id=int(user.id))

    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = _FakeRuntimeControl()

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "create_strategy_version",
            "target_strategy_id": strategy_grid_id,
            "confidence": 0.9,
            "rationale": "Attempt a generated version",
            "parameter_overrides": {
                "grid_step_pct": 0.9,
            },
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=False,
        )
    )

    assert decision.status == "blocked"
    execution_result = json.loads(decision.execution_result_json)
    assert "disabled by policy configuration" in execution_result["blocked_reason"]


def test_ai_autopilot_dry_run_create_strategy_version_supports_combo_grid_dca(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user, _, account, _, _, policy = _seed_policy_graph(db)
        combo_strategy = Strategy(
            user_id=user.id,
            name="combo-gamma",
            template_key="combo_grid_dca",
            strategy_type="combo_grid_dca",
            config_json=json.dumps(
                {
                    "exchange_account_id": account.id,
                    "symbol": "BTCUSDT",
                    "grid_count": 10,
                    "grid_step_pct": 0.45,
                    "base_order_size": 0.001,
                    "max_grid_levels": 12,
                    "cycle_seconds": 600,
                    "amount_per_cycle": 20,
                    "price_offset_pct": 0.15,
                    "min_order_volume": 0.0002,
                }
            ),
            status="stopped",
        )
        db.add(combo_strategy)
        db.commit()
        db.refresh(combo_strategy)
        policy.strategy_ids_json = json.dumps([combo_strategy.id])
        db.add(policy)
        db.commit()
        policy_id = policy.id
        combo_strategy_id = combo_strategy.id

    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = _FakeRuntimeControl()

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "create_strategy_version",
            "target_strategy_id": combo_strategy_id,
            "confidence": 0.89,
            "rationale": "Tighten both the grid cap and DCA cadence during a choppy regime",
            "parameter_overrides": {
                "max_grid_levels": 16,
                "cycle_seconds": 300,
                "price_offset_pct": 0.25,
            },
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=True,
        )
    )

    assert decision.status == "dry_run"
    execution_result = json.loads(decision.execution_result_json)
    changed_fields = execution_result["proposed_strategy"]["changed_fields"]
    assert changed_fields["max_grid_levels"] == 16
    assert changed_fields["cycle_seconds"] == 300
    assert changed_fields["price_offset_pct"] == 0.25


def test_ai_autopilot_dry_run_create_strategy_version_supports_futures_grid(async_runner, monkeypatch):
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user, _, account, _, _, policy = _seed_policy_graph(db)
        futures_strategy = Strategy(
            user_id=user.id,
            name="futures-delta",
            template_key="futures_grid",
            strategy_type="futures_grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": account.id,
                    "symbol": "BTCUSDT",
                    "grid_count": 10,
                    "grid_step_pct": 0.45,
                    "base_order_size": 0.001,
                    "max_grid_levels": 12,
                    "leverage": 3,
                    "direction": "neutral",
                }
            ),
            status="stopped",
        )
        db.add(futures_strategy)
        db.commit()
        db.refresh(futures_strategy)
        policy.strategy_ids_json = json.dumps([futures_strategy.id])
        db.add(policy)
        db.commit()
        policy_id = policy.id
        futures_strategy_id = futures_strategy.id

    service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=_FakeWsManager(),
    )
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    service._runtime_control = _FakeRuntimeControl()

    async def _fake_call_provider(*, provider, policy, context):
        return {"provider": "fake"}, {
            "action": "create_strategy_version",
            "target_strategy_id": futures_strategy_id,
            "confidence": 0.9,
            "rationale": "Bias short with higher leverage in a downside move",
            "parameter_overrides": {
                "direction": "short",
                "leverage": 7,
                "grid_step_pct": 0.55,
            },
        }

    monkeypatch.setattr(service, "_call_provider", _fake_call_provider)

    decision = async_runner(
        service.run_policy_once(
            policy_id=policy_id,
            trigger_source="manual",
            dry_run_override=True,
        )
    )

    assert decision.status == "dry_run"
    execution_result = json.loads(decision.execution_result_json)
    changed_fields = execution_result["proposed_strategy"]["changed_fields"]
    assert changed_fields["direction"] == "short"
    assert changed_fields["leverage"] == 7
    assert changed_fields["grid_step_pct"] == 0.55
