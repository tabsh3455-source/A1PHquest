from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

import httpx

from ..config import get_settings

settings = get_settings()


class StrategySupervisorError(RuntimeError):
    """Base error for worker-supervisor interactions."""


class StrategySupervisorUnavailableError(StrategySupervisorError):
    """Raised when worker-supervisor cannot be reached."""


@dataclass(slots=True)
class RuntimeState:
    # Unique runtime identity returned by worker-supervisor.
    runtime_ref: str
    # Lifecycle status reported by worker-supervisor state machine.
    status: str
    # Process and timing fields are optional for compatibility and partial states.
    process_id: str | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    # Runtime event sequence watermark from worker-supervisor.
    last_event_seq: int = 0
    last_event_type: str | None = None
    last_event_at: datetime | None = None
    # Execution observability counters.
    order_submitted_count: int = 0
    order_update_count: int = 0
    trade_fill_count: int = 0
    # Recent runtime events ordered by sequence.
    recent_events: list[dict] | None = None


class StrategySupervisorClient:
    """HTTP client for worker-supervisor service."""

    def __init__(self, base_url: str | None = None, shared_token: str | None = None):
        resolved_base_url = base_url or settings.supervisor_base_url
        self.base_url = resolved_base_url.rstrip("/")
        # Runtime stop can legitimately take longer than start/get because worker
        # needs to wait for strategy process shutdown and cleanup.
        self.timeout = _read_timeout_seconds(default=20.0)
        self.shared_token = (shared_token or os.getenv("SUPERVISOR_SHARED_TOKEN", "")).strip()

    def start_strategy(self, user_id: int, strategy_id: int, strategy_type: str, config_json: str) -> RuntimeState:
        payload = {
            "user_id": user_id,
            "strategy_id": strategy_id,
            "strategy_type": strategy_type,
            "config_json": config_json,
        }
        data = self._post("/runtime/start", payload)
        return self._to_runtime_state(data)

    def stop_strategy(self, runtime_ref: str) -> RuntimeState:
        data = self._post("/runtime/stop", {"runtime_ref": runtime_ref})
        return self._to_runtime_state(data)

    def get_runtime(self, runtime_ref: str) -> RuntimeState:
        data = self._get(f"/runtime/{runtime_ref}")
        return self._to_runtime_state(data)

    def _post(self, path: str, payload: dict) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=self._build_auth_headers(),
                )
        except httpx.RequestError as exc:
            raise StrategySupervisorUnavailableError(f"worker-supervisor unavailable: {exc}") from exc

        if resp.status_code >= 500:
            raise StrategySupervisorUnavailableError(
                f"worker-supervisor server error ({resp.status_code}): {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise StrategySupervisorError(f"worker-supervisor error ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def _get(self, path: str) -> dict:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(f"{self.base_url}{path}", headers=self._build_auth_headers())
        except httpx.RequestError as exc:
            raise StrategySupervisorUnavailableError(f"worker-supervisor unavailable: {exc}") from exc

        if resp.status_code >= 500:
            raise StrategySupervisorUnavailableError(
                f"worker-supervisor server error ({resp.status_code}): {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise StrategySupervisorError(f"worker-supervisor error ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    @staticmethod
    def _to_runtime_state(data: dict) -> RuntimeState:
        return RuntimeState(
            runtime_ref=data["runtime_ref"],
            process_id=data.get("process_id"),
            status=data["status"],
            started_at=_parse_dt(data.get("started_at")),
            stopped_at=_parse_dt(data.get("stopped_at")),
            last_heartbeat=_parse_dt(data.get("last_heartbeat")),
            last_error=data.get("last_error"),
            last_event_seq=int(data.get("last_event_seq") or 0),
            last_event_type=data.get("last_event_type"),
            last_event_at=_parse_dt(data.get("last_event_at")),
            order_submitted_count=int(data.get("order_submitted_count") or 0),
            order_update_count=int(data.get("order_update_count") or 0),
            trade_fill_count=int(data.get("trade_fill_count") or 0),
            recent_events=(
                list(data.get("recent_events"))
                if isinstance(data.get("recent_events"), list)
                else []
            ),
        )

    def _build_auth_headers(self) -> dict[str, str]:
        if not self.shared_token:
            return {}
        return {"X-Supervisor-Token": self.shared_token}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _read_timeout_seconds(default: float) -> float:
    raw = os.getenv("SUPERVISOR_HTTP_TIMEOUT_SECONDS", str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    # Guardrail: keep minimum > 0 to avoid invalid httpx timeout.
    return value if value > 0 else default
