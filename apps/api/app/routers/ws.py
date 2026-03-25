from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..db import SessionLocal
from ..deps import authenticate_access_token_user
from ..models import ExchangeAccount
from ..config import get_settings
from ..services.market_data import MarketDataError, MarketDataService
from ..ws_manager import WsManager

router = APIRouter(tags=["ws"])
settings = get_settings()


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    token = _extract_ws_token(websocket)
    if not token:
        await websocket.close(code=4401)
        return

    with SessionLocal() as db:
        try:
            user = authenticate_access_token_user(
                db=db,
                token=token,
                require_verified=True,
            )
            user_id = int(user.id)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 401)
            await websocket.close(code=4403 if status_code == 403 else 4401)
            return

    manager: WsManager = websocket.app.state.ws_manager
    market_data: MarketDataService = websocket.app.state.market_data_service
    connection_id = id(websocket)
    await manager.connect(user_id, websocket)
    try:
        while True:
            raw_message = await websocket.receive_text()
            await _handle_ws_message(
                websocket=websocket,
                user_id=user_id,
                connection_id=connection_id,
                market_data=market_data,
                raw_message=raw_message,
            )
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)
        await market_data.unsubscribe_connection(user_id, connection_id)
    except Exception:
        manager.disconnect(user_id, websocket)
        await market_data.unsubscribe_connection(user_id, connection_id)
        await websocket.close()


@router.websocket("/ws/market")
async def ws_public_market(websocket: WebSocket):
    await websocket.accept()
    market_data: MarketDataService = websocket.app.state.market_data_service
    connection_id = id(websocket)
    await market_data.register_public_connection(connection_id, websocket.send_json)
    try:
        while True:
            raw_message = await websocket.receive_text()
            await _handle_public_market_ws_message(
                websocket=websocket,
                connection_id=connection_id,
                market_data=market_data,
                raw_message=raw_message,
            )
    except WebSocketDisconnect:
        await market_data.unsubscribe_public_connection(connection_id)
    except Exception:
        await market_data.unsubscribe_public_connection(connection_id)
        await websocket.close()


def _extract_ws_token(websocket: WebSocket) -> str | None:
    """
    Resolve websocket auth token from headers first to reduce URL token leakage.
    """
    auth_header = str(websocket.headers.get("authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", maxsplit=1)[1].strip()
        if token:
            return token

    protocol_header = str(websocket.headers.get("sec-websocket-protocol") or "").strip()
    if protocol_header:
        protocols = [item.strip() for item in protocol_header.split(",") if item.strip()]
        if len(protocols) >= 2 and protocols[0].lower() == "bearer":
            return protocols[1]
        for item in protocols:
            if item.lower().startswith("bearer."):
                token = item.split(".", maxsplit=1)[1].strip()
                if token:
                    return token

    cookie_token = str(websocket.cookies.get(settings.auth_cookie_name) or "").strip()
    if cookie_token:
        return cookie_token

    if settings.ws_allow_query_token:
        query_token = str(websocket.query_params.get("token") or "").strip()
        if query_token:
            return query_token
    return None


async def _handle_ws_message(
    *,
    websocket: WebSocket,
    user_id: int,
    connection_id: int,
    market_data: MarketDataService,
    raw_message: str,
) -> None:
    message = str(raw_message or "").strip()
    if not message:
        return
    if message.lower() == "ping":
        await websocket.send_json({"type": "pong"})
        return

    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "ws_error", "message": "invalid_json"})
        return
    if not isinstance(payload, dict):
        await websocket.send_json({"type": "ws_error", "message": "invalid_message"})
        return

    action = str(payload.get("action") or payload.get("type") or "").strip().lower()
    if action in {"ping", "pong"}:
        await websocket.send_json({"type": "pong"})
        return
    if action not in {"subscribe_market", "unsubscribe_market"}:
        return

    try:
        exchange_account_id = int(payload.get("exchange_account_id") or 0)
    except (TypeError, ValueError):
        exchange_account_id = 0
    symbol = str(payload.get("symbol") or "").strip()
    interval = str(payload.get("interval") or "1m").strip()
    market_type = str(payload.get("market_type") or "spot").strip()

    if exchange_account_id <= 0 or not symbol:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": "exchange_account_id and symbol are required",
            }
        )
        return

    account = _get_owned_exchange_account(user_id=user_id, exchange_account_id=exchange_account_id)
    if not account:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": f"exchange_account_id {exchange_account_id} not found",
            }
        )
        return

    if account.exchange not in {"binance", "okx"}:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": f"exchange '{account.exchange}' is not supported for market charts",
            }
        )
        return

    try:
        if action == "subscribe_market":
            key = await market_data.subscribe(
                user_id=user_id,
                connection_id=connection_id,
                exchange=account.exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                is_testnet=account.is_testnet,
            )
        else:
            key = await market_data.unsubscribe(
                user_id=user_id,
                connection_id=connection_id,
                exchange=account.exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                is_testnet=account.is_testnet,
            )
    except MarketDataError as exc:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": str(exc),
            }
        )
        return

    await websocket.send_json(
        {
            "type": "market_subscription_ack",
            "action": action,
            "exchange_account_id": exchange_account_id,
            "exchange": key.exchange,
            "market_type": key.market_type,
            "symbol": key.symbol,
            "interval": key.interval,
            "is_testnet": key.is_testnet,
        }
    )
    if action == "subscribe_market":
        await market_data.send_subscription_status(user_id, key)


