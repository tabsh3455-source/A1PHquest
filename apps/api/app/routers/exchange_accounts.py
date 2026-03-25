from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..deps import get_current_verified_user, require_step_up_user
from ..events import build_ws_event
from ..kms import build_kms_provider
from ..models import (
    AccountBalanceSnapshot,
    ExchangeAccount,
    LighterReconcileRecord,
    OrderSnapshot,
    PositionSnapshot,
    Strategy,
    TradeFillSnapshot,
    User,
)
from ..schemas import (
    AccountBalanceSnapshotResponse,
    ExchangeConsistencyResponse,
    ExchangeAccountCreateRequest,
    ExchangeAccountResponse,
    ExchangeAccountSyncResponse,
    ExchangeAccountValidateResponse,
    LighterReconcilePendingResponse,
    LighterReconcileRecordResponse,
    LighterReconcileRetryResponse,
    OrderSnapshotResponse,
    PositionSnapshotResponse,
    TradeFillSnapshotResponse,
)
from ..services.gateway_service import GatewayService
from ..services.lighter_reconcile_service import LighterReconcileService, _classify_sync_error
from ..services.trade_fill_service import TradeFillService
from ..tenant import with_tenant

router = APIRouter(prefix="/api/exchange-accounts", tags=["exchange-accounts"])
kms = build_kms_provider()
gateway_service = GatewayService()
trade_fill_service = TradeFillService()
lighter_reconcile_service = LighterReconcileService()


@router.post("", response_model=ExchangeAccountResponse, status_code=status.HTTP_201_CREATED)
def create_exchange_account(
    payload: ExchangeAccountCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    if payload.exchange == "okx" and not payload.passphrase:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OKX account requires passphrase.",
        )

    account = ExchangeAccount(
        user_id=current_user.id,
        exchange=payload.exchange,
        account_alias=payload.account_alias,
        api_key_encrypted=kms.encrypt(payload.api_key),
        api_secret_encrypted=kms.encrypt(payload.api_secret),
        passphrase_encrypted=kms.encrypt(payload.passphrase) if payload.passphrase else None,
        is_testnet=payload.is_testnet,
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="exchange_account_create",
        resource="exchange_account",
        resource_id=str(account.id),
        details={"exchange": account.exchange, "is_testnet": account.is_testnet},
    )
    return account


@router.get("", response_model=list[ExchangeAccountResponse])
def list_exchange_accounts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    return with_tenant(db.query(ExchangeAccount), ExchangeAccount, current_user.id).order_by(
        ExchangeAccount.id.desc()
    ).all()


@router.post("/{account_id}/validate", response_model=ExchangeAccountValidateResponse)
def validate_exchange_account(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    account = _get_owned_account(db, current_user.id, account_id)

    api_key = kms.decrypt(account.api_key_encrypted)
    api_secret = kms.decrypt(account.api_secret_encrypted)
    passphrase = kms.decrypt(account.passphrase_encrypted) if account.passphrase_encrypted else None
    result = gateway_service.validate_account(account, api_key, api_secret, passphrase)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="exchange_account_validate",
        resource="exchange_account",
        resource_id=str(account.id),
        details={"validated": result.validated, "message": result.message},
    )
    return ExchangeAccountValidateResponse(
        account_id=account.id,
        exchange=account.exchange,
        validated=result.validated,
        message=result.message,
    )


