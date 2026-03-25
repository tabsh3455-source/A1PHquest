from datetime import datetime, timedelta, timezone

from app.models import StrategyRuntime
from app.routers.strategies import _build_runtime_mismatches
from app.services.strategy_supervisor import RuntimeState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_runtime_mismatch_helper_returns_empty_when_fields_aligned():
    ts = _utcnow()
    runtime = StrategyRuntime(
        strategy_id=1,
        user_id=1,
        process_id="100",
        status="running",
        started_at=ts,
        stopped_at=None,
        last_heartbeat=ts,
        last_error=None,
    )
    state = RuntimeState(
        runtime_ref="rt-1",
        status="running",
        process_id="100",
        started_at=ts,
        stopped_at=None,
        last_heartbeat=ts + timedelta(seconds=5),  # within tolerance
        last_error=None,
    )
    assert _build_runtime_mismatches(runtime, state) == {}


def test_runtime_mismatch_helper_reports_status_error_and_heartbeat():
    ts = _utcnow()
    runtime = StrategyRuntime(
        strategy_id=2,
        user_id=2,
        process_id="200",
        status="running",
        started_at=ts,
        stopped_at=None,
        last_heartbeat=ts,
        last_error=None,
    )
    state = RuntimeState(
        runtime_ref="rt-2",
        status="failed",
        process_id="200",
        started_at=ts,
        stopped_at=ts,
        last_heartbeat=ts + timedelta(seconds=20),  # outside tolerance
        last_error="gateway failed",
    )
    mismatch = _build_runtime_mismatches(runtime, state)
    assert "status" in mismatch
    assert "last_error" in mismatch
    assert "last_heartbeat" in mismatch
