from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_verified_user
from ..models import AuditEvent, LighterReconcileRecord, Strategy, StrategyRuntime, User
from ..schemas import (
    AdminOpsMetricsResponse,
    OpsAlertItem,
    OpsErrorTrendPoint,
    OpsMetricsResponse,
    OpsRuntimeDriftSample,
    OpsTopBacklogUser,
)
from ..tenant import with_tenant
from ..ws_manager import WsManager

router = APIRouter(prefix="/api/ops", tags=["ops"])
ERROR_TREND_BUCKET_MINUTES = 5
ERROR_TREND_WINDOW_MINUTES = 60
MAX_RUNTIME_DRIFT_SAMPLES = 20
settings = get_settings()


@router.get("/metrics", response_model=OpsMetricsResponse)
def get_ops_metrics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Minimal user-scoped observability endpoint for operational checks.
    """
    ws_manager: WsManager = request.app.state.ws_manager
    runtime_rows = (
        with_tenant(
            db.query(StrategyRuntime.status, func.count(StrategyRuntime.id)),
            StrategyRuntime,
            current_user.id,
        )
        .group_by(StrategyRuntime.status)
        .all()
    )
    runtime_counts = {str(status): int(count) for status, count in runtime_rows}
    strategy_process_count = sum(runtime_counts.get(name, 0) for name in ("starting", "running", "stopping"))
    runtime_status_drift_count = int(
        (
            with_tenant(
                db.query(func.count(StrategyRuntime.id)),
                StrategyRuntime,
                current_user.id,
            )
            .join(Strategy, Strategy.id == StrategyRuntime.strategy_id)
            .filter(
                Strategy.user_id == current_user.id,
                StrategyRuntime.status != Strategy.status,
            )
            .scalar()
        )
        or 0
    )

    lighter_rows = (
        with_tenant(
            db.query(LighterReconcileRecord.status, LighterReconcileRecord.raw_json),
            LighterReconcileRecord,
            current_user.id,
        )
        .all()
    )
    lighter_status_counts = {"pending": 0, "reconciled": 0, "expired": 0}
    retry_due_count = 0
    retry_blocked_count = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for status, raw_json in lighter_rows:
        normalized_status = str(status or "").lower()
        if normalized_status in lighter_status_counts:
            lighter_status_counts[normalized_status] += 1
        if normalized_status != "pending":
            continue
        payload = _load_json(raw_json)
        next_retry_at = _parse_iso_datetime(payload.get("next_retry_at"))
        if not next_retry_at:
            continue
        if next_retry_at <= now:
            retry_due_count += 1
        else:
            retry_blocked_count += 1
    oldest_pending_at = (
        with_tenant(db.query(func.min(LighterReconcileRecord.created_at)), LighterReconcileRecord, current_user.id)
        .filter(LighterReconcileRecord.status == "pending")
        .scalar()
    )
    lighter_pending_oldest_age_seconds: int | None = None
    if oldest_pending_at:
        lighter_pending_oldest_age_seconds = max(int((now - oldest_pending_at).total_seconds()), 0)

    failed_since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    audit_last_hour = with_tenant(db.query(AuditEvent), AuditEvent, current_user.id).filter(
        AuditEvent.created_at >= failed_since
    )
    audit_rows = audit_last_hour.with_entities(AuditEvent.action, AuditEvent.details_json).all()
    total_audit_events_last_hour = len(audit_rows)
    failed_audit_events_last_hour = 0
    critical_audit_events_last_hour = 0
    critical_actions = {
        "login_anomaly",
        "risk_blocked",
        "circuit_breaker_triggered",
        "strategy_runtime_error",
        "aes_rotation_failed",
    }
    audit_action_counts_last_hour: dict[str, int] = {}
    for action, details_json in audit_rows:
        action_text = str(action or "")
        audit_action_counts_last_hour[action_text] = audit_action_counts_last_hour.get(action_text, 0) + 1
        details = _load_json(details_json)
        is_retry_sync_failure = (
            action_text == "lighter_reconcile_retry_sync"
            and _is_failed_retry_sync(details)
        )
        is_failed_action_name = ("failed" in action_text.lower()) or ("error" in action_text.lower())
        if is_failed_action_name or is_retry_sync_failure:
            failed_audit_events_last_hour += 1
        if action_text in critical_actions or is_retry_sync_failure:
            critical_audit_events_last_hour += 1

    failed_audit_event_rate_last_hour = (
        round(failed_audit_events_last_hour / total_audit_events_last_hour, 4)
        if total_audit_events_last_hour
        else 0.0
    )
    return OpsMetricsResponse(
        checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ws_connection_count=ws_manager.connection_count(),
        ws_online_user_count=ws_manager.online_user_count(),
        strategy_runtime_counts=runtime_counts,
        strategy_process_count=strategy_process_count,
        runtime_status_drift_count=runtime_status_drift_count,
        lighter_reconcile_status_counts=lighter_status_counts,
        lighter_reconcile_retry_due_count=retry_due_count,
        lighter_reconcile_retry_blocked_count=retry_blocked_count,
        lighter_pending_oldest_age_seconds=lighter_pending_oldest_age_seconds,
        total_audit_events_last_hour=total_audit_events_last_hour,
        failed_audit_events_last_hour=failed_audit_events_last_hour,
        failed_audit_event_rate_last_hour=failed_audit_event_rate_last_hour,
        critical_audit_events_last_hour=critical_audit_events_last_hour,
        audit_action_counts_last_hour=audit_action_counts_last_hour,
        alert_items=_build_ops_alert_items(
            failed_audit_event_rate_last_hour=failed_audit_event_rate_last_hour,
            runtime_status_drift_count=runtime_status_drift_count,
            lighter_pending_count=int(lighter_status_counts.get("pending", 0) or 0),
            lighter_retry_blocked_count=retry_blocked_count,
            critical_audit_events_last_hour=critical_audit_events_last_hour,
        ),
    )


@router.get("/admin/metrics", response_model=AdminOpsMetricsResponse)
def get_admin_ops_metrics(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Cross-tenant operational view for authenticated operators.

    Admin mode has been removed, so this endpoint intentionally exposes only
    aggregated system-level counters to any signed-in operator. Raw order and
    audit payloads remain available through existing tenant-scoped APIs.
    """
    ws_manager: WsManager = request.app.state.ws_manager
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    total_users = int(db.query(func.count(User.id)).scalar() or 0)
    active_users = int(db.query(func.count(User.id)).filter(User.is_active.is_(True)).scalar() or 0)

    runtime_rows = db.query(StrategyRuntime.status, func.count(StrategyRuntime.id)).group_by(StrategyRuntime.status).all()
    runtime_counts = {str(status): int(count) for status, count in runtime_rows}
    strategy_process_count = sum(runtime_counts.get(name, 0) for name in ("starting", "running", "stopping"))
    runtime_status_drift_count = int(
        (
            db.query(func.count(StrategyRuntime.id))
            .join(Strategy, Strategy.id == StrategyRuntime.strategy_id)
            .filter(StrategyRuntime.status != Strategy.status)
            .scalar()
        )
        or 0
    )

    lighter_rows = db.query(
        LighterReconcileRecord.user_id,
        LighterReconcileRecord.status,
        LighterReconcileRecord.created_at,
        LighterReconcileRecord.raw_json,
    ).all()
    lighter_status_counts = {"pending": 0, "reconciled": 0, "expired": 0}
    retry_due_count = 0
    retry_blocked_count = 0
    pending_by_user: dict[int, dict[str, Any]] = {}
    for user_id, status, created_at, raw_json in lighter_rows:
        normalized_status = str(status or "").lower()
        if normalized_status in lighter_status_counts:
            lighter_status_counts[normalized_status] += 1
        if normalized_status != "pending":
            continue

        state = pending_by_user.setdefault(
            int(user_id),
            {
                "pending_count": 0,
                "retry_due_count": 0,
                "retry_blocked_count": 0,
                "oldest_pending_at": None,
            },
        )
        state["pending_count"] += 1
        if state["oldest_pending_at"] is None or created_at < state["oldest_pending_at"]:
            state["oldest_pending_at"] = created_at

        payload = _load_json(raw_json)
        next_retry_at = _parse_iso_datetime(payload.get("next_retry_at"))
        if not next_retry_at:
            continue
        if next_retry_at <= now:
            retry_due_count += 1
            state["retry_due_count"] += 1
        else:
            retry_blocked_count += 1
            state["retry_blocked_count"] += 1

    top_lighter_pending_users: list[OpsTopBacklogUser] = []
    for user_id, state in pending_by_user.items():
        oldest_pending_at = state.get("oldest_pending_at")
        oldest_age = None
        if oldest_pending_at:
            oldest_age = max(int((now - oldest_pending_at).total_seconds()), 0)
        top_lighter_pending_users.append(
            OpsTopBacklogUser(
                user_id=user_id,
                pending_count=int(state.get("pending_count", 0)),
                retry_due_count=int(state.get("retry_due_count", 0)),
                retry_blocked_count=int(state.get("retry_blocked_count", 0)),
                oldest_pending_age_seconds=oldest_age,
            )
        )
    top_lighter_pending_users.sort(
        key=lambda row: (row.pending_count, row.retry_blocked_count, row.retry_due_count),
        reverse=True,
    )
    top_lighter_pending_users = top_lighter_pending_users[:10]

    failed_since = now - timedelta(hours=1)
    audit_rows = (
        db.query(AuditEvent.action, AuditEvent.details_json, AuditEvent.created_at)
        .filter(AuditEvent.created_at >= failed_since)
        .all()
    )
    total_audit_events_last_hour = len(audit_rows)
    failed_audit_events_last_hour = 0
    critical_audit_events_last_hour = 0
    audit_action_counts_last_hour: dict[str, int] = {}
    critical_actions = {
        "login_anomaly",
        "risk_blocked",
        "circuit_breaker_triggered",
        "strategy_runtime_error",
        "aes_rotation_failed",
    }
    for action, details_json, _created_at in audit_rows:
        action_text = str(action or "")
        audit_action_counts_last_hour[action_text] = audit_action_counts_last_hour.get(action_text, 0) + 1
        details = _load_json(details_json)
        is_retry_sync_failure = (
            action_text == "lighter_reconcile_retry_sync"
            and _is_failed_retry_sync(details)
        )
        is_failed_action_name = ("failed" in action_text.lower()) or ("error" in action_text.lower())
        if is_failed_action_name or is_retry_sync_failure:
            failed_audit_events_last_hour += 1
        if action_text in critical_actions or is_retry_sync_failure:
            critical_audit_events_last_hour += 1
    failed_audit_event_rate_last_hour = (
        round(failed_audit_events_last_hour / total_audit_events_last_hour, 4)
        if total_audit_events_last_hour
        else 0.0
    )
    error_trend_last_hour = _build_error_trend(
        audit_rows,
        now=now,
        critical_actions=critical_actions,
    )
    runtime_drift_samples = (
        db.query(
            StrategyRuntime.user_id,
            StrategyRuntime.strategy_id,
            StrategyRuntime.status,
            StrategyRuntime.process_id,
            StrategyRuntime.last_heartbeat,
            StrategyRuntime.last_error,
            Strategy.name,
            Strategy.strategy_type,
            Strategy.status,
            Strategy.runtime_ref,
        )
        .join(Strategy, Strategy.id == StrategyRuntime.strategy_id)
        .filter(StrategyRuntime.status != Strategy.status)
        .order_by(
            StrategyRuntime.last_heartbeat.asc(),
            StrategyRuntime.id.asc(),
        )
        .limit(MAX_RUNTIME_DRIFT_SAMPLES)
        .all()
    )
    drift_payload = [
        OpsRuntimeDriftSample(
            user_id=int(user_id),
            strategy_id=int(strategy_id),
            strategy_name=str(strategy_name or ""),
            strategy_type=str(strategy_type or ""),
            strategy_status=str(strategy_status or ""),
            runtime_status=str(runtime_status or ""),
            runtime_ref=str(runtime_ref) if runtime_ref else None,
            process_id=str(process_id) if process_id else None,
            last_heartbeat=_to_naive_utc(last_heartbeat) if last_heartbeat else None,
            last_error=str(last_error) if last_error else None,
        )
        for (
            user_id,
            strategy_id,
            runtime_status,
            process_id,
            last_heartbeat,
            last_error,
            strategy_name,
            strategy_type,
            strategy_status,
            runtime_ref,
        ) in runtime_drift_samples
    ]

    return AdminOpsMetricsResponse(
        checked_at=now,
        total_users=total_users,
        active_users=active_users,
        ws_connection_count=ws_manager.connection_count(),
        ws_online_user_count=ws_manager.online_user_count(),
        strategy_runtime_counts=runtime_counts,
        strategy_process_count=strategy_process_count,
        runtime_status_drift_count=runtime_status_drift_count,
        lighter_reconcile_status_counts=lighter_status_counts,
        lighter_reconcile_retry_due_count=retry_due_count,
        lighter_reconcile_retry_blocked_count=retry_blocked_count,
        total_audit_events_last_hour=total_audit_events_last_hour,
        failed_audit_events_last_hour=failed_audit_events_last_hour,
        failed_audit_event_rate_last_hour=failed_audit_event_rate_last_hour,
        critical_audit_events_last_hour=critical_audit_events_last_hour,
        audit_action_counts_last_hour=audit_action_counts_last_hour,
        alert_items=_build_ops_alert_items(
            failed_audit_event_rate_last_hour=failed_audit_event_rate_last_hour,
            runtime_status_drift_count=runtime_status_drift_count,
            lighter_pending_count=int(lighter_status_counts.get("pending", 0) or 0),
            lighter_retry_blocked_count=retry_blocked_count,
            critical_audit_events_last_hour=critical_audit_events_last_hour,
        ),
        top_lighter_pending_users=top_lighter_pending_users,
        error_trend_last_hour=error_trend_last_hour,
        runtime_drift_samples=drift_payload,
    )


