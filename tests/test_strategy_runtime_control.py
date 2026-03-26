from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, ExchangeAccount, Strategy, User
from app.services import strategy_runtime_control


def _build_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)


def test_validate_live_runtime_strategy_accepts_combo_grid_dca():
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user = User(
            username="combo-user",
            email="combo@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        account = ExchangeAccount(
            user_id=user.id,
            exchange="binance",
            account_alias="combo-main",
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
            name="combo-live",
            template_key="combo_grid_dca",
            strategy_type="combo_grid_dca",
            config_json=json.dumps(
                {
                    "exchange_account_id": account.id,
                    "symbol": "BTCUSDT",
                    "grid_count": 10,
                    "grid_step_pct": 0.4,
                    "base_order_size": 0.001,
                    "cycle_seconds": 300,
                    "amount_per_cycle": 15,
                }
            ),
            status="stopped",
        )
        db.add(strategy)
        db.commit()

        strategy_runtime_control._validate_live_runtime_strategy(
            strategy=strategy,
            config=json.loads(strategy.config_json),
            db=db,
            user_id=user.id,
        )


def test_validate_live_runtime_strategy_rejects_non_live_custom_type():
    session_factory = _build_sessionmaker()
    with session_factory() as db:
        user = User(
            username="custom-user",
            email="custom@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        strategy = Strategy(
            user_id=user.id,
            name="custom-only",
            template_key="signal_bot",
            strategy_type="custom",
            config_json=json.dumps({"exchange_account_id": 1, "symbol": "BTCUSDT"}),
            status="stopped",
        )
        db.add(strategy)
        db.commit()

        try:
            strategy_runtime_control._validate_live_runtime_strategy(
                strategy=strategy,
                config=json.loads(strategy.config_json),
                db=db,
                user_id=user.id,
            )
            raise AssertionError("Expected combo runtime validator to reject custom strategy type")
        except strategy_runtime_control.StrategyRuntimeControlError as exc:
            assert "not enabled for live runtime" in str(exc)
