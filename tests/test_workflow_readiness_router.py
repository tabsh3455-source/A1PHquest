from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    AiAutopilotPolicy,
    AiProviderCredential,
    Base,
    ExchangeAccount,
    RiskRule,
    Strategy,
    User,
)
from app.routers.workflow import get_workflow_readiness


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


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


def test_workflow_readiness_for_unauthenticated_session():
    with _build_session() as db:
        response = get_workflow_readiness(db=db, current_user=None)

        assert response.authenticated is False
        assert response.enrollment_required is False
        assert response.next_required_actions[0].code == "sign_in"
        assert any(item.template_key == "spot_grid" for item in response.live_supported_templates)
        assert any(item.template_key == "futures_grid" for item in response.live_supported_templates)


def test_workflow_readiness_requires_2fa_enrollment_first():
    with _build_session() as db:
        user = _create_user(db, "pending-2fa-user", verified=False)

        response = get_workflow_readiness(db=db, current_user=user)

        assert response.authenticated is True
        assert response.enrollment_required is True
        assert response.next_required_actions[0].code == "enroll_2fa"
        assert response.next_required_actions[0].path == "/auth/enroll-2fa"


def test_workflow_readiness_exposes_missing_setup_chain_for_verified_user():
    with _build_session() as db:
        user = _create_user(db, "verified-empty-user", verified=True)

        response = get_workflow_readiness(db=db, current_user=user)
        action_codes = [item.code for item in response.next_required_actions]

        assert response.authenticated is True
        assert response.enrollment_required is False
        assert response.has_risk_rule is False
        assert response.exchange_accounts_summary.total == 0
        assert action_codes[:5] == [
            "add_exchange_account",
            "configure_risk_rule",
            "create_strategy",
            "create_ai_provider",
            "create_ai_policy",
        ]


def test_workflow_readiness_returns_review_ops_when_core_chain_is_ready():
    with _build_session() as db:
        user = _create_user(db, "workflow-ready-user", verified=True)

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
                max_daily_loss=500,
                max_position_ratio=0.8,
                max_cancel_rate_per_minute=60,
                circuit_breaker_enabled=True,
            )
        )

        strategy = Strategy(
            user_id=user.id,
            name="grid-live",
            template_key="spot_grid",
            strategy_type="grid",
            config_json=f'{{"exchange_account_id": {account.id}, "symbol": "BTCUSDT", "grid_count": 12, "grid_step_pct": 0.5, "base_order_size": 0.001, "max_grid_levels": 40}}',
            status="running",
            runtime_ref="runtime-1",
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
                strategy_ids_json=f"[{strategy.id}]",
                allowed_actions_json='["activate_strategy","stop_strategy"]',
                status="enabled",
                execution_mode="auto",
                decision_interval_seconds=300,
                minimum_confidence=0.6,
                max_actions_per_hour=4,
                custom_prompt=None,
            )
        )
        db.commit()

        response = get_workflow_readiness(db=db, current_user=user)

        assert response.authenticated is True
        assert response.has_risk_rule is True
        assert response.exchange_accounts_summary.total == 1
        assert response.exchange_accounts_summary.by_exchange["binance"].live == 1
        assert response.ai_ready.provider_count == 1
        assert response.ai_ready.policy_count == 1
        assert response.ai_ready.auto_enabled_count == 1
        assert response.running_live_strategy_instances_total == 1
        assert response.next_required_actions[0].code == "review_ops"
