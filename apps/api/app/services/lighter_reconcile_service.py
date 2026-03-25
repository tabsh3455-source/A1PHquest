from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ExchangeAccount, LighterReconcileRecord
from ..tenant import with_tenant

settings = get_settings()


class LighterReconcileService:
    """
    Manage Lighter post-trade reconciliation records.

    Records are created at submit/cancel time as `pending`, then marked
    `reconciled` when exchange sync surfaces matching order/trade snapshots.
    Stale pending records are transitioned to `expired` to avoid queue buildup.
    """

    def create_pending_record(
        self,
        db: Session,
        *,
        user_id: int,
        account: ExchangeAccount,
        operation: str,
        request_order_id: str,
        symbol: str,
        raw_payload: dict[str, Any] | None,
    ) -> None:
        if account.exchange.lower() != "lighter":
            return
        normalized_operation = operation.strip().lower()
        normalized_order_id = str(request_order_id or "").strip()
        if not normalized_order_id:
            return
        normalized_symbol = str(symbol or "").strip().upper()

        record = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account.id,
            LighterReconcileRecord.operation == normalized_operation,
            LighterReconcileRecord.request_order_id == normalized_order_id,
        ).first()
        if not record:
            record = LighterReconcileRecord(
                user_id=user_id,
                exchange_account_id=account.id,
                operation=normalized_operation,
                request_order_id=normalized_order_id,
                symbol=normalized_symbol,
            )

        record.symbol = normalized_symbol or record.symbol
        record.status = "pending"
        record.resolved_order_id = None
        record.resolved_trade_id = None
        record.last_sync_at = None
        record.resolved_at = None
        payload = raw_payload or {}
        payload.pop("next_retry_at", None)
        payload.pop("next_retry_after_seconds", None)
        record.raw_json = json.dumps(payload, ensure_ascii=False)
        db.add(record)

    def expire_pending_records(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> int:
        ttl_seconds = max(int(settings.lighter_reconcile_pending_ttl_seconds), 1)
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=ttl_seconds)
        pending = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
            LighterReconcileRecord.status == "pending",
            LighterReconcileRecord.created_at <= threshold,
        ).all()
        if not pending:
            return 0

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        changed = 0
        for record in pending:
            payload = _load_json(record.raw_json)
            payload["expire_reason"] = "pending_ttl_reached"
            record.status = "expired"
            record.last_sync_at = now
            record.resolved_at = now
            record.raw_json = json.dumps(payload, ensure_ascii=False)
            db.add(record)
            changed += 1
        return changed

    def prune_expired_records(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> int:
        """
        Permanently remove aged `expired` rows to keep reconcile table bounded.

        Pruning only touches expired rows older than retention threshold so recent
        failures remain available for troubleshooting.
        """
        retention_seconds = max(int(settings.lighter_reconcile_expired_retention_seconds), 1)
        threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=retention_seconds)
        rows = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
            LighterReconcileRecord.status == "expired",
            LighterReconcileRecord.updated_at <= threshold,
        ).all()
        if not rows:
            return 0

        for row in rows:
            db.delete(row)
        return len(rows)

    def mark_sync_result(
        self,
        db: Session,
        *,
        user_id: int,
        account: ExchangeAccount,
        synced_orders: list[dict[str, Any]],
        synced_trades: list[dict[str, Any]],
        sync_error: str | None = None,
    ) -> int:
        """
        Update pending records from latest sync snapshot.

        Matching order precedence:
        1) exact request_order_id -> order_id/trade.order_id
        2) tx-hash request id fallback by symbol (best effort for async ids)
        """
        if account.exchange.lower() != "lighter":
            return 0

        pending_records = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account.id,
            LighterReconcileRecord.status == "pending",
        ).all()
        if not pending_records:
            return 0

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order_by_id: dict[str, dict[str, Any]] = {}
        order_by_client_id: dict[str, dict[str, Any]] = {}
        trade_by_order_id: dict[str, dict[str, Any]] = {}
        symbol_index_orders: dict[str, list[dict[str, Any]]] = {}
        symbol_index_trades: dict[str, list[dict[str, Any]]] = {}

        for row in synced_orders:
            order_id = str(row.get("order_id") or "").strip()
            client_order_id = str(row.get("client_order_id") or "").strip()
            symbol = str(row.get("symbol") or "").strip().upper()
            if order_id:
                order_by_id[order_id] = row
            if client_order_id:
                order_by_client_id[client_order_id] = row
            if symbol:
                symbol_index_orders.setdefault(symbol, []).append(row)

        for row in synced_trades:
            order_id = str(row.get("order_id") or "").strip()
            symbol = str(row.get("symbol") or "").strip().upper()
            if order_id:
                current = trade_by_order_id.get(order_id)
                if not current or _trade_recency_key(row) >= _trade_recency_key(current):
                    trade_by_order_id[order_id] = row
            if symbol:
                symbol_index_trades.setdefault(symbol, []).append(row)

        reconciled = 0
        for record in pending_records:
            payload = _load_json(record.raw_json)
            record.last_sync_at = now
            if sync_error:
                retry_gate = _parse_iso_datetime(payload.get("next_retry_at"))
                if retry_gate and retry_gate > now:
                    payload["last_sync_error"] = sync_error
                    payload["last_sync_error_code"] = _classify_sync_error(sync_error)
                    payload["last_sync_skipped_due_to_backoff"] = True
                    record.raw_json = json.dumps(payload, ensure_ascii=False)
                    db.add(record)
                    continue

                sync_error_count = int(payload.get("sync_error_count", 0)) + 1
                max_sync_errors = max(int(settings.lighter_reconcile_max_sync_errors), 1)
                error_code = _classify_sync_error(sync_error)
                retry_after_seconds = _compute_retry_delay_seconds(
                    error_code=error_code,
                    sync_error_count=sync_error_count,
                )
                payload.pop("last_sync_skipped_due_to_backoff", None)
                payload["sync_error_count"] = sync_error_count
                payload["last_sync_error"] = sync_error
                payload["last_sync_error_code"] = error_code
                if retry_after_seconds is not None:
                    next_retry_at = now + timedelta(seconds=retry_after_seconds)
                    payload["next_retry_after_seconds"] = retry_after_seconds
                    payload["next_retry_at"] = next_retry_at.isoformat()
                else:
                    payload.pop("next_retry_after_seconds", None)
                    payload.pop("next_retry_at", None)
                if sync_error_count >= max_sync_errors:
                    payload["expire_reason"] = "sync_error_threshold_reached"
                    record.status = "expired"
                    record.resolved_at = now
                record.raw_json = json.dumps(payload, ensure_ascii=False)
                db.add(record)
                continue

            request_id = str(record.request_order_id or "").strip()
            request_symbol = str(record.symbol or "").strip().upper()
            matched_order: dict[str, Any] | None = None
            matched_trade: dict[str, Any] | None = None
            matched_by = ""
            matched_value = ""

            # Candidate extraction is intentionally permissive because Lighter can
            # return tx hash first and resolve order index/client index later.
            order_id_candidates, client_order_id_candidates = _build_match_candidates(record=record, raw_payload=payload)
            payload["match_candidates"] = {
                "order_ids": order_id_candidates,
                "client_order_ids": client_order_id_candidates,
            }

            for candidate in order_id_candidates:
                matched_order = order_by_id.get(candidate)
                matched_trade = trade_by_order_id.get(candidate)
                if matched_order or matched_trade:
                    matched_by = "order_id_candidate"
                    matched_value = candidate
                    break

            if not matched_order and not matched_trade:
                for client_candidate in client_order_id_candidates:
                    matched_order = order_by_client_id.get(client_candidate)
                    if matched_order:
                        resolved_order_id = str(matched_order.get("order_id") or "").strip()
                        matched_trade = trade_by_order_id.get(resolved_order_id) if resolved_order_id else None
                        matched_by = "client_order_id_candidate"
                        matched_value = client_candidate
                        break

            if not matched_order and not matched_trade and request_id.lower().startswith("0x") and request_symbol:
                symbol_orders = symbol_index_orders.get(request_symbol) or []
                symbol_trades = symbol_index_trades.get(request_symbol) or []
                # Tx-hash fallback is only safe when symbol narrows to one unique
                # order/trade candidate; otherwise we keep the record pending.
                if len(symbol_orders) == 1:
                    matched_order = symbol_orders[0]
                if len(symbol_trades) == 1:
                    matched_trade = symbol_trades[0]
                if matched_order or matched_trade:
                    matched_by = "tx_hash_symbol_fallback"
                    matched_value = request_symbol
                elif symbol_orders or symbol_trades:
                    payload["ambiguous_symbol_fallback"] = {
                        "symbol": request_symbol,
                        "order_candidates": len(symbol_orders),
                        "trade_candidates": len(symbol_trades),
                    }

            if not matched_order and not matched_trade:
                payload.pop("last_sync_error", None)
                payload.pop("sync_error_count", None)
                payload.pop("last_sync_error_code", None)
                payload.pop("next_retry_after_seconds", None)
                payload.pop("next_retry_at", None)
                record.raw_json = json.dumps(payload, ensure_ascii=False)
                db.add(record)
                continue

            record.status = "reconciled"
            record.resolved_at = now
            record.resolved_order_id = str((matched_order or matched_trade or {}).get("order_id") or "") or None
            record.resolved_trade_id = (
                str(matched_trade.get("trade_id") or "")
                if matched_trade and matched_trade.get("trade_id")
                else record.resolved_trade_id
            )
            payload.pop("last_sync_error", None)
            payload.pop("sync_error_count", None)
            payload.pop("last_sync_error_code", None)
            payload.pop("next_retry_after_seconds", None)
            payload.pop("next_retry_at", None)
            payload["resolved_match_by"] = matched_by or "unknown"
            payload["resolved_match_value"] = matched_value
            record.raw_json = json.dumps(payload, ensure_ascii=False)
            db.add(record)
            reconciled += 1

        return reconciled

    def status_stats(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> dict[str, int]:
        rows = with_tenant(db.query(LighterReconcileRecord.status), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id
        ).all()
        result = {"pending": 0, "reconciled": 0, "expired": 0}
        for (status,) in rows:
            key = str(status or "").lower()
            if key in result:
                result[key] += 1
        return result

    def list_pending(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
        limit: int = 200,
    ) -> list[LighterReconcileRecord]:
        return (
            with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id)
            .filter(
                LighterReconcileRecord.exchange_account_id == account_id,
                LighterReconcileRecord.status == "pending",
            )
            .order_by(LighterReconcileRecord.created_at.desc())
            .limit(limit)
            .all()
        )

    def oldest_pending_age_seconds(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> int | None:
        pending = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
            LighterReconcileRecord.status == "pending",
        ).order_by(LighterReconcileRecord.created_at.asc()).first()
        if not pending:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age = (now - pending.created_at).total_seconds()
        return max(int(age), 0)

    def recent_failure_reasons(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
        limit: int = 20,
    ) -> list[str]:
        rows = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
        ).order_by(LighterReconcileRecord.updated_at.desc()).limit(max(limit, 1)).all()
        reasons: list[str] = []
        seen: set[str] = set()
        for row in rows:
            payload = _load_json(row.raw_json)
            reason = str(payload.get("last_sync_error", "")).strip()
            if not reason or reason in seen:
                continue
            seen.add(reason)
            reasons.append(reason)
        return reasons

    def failure_code_stats(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> dict[str, int]:
        """
        Aggregate pending reconcile backlog by normalized sync error code.
        """
        rows = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
            LighterReconcileRecord.status == "pending",
        ).all()
        stats: dict[str, int] = {}
        for row in rows:
            payload = _load_json(row.raw_json)
            code = str(payload.get("last_sync_error_code", "")).strip().lower()
            if not code:
                reason = str(payload.get("last_sync_error", "")).strip()
                code = _classify_sync_error(reason) if reason else ""
            if not code:
                continue
            stats[code] = stats.get(code, 0) + 1
        return stats

    def retry_window_stats(
        self,
        db: Session,
        *,
        user_id: int,
        account_id: int,
    ) -> dict[str, Any]:
        """
        Aggregate pending retry window visibility for operations dashboard.

        - retry_due: records that can be retried immediately
        - retry_blocked: records still inside backoff cooldown
        - no_retry_hint: records without retry metadata (usually first sync or parse issues)
        """
        rows = with_tenant(db.query(LighterReconcileRecord), LighterReconcileRecord, user_id).filter(
            LighterReconcileRecord.exchange_account_id == account_id,
            LighterReconcileRecord.status == "pending",
        ).all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        retry_due = 0
        retry_blocked = 0
        no_retry_hint = 0
        earliest_next_retry: datetime | None = None

        for row in rows:
            payload = _load_json(row.raw_json)
            next_retry_at_raw = payload.get("next_retry_at")
            next_retry_at = _parse_iso_datetime(next_retry_at_raw)
            if not next_retry_at:
                no_retry_hint += 1
                continue
            if next_retry_at <= now:
                retry_due += 1
            else:
                retry_blocked += 1
                if earliest_next_retry is None or next_retry_at < earliest_next_retry:
                    earliest_next_retry = next_retry_at
        return {
            "retry_due": retry_due,
            "retry_blocked": retry_blocked,
            "no_retry_hint": no_retry_hint,
            "next_retry_at": earliest_next_retry,
        }


