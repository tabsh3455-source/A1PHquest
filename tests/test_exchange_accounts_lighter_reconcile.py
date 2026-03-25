from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, ExchangeAccount, LighterReconcileRecord, User
from app.routers import exchange_accounts as exchange_accounts_router
from app.services.gateway_service import GatewaySyncResult


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


def _create_account(db: Session, user_id: int, exchange: str) -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange=exchange,
        account_alias=f"{exchange}-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_list_lighter_reconcile_pending_rejects_non_lighter_account():
    with _build_session() as db:
        user = _create_user(db, "reconcile-non-lighter")
        account = _create_account(db, user.id, exchange="binance")

        try:
            exchange_accounts_router.list_lighter_reconcile_pending(
                account_id=account.id,
                limit=50,
                db=db,
                current_user=user,
            )
            raise AssertionError("Expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 400


def test_list_lighter_reconcile_pending_returns_stats_and_expires_old_records():
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner")
        other = _create_user(db, "reconcile-other")
        account = _create_account(db, user.id, exchange="lighter")
        other_account = _create_account(db, other.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        recent = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="ord-recent",
            symbol="BTC-USDC",
            status="pending",
            raw_json="{}",
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
        expired_candidate = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="cancel",
            request_order_id="ord-old",
            symbol="ETH-USDC",
            status="pending",
            raw_json="{}",
            created_at=now - timedelta(hours=10),
            updated_at=now - timedelta(hours=10),
        )
        foreign = LighterReconcileRecord(
            user_id=other.id,
            exchange_account_id=other_account.id,
            operation="submit",
            request_order_id="ord-foreign",
            symbol="SOL-USDC",
            status="pending",
            raw_json="{}",
            created_at=now - timedelta(hours=10),
            updated_at=now - timedelta(hours=10),
        )
        db.add_all([recent, expired_candidate, foreign])
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=50,
            db=db,
            current_user=user,
        )
        assert response.account_id == account.id
        assert response.expired_now == 1
        assert response.expired_pruned_now == 0
        assert response.status_stats["pending"] == 1
        assert response.status_stats["expired"] == 1
        assert response.pending_oldest_age_seconds is not None
        assert response.pending_oldest_age_seconds >= 0
        assert response.recent_failure_reasons == []
        assert response.failure_code_stats == {}
        assert response.retry_due_count == 0
        assert response.retry_blocked_count == 0
        assert response.no_retry_hint_count == 1
        assert response.next_retry_at is None
        assert len(response.records) == 1
        assert response.records[0].request_order_id == "ord-recent"
        assert response.records[0].resolved_match_by is None
        assert response.records[0].candidate_order_ids == []
        assert response.records[0].candidate_client_order_ids == []


def test_list_lighter_reconcile_pending_includes_recent_failure_reasons():
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner-errors")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        failed = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="ord-failed",
            symbol="BTC-USDC",
            status="pending",
            raw_json='{"last_sync_error":"upstream timeout","sync_error_count":2}',
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=1),
        )
        db.add(failed)
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=20,
            db=db,
            current_user=user,
        )
        assert response.recent_failure_reasons == ["upstream timeout"]
        assert response.failure_code_stats == {"upstream_unavailable": 1}
        assert response.no_retry_hint_count == 1
        assert response.records[0].sync_error_count == 2
        assert response.records[0].last_sync_error_code == "upstream_unavailable"


def test_list_lighter_reconcile_pending_exposes_match_debug_fields():
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner-match")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = {
            "resolved_match_by": "order_id_candidate",
            "resolved_match_value": "9911",
            "match_candidates": {
                "order_ids": ["0xtxhash", "9911"],
                "client_order_ids": ["cli-9911"],
            },
        }
        record = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="0xtxhash",
            symbol="ETH-USDC",
            status="pending",
            raw_json=json.dumps(payload, ensure_ascii=False),
            created_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=1),
        )
        db.add(record)
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=20,
            db=db,
            current_user=user,
        )
        assert len(response.records) == 1
        row = response.records[0]
        assert row.resolved_match_by == "order_id_candidate"
        assert row.resolved_match_value == "9911"
        assert row.candidate_order_ids == ["0xtxhash", "9911"]
        assert row.candidate_client_order_ids == ["cli-9911"]


