from __future__ import annotations

from datetime import datetime
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .runtime import Runtime, RuntimeRegistry


app = FastAPI(title="A1phquest Worker Supervisor", version="0.1.0")
registry = RuntimeRegistry()


def _read_expected_supervisor_token() -> str:
    return str(os.getenv("SUPERVISOR_SHARED_TOKEN", "")).strip()


def _require_supervisor_token(
    token: str | None = Header(default=None, alias="X-Supervisor-Token"),
) -> None:
    expected = _read_expected_supervisor_token()
    if not expected:
        raise HTTPException(status_code=503, detail="worker-supervisor token is not configured")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid supervisor authentication")


class RuntimeStartRequest(BaseModel):
    # Runtime ownership and strategy identity. These are used to load credentials
    # from database directly in worker process (no plaintext secret via API).
    user_id: int = Field(gt=0)
    strategy_id: int = Field(gt=0)
    strategy_type: str = Field(min_length=1, max_length=64)
    config_json: str = "{}"


class RuntimeStopRequest(BaseModel):
    runtime_ref: str = Field(min_length=1, max_length=128)


class RuntimeResponse(BaseModel):
    runtime_ref: str
    process_id: str | None
    status: str
    started_at: datetime | None
    stopped_at: datetime | None
    last_heartbeat: datetime | None
    last_error: str | None
    last_event_seq: int = 0
    last_event_type: str | None = None
    last_event_at: datetime | None = None
    order_submitted_count: int = 0
    order_update_count: int = 0
    trade_fill_count: int = 0
    recent_events: list[dict] = Field(default_factory=list)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "worker-supervisor"}


@app.post("/runtime/start", response_model=RuntimeResponse, status_code=status.HTTP_201_CREATED)
def start_runtime(payload: RuntimeStartRequest, _: None = Depends(_require_supervisor_token)) -> RuntimeResponse:
    try:
        runtime = registry.start(
            user_id=payload.user_id,
            strategy_id=payload.strategy_id,
            strategy_type=payload.strategy_type,
            config_json=payload.config_json,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to start runtime: {exc}") from exc
    return _to_response(runtime)


@app.post("/runtime/stop", response_model=RuntimeResponse)
def stop_runtime(payload: RuntimeStopRequest, _: None = Depends(_require_supervisor_token)) -> RuntimeResponse:
    runtime = registry.stop(payload.runtime_ref)
    if not runtime:
        raise HTTPException(status_code=404, detail=f"runtime_ref '{payload.runtime_ref}' not found")
    return _to_response(runtime)


@app.get("/runtime/{runtime_ref}", response_model=RuntimeResponse)
def get_runtime(runtime_ref: str, _: None = Depends(_require_supervisor_token)) -> RuntimeResponse:
    runtime = registry.get(runtime_ref)
    if not runtime:
        raise HTTPException(status_code=404, detail=f"runtime_ref '{runtime_ref}' not found")
    return _to_response(runtime)


def _to_response(runtime: Runtime) -> RuntimeResponse:
    process_id = str(runtime.process.pid) if runtime.process and runtime.process.pid else None
    return RuntimeResponse(
        runtime_ref=runtime.runtime_ref,
        process_id=process_id,
        status=runtime.status,
        started_at=runtime.started_at,
        stopped_at=runtime.stopped_at,
        last_heartbeat=runtime.last_heartbeat,
        last_error=runtime.last_error,
        last_event_seq=runtime.last_event_seq,
        last_event_type=runtime.last_event_type,
        last_event_at=runtime.last_event_at,
        order_submitted_count=runtime.order_submitted_count,
        order_update_count=runtime.order_update_count,
        trade_fill_count=runtime.trade_fill_count,
        recent_events=list(runtime.recent_events),
    )