async def _handle_public_market_ws_message(
    *,
    websocket: WebSocket,
    connection_id: int,
    market_data: MarketDataService,
    raw_message: str,
) -> None:
    message = str(raw_message or "").strip()
    if not message:
        return
    if message.lower() == "ping":
        await websocket.send_json({"type": "pong"})
        return

    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        await websocket.send_json({"type": "market_subscription_error", "message": "invalid_json"})
        return
    if not isinstance(payload, dict):
        await websocket.send_json({"type": "market_subscription_error", "message": "invalid_message"})
        return

    action = str(payload.get("action") or payload.get("type") or "").strip().lower()
    if action in {"ping", "pong"}:
        await websocket.send_json({"type": "pong"})
        return
    if action not in {"subscribe_market", "unsubscribe_market"}:
        return

    exchange = str(payload.get("exchange") or "").strip().lower()
    market_type = str(payload.get("market_type") or "spot").strip().lower()
    symbol = str(payload.get("symbol") or "").strip()
    interval = str(payload.get("interval") or "1m").strip()
    if not exchange or not symbol:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": "exchange and symbol are required",
            }
        )
        return

    try:
        if action == "subscribe_market":
            key = await market_data.subscribe_public(
                connection_id=connection_id,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                is_testnet=False,
            )
        else:
            key = await market_data.unsubscribe_public(
                connection_id=connection_id,
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                is_testnet=False,
            )
    except MarketDataError as exc:
        await websocket.send_json(
            {
                "type": "market_subscription_error",
                "action": action,
                "message": str(exc),
            }
        )
        return

    await websocket.send_json(
        {
            "type": "market_subscription_ack",
            "action": action,
            "exchange": key.exchange,
            "market_type": key.market_type,
            "symbol": key.symbol,
            "interval": key.interval,
            "is_testnet": key.is_testnet,
        }
    )
    if action == "subscribe_market":
        await market_data.send_public_subscription_status(connection_id, key)


def _get_owned_exchange_account(*, user_id: int, exchange_account_id: int) -> ExchangeAccount | None:
    db = SessionLocal()
    try:
        return (
            db.query(ExchangeAccount)
            .filter(
                ExchangeAccount.user_id == user_id,
                ExchangeAccount.id == exchange_account_id,
            )
            .first()
        )
    finally:
        db.close()
