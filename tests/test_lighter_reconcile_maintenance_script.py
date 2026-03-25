from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, Base, ExchangeAccount, LighterReconcileRecord, User


def _build_engine(db_url: str):
    return create_engine(db_url, future=True, connect_args={"check_same_thread": False})


def _seed_records(db: Session) -> tuple[User, ExchangeAccount]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = User(
        username="maint-user",
        email="maint-user@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    lighter_account = ExchangeAccount(
        user_id=user.id,
        exchange="lighter",
        account_alias="lighter-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    binance_account = ExchangeAccount(
        user_id=user.id,
        exchange="binance",
        account_alias="binance-main",
        api_key_encrypted="enc-key",
        api_secret_encrypted="enc-secret",
        passphrase_encrypted=None,
        is_testnet=True,
    )
    db.add_all([lighter_account, binance_account])
    db.commit()
    db.refresh(lighter_account)
    db.refresh(binance_account)

    db.add_all(
        [
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=lighter_account.id,
                operation="submit",
                request_order_id="pending-old-1",
                symbol="BTC-USDC",
                status="pending",
                raw_json="{}",
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=lighter_account.id,
                operation="submit",
                request_order_id="pending-new-1",
                symbol="ETH-USDC",
                status="pending",
                raw_json="{}",
                created_at=now - timedelta(seconds=30),
                updated_at=now - timedelta(seconds=30),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=lighter_account.id,
                operation="submit",
                request_order_id="expired-old-1",
                symbol="SOL-USDC",
                status="expired",
                raw_json='{"expire_reason":"sync_error_threshold_reached"}',
                created_at=now - timedelta(hours=5),
                updated_at=now - timedelta(hours=5),
            ),
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=lighter_account.id,
                operation="submit",
                request_order_id="expired-new-1",
                symbol="XRP-USDC",
                status="expired",
                raw_json='{"expire_reason":"pending_ttl_reached"}',
                created_at=now - timedelta(seconds=30),
                updated_at=now - timedelta(seconds=30),
            ),
            # Non-lighter account rows must not be touched by maintenance scan.
            LighterReconcileRecord(
                user_id=user.id,
                exchange_account_id=binance_account.id,
                operation="submit",
                request_order_id="binance-foreign-1",
                symbol="BTCUSDT",
                status="pending",
                raw_json="{}",
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=4),
            ),
        ]
    )
    db.commit()
    return user, lighter_account


def _run_script(*, db_url: str, dry_run: bool = False) -> dict:
    command = [
        sys.executable,
        "deploy/lighter_reconcile_maintenance.py",
        "--database-url",
        db_url,
        "--include-unchanged",
    ]
    if dry_run:
        command.append("--dry-run")

    env = {
        **os.environ,
        # Use short windows so the seeded records deterministically hit maintenance logic.
        "LIGHTER_RECONCILE_PENDING_TTL_SECONDS": "60",
        "LIGHTER_RECONCILE_EXPIRED_RETENTION_SECONDS": "120",
    }
    result = subprocess.run(command, check=True, capture_output=True, text=True, env=env)
    return json.loads(result.stdout.strip())


def test_lighter_reconcile_maintenance_script_updates_and_audits(tmp_path):
    db_file = Path(tmp_path) / "lighter_maintenance.db"
    db_url = f"sqlite:///{db_file}"
    engine = _build_engine(db_url)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        _seed_records(db)

    payload = _run_script(db_url=db_url, dry_run=False)
    assert payload["dry_run"] is False
    assert payload["scanned_accounts"] == 1
    assert payload["changed_accounts"] == 1
    assert payload["expired_now_total"] == 1
    assert payload["pruned_now_total"] == 1
    assert payload["pending_before_total"] == 2
    assert payload["pending_after_total"] == 1
    assert payload["results"]
    assert payload["results"][0]["account_id"] > 0

    with Session(engine) as db:
        row_old_pending = (
            db.query(LighterReconcileRecord)
            .filter(LighterReconcileRecord.request_order_id == "pending-old-1")
            .first()
        )
        assert row_old_pending is not None
        assert row_old_pending.status == "expired"

        row_old_expired = (
            db.query(LighterReconcileRecord)
            .filter(LighterReconcileRecord.request_order_id == "expired-old-1")
            .first()
        )
        assert row_old_expired is None

        row_foreign = (
            db.query(LighterReconcileRecord)
            .filter(LighterReconcileRecord.request_order_id == "binance-foreign-1")
            .first()
        )
        assert row_foreign is not None
        assert row_foreign.status == "pending"

        audit_rows = db.query(AuditEvent).filter(AuditEvent.action == "lighter_reconcile_maintenance").all()
        assert len(audit_rows) == 1
        details = json.loads(audit_rows[0].details_json)
        assert details["expired_now"] == 1
        assert details["pruned_now"] == 1


def test_lighter_reconcile_maintenance_script_dry_run_keeps_database_unchanged(tmp_path):
    db_file = Path(tmp_path) / "lighter_maintenance_dry.db"
    db_url = f"sqlite:///{db_file}"
    engine = _build_engine(db_url)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        _seed_records(db)

    payload = _run_script(db_url=db_url, dry_run=True)
    assert payload["dry_run"] is True
    assert payload["expired_now_total"] == 1
    assert payload["pruned_now_total"] == 1

    with Session(engine) as db:
        # Dry-run should not persist status transitions or pruning.
        status_map = {
            row.request_order_id: row.status
            for row in db.query(LighterReconcileRecord).all()
        }
        assert status_map["pending-old-1"] == "pending"
        assert "expired-old-1" in status_map
        audit_count = db.query(AuditEvent).filter(AuditEvent.action == "lighter_reconcile_maintenance").count()
        assert audit_count == 0
