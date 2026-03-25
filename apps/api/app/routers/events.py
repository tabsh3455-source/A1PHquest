from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..deps import get_current_verified_user
from ..models import User
from ..schemas import EventReplayResponse
from ..ws_manager import WsManager

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/replay", response_model=EventReplayResponse)
def replay_events(
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    since_seconds: int | None = Query(default=None, ge=1, le=86_400),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Replay recently pushed user events for reconnecting websocket clients.

    Replay storage can be backed by in-memory history for single-instance dev
    or by the database for VPS/production deployments that need replica-safe
    event ordering across reconnects.
    """
    ws_manager: WsManager = request.app.state.ws_manager
    events = ws_manager.get_user_event_history(
        current_user.id,
        after_seq=after_seq,
        since_seconds=since_seconds,
        limit=limit,
    )
    next_after_seq = int(events[-1]["event_seq"]) if events else after_seq
    return EventReplayResponse(events=events, next_after_seq=next_after_seq)