def _load_json(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _is_failed_retry_sync(details: dict[str, Any]) -> bool:
    value = details.get("success")
    if isinstance(value, bool):
        return not value
    return str(value).lower().strip() in {"0", "false", "no"}


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


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _bucket_floor(value: datetime, bucket_minutes: int) -> datetime:
    floored_minute = (value.minute // bucket_minutes) * bucket_minutes
    return value.replace(minute=floored_minute, second=0, microsecond=0)


def _build_error_trend(
    rows: list[tuple[str, str, datetime]],
    *,
    now: datetime,
    critical_actions: set[str],
    bucket_minutes: int = ERROR_TREND_BUCKET_MINUTES,
    window_minutes: int = ERROR_TREND_WINDOW_MINUTES,
) -> list[OpsErrorTrendPoint]:
    """
    Build a fixed-window error trend so dashboards can render stable time buckets.

    We pre-create buckets for the whole window to avoid missing slots when traffic is low.
    """
    window_start = now - timedelta(minutes=window_minutes)
    start_bucket = _bucket_floor(window_start, bucket_minutes)
    end_bucket = _bucket_floor(now, bucket_minutes)

    buckets: dict[datetime, dict[str, int]] = {}
    cursor = start_bucket
    while cursor <= end_bucket:
        buckets[cursor] = {"total": 0, "failed": 0, "critical": 0}
        cursor += timedelta(minutes=bucket_minutes)

    for action, details_json, created_at in rows:
        event_time = _to_naive_utc(created_at)
        if event_time < window_start:
            continue
        bucket_key = _bucket_floor(event_time, bucket_minutes)
        if bucket_key not in buckets:
            continue
        bucket = buckets[bucket_key]
        bucket["total"] += 1

        action_text = str(action or "")
        details = _load_json(details_json)
        is_retry_sync_failure = (
            action_text == "lighter_reconcile_retry_sync"
            and _is_failed_retry_sync(details)
        )
        is_failed_action_name = ("failed" in action_text.lower()) or ("error" in action_text.lower())
        if is_failed_action_name or is_retry_sync_failure:
            bucket["failed"] += 1
        if action_text in critical_actions or is_retry_sync_failure:
            bucket["critical"] += 1

    return [
        OpsErrorTrendPoint(
            bucket_start=bucket_start,
            total_events=counts["total"],
            failed_events=counts["failed"],
            critical_events=counts["critical"],
        )
        for bucket_start, counts in sorted(buckets.items())
    ]


def _build_ops_alert_items(
    *,
    failed_audit_event_rate_last_hour: float,
    runtime_status_drift_count: int,
    lighter_pending_count: int,
    lighter_retry_blocked_count: int,
    critical_audit_events_last_hour: int,
) -> list[OpsAlertItem]:
    """
    Convert raw ops metrics into stable alert items for dashboards/runbooks.

    Alert generation is additive-only and threshold-based, so callers can safely
    consume this list without parsing multiple metric fields on the frontend.
    """
    alerts: list[OpsAlertItem] = []
    failed_rate_threshold = max(float(settings.ops_alert_failed_audit_rate_threshold), 0.0)
    runtime_drift_threshold = max(int(settings.ops_alert_runtime_drift_count_threshold), 0)
    pending_threshold = max(int(settings.ops_alert_lighter_pending_threshold), 0)
    retry_blocked_threshold = max(int(settings.ops_alert_lighter_retry_blocked_threshold), 0)
    critical_events_threshold = max(int(settings.ops_alert_critical_audit_events_threshold), 0)

    if failed_rate_threshold > 0 and failed_audit_event_rate_last_hour >= failed_rate_threshold:
        alerts.append(
            OpsAlertItem(
                code="failed_audit_rate_high",
                severity=_severity_by_ratio(failed_audit_event_rate_last_hour, failed_rate_threshold),
                metric="failed_audit_event_rate_last_hour",
                value=round(float(failed_audit_event_rate_last_hour), 6),
                threshold=float(failed_rate_threshold),
                message="Failed audit event rate exceeded threshold.",
            )
        )

    if runtime_drift_threshold > 0 and runtime_status_drift_count >= runtime_drift_threshold:
        alerts.append(
            OpsAlertItem(
                code="runtime_status_drift_detected",
                severity=_severity_by_ratio(runtime_status_drift_count, runtime_drift_threshold),
                metric="runtime_status_drift_count",
                value=float(runtime_status_drift_count),
                threshold=float(runtime_drift_threshold),
                message="Strategy status and runtime status drift detected.",
            )
        )

    if pending_threshold > 0 and lighter_pending_count >= pending_threshold:
        alerts.append(
            OpsAlertItem(
                code="lighter_pending_backlog_high",
                severity=_severity_by_ratio(lighter_pending_count, pending_threshold),
                metric="lighter_pending_count",
                value=float(lighter_pending_count),
                threshold=float(pending_threshold),
                message="Lighter pending reconcile backlog exceeded threshold.",
            )
        )

    if retry_blocked_threshold > 0 and lighter_retry_blocked_count >= retry_blocked_threshold:
        alerts.append(
            OpsAlertItem(
                code="lighter_retry_blocked_high",
                severity=_severity_by_ratio(lighter_retry_blocked_count, retry_blocked_threshold),
                metric="lighter_retry_blocked_count",
                value=float(lighter_retry_blocked_count),
                threshold=float(retry_blocked_threshold),
                message="Lighter retry-blocked records exceeded threshold.",
            )
        )

    if critical_events_threshold > 0 and critical_audit_events_last_hour >= critical_events_threshold:
        alerts.append(
            OpsAlertItem(
                code="critical_audit_events_high",
                severity=_severity_by_ratio(critical_audit_events_last_hour, critical_events_threshold),
                metric="critical_audit_events_last_hour",
                value=float(critical_audit_events_last_hour),
                threshold=float(critical_events_threshold),
                message="Critical audit events in last hour exceeded threshold.",
            )
        )
    return alerts


def _severity_by_ratio(value: float | int, threshold: float | int) -> str:
    baseline = float(threshold)
    current = float(value)
    if baseline <= 0:
        return "warning"
    return "critical" if current >= baseline * 2 else "warning"
