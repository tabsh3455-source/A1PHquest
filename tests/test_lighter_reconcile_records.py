from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ExchangeAccount, LighterReconcileRecord, User
from app.services.lighter_reconcile_service import LighterReconcileService, _classify_sync_error


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


def _create_lighter_account(db: Session, user_id: int) -> ExchangeAccount:
    account = ExchangeAccount(
        user_id=user_id,
        exchange="lighter",
        account_alias="lighter-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def test_lighter_reconcile_record_resolves_by_exact_order_id():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-1")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="9001",
            symbol="ETH-USDC",
            raw_payload={"tx_hash": "0xabc"},
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[{"order_id": "9001", "symbol": "ETH-USDC"}],
            synced_trades=[{"order_id": "9001", "trade_id": "t-1", "symbol": "ETH-USDC"}],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 1
        assert record.status == "reconciled"
        assert record.resolved_order_id == "9001"
        assert record.resolved_trade_id == "t-1"
        assert record.resolved_at is not None


def test_lighter_reconcile_record_supports_tx_hash_symbol_fallback():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-2")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="cancel",
            request_order_id="0xtxhash123",
            symbol="BTC-USDC",
            raw_payload={"tx_hash": "0xtxhash123"},
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[{"order_id": "7788", "symbol": "BTC-USDC"}],
            synced_trades=[],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 1
        assert record.status == "reconciled"
        assert record.resolved_order_id == "7788"
        assert record.request_order_id == "0xtxhash123"
        # Ensure original payload is persisted for audit/troubleshooting.
        assert json.loads(record.raw_json)["tx_hash"] == "0xtxhash123"


def test_lighter_reconcile_tx_hash_symbol_fallback_stays_pending_when_symbol_is_ambiguous():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-ambiguous")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="cancel",
            request_order_id="0xtxhash-ambiguous",
            symbol="BTC-USDC",
            raw_payload={"tx_hash": "0xtxhash-ambiguous"},
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[
                {"order_id": "7788", "symbol": "BTC-USDC"},
                {"order_id": "8899", "symbol": "BTC-USDC"},
            ],
            synced_trades=[],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 0
        assert record.status == "pending"
        payload = json.loads(record.raw_json)
        assert payload["ambiguous_symbol_fallback"]["order_candidates"] == 2


def test_lighter_reconcile_record_expires_after_repeated_sync_errors(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-3")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()
        monkeypatch.setattr("app.services.lighter_reconcile_service.settings.lighter_reconcile_max_sync_errors", 2)

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="9010",
            symbol="SOL-USDC",
            raw_payload={"tx_hash": "0xerr"},
        )
        db.commit()

        service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error="network timeout",
        )
        db.commit()

        first = db.query(LighterReconcileRecord).first()
        assert first is not None
        assert first.status == "pending"
        first_payload = json.loads(first.raw_json)
        assert first_payload["sync_error_count"] == 1
        assert first_payload["last_sync_error_code"] == "network_error"
        assert first_payload["next_retry_after_seconds"] == 3
        assert "next_retry_at" in first_payload

        # Move retry gate to past so next error attempt is counted.
        first_payload["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        first.raw_json = json.dumps(first_payload, ensure_ascii=False)
        db.add(first)
        db.commit()

        service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error="network timeout",
        )
        db.commit()

        second = db.query(LighterReconcileRecord).first()
        assert second is not None
        assert second.status == "expired"
        payload = json.loads(second.raw_json)
        assert payload["sync_error_count"] == 2
        assert payload["last_sync_error_code"] == "network_error"
        assert payload["expire_reason"] == "sync_error_threshold_reached"
        assert second.resolved_at is not None


def test_lighter_reconcile_record_resolves_with_order_index_candidate():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-4")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="0xtxhash-order-index",
            symbol="ETH-USDC",
            raw_payload={
                "order_id": "0xtxhash-order-index",
                "raw": {
                    "request": {
                        "tx_info": json.dumps({"OrderIndex": "9911", "ClientOrderIndex": "cli-9911"}),
                    }
                },
            },
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[{"order_id": "9911", "client_order_id": "cli-9911", "symbol": "ETH-USDC"}],
            synced_trades=[{"order_id": "9911", "trade_id": "t-9911", "symbol": "ETH-USDC"}],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 1
        assert record.status == "reconciled"
        assert record.resolved_order_id == "9911"
        assert record.resolved_trade_id == "t-9911"

        payload = json.loads(record.raw_json)
        assert payload["resolved_match_by"] == "order_id_candidate"
        assert payload["resolved_match_value"] == "9911"
        assert "9911" in payload["match_candidates"]["order_ids"]


def test_lighter_reconcile_record_resolves_with_client_order_id_candidate():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-5")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="0xtxhash-client",
            symbol="BTC-USDC",
            raw_payload={
                "order_id": "0xtxhash-client",
                "client_order_id": "cli-55",
            },
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[{"order_id": "5500", "client_order_id": "cli-55", "symbol": "BTC-USDC"}],
            synced_trades=[{"order_id": "5500", "trade_id": "t-5500", "symbol": "BTC-USDC"}],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 1
        assert record.status == "reconciled"
        assert record.resolved_order_id == "5500"
        assert record.resolved_trade_id == "t-5500"

        payload = json.loads(record.raw_json)
        assert payload["resolved_match_by"] == "client_order_id_candidate"
        assert payload["resolved_match_value"] == "cli-55"


