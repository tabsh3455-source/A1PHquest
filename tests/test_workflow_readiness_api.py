from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.deps import get_current_user_optional
from app.models import (
    AiAutopilotPolicy,
    AiProviderCredential,
    Base,
    ExchangeAccount,
    RiskRule,
    Strategy,
    User,
)
from app.routers import workflow


def _build_workflow_client(
    *,
    current_user_id: int | None = None,
    session_factory: sessionmaker | None = None,
) -> tuple[TestClient, sessionmaker]:
    if session_factory is None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)
    else:
        factory = session_factory
    app = FastAPI()

    def _get_db_override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db_override

    def _get_current_user_optional_override():
        if current_user_id is None:
            return None
        with factory() as db:
            return db.query(User).filter(User.id == int(current_user_id)).first()

    app.dependency_overrides[get_current_user_optional] = _get_current_user_optional_override
    app.include_router(workflow.router)
    return TestClient(app), factory


def _create_user(db: Session, username: str, *, verified: bool) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="encrypted-secret" if verified else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_workflow_readiness_route_for_unauthenticated_user():
    client, _ = _build_workflow_client(current_user_id=None)
    response = client.get("/api/workflow/readiness")
    assert response.status_code == 200

    payload = response.json()
    assert payload["authenticated"] is False
    assert payload["next_required_actions"][0]["code"] == "sign_in"
    live_template_keys = {item["template_key"] for item in payload["live_supported_templates"]}
    assert {"spot_grid", "futures_grid", "dca", "combo_grid_dca"}.issubset(live_template_keys)


def test_workflow_readiness_route_for_user_missing_2fa():
    bootstrap_client, factory = _build_workflow_client(current_user_id=None)
    bootstrap_client.close()
    with factory() as db:
        user = _create_user(db, "workflow-user-pending-2fa", verified=False)
        user_id = int(user.id)

    client, _ = _build_workflow_client(current_user_id=user_id, session_factory=factory)
    response = client.get("/api/workflow/readiness")
    assert response.status_code == 200

    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["enrollment_required"] is True
    assert payload["next_required_actions"][0]["code"] == "enroll_2fa"
    assert payload["next_required_actions"][0]["path"] == "/auth/enroll-2fa"


def test_workflow_readiness_route_for_verified_user_without_risk_setup():
    bootstrap_client, factory = _build_workflow_client(current_user_id=None)
    bootstrap_client.close()
    with factory() as db:
        user = _create_user(db, "workflow-user-no-risk", verified=True)
        user_id = int(user.id)

    client, _ = _build_workflow_client(current_user_id=user_id, session_factory=factory)
    response = client.get("/api/workflow/readiness")
    assert response.status_code == 200

    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["enrollment_required"] is False
    assert payload["has_risk_rule"] is False
    assert payload["exchange_accounts_summary"]["total"] == 0
    assert [item["code"] for item in payload["next_required_actions"][:5]] == [
        "add_exchange_account",
        "configure_risk_rule",
        "create_strategy",
        "create_ai_provider",
        "create_ai_policy",
    ]


def test_workflow_readiness_route_for_fully_ready_user():
    bootstrap_client, factory = _build_workflow_client(current_user_id=None)
    bootstrap_client.close()
    with factory() as db:
        user = _create_user(db, "workflow-user-ready", verified=True)
        account = ExchangeAccount(
            user_id=user.id,
            exchange="binance",
            account_alias="main-live",
            api_key_encrypted="k",
            api_secret_encrypted="s",
            passphrase_encrypted=None,
            is_testnet=False,
        )
        db.add(account)
        db.flush()

        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=1000,
                max_daily_loss=300,
                max_position_ratio=0.7,
                max_cancel_rate_per_minute=60,
                circuit_breaker_enabled=True,
            )
        )

        strategy = Strategy(
            user_id=user.id,
            name="grid-live",
            template_key="spot_grid",
            strategy_type="grid",
            config_json=json.dumps(
                {
                    "exchange_account_id": int(account.id),
                    "symbol": "BTCUSDT",
                    "grid_count": 12,
                    "grid_step_pct": 0.5,
                    "base_order_size": 0.001,
                    "max_grid_levels": 40,
                }
            ),
            status="running",
            runtime_ref="runtime-ready-1",
        )
        db.add(strategy)
        db.flush()

        provider = AiProviderCredential(
            user_id=user.id,
            name="primary-provider",
            provider_type="openai_compatible",
            base_url="https://api.example.com/v1",
            model_name="gpt-test",
            api_key_encrypted="enc",
            is_active=True,
        )
        db.add(provider)
        db.flush()

        db.add(
            AiAutopilotPolicy(
                user_id=user.id,
                provider_id=provider.id,
                exchange_account_id=account.id,
                name="btc-auto",
                symbol="BTCUSDT",
                interval="5m",
                strategy_ids_json=json.dumps([int(strategy.id)]),
                allowed_actions_json=json.dumps(["activate_strategy", "stop_strategy"]),
                status="enabled",
                execution_mode="auto",
                decision_interval_seconds=300,
                minimum_confidence=0.6,
                max_actions_per_hour=4,
                custom_prompt=None,
            )
        )
        db.commit()
        user_id = int(user.id)

    client, _ = _build_workflow_client(current_user_id=user_id, session_factory=factory)
    response = client.get("/api/workflow/readiness")
    assert response.status_code == 200

    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["has_risk_rule"] is True
    assert payload["exchange_accounts_summary"]["total"] == 1
    assert payload["exchange_accounts_summary"]["by_exchange"]["binance"]["live"] == 1
    assert payload["running_live_strategy_instances_total"] == 1
    assert payload["ai_ready"]["provider_count"] == 1
    assert payload["ai_ready"]["policy_count"] == 1
    assert payload["ai_ready"]["auto_enabled_count"] == 1
    assert payload["next_required_actions"][0]["code"] == "review_ops"
