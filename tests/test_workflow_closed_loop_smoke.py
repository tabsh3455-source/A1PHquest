from __future__ import annotations

from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pyotp
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.csrf import CSRFMiddleware
from app.db import get_db
from app.models import Base
from app.routers import ai, auth, exchange_accounts, risk, strategies, workflow
from app.services.ai_autopilot import AiAutopilotService
from app.services.strategy_supervisor import RuntimeState


class _FakeWsManager:
    async def push_to_user(self, user_id: int, event: dict) -> None:
        return None


class _FakeMarketDataService:
    async def fetch_history(self, **kwargs):
        base_time = 1_710_000_000
        candles = []
        price = 100.0
        for index in range(80):
            candles.append(
                {
                    "time": base_time + index * 60,
                    "open": price,
                    "high": price + 1.5,
                    "low": price - 1.0,
                    "close": price + 0.8,
                    "volume": 10 + index,
                }
            )
            price += 0.3
        return candles


class _FakeSupervisor:
    def start_strategy(self, **kwargs):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return RuntimeState(
            runtime_ref="runtime-smoke-1",
            status="running",
            process_id="proc-smoke-1",
            started_at=now,
            last_heartbeat=now,
            last_event_seq=1,
            order_submitted_count=1,
            order_update_count=1,
            trade_fill_count=0,
            recent_events=[
                {
                    "seq": 1,
                    "type": "order_submitted",
                    "timestamp": now.isoformat(),
                    "payload": {"order_id": "smoke-order-1"},
                }
            ],
        )


def _build_client(monkeypatch) -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.state.ws_manager = _FakeWsManager()

    ai_service = AiAutopilotService(
        market_data_service=_FakeMarketDataService(),
        ws_manager=app.state.ws_manager,
    )

    async def _fake_call_provider(*, provider, policy, context):
        candidate = context.get("candidates", [{}])[0]
        return {"provider": "fake"}, {
            "action": "activate_strategy",
            "target_strategy_id": int(candidate.get("id") or 0),
            "confidence": 0.9,
            "rationale": "smoke test decision",
        }

    monkeypatch.setattr(ai_service, "_call_provider", _fake_call_provider)
    app.state.ai_autopilot_service = ai_service

    def _get_db_override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db_override

    # Use local in-memory DB for autopilot service internals as well.
    monkeypatch.setattr("app.services.ai_autopilot.SessionLocal", session_factory)
    monkeypatch.setattr(strategies, "supervisor", _FakeSupervisor())

    app.include_router(auth.router)
    app.include_router(exchange_accounts.router)
    app.include_router(risk.router)
    app.include_router(strategies.router)
    app.include_router(ai.router)
    app.include_router(workflow.router)
    return TestClient(app)