def test_lighter_reconcile_record_uses_most_recent_trade_for_resolved_trade_id():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-6")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="7001",
            symbol="ETH-USDC",
            raw_payload={"order_id": "7001"},
        )
        db.commit()

        resolved = service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[{"order_id": "7001", "symbol": "ETH-USDC"}],
            synced_trades=[
                {"order_id": "7001", "trade_id": "10", "symbol": "ETH-USDC", "timestamp": 1710000000000},
                {"order_id": "7001", "trade_id": "11", "symbol": "ETH-USDC", "timestamp": 1710000001000},
            ],
        )
        db.commit()

        record = db.query(LighterReconcileRecord).first()
        assert record is not None
        assert resolved == 1
        assert record.status == "reconciled"
        assert record.resolved_trade_id == "11"


def test_lighter_reconcile_failure_code_stats_counts_pending_errors():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-7")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()
        rows = [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="err-1",
                symbol="BTC-USDC",
                status="pending",
                raw_json='{"last_sync_error_code":"network_error"}',
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="err-2",
                symbol="ETH-USDC",
                status="pending",
                raw_json='{"last_sync_error_code":"rate_limited"}',
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="err-3",
                symbol="SOL-USDC",
                status="pending",
                raw_json='{"last_sync_error_code":"network_error"}',
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="done-1",
                symbol="SOL-USDC",
                status="reconciled",
                raw_json='{"last_sync_error_code":"auth_failed"}',
            ),
        ]
        db.add_all(rows)
        db.commit()

        stats = service.failure_code_stats(db, user_id=user.id, account_id=account.id)
        assert stats == {"network_error": 2, "rate_limited": 1}


def test_lighter_reconcile_retry_window_stats_tracks_due_and_blocked():
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-8")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        rows = [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="due-1",
                symbol="BTC-USDC",
                status="pending",
                raw_json=json.dumps({"next_retry_at": (now - timedelta(seconds=5)).isoformat()}),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="blocked-1",
                symbol="ETH-USDC",
                status="pending",
                raw_json=json.dumps({"next_retry_at": (now + timedelta(seconds=120)).isoformat()}),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="none-1",
                symbol="SOL-USDC",
                status="pending",
                raw_json="{}",
            ),
        ]
        db.add_all(rows)
        db.commit()

        stats = service.retry_window_stats(db, user_id=user.id, account_id=account.id)
        assert stats["retry_due"] == 1
        assert stats["retry_blocked"] == 1
        assert stats["no_retry_hint"] == 1
        assert isinstance(stats["next_retry_at"], datetime)


def test_lighter_reconcile_sync_error_respects_backoff_window(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-9")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()
        monkeypatch.setattr("app.services.lighter_reconcile_service.settings.lighter_reconcile_max_sync_errors", 5)

        service.create_pending_record(
            db,
            user_id=user.id,
            account=account,
            operation="submit",
            request_order_id="retry-window-1",
            symbol="BTC-USDC",
            raw_payload={"tx_hash": "0xretry"},
        )
        db.commit()

        service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error="network timeout",
        )
        db.commit()
        row = db.query(LighterReconcileRecord).first()
        assert row is not None
        payload = json.loads(row.raw_json)
        assert payload["sync_error_count"] == 1
        assert payload["next_retry_after_seconds"] == 3

        # Immediate second error stays inside cooldown window, so counter should not increase.
        service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error="network timeout",
        )
        db.commit()
        row = db.query(LighterReconcileRecord).first()
        assert row is not None
        payload = json.loads(row.raw_json)
        assert payload["sync_error_count"] == 1
        assert payload["last_sync_skipped_due_to_backoff"] is True

        # Once cooldown expires, next error increments again.
        payload["next_retry_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        row.raw_json = json.dumps(payload, ensure_ascii=False)
        db.add(row)
        db.commit()

        service.mark_sync_result(
            db,
            user_id=user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error="network timeout",
        )
        db.commit()
        row = db.query(LighterReconcileRecord).first()
        assert row is not None
        payload = json.loads(row.raw_json)
        assert payload["sync_error_count"] == 2
        assert "last_sync_skipped_due_to_backoff" not in payload


def test_lighter_reconcile_prunes_aged_expired_records(monkeypatch):
    with _build_session() as db:
        user = _create_user(db, "lighter-reconcile-10")
        account = _create_lighter_account(db, user.id)
        service = LighterReconcileService()
        monkeypatch.setattr(
            "app.services.lighter_reconcile_service.settings.lighter_reconcile_expired_retention_seconds",
            3600,
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="expired-old-1",
                symbol="BTC-USDC",
                status="expired",
                raw_json='{"expire_reason":"sync_error_threshold_reached"}',
                created_at=now - timedelta(hours=10),
                updated_at=now - timedelta(hours=2),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=account.id,
                operation="submit",
                request_order_id="expired-new-1",
                symbol="ETH-USDC",
                status="expired",
                raw_json='{"expire_reason":"pending_ttl_reached"}',
                created_at=now - timedelta(minutes=30),
                updated_at=now - timedelta(minutes=10),
            ),
        ]
        db.add_all(rows)
        db.commit()

        pruned = service.prune_expired_records(db, user_id=user.id, account_id=account.id)
        db.commit()

        assert pruned == 1
        remaining_ids = {
            row.request_order_id
            for row in db.query(LighterReconcileRecord).all()
        }
        assert remaining_ids == {"expired-new-1"}


def test_classify_sync_error_normalizes_common_buckets():
    assert _classify_sync_error("HTTP 429 Too many requests") == "rate_limited"
    assert _classify_sync_error("401 unauthorized signature invalid") == "auth_failed"
    assert _classify_sync_error("503 Service Unavailable") == "upstream_unavailable"
    assert _classify_sync_error("network timeout while syncing") == "network_error"
    assert _classify_sync_error("invalid payload format") == "invalid_payload"