def _load_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _classify_sync_error(message: str) -> str:
    text = str(message or "").strip().lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("429", "rate limit", "too many requests", "throttle")):
        return "rate_limited"
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "auth", "signature")):
        return "auth_failed"
    if any(token in text for token in ("500", "502", "503", "504", "bad gateway", "service unavailable", "upstream")):
        return "upstream_unavailable"
    if any(token in text for token in ("timeout", "timed out", "network", "connection", "dns", "socket")):
        return "network_error"
    if any(token in text for token in ("invalid payload", "invalid", "decode", "parse")):
        return "invalid_payload"
    return "sync_failed"


def _compute_retry_delay_seconds(*, error_code: str, sync_error_count: int) -> int | None:
    """
    Compute reconcile retry delay using bounded exponential backoff.

    Auth/invalid payload errors usually require operator intervention, so
    these do not emit retry hints.
    """
    attempts = max(int(sync_error_count), 1)
    if error_code in {"auth_failed", "invalid_payload"}:
        return None
    if error_code == "rate_limited":
        return min(300, 5 * (2 ** (attempts - 1)))
    if error_code in {"upstream_unavailable", "network_error"}:
        return min(120, 3 * (2 ** (attempts - 1)))
    if error_code in {"sync_failed", "unknown"}:
        return min(180, 10 * (2 ** (attempts - 1)))
    return 30


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