def test_list_lighter_reconcile_pending_prunes_old_expired_records(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner-prune")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        monkeypatch.setattr(
            "app.services.lighter_reconcile_service.settings.lighter_reconcile_expired_retention_seconds",
            3600,
        )

        old_expired = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="prune-old-1",
            symbol="BTC-USDC",
            status="expired",
            raw_json='{"expire_reason":"sync_error_threshold_reached"}',
            created_at=now - timedelta(days=2),
            updated_at=now - timedelta(hours=5),
        )
        pending = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="pending-1",
            symbol="ETH-USDC",
            status="pending",
            raw_json="{}",
            created_at=now - timedelta(minutes=20),
            updated_at=now - timedelta(minutes=20),
        )
        db.add_all([old_expired, pending])
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=20,
            db=db,
            current_user=user,
        )
        assert response.expired_pruned_now == 1
        assert response.status_stats["pending"] == 1
        remaining_ids = {
            row.request_order_id
            for row in db.query(LighterReconcileRecord).all()
        }
        assert remaining_ids == {"pending-1"}


def test_list_lighter_reconcile_pending_aggregates_failure_code_stats():
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner-code-stats")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="code-1",
                symbol="BTC-USDC",
                status="pending",
                raw_json='{"last_sync_error":"timeout","last_sync_error_code":"network_error"}',
                created_at=now - timedelta(minutes=8),
                updated_at=now - timedelta(minutes=1),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="cancel",
                request_order_id="code-2",
                symbol="ETH-USDC",
                status="pending",
                raw_json='{"last_sync_error":"429 too many requests","last_sync_error_code":"rate_limited"}',
                created_at=now - timedelta(minutes=6),
                updated_at=now - timedelta(minutes=2),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="code-3",
                symbol="SOL-USDC",
                status="pending",
                raw_json='{"last_sync_error":"timeout","last_sync_error_code":"network_error"}',
                created_at=now - timedelta(minutes=4),
                updated_at=now - timedelta(minutes=3),
            ),
        ]
        db.add_all(rows)
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=20,
            db=db,
            current_user=user,
        )
        assert response.failure_code_stats == {"network_error": 2, "rate_limited": 1}
        assert response.retry_due_count == 0
        assert response.retry_blocked_count == 0
        assert response.no_retry_hint_count == 3


def test_list_lighter_reconcile_pending_exposes_retry_window_fields():
    with _build_session() as db:
        user = _create_user(db, "reconcile-owner-retry-window")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        due_retry_at = (now - timedelta(seconds=10)).isoformat()
        blocked_retry_at = (now + timedelta(seconds=120)).isoformat()
        rows = [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="retry-due",
                symbol="BTC-USDC",
                status="pending",
                raw_json=json.dumps(
                    {
                        "next_retry_at": due_retry_at,
                        "next_retry_after_seconds": 3,
                    }
                ),
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=1),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="retry-blocked",
                symbol="ETH-USDC",
                status="pending",
                raw_json=json.dumps(
                    {
                        "next_retry_at": blocked_retry_at,
                        "next_retry_after_seconds": 30,
                    }
                ),
                created_at=now - timedelta(minutes=5),
                updated_at=now - timedelta(minutes=1),
            ),
        ]
        db.add_all(rows)
        db.commit()

        response = exchange_accounts_router.list_lighter_reconcile_pending(
            account_id=account.id,
            limit=20,
            db=db,
            current_user=user,
        )
        assert response.retry_due_count == 1
        assert response.retry_blocked_count == 1
        assert response.no_retry_hint_count == 0
        assert response.next_retry_at is not None
        record_map = {item.request_order_id: item for item in response.records}
        assert record_map["retry-due"].next_retry_after_seconds == 3
        assert record_map["retry-due"].next_retry_at is not None
        assert record_map["retry-blocked"].next_retry_after_seconds == 30


