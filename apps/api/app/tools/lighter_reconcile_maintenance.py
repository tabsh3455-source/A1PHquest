"""Periodic maintenance job for Lighter reconcile backlog.

This module is designed for VPS cron/systemd or `docker compose exec` usage.
It performs two actions for each Lighter exchange account:
1) prune aged `expired` records beyond retention window
2) expire stale `pending` records that exceeded TTL

A compact JSON summary is printed to stdout for ops collection.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AuditEvent, ExchangeAccount
from app.services.lighter_reconcile_service import LighterReconcileService

MAINTENANCE_ACTION = "lighter_reconcile_maintenance"


@dataclass(slots=True)
class AccountMaintenanceResult:
    user_id: int
    account_id: int
    expired_now: int
    pruned_now: int
    status_before: dict[str, int]
    status_after: dict[str, int]
    retry_due_count: int
    retry_blocked_count: int
    no_retry_hint_count: int
    next_retry_at: str | None

    @property
    def changed(self) -> bool:
        return self.expired_now > 0 or self.pruned_now > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "account_id": self.account_id,
            "expired_now": self.expired_now,
            "pruned_now": self.pruned_now,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "retry_due_count": self.retry_due_count,
            "retry_blocked_count": self.retry_blocked_count,
            "no_retry_hint_count": self.no_retry_hint_count,
            "next_retry_at": self.next_retry_at,
            "changed": self.changed,
        }


def _build_engine(database_url: str):
    try:
        if database_url.startswith("sqlite"):
            return create_engine(database_url, future=True, connect_args={"check_same_thread": False})
        return create_engine(database_url, future=True, pool_pre_ping=True)
    except ModuleNotFoundError as exc:
        # Make dependency failure explicit instead of silently falling back to
        # a different database, because this is an ops write path.
        raise SystemExit(
            "Database driver is missing for DATABASE_URL. "
            "Install required driver (for PostgreSQL: psycopg2-binary) "
            "or pass a supported --database-url."
        ) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Lighter reconcile maintenance.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./a1phquest_dev.db"),
        help="SQLAlchemy database URL.",
    )
    parser.add_argument("--user-id", type=int, default=0, help="Optional user scope.")
    parser.add_argument("--account-id", type=int, default=0, help="Optional account scope.")
    parser.add_argument(
        "--max-accounts",
        type=int,
        default=0,
        help="Optional cap for scanned accounts (0 means no limit).",
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="Include unchanged accounts in output result list.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run maintenance without persisting database changes.",
    )
    return parser.parse_args()


def _query_accounts(
    db: Session,
    *,
    user_id: int | None,
    account_id: int | None,
    max_accounts: int | None,
) -> list[ExchangeAccount]:
    query = db.query(ExchangeAccount).filter(ExchangeAccount.exchange == "lighter")
    if user_id:
        query = query.filter(ExchangeAccount.user_id == user_id)
    if account_id:
        query = query.filter(ExchangeAccount.id == account_id)
    query = query.order_by(ExchangeAccount.id.asc())
    if max_accounts and max_accounts > 0:
        query = query.limit(max_accounts)
    return query.all()


def _append_audit_event(
    db: Session,
    *,
    result: AccountMaintenanceResult,
) -> None:
    details = {
        "expired_now": result.expired_now,
        "pruned_now": result.pruned_now,
        "status_before": result.status_before,
        "status_after": result.status_after,
        "retry_due_count": result.retry_due_count,
        "retry_blocked_count": result.retry_blocked_count,
        "no_retry_hint_count": result.no_retry_hint_count,
        "next_retry_at": result.next_retry_at,
    }
    db.add(
        AuditEvent(
            user_id=result.user_id,
            action=MAINTENANCE_ACTION,
            resource="exchange_account",
            resource_id=str(result.account_id),
            details_json=json.dumps(details, ensure_ascii=False),
        )
    )


def _run_job(
    db: Session,
    *,
    service: LighterReconcileService,
    user_id: int | None,
    account_id: int | None,
    max_accounts: int | None,
    include_unchanged: bool,
    dry_run: bool,
) -> dict[str, Any]:
    accounts = _query_accounts(
        db,
        user_id=user_id,
        account_id=account_id,
        max_accounts=max_accounts,
    )
    results: list[AccountMaintenanceResult] = []
    expired_now_total = 0
    pruned_now_total = 0
    changed_accounts = 0
    pending_before_total = 0
    pending_after_total = 0

    for account in accounts:
        status_before = service.status_stats(db, user_id=account.user_id, account_id=account.id)
        # Keep same ordering as API paths: prune aged expired rows first, then expire
        # stale pending rows. This avoids pruning rows that just expired in this run.
        pruned_now = service.prune_expired_records(db, user_id=account.user_id, account_id=account.id)
        expired_now = service.expire_pending_records(db, user_id=account.user_id, account_id=account.id)
        status_after = service.status_stats(db, user_id=account.user_id, account_id=account.id)
        retry_stats = service.retry_window_stats(db, user_id=account.user_id, account_id=account.id)
        next_retry_at = retry_stats.get("next_retry_at")
        if isinstance(next_retry_at, datetime):
            next_retry_at = next_retry_at.isoformat()
        elif next_retry_at is not None:
            next_retry_at = str(next_retry_at)

        row = AccountMaintenanceResult(
            user_id=int(account.user_id),
            account_id=int(account.id),
            expired_now=int(expired_now),
            pruned_now=int(pruned_now),
            status_before=status_before,
            status_after=status_after,
            retry_due_count=int(retry_stats.get("retry_due", 0) or 0),
            retry_blocked_count=int(retry_stats.get("retry_blocked", 0) or 0),
            no_retry_hint_count=int(retry_stats.get("no_retry_hint", 0) or 0),
            next_retry_at=next_retry_at,
        )
        expired_now_total += row.expired_now
        pruned_now_total += row.pruned_now
        pending_before_total += int(row.status_before.get("pending", 0) or 0)
        pending_after_total += int(row.status_after.get("pending", 0) or 0)
        if row.changed:
            changed_accounts += 1
            if not dry_run:
                _append_audit_event(db, result=row)
        if include_unchanged or row.changed:
            results.append(row)

    if dry_run:
        # Keep calculated summary but discard data mutations/audit writes.
        db.rollback()
    else:
        db.commit()

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "scanned_accounts": len(accounts),
        "changed_accounts": changed_accounts,
        "expired_now_total": expired_now_total,
        "pruned_now_total": pruned_now_total,
        "pending_before_total": pending_before_total,
        "pending_after_total": pending_after_total,
        "filters": {
            "user_id": user_id,
            "account_id": account_id,
            "max_accounts": max_accounts,
            "include_unchanged": include_unchanged,
        },
        "results": [row.to_dict() for row in results],
    }


def main() -> int:
    args = _parse_args()
    user_id = args.user_id if args.user_id > 0 else None
    account_id = args.account_id if args.account_id > 0 else None
    max_accounts = args.max_accounts if args.max_accounts > 0 else None

    engine = _build_engine(args.database_url)
    with Session(engine) as db:
        payload = _run_job(
            db,
            service=LighterReconcileService(),
            user_id=user_id,
            account_id=account_id,
            max_accounts=max_accounts,
            include_unchanged=bool(args.include_unchanged),
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
