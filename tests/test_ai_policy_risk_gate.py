from __future__ import annotations

from types import SimpleNamespace
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AiAutopilotPolicy, AiProviderCredential, Base, ExchangeAccount, Strategy, User
from app.routers import ai
from app.schemas import AiAutopilotPolicyCreateRequest, AiAutopilotPolicyUpdateRequest, AiAutopilotRunRequest
from starlette.requests import Request


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_graph(db: Session) -> tuple[User, AiProviderCredential, ExchangeAccount, Strategy]:
    user = User(
        username="risk-gate-user",
        email="risk-gate-user@example.com",
        password_hash="x",
        role="user",
        is_active=True,
        totp_secret_encrypted="enc",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

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

    strategy = Strategy(
        user_id=user.id,
        name="grid-alpha",
        template_key="spot_grid",
        strategy_type="grid",
        config_json=json.dumps(
            {
                "exchange_account_id": int(account.id),
                "symbol": "BTCUSDT",
                "grid_count": 10,
                "grid_step_pct": 0.5,
                "base_order_size": 0.001,
                "max_grid_levels": 20,
            }
        ),
        status="stopped",
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return user, provider, account, strategy


def _build_policy_payload(*, provider_id: int, exchange_account_id: int, strategy_id: int) -> AiAutopilotPolicyCreateRequest:
    return AiAutopilotPolicyCreateRequest(
        name="btc-auto",
        provider_id=int(provider_id),
        exchange_account_id=int(exchange_account_id),
        symbol="BTCUSDT",
        interval="5m",
        strategy_ids=[int(strategy_id)],
        allowed_actions=["activate_strategy", "stop_strategy", "create_strategy_version"],
        execution_mode="auto",
        status="enabled",
        decision_interval_seconds=300,
        minimum_confidence=0.6,
        max_actions_per_hour=4,
        custom_prompt=None,
    )


def test_create_auto_enabled_policy_requires_risk_rule():
    with _build_session() as db:
        user, provider, account, strategy = _seed_graph(db)
        payload = _build_policy_payload(
            provider_id=provider.id,
            exchange_account_id=account.id,
            strategy_id=strategy.id,
        )
        with pytest.raises(ai.HTTPException) as exc:
            ai.create_ai_policy(payload=payload, db=db, current_user=user)
        assert exc.value.status_code == 403
        assert "Risk rule is required" in str(exc.value.detail)


def test_update_auto_enabled_policy_requires_risk_rule():
    with _build_session() as db:
        user, provider, account, strategy = _seed_graph(db)
        policy = AiAutopilotPolicy(
            user_id=user.id,
            provider_id=provider.id,
            exchange_account_id=account.id,
            name="btc-dry",
            symbol="BTCUSDT",
            interval="5m",
            strategy_ids_json=json.dumps([strategy.id]),
            allowed_actions_json=json.dumps(["activate_strategy", "stop_strategy", "create_strategy_version"]),
            execution_mode="dry_run",
            status="disabled",
            decision_interval_seconds=300,
            minimum_confidence=0.6,
            max_actions_per_hour=4,
            custom_prompt=None,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)

        payload = AiAutopilotPolicyUpdateRequest(
            name=policy.name,
            provider_id=policy.provider_id,
            exchange_account_id=policy.exchange_account_id,
            symbol=policy.symbol,
            interval="5m",
            strategy_ids=[strategy.id],
            allowed_actions=["activate_strategy", "stop_strategy", "create_strategy_version"],
            execution_mode="auto",
            status="enabled",
            decision_interval_seconds=300,
            minimum_confidence=0.6,
            max_actions_per_hour=4,
            custom_prompt=None,
        )
        with pytest.raises(ai.HTTPException) as exc:
            ai.update_ai_policy(policy_id=policy.id, payload=payload, db=db, current_user=user)
        assert exc.value.status_code == 403
        assert "Risk rule is required" in str(exc.value.detail)


def test_enable_auto_policy_requires_risk_rule():
    with _build_session() as db:
        user, provider, account, strategy = _seed_graph(db)
        policy = AiAutopilotPolicy(
            user_id=user.id,
            provider_id=provider.id,
            exchange_account_id=account.id,
            name="btc-auto",
            symbol="BTCUSDT",
            interval="5m",
            strategy_ids_json=json.dumps([strategy.id]),
            allowed_actions_json=json.dumps(["activate_strategy", "stop_strategy"]),
            execution_mode="auto",
            status="disabled",
            decision_interval_seconds=300,
            minimum_confidence=0.6,
            max_actions_per_hour=4,
            custom_prompt=None,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)

        with pytest.raises(ai.HTTPException) as exc:
            ai.enable_ai_policy(policy_id=policy.id, db=db, current_user=user)
        assert exc.value.status_code == 403
        assert "Risk rule is required" in str(exc.value.detail)


def test_manual_auto_run_requires_risk_rule(async_runner):
    with _build_session() as db:
        user, provider, account, strategy = _seed_graph(db)
        policy = AiAutopilotPolicy(
            user_id=user.id,
            provider_id=provider.id,
            exchange_account_id=account.id,
            name="btc-auto",
            symbol="BTCUSDT",
            interval="5m",
            strategy_ids_json=json.dumps([strategy.id]),
            allowed_actions_json=json.dumps(["activate_strategy", "stop_strategy"]),
            execution_mode="auto",
            status="disabled",
            decision_interval_seconds=300,
            minimum_confidence=0.6,
            max_actions_per_hour=4,
            custom_prompt=None,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)

        request = Request(
            {
                "type": "http",
                "app": SimpleNamespace(
                    state=SimpleNamespace(
                        ai_autopilot_service=SimpleNamespace(run_policy_once=None),
                    )
                ),
            }
        )
        payload = AiAutopilotRunRequest(dry_run_override=False)

        with pytest.raises(ai.HTTPException) as exc:
            async_runner(
                ai.run_ai_policy_once(
                    policy_id=policy.id,
                    payload=payload,
                    request=request,
                    db=db,
                    current_user=user,
                )
            )
        assert exc.value.status_code == 403
        assert "Risk rule is required" in str(exc.value.detail)