def test_retry_lighter_reconcile_sync_reconciles_pending_records(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "reconcile-retry-sync-owner")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        record = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="9001",
            symbol="BTC-USDC",
            status="pending",
            raw_json='{"tx_hash":"0xabc"}',
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
        db.add(record)
        db.commit()

        monkeypatch.setattr(
            exchange_accounts_router,
            "kms",
            SimpleNamespace(decrypt=lambda _value: "dummy"),
        )

        def _fake_fetch(*args, **kwargs):
            return GatewaySyncResult(
                success=True,
                message="lighter sync ok",
                balances=[],
                positions=[],
                orders=[
                    {
                        "order_id": "9001",
                        "symbol": "BTC-USDC",
                        "status": "OPEN",
                        "side": "BUY",
                        "order_type": "LIMIT",
                        "price": 100,
                        "quantity": 1,
                        "filled_quantity": 1,
                    }
                ],
                trades=[
                        {
                            "symbol": "BTC-USDC",
                            "order_id": "9001",
                            "trade_id": "t-9001",
                            "side": "BUY",
                        "price": 100,
                            "quantity": 1,
                            "quote_quantity": 100,
                            "fee": 0,
                            "trade_time": now.isoformat(),
                        }
                    ],
                )

        monkeypatch.setattr(exchange_accounts_router.gateway_service, "fetch_account_state", _fake_fetch)

        response = exchange_accounts_router.retry_lighter_reconcile_sync(
            account_id=account.id,
            db=db,
            current_user=user,
        )
        assert response.success is True
        assert response.pending_before == 1
        assert response.pending_after == 0
        assert response.reconciled_now == 1
        assert response.orders_synced == 1
        assert response.trades_synced == 1
        assert response.expired_pruned_now == 0

        updated = db.query(LighterReconcileRecord).filter(LighterReconcileRecord.id == record.id).first()
        assert updated is not None
        assert updated.status == "reconciled"
        assert updated.resolved_order_id == "9001"
        assert updated.resolved_trade_id == "t-9001"
        audit = (
            db.query(AuditEvent)
            .filter(AuditEvent.user_id == user.id, AuditEvent.action == "lighter_reconcile_retry_sync")
            .order_by(AuditEvent.id.desc())
            .first()
        )
        assert audit is not None
        details = json.loads(audit.details_json)
        assert details["success"] is True
        assert details["reconciled_now"] == 1


def test_retry_lighter_reconcile_sync_skips_blocked_backoff(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "reconcile-retry-sync-blocked")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        record = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="blocked-9001",
            symbol="BTC-USDC",
            status="pending",
            raw_json=json.dumps({"next_retry_at": (now + timedelta(minutes=5)).isoformat()}),
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
        db.add(record)
        db.commit()

        def _should_not_call(*args, **kwargs):
            raise AssertionError("gateway fetch should be skipped for blocked backoff records")

        monkeypatch.setattr(exchange_accounts_router.gateway_service, "fetch_account_state", _should_not_call)

        response = exchange_accounts_router.retry_lighter_reconcile_sync(
            account_id=account.id,
            db=db,
            current_user=user,
        )
        assert response.success is True
        assert "backoff" in response.message.lower()
        assert response.pending_before == 1
        assert response.pending_after == 1
        assert response.reconciled_now == 0
        assert response.expired_pruned_now == 0


def test_retry_lighter_reconcile_sync_records_failure_audit(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "reconcile-retry-sync-failed")
        account = _create_account(db, user.id, exchange="lighter")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        record = LighterReconcileRecord(
            user_id=user.id,
            exchange_account_id=account.id,
            operation="submit",
            request_order_id="failed-9001",
            symbol="BTC-USDC",
            status="pending",
            raw_json="{}",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
        db.add(record)
        db.commit()

        monkeypatch.setattr(
            exchange_accounts_router,
            "kms",
            SimpleNamespace(decrypt=lambda _value: "dummy"),
        )
        monkeypatch.setattr(
            exchange_accounts_router.gateway_service,
            "fetch_account_state",
            lambda *args, **kwargs: GatewaySyncResult(
                success=False,
                message="upstream timeout",
                balances=[],
                positions=[],
                orders=[],
                trades=[],
            ),
        )

        response = exchange_accounts_router.retry_lighter_reconcile_sync(
            account_id=account.id,
            db=db,
            current_user=user,
        )
        assert response.success is False
        assert "failed" in response.message
        assert response.pending_before == 1
        assert response.pending_after == 1
        assert response.expired_pruned_now == 0
        audit = (
            db.query(AuditEvent)
            .filter(AuditEvent.user_id == user.id, AuditEvent.action == "lighter_reconcile_retry_sync")
            .order_by(AuditEvent.id.desc())
            .first()
        )
        assert audit is not None
        details = json.loads(audit.details_json)
        assert details["success"] is False
        assert details["message"] == "upstream timeout"


def test_retry_lighter_reconcile_sync_rejects_cross_tenant_account():
    with _build_session() as db:
        owner = _create_user(db, "reconcile-owner-cross")
        intruder = _create_user(db, "reconcile-intruder-cross")
        account = _create_account(db, owner.id, exchange="lighter")

        try:
            exchange_accounts_router.retry_lighter_reconcile_sync(
                account_id=account.id,
                db=db,
                current_user=intruder,
            )
            raise AssertionError("Expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 404