def _build_match_candidates(
    *,
    record: LighterReconcileRecord,
    raw_payload: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """
    Build candidate keys used to resolve pending Lighter records.

    Why we need this:
    - submit/cancel response can contain tx hash before order index is available
    - tx_info may include order/client indexes signed by client
    - active order snapshot can expose either order_id or client_order_id
    """
    raw_block = raw_payload.get("raw") if isinstance(raw_payload.get("raw"), dict) else {}
    request_block = raw_block.get("request") if isinstance(raw_block.get("request"), dict) else {}
    response_block = raw_block.get("response") if isinstance(raw_block.get("response"), dict) else {}
    tx_info = _load_nested_dict(request_block.get("tx_info"))

    order_id_candidates = _collect_unique_strings(
        [
            record.request_order_id,
            raw_payload.get("order_id"),
            raw_payload.get("resolved_order_id"),
            raw_payload.get("tx_hash"),
            response_block.get("tx_hash"),
            response_block.get("order_id"),
            response_block.get("order_index"),
            tx_info.get("OrderIndex"),
            tx_info.get("order_index"),
            tx_info.get("ClientOrderIndex"),
            tx_info.get("client_order_index"),
            tx_info.get("ClientOrderId"),
            tx_info.get("client_order_id"),
        ]
    )
    client_order_id_candidates = _collect_unique_strings(
        [
            raw_payload.get("client_order_id"),
            raw_payload.get("client_order_index"),
            response_block.get("client_order_id"),
            response_block.get("client_order_index"),
            tx_info.get("ClientOrderId"),
            tx_info.get("client_order_id"),
            tx_info.get("ClientOrderIndex"),
            tx_info.get("client_order_index"),
        ]
    )
    return order_id_candidates, client_order_id_candidates


def _load_nested_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _collect_unique_strings(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _trade_recency_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """
    Deterministically choose the most recent trade per order for reconcile linkage.

    Priority:
    1) trade timestamp (normalized to ms)
    2) numeric trade_id when available
    3) lexical trade_id fallback
    """
    raw_time = row.get("trade_time", row.get("timestamp"))
    trade_time = _to_epoch_ms(raw_time)
    trade_id_raw = str(row.get("trade_id") or "")
    try:
        trade_id_num = int(trade_id_raw)
    except (TypeError, ValueError):
        trade_id_num = -1
    return trade_time, trade_id_num, trade_id_raw


def _to_epoch_ms(value: object) -> int:
    if isinstance(value, datetime):
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(normalized.timestamp() * 1000)
    try:
        raw = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    # Lighter may return seconds-scale timestamps.
    return raw * 1000 if raw < 10_000_000_000 else raw
