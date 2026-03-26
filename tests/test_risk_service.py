from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    AccountBalanceSnapshot,
    AuditEvent,
    Base,
    ExchangeAccount,
    PositionSnapshot,
    RiskRule,
    TradeFillSnapshot,
    User,
)
from app.services.risk_service import RiskService


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


def _create_account(db: Session, *, user_id: int, exchange: str = "binance") -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange=exchange,
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


def _create_fill(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    symbol: str,
    order_id: str,
    trade_id: str,
    side: str,
    price: float,
    quantity: float,
    fee: float,
    fee_asset: str | None,
    trade_time: datetime,
) -> None:
    fill = TradeFillSnapshot(
        user_id=user_id,
        exchange_account_id=account_id,
        exchange="binance",
        symbol=symbol,
        order_id=order_id,
        trade_id=trade_id,
        side=side,
        price=price,
        quantity=quantity,
        quote_quantity=price * quantity,
        fee=fee,
        fee_asset=fee_asset,
        is_maker=False,
        trade_time=trade_time,
        raw_json="{}",
    )
    db.add(fill)


def _create_balance(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    asset: str,
    total: float,
) -> None:
    db.add(
        AccountBalanceSnapshot(
            user_id=user_id,
            exchange_account_id=account_id,
            exchange="binance",
            asset=asset,
            free=total,
            locked=0,
            total=total,
        )
    )


def _create_position(
    db: Session,
    *,
    user_id: int,
    account_id: int,
    symbol: str,
    quantity: float,
    entry_price: float,
    mark_price: float,
) -> None:
    db.add(
        PositionSnapshot(
            user_id=user_id,
            exchange_account_id=account_id,
            exchange="binance",
            symbol=symbol,
            side="LONG",
            quantity=quantity,
            entry_price=entry_price,
            mark_price=mark_price,
            unrealized_pnl=0,
        )
    )


def test_cancel_rate_denies_when_threshold_reached():
    service = RiskService()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with _build_session() as db:
        user = _create_user(db, "alice")
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=0,
                max_position_ratio=1,
                max_cancel_rate_per_minute=2,
                circuit_breaker_enabled=True,
            )
        )
        db.add_all(
            [
                AuditEvent(
                    user_id=user.id,
                    action="order_cancel",
                    resource="order",
                    resource_id="1",
                    details_json="{}",
                    created_at=now - timedelta(seconds=20),
                ),
                AuditEvent(
                    user_id=user.id,
                    action="order_cancel",
                    resource="order",
                    resource_id="2",
                    details_json="{}",
                    created_at=now - timedelta(seconds=10),
                ),
            ]
        )
        db.commit()

        decision = service.evaluate_cancel_rate(db, user_id=user.id)
        assert not decision.allowed
        assert "exceeds" in decision.reason.lower()


def test_cancel_rate_allows_when_below_threshold():
    service = RiskService()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with _build_session() as db:
        user = _create_user(db, "bob")
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=0,
                max_position_ratio=1,
                max_cancel_rate_per_minute=3,
                circuit_breaker_enabled=True,
            )
        )
        db.add(
            AuditEvent(
                user_id=user.id,
                action="order_cancel",
                resource="order",
                resource_id="1",
                details_json="{}",
                created_at=now - timedelta(seconds=20),
            )
        )
        db.commit()

        decision = service.evaluate_cancel_rate(db, user_id=user.id)
        assert decision.allowed


def test_order_check_fails_closed_when_risk_rule_missing():
    service = RiskService()
    with _build_session() as db:
        user = _create_user(db, "risk-missing")
        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=100,
            projected_daily_loss=0,
            projected_position_ratio=0.1,
        )
        assert decision.allowed is False
        assert decision.code == "risk_rule_required"


def test_order_check_allows_dry_run_when_rule_missing():
    service = RiskService()
    with _build_session() as db:
        user = _create_user(db, "risk-missing-dry-run")
        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=100,
            projected_daily_loss=0,
            projected_position_ratio=0.1,
            require_rule=False,
        )
        assert decision.allowed is True
        assert decision.code == "rule_missing_dry_run"


def test_order_notional_check_rejects_oversized_order():
    service = RiskService()

    with _build_session() as db:
        user = _create_user(db, "carol")
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=1000,
                max_daily_loss=0,
                max_position_ratio=1,
                max_cancel_rate_per_minute=10,
                circuit_breaker_enabled=True,
            )
        )
        db.commit()

        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=1000.01,
            projected_daily_loss=0,
            projected_position_ratio=0.3,
        )
        assert not decision.allowed
        assert "notional" in decision.reason.lower()


def test_server_side_position_ratio_uses_account_snapshots():
    service = RiskService()

    with _build_session() as db:
        user = _create_user(db, "carol-position")
        account = _create_account(db, user_id=user.id)
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=0,
                max_position_ratio=0.75,
                max_cancel_rate_per_minute=10,
                circuit_breaker_enabled=True,
            )
        )
        _create_balance(db, user_id=user.id, account_id=account.id, asset="USDT", total=1000)
        _create_position(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="BTCUSDT",
            quantity=4,
            entry_price=100,
            mark_price=100,
        )
        db.commit()

        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=400,
            projected_daily_loss=0,
            projected_position_ratio=0,
            account_id=account.id,
            symbol="BTCUSDT",
        )
        assert decision.allowed is False
        assert decision.code == "position_ratio_limit_exceeded"
        assert decision.evaluated_position_ratio == 0.8


