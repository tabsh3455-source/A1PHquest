from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..audit import log_audit_event
from ..db import get_db
from ..deps import require_step_up_user
from ..events import build_ws_event
from ..kms import build_kms_provider
from ..models import ExchangeAccount, OrderSnapshot, TradeFillSnapshot, User
from ..schemas import OrderCancelRequest, OrderCancelResponse, OrderCreateRequest, OrderSubmitResponse
from ..services.circuit_breaker import CircuitBreakerService
from ..services.gateway_service import GatewayService
from ..services.lighter_reconcile_service import LighterReconcileService
from ..services.notifications import NotificationService
from ..services.risk_service import RiskService
from ..services.trade_fill_service import TradeFillService
from ..tenant import with_tenant

router = APIRouter(prefix="/api/orders", tags=["orders"])
kms = build_kms_provider()
gateway_service = GatewayService()
notification_service = NotificationService()
risk_service = RiskService()
circuit_breaker_service = CircuitBreakerService()
trade_fill_service = TradeFillService()
lighter_reconcile_service = LighterReconcileService()


@router.post("", response_model=OrderSubmitResponse, status_code=status.HTTP_201_CREATED)
async def submit_order(
    payload: OrderCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    account = _get_owned_account(db, current_user.id, payload.account_id)
    payload_validation_error = _lighter_payload_validation_error(
        exchange=account.exchange,
        exchange_payload=payload.exchange_payload,
        operation="submit",
    )
    if payload_validation_error:
        error_code, error_message = payload_validation_error
        log_audit_event(
            db,
            user_id=current_user.id,
            action="order_submit_rejected_validation",
            resource="order",
            details={
                "account_id": account.id,
                "exchange": account.exchange,
                "symbol": payload.symbol,
                "side": payload.side,
                "order_type": payload.order_type,
                "validation_error_code": error_code,
                "message": error_message,
            },
        )
        raise HTTPException(status_code=400, detail=error_message)
    if payload.order_type == "LIMIT" and payload.price is None:
        raise HTTPException(status_code=400, detail="LIMIT order requires price")
    if payload.order_type == "MARKET" and payload.reference_price is None:
        raise HTTPException(
            status_code=400,
            detail="MARKET order requires reference_price for risk notional calculation",
        )

    ref_price = payload.price if payload.price is not None else payload.reference_price or 0.0
    order_notional = payload.quantity * ref_price
    decision = risk_service.evaluate_order(
        db,
        user_id=current_user.id,
        order_notional=order_notional,
        # Daily-loss baseline now comes from server-side fill history to avoid
        # dependency on client-provided projected loss values.
        projected_daily_loss=0.0,
        # Live enforcement must come from server-side account snapshots rather than
        # caller-controlled input. The request field is kept only for backward-
        # compatible dry-run tooling.
        projected_position_ratio=0.0,
        account_id=account.id,
        symbol=payload.symbol,
    )
    if not decision.allowed:
        details: dict[str, Any] = {
            "account_id": payload.account_id,
            "symbol": payload.symbol,
            "side": payload.side,
            "order_type": payload.order_type,
            "order_notional": order_notional,
            "daily_realized_loss": decision.realized_daily_loss,
            "daily_loss_used": decision.evaluated_daily_loss,
        }
        if decision.evaluated_position_ratio is not None:
            details["evaluated_position_ratio"] = decision.evaluated_position_ratio
        if decision.code == "daily_loss_limit_exceeded":
            circuit_result = await _trigger_user_circuit_breaker(
                db=db,
                request=request,
                user=current_user,
                reason=decision.reason,
                source_action="order_submit_rejected_risk",
            )
            details["circuit_breaker_triggered"] = circuit_result.triggered
            details["stopped_strategy_ids"] = circuit_result.stopped_strategy_ids
            if circuit_result.errors:
                details["circuit_breaker_errors"] = circuit_result.errors
        await _emit_risk_blocked(
            db=db,
            request=request,
            user=current_user,
            action="order_submit_rejected_risk",
            reason=decision.reason,
            details=details,
        )
        raise HTTPException(status_code=403, detail=decision.reason)

    api_key = kms.decrypt(account.api_key_encrypted)
    api_secret = kms.decrypt(account.api_secret_encrypted)
    passphrase = kms.decrypt(account.passphrase_encrypted) if account.passphrase_encrypted else None
    result = gateway_service.place_order(
        account,
        api_key,
        api_secret,
        passphrase,
        payload.model_dump(),
    )
    if not result.success or not result.order:
        log_audit_event(
            db,
            user_id=current_user.id,
            action="order_submit_failed",
            resource="order",
            details={
                "account_id": account.id,
                "exchange": account.exchange,
                "symbol": payload.symbol,
                "side": payload.side,
                "order_type": payload.order_type,
                "message": result.message,
            },
        )
        raise HTTPException(status_code=400, detail=result.message)

    snapshot = _upsert_order_snapshot(
        db=db,
        user_id=current_user.id,
        account=account,
        row=result.order,
    )
    trades_synced, upserted_trades = trade_fill_service.upsert_fills(
        db,
        user_id=current_user.id,
        account=account,
        rows=list(result.order.get("trades") or []),
    )
    reconciled_orders_synced = 0
    reconciled_trades_synced = 0
    reconciled_trades: list = []
    if account.exchange.lower() == "lighter":
        _create_lighter_reconcile_record(
            db=db,
            user_id=current_user.id,
            account=account,
            operation="submit",
            request_order_id=snapshot.order_id,
            symbol=snapshot.symbol,
            raw_payload=result.order or {},
        )
        (
            reconciled_orders_synced,
            reconciled_trades_synced,
            reconciled_trades,
        ) = _reconcile_lighter_account_state(
            db=db,
            user_id=current_user.id,
            account=account,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
    db.commit()
    db.refresh(snapshot)
    for fill in upserted_trades:
        db.refresh(fill)
    for fill in reconciled_trades:
        db.refresh(fill)

    total_trade_rows = trades_synced + reconciled_trades_synced

    log_audit_event(
        db,
        user_id=current_user.id,
        action="order_submit",
        resource="order",
        resource_id=snapshot.order_id,
        details={
            "snapshot_id": snapshot.id,
            "account_id": account.id,
            "exchange": account.exchange,
            "symbol": snapshot.symbol,
            "status": snapshot.status,
            "order_notional": order_notional,
            "daily_realized_loss": decision.realized_daily_loss,
            "daily_loss_used": decision.evaluated_daily_loss,
            "evaluated_position_ratio": decision.evaluated_position_ratio,
            "trades_synced": total_trade_rows,
            "reconciled_orders_synced": reconciled_orders_synced,
            "reconciled_trades_synced": reconciled_trades_synced,
            "message": result.message,
        },
    )

    await request.app.state.ws_manager.push_to_user(
        current_user.id,
        build_ws_event(
            event_type="order_submitted",
            resource_id=snapshot.order_id,
            dedupe_key=f"order_submitted:{account.id}:{snapshot.order_id}:{snapshot.status}",
            payload={
                "account_id": account.id,
                "exchange": account.exchange,
                "snapshot_id": snapshot.id,
                "order_id": snapshot.order_id,
                "symbol": snapshot.symbol,
                "status": snapshot.status,
                "trades_synced": total_trade_rows,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "message": result.message,
            },
        ),
    )
    merged_trades = _merge_trade_rows(upserted_trades, reconciled_trades)
    if merged_trades:
        await request.app.state.ws_manager.push_to_user(
            current_user.id,
            build_ws_event(
                event_type="trade_filled",
                resource_id=snapshot.order_id,
                dedupe_key=_trade_batch_dedupe_key(
                    event_type="trade_filled",
                    account_id=account.id,
                    order_id=snapshot.order_id,
                    fills=merged_trades,
                ),
                payload={
                    "account_id": account.id,
                    "exchange": account.exchange,
                    "order_id": snapshot.order_id,
                    "count": total_trade_rows,
                    "trades": [trade_fill_service.to_event(fill) for fill in merged_trades[:200]],
                },
            ),
        )
    return OrderSubmitResponse(
        account_id=account.id,
        exchange=account.exchange,
        order_id=snapshot.order_id,
        status=snapshot.status,
        message=result.message,
        synced_snapshot_id=snapshot.id,
    )


@router.post("/{order_id}/cancel", response_model=OrderCancelResponse)
async def cancel_order(
    order_id: str,
    payload: OrderCancelRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_step_up_user),
):
    account = _get_owned_account(db, current_user.id, payload.account_id)
    payload_validation_error = _lighter_payload_validation_error(
        exchange=account.exchange,
        exchange_payload=payload.exchange_payload,
        operation="cancel",
    )
    if payload_validation_error:
        error_code, error_message = payload_validation_error
        log_audit_event(
            db,
            user_id=current_user.id,
            action="order_cancel_rejected_validation",
            resource="order",
            resource_id=order_id,
            details={
                "account_id": account.id,
                "exchange": account.exchange,
                "symbol": payload.symbol,
                "validation_error_code": error_code,
                "message": error_message,
            },
        )
        raise HTTPException(status_code=400, detail=error_message)

    decision = risk_service.evaluate_cancel_rate(db, user_id=current_user.id)
    if not decision.allowed:
        details: dict[str, Any] = {
            "account_id": payload.account_id,
            "order_id": order_id,
            "symbol": payload.symbol,
        }
        if (
            decision.reason == "Cancel rate exceeds limit"
            and risk_service.is_circuit_breaker_enabled(db, user_id=current_user.id)
        ):
            # Cancel-rate bursts are treated as runaway behavior and can stop
            # live strategies immediately when circuit breaker is enabled.
            circuit_result = await _trigger_user_circuit_breaker(
                db=db,
                request=request,
                user=current_user,
                reason=decision.reason,
                source_action="order_cancel_rejected_risk",
            )
            details["circuit_breaker_triggered"] = circuit_result.triggered
            details["stopped_strategy_ids"] = circuit_result.stopped_strategy_ids
            if circuit_result.errors:
                details["circuit_breaker_errors"] = circuit_result.errors
        await _emit_risk_blocked(
            db=db,
            request=request,
            user=current_user,
            action="order_cancel_rejected_risk",
            reason=decision.reason,
            details=details,
        )
        raise HTTPException(status_code=429, detail=decision.reason)

    api_key = kms.decrypt(account.api_key_encrypted)
    api_secret = kms.decrypt(account.api_secret_encrypted)
    passphrase = kms.decrypt(account.passphrase_encrypted) if account.passphrase_encrypted else None
    result = gateway_service.cancel_order(
        account,
        api_key,
        api_secret,
        passphrase,
        order_id=order_id,
        symbol=payload.symbol,
        client_order_id=payload.client_order_id,
        exchange_payload=payload.exchange_payload,
    )
    if not result.success:
        log_audit_event(
            db,
            user_id=current_user.id,
            action="order_cancel_failed",
            resource="order",
            resource_id=order_id,
            details={
                "account_id": account.id,
                "exchange": account.exchange,
                "symbol": payload.symbol,
                "message": result.message,
            },
        )
        raise HTTPException(status_code=400, detail=result.message)

    row = dict(result.order or {})
    row.setdefault("order_id", order_id)
    row.setdefault("symbol", payload.symbol)
    row.setdefault("status", "CANCELED")
    snapshot = _upsert_order_snapshot(
        db=db,
        user_id=current_user.id,
        account=account,
        row=row,
    )
    reconciled_orders_synced = 0
    reconciled_trades_synced = 0
    reconciled_trades: list = []
    if account.exchange.lower() == "lighter":
        _create_lighter_reconcile_record(
            db=db,
            user_id=current_user.id,
            account=account,
            operation="cancel",
            request_order_id=snapshot.order_id,
            symbol=snapshot.symbol,
            raw_payload=result.order or {},
        )
        (
            reconciled_orders_synced,
            reconciled_trades_synced,
            reconciled_trades,
        ) = _reconcile_lighter_account_state(
            db=db,
            user_id=current_user.id,
            account=account,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
        )
    db.commit()
    db.refresh(snapshot)
    for fill in reconciled_trades:
        db.refresh(fill)

    log_audit_event(
        db,
        user_id=current_user.id,
        action="order_cancel",
        resource="order",
        resource_id=snapshot.order_id,
        details={
            "snapshot_id": snapshot.id,
            "account_id": account.id,
            "exchange": account.exchange,
            "symbol": snapshot.symbol,
            "status": snapshot.status,
            "reconciled_orders_synced": reconciled_orders_synced,
            "reconciled_trades_synced": reconciled_trades_synced,
            "message": result.message,
        },
    )

    await request.app.state.ws_manager.push_to_user(
        current_user.id,
        build_ws_event(
            event_type="order_canceled",
            resource_id=snapshot.order_id,
            dedupe_key=f"order_canceled:{account.id}:{snapshot.order_id}:{snapshot.status}",
            payload={
                "account_id": account.id,
                "exchange": account.exchange,
                "snapshot_id": snapshot.id,
                "order_id": snapshot.order_id,
                "symbol": snapshot.symbol,
                "status": snapshot.status,
                "canceled_at": datetime.now(timezone.utc).isoformat(),
                "message": result.message,
            },
        ),
    )
    if reconciled_trades:
        await request.app.state.ws_manager.push_to_user(
            current_user.id,
            build_ws_event(
                event_type="trade_filled",
                resource_id=snapshot.order_id,
                dedupe_key=_trade_batch_dedupe_key(
                    event_type="trade_filled",
                    account_id=account.id,
                    order_id=snapshot.order_id,
                    fills=reconciled_trades,
                ),
                payload={
                    "account_id": account.id,
                    "exchange": account.exchange,
                    "order_id": snapshot.order_id,
                    "count": reconciled_trades_synced,
                    "trades": [trade_fill_service.to_event(fill) for fill in reconciled_trades[:200]],
                },
            ),
        )

    return OrderCancelResponse(
        account_id=account.id,
        exchange=account.exchange,
        order_id=snapshot.order_id,
        status=snapshot.status,
        message=result.message,
    )


async def _emit_risk_blocked(
    *,
    db: Session,
    request: Request,
    user: User,
    action: str,
    reason: str,
    details: dict[str, Any],
) -> None:
    notification_service.send_risk_alert(user.id, reason)
    log_audit_event(
        db,
        user_id=user.id,
        action=action,
        resource="order",
        details={"reason": reason, **details},
    )
    await request.app.state.ws_manager.push_to_user(
        user.id,
        build_ws_event(
            event_type="risk_blocked",
            resource_id=details.get("order_id", ""),
            dedupe_key=f"risk_blocked:{action}:{details.get('order_id', '')}:{reason}",
            payload={"action": action, "reason": reason, **details},
        ),
    )

    # Secondary protection: repeated rejections in a short window can indicate
    # a runaway strategy loop. When threshold is hit we trigger user circuit breaker.
    burst_decision = risk_service.evaluate_rejection_burst(db, user_id=user.id)
    if not burst_decision.allowed:
        await _trigger_user_circuit_breaker(
            db=db,
            request=request,
            user=user,
            reason=burst_decision.reason,
            source_action=action,
        )


async def _trigger_user_circuit_breaker(
    *,
    db: Session,
    request: Request,
    user: User,
    reason: str,
    source_action: str,
):
    """
    Execute stop-all, then persist/push a single normalized breaker event.

    Returning the raw service result lets caller include details in risk audit
    payloads without duplicating trigger execution.
    """
    circuit_result = circuit_breaker_service.trigger_user_circuit_breaker(
        db,
        user_id=user.id,
        reason=reason,
    )
    if not circuit_result.triggered and not circuit_result.errors:
        return circuit_result

    log_audit_event(
        db,
        user_id=user.id,
        action="circuit_breaker_trigger",
        resource="strategy",
        details={
            "reason": reason,
            "stopped_strategy_ids": circuit_result.stopped_strategy_ids,
            "errors": circuit_result.errors,
            "source_action": source_action,
        },
    )
    await request.app.state.ws_manager.push_to_user(
        user.id,
        build_ws_event(
            event_type="circuit_breaker_triggered",
            resource_id=str(user.id),
            dedupe_key=f"circuit_breaker:{user.id}:{reason}:{len(circuit_result.stopped_strategy_ids)}",
            payload={
                "reason": reason,
                "stopped_strategy_ids": circuit_result.stopped_strategy_ids,
                "errors": circuit_result.errors,
            },
        ),
    )
    return circuit_result


def _get_owned_account(db: Session, user_id: int, account_id: int) -> ExchangeAccount:
    account = with_tenant(db.query(ExchangeAccount), ExchangeAccount, user_id).filter(
        ExchangeAccount.id == account_id
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def _upsert_order_snapshot(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    row: dict[str, Any],
) -> OrderSnapshot:
    order_id = str(row.get("order_id") or "")
    if not order_id:
        raise HTTPException(status_code=502, detail="Gateway did not return order_id")

    snapshot = with_tenant(db.query(OrderSnapshot), OrderSnapshot, user_id).filter(
        OrderSnapshot.exchange_account_id == account.id,
        OrderSnapshot.order_id == order_id,
    ).first()
    if not snapshot:
        snapshot = OrderSnapshot(
            user_id=user_id,
            exchange_account_id=account.id,
            exchange=account.exchange,
            symbol=str(row.get("symbol") or "").upper(),
            order_id=order_id,
            status=str(row.get("status") or "UNKNOWN").upper(),
            side=str(row.get("side") or "UNKNOWN").upper(),
            order_type=str(row.get("order_type") or "UNKNOWN").upper(),
            price=0,
            quantity=0,
            filled_quantity=0,
        )

    snapshot.symbol = str(row.get("symbol") or snapshot.symbol or "").upper()
    snapshot.client_order_id = row.get("client_order_id") or snapshot.client_order_id
    snapshot.status = str(row.get("status") or snapshot.status or "UNKNOWN").upper()

    incoming_side = str(row.get("side") or "").upper()
    if incoming_side:
        snapshot.side = incoming_side
    elif not snapshot.side:
        snapshot.side = "UNKNOWN"

    incoming_order_type = str(row.get("order_type") or "").upper()
    if incoming_order_type:
        snapshot.order_type = incoming_order_type
    elif not snapshot.order_type:
        snapshot.order_type = "UNKNOWN"

    snapshot.price = _float_or_default(row.get("price"), snapshot.price)
    snapshot.quantity = _float_or_default(row.get("quantity"), snapshot.quantity)
    snapshot.filled_quantity = _float_or_default(row.get("filled_quantity"), snapshot.filled_quantity)
    snapshot.avg_fill_price = _nullable_float(
        row.get("avg_fill_price"),
        snapshot.avg_fill_price,
    )
    snapshot.raw_json = json.dumps(row.get("raw", row), ensure_ascii=False)
    db.add(snapshot)
    return snapshot


def _float_or_default(value: Any, default: float) -> float:
    if value in (None, ""):
        return float(default)
    return float(value)


def _nullable_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    return float(value)


def _validate_lighter_signed_payload(
    *,
    exchange: str,
    exchange_payload: dict[str, Any] | None,
    operation: str,
) -> None:
    """
    Enforce Lighter signed-transaction payload shape at API boundary.

    We intentionally keep client-side signing ownership:
    - `tx_type` and `tx_info` must be provided by client
    - server only forwards validated payload to Lighter gateway API
    """
    error = _lighter_payload_validation_error(
        exchange=exchange,
        exchange_payload=exchange_payload,
        operation=operation,
    )
    if error:
        raise HTTPException(status_code=400, detail=error[1])


def _lighter_payload_validation_error(
    *,
    exchange: str,
    exchange_payload: dict[str, Any] | None,
    operation: str,
) -> tuple[str, str] | None:
    """
    Return deterministic validation error for Lighter signed payload requirements.

    This keeps the API surface unchanged (HTTP 400 + message) while exposing
    a machine-readable error code for audit and ops analytics.
    """
    if exchange.lower() != "lighter":
        return None
    payload = exchange_payload or {}
    tx_type = payload.get("tx_type")
    tx_info = payload.get("tx_info")
    if tx_type is None and tx_info in (None, ""):
        return (
            "missing_tx_type_and_tx_info",
            f"Lighter {operation} requires exchange_payload.tx_type and exchange_payload.tx_info (client-signed payload)",
        )
    if tx_type is None:
        return (
            "missing_tx_type",
            f"Lighter {operation} requires exchange_payload.tx_type and exchange_payload.tx_info (client-signed payload)",
        )
    if tx_info in (None, ""):
        return (
            "missing_tx_info",
            f"Lighter {operation} requires exchange_payload.tx_type and exchange_payload.tx_info (client-signed payload)",
        )
    return None


def _reconcile_lighter_account_state(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    api_key: str,
    api_secret: str,
    passphrase: str | None,
) -> tuple[int, int, list]:
    """
    Best-effort Lighter reconciliation after submit/cancel.

    Lighter order identifiers can be asynchronous (tx hash -> order index), so we
    trigger a fresh account sync to converge local snapshots with exchange state.
    """
    lighter_reconcile_service.expire_pending_records(
        db,
        user_id=user_id,
        account_id=account.id,
    )

    trade_cursors = _build_lighter_trade_cursors(db, user_id=user_id, account_id=account.id)
    try:
        sync_result = gateway_service.fetch_account_state(
            account,
            api_key,
            api_secret,
            passphrase,
            trade_cursors=trade_cursors,
        )
    except Exception as exc:
        lighter_reconcile_service.mark_sync_result(
            db,
            user_id=user_id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error=str(exc),
        )
        return 0, 0, []
    if not sync_result.success:
        lighter_reconcile_service.mark_sync_result(
            db,
            user_id=user_id,
            account=account,
            synced_orders=[],
            synced_trades=[],
            sync_error=sync_result.message,
        )
        return 0, 0, []

    orders_synced = 0
    synced_order_rows: list[dict[str, Any]] = []
    for row in sync_result.orders:
        _upsert_order_snapshot(db=db, user_id=user_id, account=account, row=row)
        synced_order_rows.append(row)
        orders_synced += 1
    trades_synced, upserted_trades = trade_fill_service.upsert_fills(
        db,
        user_id=user_id,
        account=account,
        rows=sync_result.trades,
    )
    lighter_reconcile_service.mark_sync_result(
        db,
        user_id=user_id,
        account=account,
        synced_orders=synced_order_rows,
        synced_trades=sync_result.trades,
    )
    lighter_reconcile_service.expire_pending_records(
        db,
        user_id=user_id,
        account_id=account.id,
    )
    return orders_synced, trades_synced, upserted_trades


def _create_lighter_reconcile_record(
    *,
    db: Session,
    user_id: int,
    account: ExchangeAccount,
    operation: str,
    request_order_id: str,
    symbol: str,
    raw_payload: dict[str, Any],
) -> None:
    lighter_reconcile_service.create_pending_record(
        db,
        user_id=user_id,
        account=account,
        operation=operation,
        request_order_id=request_order_id,
        symbol=symbol,
        raw_payload=raw_payload,
    )


def _build_lighter_trade_cursors(db: Session, *, user_id: int, account_id: int) -> dict[str, Any]:
    """
    Build trade cursor hints for Lighter incremental sync.

    We send both global and symbol-level cursor hints. Gateway layer may ignore
    symbol granularity when exchange API only supports account-level windows.
    """
    symbol_rows = (
        with_tenant(db.query(TradeFillSnapshot.symbol, func.max(TradeFillSnapshot.trade_time)), TradeFillSnapshot, user_id)
        .filter(TradeFillSnapshot.exchange_account_id == account_id)
        .group_by(TradeFillSnapshot.symbol)
        .all()
    )
    symbols: dict[str, dict[str, int]] = {}
    for symbol, last_trade_time in symbol_rows:
        normalized = str(symbol or "").strip().upper()
        if not normalized:
            continue
        cursor_ms = _to_epoch_ms(last_trade_time)
        symbols[normalized] = {"last_trade_time_ms": cursor_ms} if cursor_ms > 0 else {}

    global_last_trade_time = (
        with_tenant(db.query(func.max(TradeFillSnapshot.trade_time)), TradeFillSnapshot, user_id)
        .filter(TradeFillSnapshot.exchange_account_id == account_id)
        .scalar()
    )
    global_ms = _to_epoch_ms(global_last_trade_time)
    global_cursor = {"last_trade_time_ms": global_ms} if global_ms > 0 else {}
    return {"symbols": symbols, "global": global_cursor}


def _to_epoch_ms(value: object) -> int:
    if not isinstance(value, datetime):
        return 0
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return int(normalized.timestamp() * 1000)


def _merge_trade_rows(primary: list, secondary: list) -> list:
    merged: list = []
    seen: set[tuple[str, str]] = set()
    for fill in [*primary, *secondary]:
        key = (str(getattr(fill, "symbol", "")), str(getattr(fill, "trade_id", "")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(fill)
    return merged


def _trade_batch_dedupe_key(*, event_type: str, account_id: int, order_id: str, fills: list) -> str:
    if not fills:
        return f"{event_type}:{account_id}:{order_id}:empty"
    trade_ids = sorted({str(getattr(fill, "trade_id", "")) for fill in fills})
    return f"{event_type}:{account_id}:{order_id}:{'|'.join(trade_ids[:200])}"