@router.post("/{account_id}/sync", response_model=ExchangeAccountSyncResponse)
async def sync_exchange_account(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    account = _get_owned_account(db, current_user.id, account_id)
    api_key = kms.decrypt(account.api_key_encrypted)
    api_secret = kms.decrypt(account.api_secret_encrypted)
    passphrase = kms.decrypt(account.passphrase_encrypted) if account.passphrase_encrypted else None

    trade_cursors = _build_trade_cursors(db, user_id=current_user.id, account_id=account.id)
    result = gateway_service.fetch_account_state(
        account,
        api_key,
        api_secret,
        passphrase,
        trade_cursors=trade_cursors,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)

    balances_synced = _upsert_balances(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=result.balances,
    )
    positions_synced = _upsert_positions(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=result.positions,
    )
    orders_synced = _upsert_orders(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=result.orders,
    )
    trades_synced, upserted_trades = trade_fill_service.upsert_fills(
        db,
        user_id=current_user.id,
        account=account,
        rows=result.trades,
    )
    db.commit()
    for fill in upserted_trades:
        db.refresh(fill)

    synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log_audit_event(
        db,
        user_id=current_user.id,
        action="exchange_account_sync",
        resource="exchange_account",
        resource_id=str(account.id),
        details={
            "balances_synced": balances_synced,
            "positions_synced": positions_synced,
            "orders_synced": orders_synced,
            "trades_synced": trades_synced,
            "trade_cursor_symbols": len(trade_cursors.get("symbols", {})),
            "message": result.message,
        },
    )

    await request.app.state.ws_manager.push_to_user(
        current_user.id,
        build_ws_event(
            event_type="exchange_sync",
            resource_id=str(account.id),
            payload={
                "account_id": account.id,
                "exchange": account.exchange,
                "balances_synced": balances_synced,
                "positions_synced": positions_synced,
                "orders_synced": orders_synced,
                "trades_synced": trades_synced,
                "synced_at": synced_at.isoformat(),
                "message": result.message,
            },
        ),
    )
    if upserted_trades:
        await request.app.state.ws_manager.push_to_user(
            current_user.id,
            build_ws_event(
                event_type="trade_fills_synced",
                resource_id=str(account.id),
                payload={
                    "account_id": account.id,
                    "exchange": account.exchange,
                    "count": trades_synced,
                    "trades": [trade_fill_service.to_event(fill) for fill in upserted_trades[:200]],
                },
            ),
        )

    return ExchangeAccountSyncResponse(
        account_id=account.id,
        exchange=account.exchange,
        balances_synced=balances_synced,
        positions_synced=positions_synced,
        orders_synced=orders_synced,
        trades_synced=trades_synced,
        message=result.message,
        synced_at=synced_at,
    )


@router.get("/{account_id}/balances", response_model=list[AccountBalanceSnapshotResponse])
def list_balances(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    _get_owned_account(db, current_user.id, account_id)
    return with_tenant(db.query(AccountBalanceSnapshot), AccountBalanceSnapshot, current_user.id).filter(
        AccountBalanceSnapshot.exchange_account_id == account_id
    ).order_by(AccountBalanceSnapshot.asset.asc()).all()


@router.get("/{account_id}/positions", response_model=list[PositionSnapshotResponse])
def list_positions(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    _get_owned_account(db, current_user.id, account_id)
    return with_tenant(db.query(PositionSnapshot), PositionSnapshot, current_user.id).filter(
        PositionSnapshot.exchange_account_id == account_id
    ).order_by(PositionSnapshot.updated_at.desc()).all()


@router.get("/{account_id}/orders", response_model=list[OrderSnapshotResponse])
def list_orders(
    account_id: int,
    limit: int = Query(default=200, ge=1, le=2000),
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    _get_owned_account(db, current_user.id, account_id)
    query = with_tenant(db.query(OrderSnapshot), OrderSnapshot, current_user.id).filter(
        OrderSnapshot.exchange_account_id == account_id
    )
    if status_filter:
        query = query.filter(OrderSnapshot.status == status_filter)
    return query.order_by(OrderSnapshot.updated_at.desc()).limit(limit).all()


@router.get("/{account_id}/trades", response_model=list[TradeFillSnapshotResponse])
def list_trades(
    account_id: int,
    limit: int = Query(default=200, ge=1, le=2000),
    symbol: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    _get_owned_account(db, current_user.id, account_id)
    query = with_tenant(db.query(TradeFillSnapshot), TradeFillSnapshot, current_user.id).filter(
        TradeFillSnapshot.exchange_account_id == account_id
    )
    if symbol:
        query = query.filter(TradeFillSnapshot.symbol == symbol.upper())
    return query.order_by(TradeFillSnapshot.trade_time.desc()).limit(limit).all()


@router.get("/{account_id}/consistency", response_model=ExchangeConsistencyResponse)
def check_exchange_consistency(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    _get_owned_account(db, current_user.id, account_id)
    summary = _build_order_trade_consistency(db, user_id=current_user.id, account_id=account_id)
    return ExchangeConsistencyResponse(
        account_id=account_id,
        checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
        consistent=summary["consistent"],
        total_orders=summary["total_orders"],
        total_trades=summary["total_trades"],
        trades_without_order_count=len(summary["trades_without_order"]),
        orders_with_fill_but_no_trade_count=len(summary["orders_with_fill_but_no_trade"]),
        trades_without_order_samples=summary["trades_without_order"][:100],
        orders_with_fill_but_no_trade_samples=summary["orders_with_fill_but_no_trade"][:100],
    )


@router.get("/{account_id}/lighter-reconcile/pending", response_model=LighterReconcilePendingResponse)
def list_lighter_reconcile_pending(
    account_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    account = _get_owned_account(db, current_user.id, account_id)
    if account.exchange.lower() != "lighter":
        raise HTTPException(status_code=400, detail="Endpoint only supports lighter accounts")

    expired_pruned_now = lighter_reconcile_service.prune_expired_records(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    expired_now = lighter_reconcile_service.expire_pending_records(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    if expired_now or expired_pruned_now:
        db.commit()

    records = lighter_reconcile_service.list_pending(
        db,
        user_id=current_user.id,
        account_id=account.id,
        limit=limit,
    )
    stats = lighter_reconcile_service.status_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    pending_oldest_age_seconds = lighter_reconcile_service.oldest_pending_age_seconds(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    recent_failure_reasons = lighter_reconcile_service.recent_failure_reasons(
        db,
        user_id=current_user.id,
        account_id=account.id,
        limit=20,
    )
    failure_code_stats = lighter_reconcile_service.failure_code_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    retry_window_stats = lighter_reconcile_service.retry_window_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    payload = [_to_lighter_record_response(item) for item in records]
    return LighterReconcilePendingResponse(
        account_id=account.id,
        expired_now=expired_now,
        expired_pruned_now=expired_pruned_now,
        status_stats=stats,
        pending_oldest_age_seconds=pending_oldest_age_seconds,
        recent_failure_reasons=recent_failure_reasons,
        failure_code_stats=failure_code_stats,
        retry_due_count=int(retry_window_stats.get("retry_due", 0) or 0),
        retry_blocked_count=int(retry_window_stats.get("retry_blocked", 0) or 0),
        no_retry_hint_count=int(retry_window_stats.get("no_retry_hint", 0) or 0),
        next_retry_at=retry_window_stats.get("next_retry_at"),
        records=payload,
    )


@router.post("/{account_id}/lighter-reconcile/retry-sync", response_model=LighterReconcileRetryResponse)
def retry_lighter_reconcile_sync(
    account_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    """
    Trigger best-effort reconcile sync for pending Lighter records.

    Behavior:
    - applies TTL expiration before retry pass
    - skips gateway sync when all pending records are still under backoff
    - updates snapshots + reconcile status when sync succeeds
    """
    account = _get_owned_account(db, current_user.id, account_id)
    if account.exchange.lower() != "lighter":
        raise HTTPException(status_code=400, detail="Endpoint only supports lighter accounts")

    expired_pruned_now = lighter_reconcile_service.prune_expired_records(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    expired_now = lighter_reconcile_service.expire_pending_records(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    status_before = lighter_reconcile_service.status_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    retry_before = lighter_reconcile_service.retry_window_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    pending_before = int(status_before.get("pending", 0) or 0)
    retry_due_before = int(retry_before.get("retry_due", 0) or 0)
    retry_blocked_before = int(retry_before.get("retry_blocked", 0) or 0)
    no_retry_hint_before = int(retry_before.get("no_retry_hint", 0) or 0)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if pending_before == 0:
        log_audit_event(
            db,
            user_id=current_user.id,
            action="lighter_reconcile_retry_sync",
            resource="exchange_account",
            resource_id=str(account.id),
            details={
                "success": True,
                "message": "no pending reconcile records",
                "pending_before": 0,
                "pending_after": 0,
                "reconciled_now": 0,
                "expired_now": expired_now,
                "expired_pruned_now": expired_pruned_now,
            },
        )
        db.commit()
        return LighterReconcileRetryResponse(
            account_id=account.id,
            success=True,
            message="no pending reconcile records",
            pending_before=0,
            pending_after=0,
            reconciled_now=0,
            expired_now=expired_now,
            expired_pruned_now=expired_pruned_now,
            retry_due_before=retry_due_before,
            retry_blocked_before=retry_blocked_before,
            no_retry_hint_before=no_retry_hint_before,
            synced_at=now,
        )

    if retry_due_before == 0 and no_retry_hint_before == 0:
        log_audit_event(
            db,
            user_id=current_user.id,
            action="lighter_reconcile_retry_sync",
            resource="exchange_account",
            resource_id=str(account.id),
            details={
                "success": True,
                "message": "all pending records are still inside retry backoff window",
                "pending_before": pending_before,
                "pending_after": pending_before,
                "reconciled_now": 0,
                "expired_now": expired_now,
                "expired_pruned_now": expired_pruned_now,
                "retry_due_before": retry_due_before,
                "retry_blocked_before": retry_blocked_before,
                "no_retry_hint_before": no_retry_hint_before,
            },
        )
        db.commit()
        return LighterReconcileRetryResponse(
            account_id=account.id,
            success=True,
            message="all pending records are still inside retry backoff window",
            pending_before=pending_before,
            pending_after=pending_before,
            reconciled_now=0,
            expired_now=expired_now,
            expired_pruned_now=expired_pruned_now,
            retry_due_before=retry_due_before,
            retry_blocked_before=retry_blocked_before,
            no_retry_hint_before=no_retry_hint_before,
            synced_at=now,
        )

    api_key = kms.decrypt(account.api_key_encrypted)
    api_secret = kms.decrypt(account.api_secret_encrypted)
    passphrase = kms.decrypt(account.passphrase_encrypted) if account.passphrase_encrypted else None

    trade_cursors = _build_trade_cursors(db, user_id=current_user.id, account_id=account.id)
    sync_result = gateway_service.fetch_account_state(
        account,
        api_key,
        api_secret,
        passphrase,
        trade_cursors=trade_cursors,
    )
    if not sync_result.success:
        lighter_reconcile_service.mark_sync_result(
            db,
            user_id=current_user.id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error=sync_result.message,
        )
        expired_more = lighter_reconcile_service.expire_pending_records(
            db,
            user_id=current_user.id,
            account_id=account.id,
        )
        db.commit()
        status_after = lighter_reconcile_service.status_stats(
            db,
            user_id=current_user.id,
            account_id=account.id,
        )
        log_audit_event(
            db,
            user_id=current_user.id,
            action="lighter_reconcile_retry_sync",
            resource="exchange_account",
            resource_id=str(account.id),
            details={
                "success": False,
                "message": sync_result.message,
                "pending_before": pending_before,
                "pending_after": int(status_after.get("pending", 0) or 0),
                "reconciled_now": 0,
                "expired_now": expired_now + expired_more,
                "expired_pruned_now": expired_pruned_now,
                "retry_due_before": retry_due_before,
                "retry_blocked_before": retry_blocked_before,
                "no_retry_hint_before": no_retry_hint_before,
            },
        )
        db.commit()
        return LighterReconcileRetryResponse(
            account_id=account.id,
            success=False,
            message=f"reconcile sync failed: {sync_result.message}",
            pending_before=pending_before,
            pending_after=int(status_after.get("pending", 0) or 0),
            reconciled_now=0,
            expired_now=expired_now + expired_more,
            expired_pruned_now=expired_pruned_now,
            retry_due_before=retry_due_before,
            retry_blocked_before=retry_blocked_before,
            no_retry_hint_before=no_retry_hint_before,
            synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

    balances_synced = _upsert_balances(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=sync_result.balances,
    )
    positions_synced = _upsert_positions(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=sync_result.positions,
    )
    orders_synced = _upsert_orders(
        db=db,
        user_id=current_user.id,
        account=account,
        rows=sync_result.orders,
    )
    trades_synced, _ = trade_fill_service.upsert_fills(
        db,
        user_id=current_user.id,
        account=account,
        rows=sync_result.trades,
    )
    reconciled_now = lighter_reconcile_service.mark_sync_result(
        db,
        user_id=current_user.id,
        account=account,
        synced_orders=sync_result.orders,
        synced_trades=sync_result.trades,
    )
    expired_more = lighter_reconcile_service.expire_pending_records(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    db.commit()

    status_after = lighter_reconcile_service.status_stats(
        db,
        user_id=current_user.id,
        account_id=account.id,
    )
    log_audit_event(
        db,
        user_id=current_user.id,
        action="lighter_reconcile_retry_sync",
        resource="exchange_account",
        resource_id=str(account.id),
        details={
            "success": True,
            "message": sync_result.message,
            "pending_before": pending_before,
            "pending_after": int(status_after.get("pending", 0) or 0),
            "reconciled_now": reconciled_now,
            "expired_now": expired_now + expired_more,
            "expired_pruned_now": expired_pruned_now,
            "retry_due_before": retry_due_before,
            "retry_blocked_before": retry_blocked_before,
            "no_retry_hint_before": no_retry_hint_before,
            "balances_synced": balances_synced,
            "positions_synced": positions_synced,
            "orders_synced": orders_synced,
            "trades_synced": trades_synced,
        },
    )
    db.commit()
    return LighterReconcileRetryResponse(
        account_id=account.id,
        success=True,
        message=sync_result.message,
        pending_before=pending_before,
        pending_after=int(status_after.get("pending", 0) or 0),
        reconciled_now=reconciled_now,
        expired_now=expired_now + expired_more,
        expired_pruned_now=expired_pruned_now,
        retry_due_before=retry_due_before,
        retry_blocked_before=retry_blocked_before,
        no_retry_hint_before=no_retry_hint_before,
        balances_synced=balances_synced,
        positions_synced=positions_synced,
        orders_synced=orders_synced,
        trades_synced=trades_synced,
        synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _get_owned_account(db: Session, user_id: int, account_id: int) -> ExchangeAccount:
    account = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id).filter(
        ExchangeAccount.id == account_id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _upsert_balances(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    rows: list[dict],
) -> int:
    existing = with_tenant(db.query(AccountBalanceSnapshot), AccountBalanceSnapshot, user_id).filter(
        AccountBalanceSnapshot.exchange_account_id == account.id
    ).all()
    existing_map = {item.asset: item for item in existing}
    seen: set[str] = set()

    for row in rows:
        asset = str(row.get("asset", "")).upper()
        if not asset:
            continue
        seen.add(asset)
        record = existing_map.get(asset)
        if not record:
            record = AccountBalanceSnapshot(
                user_id=user_id,
                exchange_account_id=account.id,
                exchange=account.exchange,
                asset=asset,
            )
        record.free = float(row.get("free", 0))
        record.locked = float(row.get("locked", 0))
        record.total = float(row.get("total", 0))
        db.add(record)

    for asset, record in existing_map.items():
        if asset not in seen:
            db.delete(record)
    return len(seen)


def _upsert_positions(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    rows: list[dict],
) -> int:
    existing = with_tenant(db.query(PositionSnapshot), PositionSnapshot, user_id).filter(
        PositionSnapshot.exchange_account_id == account.id
    ).all()
    existing_map = {(item.symbol, item.side): item for item in existing}
    seen: set[tuple[str, str]] = set()

    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        side = str(row.get("side", "")).upper()
        if not symbol or not side:
            continue
        key = (symbol, side)
        seen.add(key)
        record = existing_map.get(key)
        if not record:
            record = PositionSnapshot(
                user_id=user_id,
                exchange_account_id=account.id,
                exchange=account.exchange,
                symbol=symbol,
                side=side,
            )
        record.quantity = float(row.get("quantity", 0))
        record.entry_price = float(row.get("entry_price", 0))
        record.mark_price = _nullable_float(row.get("mark_price"))
        record.unrealized_pnl = _nullable_float(row.get("unrealized_pnl"))
        db.add(record)

    for key, record in existing_map.items():
        if key not in seen:
            db.delete(record)
    return len(seen)


def _upsert_orders(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    rows: list[dict],
) -> int:
    existing = with_tenant(db.query(OrderSnapshot), OrderSnapshot, user_id).filter(
        OrderSnapshot.exchange_account_id == account.id
    ).all()
    existing_map = {item.order_id: item for item in existing}
    seen: set[str] = set()

    for row in rows:
        order_id = str(row.get("order_id", ""))
        if not order_id:
            continue
        seen.add(order_id)
        record = existing_map.get(order_id)
        if not record:
            record = OrderSnapshot(
                user_id=user_id,
                exchange_account_id=account.id,
                exchange=account.exchange,
                order_id=order_id,
                symbol=str(row.get("symbol", "")).upper(),
                status=str(row.get("status", "")),
                side=str(row.get("side", "")),
                order_type=str(row.get("order_type", "")),
            )
        record.symbol = str(row.get("symbol", "")).upper()
        record.client_order_id = row.get("client_order_id")
        record.status = str(row.get("status", ""))
        record.side = str(row.get("side", ""))
        record.order_type = str(row.get("order_type", ""))
        record.price = float(row.get("price", 0))
        record.quantity = float(row.get("quantity", 0))
        record.filled_quantity = float(row.get("filled_quantity", 0))
        record.avg_fill_price = _nullable_float(row.get("avg_fill_price"))
        record.raw_json = json.dumps(row.get("raw", {}), ensure_ascii=False)
        db.add(record)

    # Sync sources only return active/pending orders, so deleting unseen rows here
    # would erase terminal order history and break order->trade audit linkage.
    return len(seen)


def _nullable_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _build_trade_cursors(db: Session, *, user_id: int, account_id: int) -> dict[str, Any]:
    """
    Build incremental trade-sync hints from local snapshots.

    - symbols: per-symbol last trade timestamp (ms)
    - global: overall newest trade timestamp (ms)

    Exchange adapters can decide how to apply these hints to their own pagination params.
    """
    symbol_rows = (
        with_tenant(db.query(TradeFillSnapshot.symbol, func.max(TradeFillSnapshot.trade_time)), TradeFillSnapshot, user_id)
        .filter(TradeFillSnapshot.exchange_account_id == account_id)
        .group_by(TradeFillSnapshot.symbol)
        .all()
    )
    symbols: dict[str, dict[str, int]] = {}
    for symbol, last_trade_time in symbol_rows:
        normalized_symbol = str(symbol or "").upper().strip()
        if not normalized_symbol:
            continue
        symbol_cursor: dict[str, int] = {}
        cursor_ms = _to_epoch_ms(last_trade_time)
        if cursor_ms > 0:
            symbol_cursor["last_trade_time_ms"] = cursor_ms
        symbols[normalized_symbol] = symbol_cursor

    # Include known order symbols so we still query fills even if local trade snapshots are empty.
    known_order_symbols = (
        with_tenant(db.query(OrderSnapshot.symbol), OrderSnapshot, user_id)
        .filter(OrderSnapshot.exchange_account_id == account_id)
        .distinct()
        .all()
    )
    for (symbol,) in known_order_symbols:
        normalized_symbol = str(symbol or "").upper().strip()
        if normalized_symbol:
            symbols.setdefault(normalized_symbol, {})

    # Strategy configuration is another durable symbol hint on first sync. This
    # helps bootstrap trade queries before any local order/trade rows exist.
    strategy_rows = with_tenant(db.query(Strategy.config_json), Strategy, user_id).all()
    for (config_json,) in strategy_rows:
        config = _load_json(config_json)
        try:
            exchange_account_id = int(config.get("exchange_account_id") or 0)
        except (TypeError, ValueError):
            exchange_account_id = 0
        if exchange_account_id != account_id:
            continue
        normalized_symbol = str(config.get("symbol") or "").upper().strip()
        if normalized_symbol:
            symbols.setdefault(normalized_symbol, {})

    global_last_trade_time = (
        with_tenant(db.query(func.max(TradeFillSnapshot.trade_time)), TradeFillSnapshot, user_id)
        .filter(TradeFillSnapshot.exchange_account_id == account_id)
        .scalar()
    )
    global_cursor_ms = _to_epoch_ms(global_last_trade_time)
    global_cursor = {"last_trade_time_ms": global_cursor_ms} if global_cursor_ms > 0 else {}
    return {"symbols": symbols, "global": global_cursor}


def _to_epoch_ms(value: object) -> int:
    if not isinstance(value, datetime):
        return 0
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return int(normalized.timestamp() * 1000)


def _build_order_trade_consistency(db: Session, *, user_id: int, account_id: int) -> dict[str, Any]:
    """
    Compare order snapshots and trade snapshots for obvious linkage gaps.

    Rules:
    - each trade should point to an existing order_id within same account
    - each order with filled_quantity > 0 should have at least one trade row
    """
    order_rows = (
        with_tenant(db.query(OrderSnapshot.order_id, OrderSnapshot.filled_quantity), OrderSnapshot, user_id)
        .filter(OrderSnapshot.exchange_account_id == account_id)
        .all()
    )
    trade_rows = (
        with_tenant(db.query(TradeFillSnapshot.order_id), TradeFillSnapshot, user_id)
        .filter(TradeFillSnapshot.exchange_account_id == account_id)
        .all()
    )

    order_ids = {str(order_id) for order_id, _ in order_rows if str(order_id).strip()}
    trade_order_ids = [str(order_id) for (order_id,) in trade_rows if str(order_id).strip()]
    trade_order_id_set = set(trade_order_ids)

    trades_without_order = sorted({item for item in trade_order_ids if item not in order_ids})
    orders_with_fill_but_no_trade = sorted(
        {
            str(order_id)
            for order_id, filled_quantity in order_rows
            if float(filled_quantity or 0) > 0 and str(order_id) not in trade_order_id_set
        }
    )
    return {
        "consistent": not trades_without_order and not orders_with_fill_but_no_trade,
        "total_orders": len(order_rows),
        "total_trades": len(trade_rows),
        "trades_without_order": trades_without_order,
        "orders_with_fill_but_no_trade": orders_with_fill_but_no_trade,
    }


def _to_lighter_record_response(record: LighterReconcileRecord) -> LighterReconcileRecordResponse:
    raw = _load_json(record.raw_json)
    match_candidates = raw.get("match_candidates") if isinstance(raw.get("match_candidates"), dict) else {}
    order_candidates = match_candidates.get("order_ids") if isinstance(match_candidates.get("order_ids"), list) else []
    client_candidates = (
        match_candidates.get("client_order_ids")
        if isinstance(match_candidates.get("client_order_ids"), list)
        else []
    )
    sync_error = str(raw.get("last_sync_error", "")).strip() or None
    sync_error_code = str(raw.get("last_sync_error_code", "")).strip().lower()
    if not sync_error_code and sync_error:
        sync_error_code = _classify_sync_error(sync_error)
    next_retry_at = _parse_iso_datetime(raw.get("next_retry_at"))
    next_retry_after_raw = raw.get("next_retry_after_seconds")
    try:
        next_retry_after_seconds = int(next_retry_after_raw) if next_retry_after_raw is not None else None
    except (TypeError, ValueError):
        next_retry_after_seconds = None
    return LighterReconcileRecordResponse(
        id=record.id,
        operation=record.operation,
        request_order_id=record.request_order_id,
        symbol=record.symbol,
        status=record.status,
        resolved_order_id=record.resolved_order_id,
        resolved_trade_id=record.resolved_trade_id,
        last_sync_at=record.last_sync_at,
        resolved_at=record.resolved_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_sync_error=sync_error,
        last_sync_error_code=sync_error_code or None,
        sync_error_count=int(raw.get("sync_error_count", 0) or 0),
        next_retry_at=next_retry_at,
        next_retry_after_seconds=next_retry_after_seconds,
        resolved_match_by=str(raw.get("resolved_match_by", "")).strip() or None,
        resolved_match_value=str(raw.get("resolved_match_value", "")).strip() or None,
        candidate_order_ids=[str(value).strip() for value in order_candidates if str(value).strip()][:20],
        candidate_client_order_ids=[str(value).strip() for value in client_candidates if str(value).strip()][:20],
    )


def _load_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=None)
