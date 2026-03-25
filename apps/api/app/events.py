from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_ws_event(
    *,
    event_type: str,
    resource_id: str | None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    flatten_payload: bool = True,
) -> dict[str, Any]:
    """
    Build a unified user event envelope for WebSocket push.

    Envelope fields:
    - type: event category name
    - timestamp: server-side UTC event time
    - resource_id: primary entity id for tracing
    - payload: event-specific data

    To preserve backward compatibility with existing frontend consumers,
    payload keys can still be copied to top-level when `flatten_payload=True`.
    """
    normalized_payload = dict(payload or {})
    event: dict[str, Any] = {
        "type": event_type,
        "timestamp": _utcnow().isoformat(),
        "resource_id": resource_id,
        "payload": normalized_payload,
    }
    if dedupe_key:
        # Optional idempotency key used by WsManager to skip duplicate pushes.
        event["dedupe_key"] = dedupe_key
    if flatten_payload:
        for key, value in normalized_payload.items():
            if key not in event:
                event[key] = value
    return event


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
