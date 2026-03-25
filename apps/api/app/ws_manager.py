from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import json
import threading
from typing import Any

from fastapi import WebSocket
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from .config import get_settings
from .db import SessionLocal
from .models import UserEvent, UserEventSequence

settings = get_settings()


class WsManager:
    def __init__(
        self,
        *,
        backend: str | None = None,
        session_factory: sessionmaker | None = None,
        history_size: int | None = None,
        dedupe_cache_size: int | None = None,
    ) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._backend = str(backend or settings.ws_replay_backend or "memory").strip().lower() or "memory"
        # Maintain per-user event sequence so frontend can apply deterministic ordering.
        self._event_sequences: dict[int, int] = defaultdict(int)
        # Keep bounded in-memory history for disconnect replay without DB overhead.
        self._history_size = max(int(history_size or settings.ws_replay_history_size), 1)
        self._history: dict[int, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._history_size)
        )
        # Deduplicate retried pushes by user-scoped dedupe key to keep replay
        # stream idempotent for disconnect recovery.
        self._dedupe_cache_size = max(int(dedupe_cache_size or settings.ws_dedupe_cache_size), 1)
        self._dedupe_sequences: dict[int, dict[str, int]] = defaultdict(dict)
        self._dedupe_order: dict[int, deque[str]] = defaultdict(deque)
        self._sequence_lock = threading.Lock()
        self._session_factory = session_factory or SessionLocal

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        if user_id in self._connections and websocket in self._connections[user_id]:
            self._connections[user_id].remove(websocket)

    async def push_to_user(self, user_id: int, event: dict) -> None:
        payload = dict(event)
        dedupe_key = str(payload.pop("dedupe_key", "")).strip() or None
        if self._backend == "db":
            stored_payload = self._persist_db_event(user_id, payload, dedupe_key=dedupe_key)
            if stored_payload is None:
                return
            payload = stored_payload
        else:
            payload = self._persist_memory_event(user_id, payload, dedupe_key=dedupe_key)
            if payload is None:
                return
        for ws in list(self._connections.get(user_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(user_id, ws)

    def _next_event_sequence(self, user_id: int) -> int:
        with self._sequence_lock:
            self._event_sequences[user_id] += 1
            return self._event_sequences[user_id]

    def _remember_dedupe_key(self, user_id: int, dedupe_key: str, seq: int) -> None:
        user_sequences = self._dedupe_sequences[user_id]
        if dedupe_key in user_sequences:
            return
        user_sequences[dedupe_key] = seq
        order = self._dedupe_order[user_id]
        order.append(dedupe_key)
        while len(order) > self._dedupe_cache_size:
            oldest = order.popleft()
            user_sequences.pop(oldest, None)

    def get_user_event_history(
        self,
        user_id: int,
        *,
        after_seq: int | None = None,
        since_seconds: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self._backend == "db":
            return self._get_db_event_history(
                user_id,
                after_seq=after_seq,
                since_seconds=since_seconds,
                limit=limit,
            )
        events = sorted(
            list(self._history.get(user_id, deque())),
            key=lambda event: int(event.get("event_seq", 0)),
        )
        if after_seq is not None:
            events = [event for event in events if int(event.get("event_seq", 0)) > after_seq]
        if since_seconds is not None and since_seconds > 0:
            threshold = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
            events = [
                event for event in events if _parse_timestamp(event.get("timestamp")) >= threshold
            ]
        if limit > 0:
            if after_seq is None and len(events) > limit:
                # First replay request defaults to the most recent window.
                events = events[-limit:]
            else:
                # Incremental replay keeps oldest-first ordering for pagination.
                events = events[:limit]
        return events

    def connection_count(self) -> int:
        return sum(len(items) for items in self._connections.values())

    def online_user_count(self) -> int:
        return sum(1 for items in self._connections.values() if items)

    def _persist_memory_event(
        self,
        user_id: int,
        payload: dict[str, Any],
        *,
        dedupe_key: str | None,
    ) -> dict[str, Any] | None:
        existing_seq: int | None = None
        assigned_seq: int
        with self._sequence_lock:
            if dedupe_key:
                existing_seq = self._dedupe_sequences[user_id].get(dedupe_key)
            if existing_seq is not None:
                return None
            self._event_sequences[user_id] += 1
            assigned_seq = self._event_sequences[user_id]
            if dedupe_key:
                self._remember_dedupe_key(user_id, dedupe_key, assigned_seq)
        normalized = _normalize_event_envelope(
            user_id,
            payload,
            assigned_seq=assigned_seq,
        )
        self._history[user_id].append(normalized)
        return normalized

    def _persist_db_event(
        self,
        user_id: int,
        payload: dict[str, Any],
        *,
        dedupe_key: str | None,
    ) -> dict[str, Any] | None:
        """
        Persist replayable WS events so reconnects and multi-instance API replicas
        share the same ordered event stream.
        """
        normalized = _normalize_event_envelope(user_id, payload)
        timestamp = _parse_timestamp(normalized.get("timestamp")).replace(tzinfo=None)
        event_type = str(normalized.get("type") or "event").strip() or "event"
        resource_id = _to_optional_text(normalized.get("resource_id"))
        body_payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}

        for _attempt in range(3):
            db = self._session_factory()
            try:
                with db.begin():
                    if dedupe_key:
                        existing = (
                            db.query(UserEvent)
                            .filter(
                                UserEvent.user_id == user_id,
                                UserEvent.dedupe_key == dedupe_key,
                            )
                            .first()
                        )
                        if existing:
                            return None

                    sequence_row = self._load_or_create_sequence_row(db, user_id)
                    sequence_row.last_event_seq = int(sequence_row.last_event_seq or 0) + 1
                    assigned_seq = int(sequence_row.last_event_seq)

                    db.add(
                        UserEvent(
                            user_id=user_id,
                            event_seq=assigned_seq,
                            event_type=event_type,
                            resource_id=resource_id,
                            payload_json=json.dumps(_json_safe(body_payload), ensure_ascii=False),
                            dedupe_key=dedupe_key,
                            created_at=timestamp,
                        )
                    )
                    self._prune_db_history(db, user_id)
                normalized["event_seq"] = assigned_seq
                return normalized
            except IntegrityError:
                db.rollback()
                # Concurrent writers can race on the sequence row or dedupe unique key.
                # Retry a few times so API replicas converge on one monotonic sequence.
                if dedupe_key:
                    existing = (
                        db.query(UserEvent)
                        .filter(
                            UserEvent.user_id == user_id,
                            UserEvent.dedupe_key == dedupe_key,
                        )
                        .first()
                    )
                    if existing:
                        return None
            finally:
                db.close()
        raise RuntimeError("failed to persist websocket event after retries")

    def _load_or_create_sequence_row(self, db, user_id: int) -> UserEventSequence:
        query = db.query(UserEventSequence).filter(UserEventSequence.user_id == user_id)
        bind = db.get_bind()
        if bind is not None and bind.dialect.name != "sqlite":
            query = query.with_for_update()
        sequence_row = query.first()
        if sequence_row:
            return sequence_row
        sequence_row = UserEventSequence(user_id=user_id, last_event_seq=0)
        db.add(sequence_row)
        db.flush()
        return sequence_row

    def _prune_db_history(self, db, user_id: int) -> None:
        stale_rows = (
            db.query(UserEvent.id)
            .filter(UserEvent.user_id == user_id)
            .order_by(UserEvent.event_seq.desc())
            .offset(self._history_size)
            .all()
        )
        stale_ids = [int(row[0]) for row in stale_rows]
        if not stale_ids:
            return
        db.query(UserEvent).filter(UserEvent.id.in_(stale_ids)).delete(synchronize_session=False)

    def _get_db_event_history(
        self,
        user_id: int,
        *,
        after_seq: int | None = None,
        since_seconds: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        db = self._session_factory()
        try:
            query = db.query(UserEvent).filter(UserEvent.user_id == user_id)
            if after_seq is not None:
                query = query.filter(UserEvent.event_seq > after_seq)
            if since_seconds is not None and since_seconds > 0:
                threshold = datetime.now(timezone.utc) - timedelta(seconds=since_seconds)
                query = query.filter(UserEvent.created_at >= threshold.replace(tzinfo=None))

            if limit > 0 and after_seq is None:
                rows = query.order_by(UserEvent.event_seq.desc()).limit(limit).all()
                rows.reverse()
            elif limit > 0:
                rows = query.order_by(UserEvent.event_seq.asc()).limit(limit).all()
            else:
                rows = query.order_by(UserEvent.event_seq.asc()).all()

            return [_user_event_to_payload(row) for row in rows]
        finally:
            db.close()


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _normalize_event_envelope(
    user_id: int,
    payload: dict[str, Any],
    *,
    assigned_seq: int | None = None,
) -> dict[str, Any]:
    normalized = _json_safe(dict(payload))
    timestamp_value = normalized.get("timestamp")
    timestamp = _parse_timestamp(timestamp_value)
    if timestamp == datetime.min.replace(tzinfo=timezone.utc):
        timestamp = datetime.now(timezone.utc)
    normalized["timestamp"] = timestamp.isoformat()
    normalized["user_id"] = user_id
    if assigned_seq is not None:
        normalized["event_seq"] = assigned_seq
    return normalized


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _user_event_to_payload(row: UserEvent) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "type": row.event_type,
        "timestamp": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "resource_id": row.resource_id,
        "user_id": int(row.user_id),
        "event_seq": int(row.event_seq),
        "payload": payload if isinstance(payload, dict) else {},
    }


def _to_optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