def test_minimal_trading_loop_smoke_with_readiness_and_ai_dry_run(monkeypatch):
    client = _build_client(monkeypatch)
    csrf_token: str | None = None

    def _request(method: str, path: str, payload: dict | None = None, *, step_up_token: str | None = None):
        headers: dict[str, str] = {}
        if csrf_token:
            headers["X-CSRF-Token"] = csrf_token
        if step_up_token:
            headers["X-StepUp-Token"] = step_up_token
        return client.request(method.upper(), path, json=payload, headers=headers)

    # Unauthenticated readiness state.
    unauth_readiness = client.get("/api/workflow/readiness")
    assert unauth_readiness.status_code == 200
    assert unauth_readiness.json()["next_required_actions"][0]["code"] == "sign_in"

    # Register + bind GA in one flow.
    suffix = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
    username = f"smoke_{suffix}"
    password = "StrongPass123!"
    start_response = _request(
        "POST",
        "/api/auth/register/start",
        {"username": username, "email": f"{username}@example.com", "password": password},
    )
    assert start_response.status_code == 201
    start_payload = start_response.json()
    otp_secret = str(start_payload["otp_secret"])
    register_token = str(start_payload["registration_token"])

    complete_response = _request(
        "POST",
        "/api/auth/register/complete",
        {
            "registration_token": register_token,
            "otp_code": pyotp.TOTP(otp_secret).now(),
        },
    )
    assert complete_response.status_code == 201
    csrf_token = str(complete_response.json()["csrf_token"])

    ready_after_signup = client.get("/api/workflow/readiness")
    assert ready_after_signup.status_code == 200
    ready_after_signup_payload = ready_after_signup.json()
    assert ready_after_signup_payload["authenticated"] is True
    assert ready_after_signup_payload["next_required_actions"][0]["code"] == "add_exchange_account"

    # Step-up token for high-risk endpoints.
    step_up_response = _request(
        "POST",
        "/api/auth/2fa/step-up",
        {"code": pyotp.TOTP(otp_secret).now()},
    )
    assert step_up_response.status_code == 200
    step_up_token = str(step_up_response.json()["step_up_token"])

    # Account -> Risk -> Strategy -> Start runtime.
    account_response = _request(
        "POST",
        "/api/exchange-accounts",
        {
            "exchange": "binance",
            "account_alias": "smoke-binance",
            "api_key": "smoke-key",
            "api_secret": "smoke-secret",
            "is_testnet": True,
        },
        step_up_token=step_up_token,
    )
    assert account_response.status_code == 201
    exchange_account_id = int(account_response.json()["id"])

    risk_response = _request(
        "PUT",
        "/api/risk-rules",
        {
            "max_order_notional": 5000,
            "max_daily_loss": 1000,
            "max_position_ratio": 0.5,
            "max_cancel_rate_per_minute": 60,
            "circuit_breaker_enabled": True,
        },
        step_up_token=step_up_token,
    )
    assert risk_response.status_code == 200

    strategy_response = _request(
        "POST",
        "/api/strategies",
        {
            "name": "smoke-spot-grid",
            "template_key": "spot_grid",
            "config": {
                "exchange_account_id": exchange_account_id,
                "symbol": "BTCUSDT",
                "grid_count": 8,
                "grid_step_pct": 0.5,
                "base_order_size": 0.001,
            },
        },
    )
    assert strategy_response.status_code == 201
    strategy_id = int(strategy_response.json()["id"])

    refresh_step_up_response = _request(
        "POST",
        "/api/auth/2fa/step-up",
        {"code": pyotp.TOTP(otp_secret).now()},
    )
    assert refresh_step_up_response.status_code == 200
    step_up_token = str(refresh_step_up_response.json()["step_up_token"])

    start_runtime_response = _request(
        "POST",
        f"/api/strategies/{strategy_id}/start",
        {},
        step_up_token=step_up_token,
    )
    assert start_runtime_response.status_code == 200
    assert str(start_runtime_response.json().get("status")) in {"starting", "running", "failed"}

    # AI provider + policy + dry-run execution.
    provider_response = _request(
        "POST",
        "/api/ai/providers",
        {
            "name": "smoke-provider",
            "provider_type": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-test",
            "api_key": "smoke-api-key",
            "is_active": True,
        },
        step_up_token=step_up_token,
    )
    assert provider_response.status_code == 201
    provider_id = int(provider_response.json()["id"])

    policy_response = _request(
        "POST",
        "/api/ai/policies",
        {
            "name": "smoke-policy",
            "provider_id": provider_id,
            "exchange_account_id": exchange_account_id,
            "symbol": "BTCUSDT",
            "interval": "5m",
            "strategy_ids": [strategy_id],
            "allowed_actions": ["activate_strategy", "stop_strategy", "create_strategy_version"],
            "execution_mode": "auto",
            "status": "enabled",
            "decision_interval_seconds": 300,
            "minimum_confidence": 0.6,
            "max_actions_per_hour": 4,
            "custom_prompt": None,
        },
        step_up_token=step_up_token,
    )
    assert policy_response.status_code == 201
    policy_id = int(policy_response.json()["id"])

    ai_run_response = _request(
        "POST",
        f"/api/ai/policies/{policy_id}/run",
        {"dry_run_override": True},
        step_up_token=step_up_token,
    )
    assert ai_run_response.status_code == 200
    ai_run_payload = ai_run_response.json()
    assert ai_run_payload["status"] == "dry_run"
    assert ai_run_payload["action"] == "activate_strategy"

    final_readiness = client.get("/api/workflow/readiness")
    assert final_readiness.status_code == 200
    final_payload = final_readiness.json()
    assert final_payload["has_risk_rule"] is True
    assert final_payload["exchange_accounts_summary"]["total"] == 1
    assert final_payload["running_live_strategy_instances_total"] >= 1
    assert final_payload["ai_ready"]["provider_count"] == 1
    assert final_payload["ai_ready"]["policy_count"] == 1
    assert final_payload["ai_ready"]["auto_enabled_count"] == 1
    assert final_payload["next_required_actions"][0]["code"] == "review_ops"