def test_server_side_position_ratio_fails_closed_without_balance_snapshots():
    service = RiskService()

    with _build_session() as db:
        user = _create_user(db, "carol-position-missing")
        account = _create_account(db, user_id=user.id)
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=0,
                max_position_ratio=0.75,
                max_cancel_rate_per_minute=10,
                circuit_breaker_enabled=True,
            )
        )
        db.commit()

        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=100,
            projected_daily_loss=0,
            projected_position_ratio=0,
            account_id=account.id,
            symbol="BTCUSDT",
        )
        assert decision.allowed is False
        assert decision.code == "position_ratio_context_unavailable"


def test_rejection_burst_detects_repeated_risk_blocks():
    service = RiskService()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with _build_session() as db:
        user = _create_user(db, "dave")
        for index in range(6):
            db.add(
                AuditEvent(
                    user_id=user.id,
                    action="order_submit_rejected_risk",
                    resource="order",
                    resource_id=str(index),
                    details_json="{}",
                    created_at=now - timedelta(seconds=30),
                )
            )
        db.commit()

        decision = service.evaluate_rejection_burst(db, user_id=user.id)
        assert not decision.allowed
        assert "burst" in decision.reason.lower()


def test_is_circuit_breaker_enabled_defaults_false_without_rule():
    service = RiskService()
    with _build_session() as db:
        user = _create_user(db, "eve")
        assert service.is_circuit_breaker_enabled(db, user_id=user.id) is False


def test_is_circuit_breaker_enabled_reads_rule_flag():
    service = RiskService()
    with _build_session() as db:
        user = _create_user(db, "frank")
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=0,
                max_position_ratio=1,
                max_cancel_rate_per_minute=1,
                circuit_breaker_enabled=False,
            )
        )
        db.commit()
        assert service.is_circuit_breaker_enabled(db, user_id=user.id) is False

        rule = db.query(RiskRule).filter(RiskRule.user_id == user.id).first()
        assert rule is not None
        rule.circuit_breaker_enabled = True
        db.add(rule)
        db.commit()

        assert service.is_circuit_breaker_enabled(db, user_id=user.id) is True


def test_calculate_daily_realized_loss_uses_today_fills_only():
    service = RiskService()
    now = datetime(2026, 3, 23, 12, 0, 0)

    with _build_session() as db:
        user = _create_user(db, "grace")
        account = _create_account(db, user_id=user.id)
        # Today's closed long: (sell 80 - buy 100) * 1 - fees(1+1) = -22
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="BTCUSDT",
            order_id="o1",
            trade_id="t1",
            side="BUY",
            price=100,
            quantity=1,
            fee=1,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=20),
        )
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="BTCUSDT",
            order_id="o2",
            trade_id="t2",
            side="SELL",
            price=80,
            quantity=1,
            fee=1,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=10),
        )
        # Yesterday's loss should not enter today's calculation window.
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="ETHUSDT",
            order_id="o3",
            trade_id="t3",
            side="BUY",
            price=100,
            quantity=1,
            fee=0,
            fee_asset="USDT",
            trade_time=now - timedelta(days=1),
        )
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="ETHUSDT",
            order_id="o4",
            trade_id="t4",
            side="SELL",
            price=1,
            quantity=1,
            fee=0,
            fee_asset="USDT",
            trade_time=now - timedelta(days=1, minutes=-1),
        )
        # Another user should be tenant-isolated.
        other_user = _create_user(db, "heidi")
        other_account = _create_account(db, user_id=other_user.id)
        _create_fill(
            db,
            user_id=other_user.id,
            account_id=other_account.id,
            symbol="BTCUSDT",
            order_id="o5",
            trade_id="t5",
            side="BUY",
            price=1000,
            quantity=1,
            fee=0,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=5),
        )
        _create_fill(
            db,
            user_id=other_user.id,
            account_id=other_account.id,
            symbol="BTCUSDT",
            order_id="o6",
            trade_id="t6",
            side="SELL",
            price=1,
            quantity=1,
            fee=0,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=1),
        )
        db.commit()

        loss = service.calculate_daily_realized_loss(db, user_id=user.id, now=now.replace(tzinfo=timezone.utc))
        assert loss == 22


def test_evaluate_order_uses_realized_loss_from_fills_without_projected_input():
    service = RiskService()
    now = datetime(2026, 3, 23, 14, 0, 0, tzinfo=timezone.utc)

    with _build_session() as db:
        user = _create_user(db, "ivan")
        account = _create_account(db, user_id=user.id)
        db.add(
            RiskRule(
                user_id=user.id,
                max_order_notional=0,
                max_daily_loss=20,
                max_position_ratio=1,
                max_cancel_rate_per_minute=10,
                circuit_breaker_enabled=True,
            )
        )
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="BTCUSDT",
            order_id="o7",
            trade_id="t7",
            side="BUY",
            price=100,
            quantity=1,
            fee=1,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=20),
        )
        _create_fill(
            db,
            user_id=user.id,
            account_id=account.id,
            symbol="BTCUSDT",
            order_id="o8",
            trade_id="t8",
            side="SELL",
            price=80,
            quantity=1,
            fee=1,
            fee_asset="USDT",
            trade_time=now - timedelta(minutes=10),
        )
        db.commit()

        decision = service.evaluate_order(
            db,
            user_id=user.id,
            order_notional=10,
            projected_daily_loss=0,
            projected_position_ratio=0.1,
            now=now,
        )
        assert decision.allowed is False
        assert decision.code == "daily_loss_limit_exceeded"
        assert "daily realized loss" in decision.reason.lower()
        assert decision.realized_daily_loss == 22
        assert decision.evaluated_daily_loss == 22
