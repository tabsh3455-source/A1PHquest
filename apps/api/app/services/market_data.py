from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, Awaitable, Callable

import httpx
from websockets.asyncio.client import ClientConnection, connect

from ..config import get_settings
from ..events import build_ws_event
from ..ws_manager import WsManager

settings = get_settings()
logger = logging.getLogger(__name__)

SUPPORTED_MARKET_INTERVALS: dict[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
LIVE_MARKET_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h")

_BINANCE_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

_OKX_INTERVALS = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "4h": "4H",
    "1d": "1Dutc",
}

_BINANCE_SPOT_WS_URL = "wss://stream.binance.com:9443/ws"
_BINANCE_TESTNET_WS_URL = "wss://stream.testnet.binance.vision/ws"
_BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"
_BINANCE_FUTURES_TESTNET_WS_URL = "wss://stream.binancefuture.com/ws"
_OKX_PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
_OKX_DEMO_PUBLIC_WS_URL = "wss://wspap.okx.com:8443/ws/v5/public?brokerId=9999"
_STREAM_HEALTH_STATES = {"connecting", "live", "reconnecting", "stale", "error"}


class MarketDataError(RuntimeError):
    """Raised when public market data cannot be resolved."""


@dataclass(frozen=True, slots=True)
class MarketSymbolKey:
    exchange: str
    market_type: str
    symbol: str
    is_testnet: bool

    @property
    def resource_id(self) -> str:
        return f"{self.exchange}:{self.market_type}:{self.symbol}:{int(self.is_testnet)}"


@dataclass(frozen=True, slots=True)
class MarketStreamKey:
    exchange: str
    market_type: str
    symbol: str
    interval: str
    is_testnet: bool

    @property
    def resource_id(self) -> str:
        return f"{self.exchange}:{self.market_type}:{self.symbol}:{self.interval}:{int(self.is_testnet)}"

    @property
    def symbol_key(self) -> MarketSymbolKey:
        return MarketSymbolKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            is_testnet=self.is_testnet,
        )


@dataclass(frozen=True, slots=True)
class TradeTick:
    exchange: str
    market_type: str
    symbol: str
    price: float
    size: float
    ts_ms: int
    is_testnet: bool

    @property
    def symbol_key(self) -> MarketSymbolKey:
        return MarketSymbolKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=self.symbol,
            is_testnet=self.is_testnet,
        )


@dataclass(slots=True)
class CandleCacheEntry:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = False
    source: str = "ws"

    def to_market_kline(self) -> dict[str, Any]:
        return {
            "time": int(self.time),
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": float(self.volume),
        }

    def copy(self) -> CandleCacheEntry:
        return CandleCacheEntry(
            time=int(self.time),
            open=float(self.open),
            high=float(self.high),
            low=float(self.low),
            close=float(self.close),
            volume=float(self.volume),
            is_closed=bool(self.is_closed),
            source=str(self.source or "ws"),
        )


@dataclass(slots=True)
class MarketStreamHealth:
    status: str
    updated_at: datetime
    message: str | None = None


class PublicMarketDataClient:
    def __init__(self, *, timeout_seconds: float | None = None) -> None:
        self._timeout_seconds = float(timeout_seconds or max(settings.gateway_validate_timeout_seconds, 5))

    async def fetch_history(
        self,
        *,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        limit: int,
        is_testnet: bool,
    ) -> list[dict[str, Any]]:
        normalized_exchange = normalize_market_exchange(exchange)
        normalized_market_type = normalize_market_type(market_type)
        normalized_interval = normalize_market_interval(interval)
        normalized_symbol = normalize_market_symbol(normalized_exchange, symbol, normalized_market_type)
        normalized_limit = max(min(int(limit), 1000), 1)

        if normalized_exchange == "binance":
            return await self._fetch_binance_history(
                market_type=normalized_market_type,
                symbol=normalized_symbol,
                interval=normalized_interval,
                limit=normalized_limit,
                is_testnet=is_testnet,
            )
        if normalized_exchange == "okx":
            return await self._fetch_okx_history(
                market_type=normalized_market_type,
                symbol=normalized_symbol,
                interval=normalized_interval,
                limit=normalized_limit,
            )
        raise MarketDataError(f"Exchange '{normalized_exchange}' is not supported for market data")

    async def _fetch_binance_history(
        self,
        *,
        market_type: str,
        symbol: str,
        interval: str,
        limit: int,
        is_testnet: bool,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            _binance_public_base_url(is_testnet=is_testnet, market_type=market_type),
            "/fapi/v1/klines" if market_type == "perp" else "/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": _BINANCE_INTERVALS[interval],
                "limit": limit,
            },
        )
        if not isinstance(payload, list):
            raise MarketDataError(f"Binance klines returned invalid payload for {symbol}")

        candles: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, list) or len(row) < 6:
                continue
            candles.append(
                _build_candle(
                    time_seconds=_to_int(row[0]) // 1000,
                    open_price=_to_float(row[1]),
                    high_price=_to_float(row[2]),
                    low_price=_to_float(row[3]),
                    close_price=_to_float(row[4]),
                    volume=_to_float(row[5]),
                )
            )
        return candles

    async def _fetch_okx_history(
        self,
        *,
        market_type: str,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            settings.okx_base_url,
            "/api/v5/market/candles",
            params={
                "instId": symbol,
                "bar": _OKX_INTERVALS[interval],
                "limit": min(limit, 300),
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MarketDataError(f"OKX candles returned invalid payload for {symbol}")

        candles: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            candles.append(
                _build_candle(
                    time_seconds=_to_int(row[0]) // 1000,
                    open_price=_to_float(row[1]),
                    high_price=_to_float(row[2]),
                    low_price=_to_float(row[3]),
                    close_price=_to_float(row[4]),
                    volume=_to_float(row[5]),
                )
            )
        candles.sort(key=lambda item: int(item["time"]))
        return candles[-limit:]

    async def _request_json(
        self,
        base_url: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=self._timeout_seconds) as client:
                response = await client.get(path, params=params)
        except httpx.RequestError as exc:
            raise MarketDataError(f"market data request failed: {exc}") from exc

        if response.status_code >= 400:
            raise MarketDataError(
                f"market data request failed ({response.status_code}): {response.text[:240]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise MarketDataError("market data response was not valid JSON") from exc


class ExchangeStreamManager:
    def __init__(
        self,
        *,
        exchange: str,
        market_type: str,
        is_testnet: bool,
        ws_url: str,
        idle_timeout_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        on_trade_tick: Callable[[TradeTick], Awaitable[None]],
        on_status_change: Callable[[MarketSymbolKey, str, str | None], Awaitable[None]],
    ) -> None:
        self.exchange = normalize_market_exchange(exchange)
        self.market_type = normalize_market_type(market_type)
        self.is_testnet = bool(is_testnet)
        self._ws_url = ws_url
        self._idle_timeout_seconds = max(float(idle_timeout_seconds), 5.0)
        self._reconnect_base_seconds = max(float(reconnect_base_seconds), 0.5)
        self._reconnect_max_seconds = max(float(reconnect_max_seconds), self._reconnect_base_seconds)
        self._on_trade_tick = on_trade_tick
        self._on_status_change = on_status_change
        self._desired_symbols: set[str] = set()
        self._subscribed_symbols: set[str] = set()
        self._pending_subscribes: set[str] = set()
        self._pending_unsubscribes: set[str] = set()
        self._pending_requests: dict[int, tuple[str, str]] = {}
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._connection: ClientConnection | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(),
            name=f"a1phquest-market-stream:{self.exchange}:{self.market_type}:{int(self.is_testnet)}",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def ensure_symbol(self, symbol: str) -> None:
        normalized_symbol = normalize_market_symbol(self.exchange, symbol, self.market_type)
        async with self._lock:
            self._desired_symbols.add(normalized_symbol)
        await self.start()
        self._wake_event.set()

    async def release_symbol(self, symbol: str) -> None:
        normalized_symbol = normalize_market_symbol(self.exchange, symbol, self.market_type)
        async with self._lock:
            self._desired_symbols.discard(normalized_symbol)
        self._wake_event.set()

    async def _run(self) -> None:
        backoff_seconds = self._reconnect_base_seconds
        has_connected = False

        while not self._stop_event.is_set():
            desired_symbols = await self._snapshot_desired_symbols()
            if not desired_symbols:
                self._wake_event.clear()
                await self._wait_for_signal()
                backoff_seconds = self._reconnect_base_seconds
                has_connected = False
                continue

            await self._update_symbols_status(
                desired_symbols,
                "reconnecting" if has_connected else "connecting",
            )
            try:
                async with connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=10,
                    close_timeout=10,
                    max_queue=None,
                    compression=None,
                ) as websocket:
                    self._connection = websocket
                    self._subscribed_symbols.clear()
                    self._pending_subscribes.clear()
                    self._pending_unsubscribes.clear()
                    self._pending_requests.clear()
                    await self._sync_subscriptions(websocket)
                    backoff_seconds = self._reconnect_base_seconds
                    has_connected = True

                    while not self._stop_event.is_set():
                        event_type, raw_message = await self._wait_for_message_or_signal(websocket)
                        if event_type == "stop":
                            return
                        if event_type == "wake":
                            await self._sync_subscriptions(websocket)
                            if not await self._snapshot_desired_symbols():
                                await websocket.close()
                                break
                            continue
                        if event_type == "idle":
                            await self._update_symbols_status(await self._snapshot_desired_symbols(), "stale")
                            await self._send_keepalive(websocket)
                            continue

                        await self._handle_message(websocket, raw_message)
                        await self._sync_subscriptions(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                desired_symbols = await self._snapshot_desired_symbols()
                if desired_symbols:
                    await self._update_symbols_status(
                        desired_symbols,
                        "reconnecting",
                        _truncate_error_message(exc),
                    )
                logger.warning(
                    "market stream connection failed for %s:%s: %s",
                    self.exchange,
                    int(self.is_testnet),
                    exc,
                )
                await self._sleep_for_retry(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, self._reconnect_max_seconds)
            finally:
                self._connection = None
                self._subscribed_symbols.clear()
                self._pending_subscribes.clear()
                self._pending_unsubscribes.clear()
                self._pending_requests.clear()

    async def _wait_for_signal(self) -> None:
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        try:
            done, _ = await asyncio.wait(
                {stop_task, wake_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wake_task in done:
                self._wake_event.clear()
        finally:
            for task in (stop_task, wake_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    async def _wait_for_message_or_signal(self, websocket: ClientConnection) -> tuple[str, Any]:
        recv_task = asyncio.create_task(websocket.recv())
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        try:
            done, _ = await asyncio.wait(
                {recv_task, stop_task, wake_task},
                timeout=self._idle_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                return "idle", None
            if stop_task in done:
                return "stop", None
            if wake_task in done:
                self._wake_event.clear()
                return "wake", None
            return "message", recv_task.result()
        finally:
            for task in (recv_task, stop_task, wake_task):
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

    async def _sync_subscriptions(self, websocket: ClientConnection) -> None:
        desired_symbols = await self._snapshot_desired_symbols()
        to_subscribe = [
            symbol
            for symbol in sorted(desired_symbols)
            if symbol not in self._subscribed_symbols and symbol not in self._pending_subscribes
        ]
        to_unsubscribe = [
            symbol
            for symbol in sorted(self._subscribed_symbols)
            if symbol not in desired_symbols and symbol not in self._pending_unsubscribes
        ]

        for symbol in to_subscribe:
            await self._send_subscribe(websocket, symbol)
        for symbol in to_unsubscribe:
            await self._send_unsubscribe(websocket, symbol)

    async def _handle_message(self, websocket: ClientConnection, raw_message: Any) -> None:
        payload = _deserialize_ws_message(raw_message)
        if await self._handle_protocol_message(websocket, payload):
            return
        for tick in self._extract_trade_ticks(payload):
            await self._mark_symbol_live(websocket, tick.symbol)
            await self._on_trade_tick(tick)

    async def _send_subscribe(self, websocket: ClientConnection, symbol: str) -> None:
        request_id = self._next_request_id()
        async with self._lock:
            self._pending_subscribes.add(symbol)
            self._pending_requests[request_id] = ("subscribe", symbol)
        await websocket.send(json.dumps(self._build_subscribe_payload(symbol, request_id)))

    async def _send_unsubscribe(self, websocket: ClientConnection, symbol: str) -> None:
        request_id = self._next_request_id()
        async with self._lock:
            self._pending_unsubscribes.add(symbol)
            self._pending_requests[request_id] = ("unsubscribe", symbol)
        await websocket.send(json.dumps(self._build_unsubscribe_payload(symbol, request_id)))

    async def _mark_symbol_live(self, websocket: ClientConnection, symbol: str) -> None:
        normalized_symbol = normalize_market_symbol(self.exchange, symbol, self.market_type)
        async with self._lock:
            desired = normalized_symbol in self._desired_symbols
            self._pending_subscribes.discard(normalized_symbol)
            if desired:
                self._subscribed_symbols.add(normalized_symbol)
        if desired:
            await self._on_status_change(self._symbol_key(normalized_symbol), "live", None)
            return
        await self._send_unsubscribe_if_needed(websocket, normalized_symbol)

    async def _mark_symbol_unsubscribed(self, symbol: str) -> None:
        normalized_symbol = normalize_market_symbol(self.exchange, symbol, self.market_type)
        async with self._lock:
            self._pending_unsubscribes.discard(normalized_symbol)
            self._subscribed_symbols.discard(normalized_symbol)

    async def _emit_symbol_error(self, symbol: str, message: str) -> None:
        normalized_symbol = normalize_market_symbol(self.exchange, symbol, self.market_type)
        async with self._lock:
            self._pending_subscribes.discard(normalized_symbol)
            self._pending_unsubscribes.discard(normalized_symbol)
        await self._on_status_change(self._symbol_key(normalized_symbol), "error", message)

    async def _send_unsubscribe_if_needed(self, websocket: ClientConnection, symbol: str) -> None:
        async with self._lock:
            should_unsubscribe = symbol in self._subscribed_symbols and symbol not in self._pending_unsubscribes
        if should_unsubscribe:
            await self._send_unsubscribe(websocket, symbol)

    async def _snapshot_desired_symbols(self) -> set[str]:
        async with self._lock:
            return set(self._desired_symbols)

    async def _update_symbols_status(
        self,
        symbols: set[str],
        status: str,
        message: str | None = None,
    ) -> None:
        for symbol in sorted(symbols):
            await self._on_status_change(
                self._symbol_key(symbol),
                status,
                message,
            )

    async def _sleep_for_retry(self, delay_seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay_seconds)
        except asyncio.TimeoutError:
            pass

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _symbol_key(self, symbol: str) -> MarketSymbolKey:
        return MarketSymbolKey(
            exchange=self.exchange,
            market_type=self.market_type,
            symbol=normalize_market_symbol(self.exchange, symbol, self.market_type),
            is_testnet=self.is_testnet,
        )

    def _build_subscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        raise NotImplementedError

    def _build_unsubscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        raise NotImplementedError

    async def _handle_protocol_message(self, websocket: ClientConnection, payload: Any) -> bool:
        raise NotImplementedError

    def _extract_trade_ticks(self, payload: Any) -> list[TradeTick]:
        raise NotImplementedError

    async def _send_keepalive(self, websocket: ClientConnection) -> None:
        raise NotImplementedError


class BinanceStreamManager(ExchangeStreamManager):
    def __init__(
        self,
        *,
        market_type: str,
        is_testnet: bool,
        idle_timeout_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        on_trade_tick: Callable[[TradeTick], Awaitable[None]],
        on_status_change: Callable[[MarketSymbolKey, str, str | None], Awaitable[None]],
    ) -> None:
        super().__init__(
            exchange="binance",
            market_type=market_type,
            is_testnet=is_testnet,
            ws_url=_resolve_binance_ws_url(is_testnet=is_testnet, market_type=market_type),
            idle_timeout_seconds=idle_timeout_seconds,
            reconnect_base_seconds=reconnect_base_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
            on_trade_tick=on_trade_tick,
            on_status_change=on_status_change,
        )

    def _build_subscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        return {
            "method": "SUBSCRIBE",
            "params": [f"{symbol.lower()}@trade"],
            "id": request_id,
        }

    def _build_unsubscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        return {
            "method": "UNSUBSCRIBE",
            "params": [f"{symbol.lower()}@trade"],
            "id": request_id,
        }

    async def _handle_protocol_message(self, websocket: ClientConnection, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        request_id = _to_int(payload.get("id"), default=-1)
        if request_id >= 0 and (request_id in self._pending_requests or "result" in payload or "code" in payload):
            action, symbol = self._pending_requests.pop(request_id, ("", ""))
            if payload.get("result") is None and action == "subscribe" and symbol:
                await self._mark_symbol_live(websocket, symbol)
                return True
            if payload.get("result") is None and action == "unsubscribe" and symbol:
                await self._mark_symbol_unsubscribed(symbol)
                return True
            if symbol:
                await self._emit_symbol_error(
                    symbol,
                    str(payload.get("msg") or payload.get("message") or "subscription_error"),
                )
                return True

        return payload.get("e") != "trade"

    def _extract_trade_ticks(self, payload: Any) -> list[TradeTick]:
        if not isinstance(payload, dict) or payload.get("e") != "trade":
            return []
        symbol = normalize_market_symbol(self.exchange, str(payload.get("s") or ""), self.market_type)
        price = _to_float(payload.get("p"))
        size = _to_float(payload.get("q"))
        ts_ms = _to_int(payload.get("T") or payload.get("E"))
        if not symbol or price <= 0 or ts_ms <= 0:
            return []
        return [
            TradeTick(
                exchange=self.exchange,
                market_type=self.market_type,
                symbol=symbol,
                price=price,
                size=max(size, 0.0),
                ts_ms=ts_ms,
                is_testnet=self.is_testnet,
            )
        ]

    async def _send_keepalive(self, websocket: ClientConnection) -> None:
        pong_waiter = await websocket.ping()
        await asyncio.wait_for(pong_waiter, timeout=10)


class OkxStreamManager(ExchangeStreamManager):
    def __init__(
        self,
        *,
        market_type: str,
        is_testnet: bool,
        idle_timeout_seconds: float,
        reconnect_base_seconds: float,
        reconnect_max_seconds: float,
        on_trade_tick: Callable[[TradeTick], Awaitable[None]],
        on_status_change: Callable[[MarketSymbolKey, str, str | None], Awaitable[None]],
    ) -> None:
        super().__init__(
            exchange="okx",
            market_type=market_type,
            is_testnet=is_testnet,
            ws_url=_OKX_DEMO_PUBLIC_WS_URL if is_testnet else _OKX_PUBLIC_WS_URL,
            idle_timeout_seconds=idle_timeout_seconds,
            reconnect_base_seconds=reconnect_base_seconds,
            reconnect_max_seconds=reconnect_max_seconds,
            on_trade_tick=on_trade_tick,
            on_status_change=on_status_change,
        )

    def _build_subscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        return {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": symbol}],
        }

    def _build_unsubscribe_payload(self, symbol: str, request_id: int) -> dict[str, Any]:
        return {
            "op": "unsubscribe",
            "args": [{"channel": "trades", "instId": symbol}],
        }

    async def _handle_protocol_message(self, websocket: ClientConnection, payload: Any) -> bool:
        if payload == "pong":
            return True
        if not isinstance(payload, dict):
            return False

        event = str(payload.get("event") or "").strip().lower()
        arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
        symbol = normalize_market_symbol(self.exchange, str(arg.get("instId") or ""), self.market_type) if arg else ""

        if event == "subscribe" and symbol:
            await self._mark_symbol_live(websocket, symbol)
            return True
        if event == "unsubscribe" and symbol:
            await self._mark_symbol_unsubscribed(symbol)
            return True
        if event == "error":
            message = str(payload.get("msg") or payload.get("message") or "subscription_error")
            if symbol:
                await self._emit_symbol_error(symbol, message)
            else:
                for pending_action, pending_symbol in list(self._pending_requests.values()):
                    if pending_action == "subscribe":
                        await self._emit_symbol_error(pending_symbol, message)
            return True

        if arg and str(arg.get("channel") or "").strip().lower() == "trades":
            return False
        return True

    def _extract_trade_ticks(self, payload: Any) -> list[TradeTick]:
        if not isinstance(payload, dict):
            return []
        arg = payload.get("arg") if isinstance(payload.get("arg"), dict) else {}
        if str(arg.get("channel") or "").strip().lower() != "trades":
            return []

        data = payload.get("data")
        if not isinstance(data, list):
            return []

        ticks: list[TradeTick] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            symbol = normalize_market_symbol(
                self.exchange,
                str(row.get("instId") or arg.get("instId") or ""),
                self.market_type,
            )
            price = _to_float(row.get("px"))
            size = _to_float(row.get("sz"))
            ts_ms = _to_int(row.get("ts"))
            if not symbol or price <= 0 or ts_ms <= 0:
                continue
            ticks.append(
                TradeTick(
                    exchange=self.exchange,
                    market_type=self.market_type,
                    symbol=symbol,
                    price=price,
                    size=max(size, 0.0),
                    ts_ms=ts_ms,
                    is_testnet=self.is_testnet,
                )
            )
        return ticks

    async def _send_keepalive(self, websocket: ClientConnection) -> None:
        await websocket.send("ping")


class MarketDataService:
    def __init__(
        self,
        *,
        ws_manager: WsManager,
        market_client: PublicMarketDataClient | None = None,
        reconnect_base_seconds: float | None = None,
        reconnect_max_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        cache_size: int | None = None,
        rest_backfill_limit: int | None = None,
    ) -> None:
        self._ws_manager = ws_manager
        self._market_client = market_client or PublicMarketDataClient()
        self._reconnect_base_seconds = float(
            reconnect_base_seconds or settings.market_ws_reconnect_base_seconds
        )
        self._reconnect_max_seconds = float(
            reconnect_max_seconds or settings.market_ws_reconnect_max_seconds
        )
        self._idle_timeout_seconds = float(
            idle_timeout_seconds or settings.market_ws_idle_timeout_seconds
        )
        self._cache_size = max(int(cache_size or settings.market_candle_cache_size), 10)
        self._rest_backfill_limit = max(
            int(rest_backfill_limit or settings.market_rest_backfill_limit),
            1,
        )
        self._lock = asyncio.Lock()
        self._connection_streams: dict[tuple[int, int], set[MarketStreamKey]] = {}
        self._stream_subscribers: dict[MarketStreamKey, set[tuple[int, int]]] = {}
        self._public_connection_streams: dict[int, set[MarketStreamKey]] = {}
        self._public_stream_subscribers: dict[MarketStreamKey, set[int]] = {}
        self._public_senders: dict[int, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self._candle_caches: dict[MarketStreamKey, deque[CandleCacheEntry]] = {}
        self._symbol_health: dict[MarketSymbolKey, MarketStreamHealth] = {}
        self._managers: dict[tuple[str, str, bool], ExchangeStreamManager] = {}
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for exchange_name in ("binance", "okx"):
            for market_type in ("spot", "perp"):
                for is_testnet in (False, True):
                    manager = self._ensure_manager(
                        exchange=exchange_name,
                        market_type=market_type,
                        is_testnet=is_testnet,
                    )
                    await manager.start()

    async def stop(self) -> None:
        managers = list(self._managers.values())
        self._started = False
        await asyncio.gather(*(manager.stop() for manager in managers), return_exceptions=True)

    def current_runtime_config(self) -> dict[str, float | int]:
        return {
            "market_ws_reconnect_base_seconds": float(self._reconnect_base_seconds),
            "market_ws_reconnect_max_seconds": float(self._reconnect_max_seconds),
            "market_ws_idle_timeout_seconds": float(self._idle_timeout_seconds),
            "market_candle_cache_size": int(self._cache_size),
            "market_rest_backfill_limit": int(self._rest_backfill_limit),
        }

    async def apply_runtime_config(self, config: dict[str, Any]) -> dict[str, float | int]:
        normalized = normalize_market_runtime_config(config)
        active_symbol_keys: list[MarketSymbolKey] = []

        async with self._lock:
            self._reconnect_base_seconds = float(normalized["market_ws_reconnect_base_seconds"])
            self._reconnect_max_seconds = float(normalized["market_ws_reconnect_max_seconds"])
            self._idle_timeout_seconds = float(normalized["market_ws_idle_timeout_seconds"])
            self._rest_backfill_limit = int(normalized["market_rest_backfill_limit"])
            self._cache_size = int(normalized["market_candle_cache_size"])
            self._resize_caches_locked()
            managers = list(self._managers.values())
            self._managers = {}
            if self._started:
                active_symbol_keys = sorted(
                    {
                        key.symbol_key
                        for key, subscribers in self._stream_subscribers.items()
                        if subscribers
                    }
                    | {
                        key.symbol_key
                        for key, subscribers in self._public_stream_subscribers.items()
                        if subscribers
                    },
                    key=lambda item: (item.exchange, item.market_type, int(item.is_testnet), item.symbol),
                )

        await asyncio.gather(*(manager.stop() for manager in managers), return_exceptions=True)

        if self._started:
            for symbol_key in active_symbol_keys:
                manager = self._ensure_manager(
                    exchange=symbol_key.exchange,
                    market_type=symbol_key.market_type,
                    is_testnet=symbol_key.is_testnet,
                )
                await manager.start()
                await manager.ensure_symbol(symbol_key.symbol)

        return normalized

    async def fetch_history(
        self,
        *,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        limit: int,
        is_testnet: bool,
    ) -> list[dict[str, Any]]:
        key = MarketStreamKey(
            exchange=normalize_market_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_market_symbol(exchange, symbol, market_type),
            interval=normalize_market_interval(interval),
            is_testnet=bool(is_testnet),
        )
        normalized_limit = max(min(int(limit), 1000), 1)

        if key.interval not in LIVE_MARKET_INTERVALS:
            return await self._market_client.fetch_history(
                exchange=key.exchange,
                market_type=key.market_type,
                symbol=key.symbol,
                interval=key.interval,
                limit=normalized_limit,
                is_testnet=key.is_testnet,
            )

        cached = await self._snapshot_cache(key)
        if len(cached) >= normalized_limit:
            return [entry.to_market_kline() for entry in cached[-normalized_limit:]]

        rest_limit = min(max(normalized_limit, 2), self._rest_backfill_limit)
        history = await self._market_client.fetch_history(
            exchange=key.exchange,
            market_type=key.market_type,
            symbol=key.symbol,
            interval=key.interval,
            limit=rest_limit,
            is_testnet=key.is_testnet,
        )
        await self._merge_rest_seed(key=key, candles=history)
        cached = await self._snapshot_cache(key)
        if cached:
            return [entry.to_market_kline() for entry in cached[-normalized_limit:]]
        return history[-normalized_limit:]

    async def subscribe(
        self,
        *,
        user_id: int,
        connection_id: int,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        is_testnet: bool,
    ) -> MarketStreamKey:
        key = MarketStreamKey(
            exchange=normalize_market_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_market_symbol(exchange, symbol, market_type),
            interval=normalize_live_market_interval(interval),
            is_testnet=bool(is_testnet),
        )
        connection_key = (user_id, connection_id)

        async with self._lock:
            self._connection_streams.setdefault(connection_key, set()).add(key)
            self._stream_subscribers.setdefault(key, set()).add(connection_key)
            self._candle_caches.setdefault(key, deque(maxlen=self._cache_size))
            self._symbol_health.setdefault(
                key.symbol_key,
                MarketStreamHealth(
                    status="connecting",
                    updated_at=datetime.now(timezone.utc),
                    message=None,
                ),
            )

        manager = self._ensure_manager(exchange=key.exchange, market_type=key.market_type, is_testnet=key.is_testnet)
        await manager.start()
        await manager.ensure_symbol(key.symbol)
        return key

    async def unsubscribe(
        self,
        *,
        user_id: int,
        connection_id: int,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        is_testnet: bool,
    ) -> MarketStreamKey:
        key = MarketStreamKey(
            exchange=normalize_market_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_market_symbol(exchange, symbol, market_type),
            interval=normalize_live_market_interval(interval),
            is_testnet=bool(is_testnet),
        )
        await self._remove_subscription((user_id, connection_id), key)
        return key

    async def unsubscribe_connection(self, user_id: int, connection_id: int) -> None:
        connection_key = (user_id, connection_id)
        async with self._lock:
            keys = list(self._connection_streams.pop(connection_key, set()))
        for key in keys:
            await self._remove_subscription(connection_key, key)

    async def register_public_connection(
        self,
        connection_id: int,
        sender: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        async with self._lock:
            self._public_senders[connection_id] = sender

    async def unsubscribe_public_connection(self, connection_id: int) -> None:
        async with self._lock:
            self._public_senders.pop(connection_id, None)
            keys = list(self._public_connection_streams.pop(connection_id, set()))
        for key in keys:
            await self._remove_public_subscription(connection_id, key)

    async def subscribe_public(
        self,
        *,
        connection_id: int,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        is_testnet: bool = False,
    ) -> MarketStreamKey:
        key = MarketStreamKey(
            exchange=normalize_market_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_market_symbol(exchange, symbol, market_type),
            interval=normalize_live_market_interval(interval),
            is_testnet=bool(is_testnet),
        )
        async with self._lock:
            self._public_connection_streams.setdefault(connection_id, set()).add(key)
            self._public_stream_subscribers.setdefault(key, set()).add(connection_id)
            self._candle_caches.setdefault(key, deque(maxlen=self._cache_size))
            self._symbol_health.setdefault(
                key.symbol_key,
                MarketStreamHealth(status="connecting", updated_at=datetime.now(timezone.utc), message=None),
            )
        manager = self._ensure_manager(exchange=key.exchange, market_type=key.market_type, is_testnet=key.is_testnet)
        await manager.start()
        await manager.ensure_symbol(key.symbol)
        return key

    async def unsubscribe_public(
        self,
        *,
        connection_id: int,
        exchange: str,
        market_type: str = "spot",
        symbol: str,
        interval: str,
        is_testnet: bool = False,
    ) -> MarketStreamKey:
        key = MarketStreamKey(
            exchange=normalize_market_exchange(exchange),
            market_type=normalize_market_type(market_type),
            symbol=normalize_market_symbol(exchange, symbol, market_type),
            interval=normalize_live_market_interval(interval),
            is_testnet=bool(is_testnet),
        )
        await self._remove_public_subscription(connection_id, key)
        return key

    async def send_subscription_status(self, user_id: int, key: MarketStreamKey) -> None:
        async with self._lock:
            health = self._symbol_health.get(
                key.symbol_key,
                MarketStreamHealth(
                    status="connecting",
                    updated_at=datetime.now(timezone.utc),
                    message=None,
                ),
            )
        await self._push_stream_status(
            user_id=user_id,
            key=key,
            health=health,
        )

    async def send_public_subscription_status(self, connection_id: int, key: MarketStreamKey) -> None:
        async with self._lock:
            health = self._symbol_health.get(
                key.symbol_key,
                MarketStreamHealth(status="connecting", updated_at=datetime.now(timezone.utc), message=None),
            )
        await self._push_public_stream_status(connection_id=connection_id, key=key, health=health)

    async def ingest_trade_tick(self, tick: TradeTick) -> None:
        await self._apply_trade_tick(tick)

    async def update_symbol_status(
        self,
        symbol_key: MarketSymbolKey,
        status: str,
        message: str | None = None,
    ) -> None:
        normalized_status = normalize_market_stream_status(status)
        should_backfill = False

        async with self._lock:
            previous = self._symbol_health.get(symbol_key)
            if (
                previous is not None
                and previous.status == normalized_status
                and str(previous.message or "") == str(message or "")
            ):
                return
            if normalized_status == "live" and previous is not None and previous.status in {
                "stale",
                "reconnecting",
                "error",
            }:
                should_backfill = True
            health = MarketStreamHealth(
                status=normalized_status,
                updated_at=datetime.now(timezone.utc),
                message=message,
            )
            self._symbol_health[symbol_key] = health
            active_keys = [
                key
                for key in self._stream_subscribers
                if key.symbol_key == symbol_key and self._stream_subscribers.get(key)
            ] + [
                key
                for key in self._public_stream_subscribers
                if key.symbol_key == symbol_key and self._public_stream_subscribers.get(key)
            ]
            user_ids_by_key = {
                key: sorted({user_id for user_id, _ in self._stream_subscribers.get(key, set())})
                for key in active_keys
            }
            connection_ids_by_key = {
                key: sorted(self._public_stream_subscribers.get(key, set()))
                for key in active_keys
            }

        if should_backfill:
            await self._backfill_symbol(symbol_key)

        for key in active_keys:
            for user_id in user_ids_by_key.get(key, []):
                await self._push_stream_status(
                    user_id=user_id,
                    key=key,
                    health=health,
                )
            for connection_id in connection_ids_by_key.get(key, []):
                await self._push_public_stream_status(
                    connection_id=connection_id,
                    key=key,
                    health=health,
                )

    async def _remove_subscription(
        self,
        connection_key: tuple[int, int],
        key: MarketStreamKey,
    ) -> None:
        release_symbol = False
        async with self._lock:
            subscribers = self._stream_subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(connection_key)
                if not subscribers:
                    self._stream_subscribers.pop(key, None)
            connection_streams = self._connection_streams.get(connection_key)
            if connection_streams is not None:
                connection_streams.discard(key)
                if not connection_streams:
                    self._connection_streams.pop(connection_key, None)
            release_symbol = not any(
                stream_key.symbol_key == key.symbol_key
                for stream_key, stream_subscribers in self._stream_subscribers.items()
                if stream_subscribers
            ) and not any(
                stream_key.symbol_key == key.symbol_key
                for stream_key, stream_subscribers in self._public_stream_subscribers.items()
                if stream_subscribers
            )
            if release_symbol:
                self._symbol_health.pop(key.symbol_key, None)

        if release_symbol:
            manager = self._ensure_manager(exchange=key.exchange, market_type=key.market_type, is_testnet=key.is_testnet)
            await manager.release_symbol(key.symbol)

    async def _remove_public_subscription(
        self,
        connection_id: int,
        key: MarketStreamKey,
    ) -> None:
        release_symbol = False
        async with self._lock:
            subscribers = self._public_stream_subscribers.get(key)
            if subscribers is not None:
                subscribers.discard(connection_id)
                if not subscribers:
                    self._public_stream_subscribers.pop(key, None)
            connection_streams = self._public_connection_streams.get(connection_id)
            if connection_streams is not None:
                connection_streams.discard(key)
                if not connection_streams:
                    self._public_connection_streams.pop(connection_id, None)
            release_symbol = not any(
                stream_key.symbol_key == key.symbol_key
                for stream_key, stream_subscribers in self._stream_subscribers.items()
                if stream_subscribers
            ) and not any(
                stream_key.symbol_key == key.symbol_key
                for stream_key, stream_subscribers in self._public_stream_subscribers.items()
                if stream_subscribers
            )
            if release_symbol:
                self._symbol_health.pop(key.symbol_key, None)

        if release_symbol:
            manager = self._ensure_manager(exchange=key.exchange, market_type=key.market_type, is_testnet=key.is_testnet)
            await manager.release_symbol(key.symbol)

    async def _apply_trade_tick(self, tick: TradeTick) -> None:
        emitted: list[tuple[MarketStreamKey, CandleCacheEntry, list[int], list[int]]] = []

        async with self._lock:
            has_subscribers = any(
                key.symbol_key == tick.symbol_key and subscribers
                for key, subscribers in self._stream_subscribers.items()
            )
            if not has_subscribers:
                return

            for interval in LIVE_MARKET_INTERVALS:
                key = MarketStreamKey(
                    exchange=tick.exchange,
                    market_type=tick.market_type,
                    symbol=tick.symbol,
                    interval=interval,
                    is_testnet=tick.is_testnet,
                )
                cache = self._candle_caches.setdefault(key, deque(maxlen=self._cache_size))
                interval_seconds = SUPPORTED_MARKET_INTERVALS[interval]
                bucket_start = _bucket_start_from_timestamp_ms(
                    timestamp_ms=tick.ts_ms,
                    interval_seconds=interval_seconds,
                )
                user_ids = sorted({user_id for user_id, _ in self._stream_subscribers.get(key, set())})
                connection_ids = sorted(self._public_stream_subscribers.get(key, set()))

                rollover_events = _apply_trade_to_cache(
                    cache=cache,
                    bucket_start=bucket_start,
                    price=tick.price,
                    size=tick.size,
                )
                for entry in rollover_events:
                    emitted.append((key, entry, user_ids, connection_ids))

        await self.update_symbol_status(tick.symbol_key, "live")

        for key, entry, user_ids, connection_ids in emitted:
            tasks = [self._push_candle(user_id=user_id, key=key, candle=entry) for user_id in user_ids]
            tasks.extend(
                self._push_public_candle(connection_id=connection_id, key=key, candle=entry)
                for connection_id in connection_ids
            )
            if tasks:
                await asyncio.gather(*tasks)

    async def _backfill_symbol(self, symbol_key: MarketSymbolKey) -> None:
        await asyncio.gather(
            *(
                self._backfill_interval(
                    MarketStreamKey(
                        exchange=symbol_key.exchange,
                        market_type=symbol_key.market_type,
                        symbol=symbol_key.symbol,
                        interval=interval,
                        is_testnet=symbol_key.is_testnet,
                    )
                )
                for interval in LIVE_MARKET_INTERVALS
            ),
            return_exceptions=True,
        )

    async def _backfill_interval(self, key: MarketStreamKey) -> None:
        try:
            history = await self._market_client.fetch_history(
                exchange=key.exchange,
                symbol=key.symbol,
                interval=key.interval,
                limit=min(self._rest_backfill_limit, self._cache_size),
                is_testnet=key.is_testnet,
            )
        except MarketDataError as exc:
            logger.warning("market backfill failed for %s: %s", key.resource_id, exc)
            return

        inserted_entries = await self._merge_rest_seed(key=key, candles=history, reconnect_mode=True)
        if not inserted_entries:
            return

        async with self._lock:
            user_ids = sorted({user_id for user_id, _ in self._stream_subscribers.get(key, set())})
            connection_ids = sorted(self._public_stream_subscribers.get(key, set()))

        if not user_ids and not connection_ids:
            return

        for entry in inserted_entries:
            tasks = [self._push_candle(user_id=user_id, key=key, candle=entry) for user_id in user_ids]
            tasks.extend(
                self._push_public_candle(connection_id=connection_id, key=key, candle=entry)
                for connection_id in connection_ids
            )
            await asyncio.gather(*tasks)

    async def _merge_rest_seed(
        self,
        *,
        key: MarketStreamKey,
        candles: list[dict[str, Any]],
        reconnect_mode: bool = False,
    ) -> list[CandleCacheEntry]:
        if not candles:
            return []

        interval_seconds = SUPPORTED_MARKET_INTERVALS[key.interval]
        now_bucket = _bucket_start_from_timestamp_ms(
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            interval_seconds=interval_seconds,
        )

        async with self._lock:
            cache = self._candle_caches.setdefault(key, deque(maxlen=self._cache_size))
            previous_latest_time = cache[-1].time if cache else 0
            by_time = {entry.time: entry.copy() for entry in cache}

            for candle in candles:
                entry = CandleCacheEntry(
                    time=_to_int(candle.get("time")),
                    open=_to_float(candle.get("open")),
                    high=_to_float(candle.get("high")),
                    low=_to_float(candle.get("low")),
                    close=_to_float(candle.get("close")),
                    volume=max(_to_float(candle.get("volume")), 0.0),
                    is_closed=_to_int(candle.get("time")) < now_bucket,
                    source="rest_seed",
                )
                if entry.time <= 0:
                    continue
                existing = by_time.get(entry.time)
                if existing is None:
                    by_time[entry.time] = entry
                    continue
                if existing.source == "ws":
                    continue
                by_time[entry.time] = entry

            merged_entries = sorted(by_time.values(), key=lambda item: item.time)[-self._cache_size :]
            cache.clear()
            cache.extend(entry.copy() for entry in merged_entries)

        if not reconnect_mode or previous_latest_time <= 0:
            return []

        return [
            entry.copy()
            for entry in merged_entries
            if entry.time >= previous_latest_time
        ]

    async def _snapshot_cache(self, key: MarketStreamKey) -> list[CandleCacheEntry]:
        async with self._lock:
            return [entry.copy() for entry in self._candle_caches.get(key, deque())]

    async def _push_candle(self, *, user_id: int, key: MarketStreamKey, candle: CandleCacheEntry) -> None:
        await self._ws_manager.push_to_user(
            user_id,
            build_ws_event(
                event_type="market_candle",
                resource_id=key.resource_id,
                flatten_payload=False,
                payload={
                    "exchange": key.exchange,
                    "symbol": key.symbol,
                    "interval": key.interval,
                    "is_testnet": key.is_testnet,
                    "source": candle.source,
                    "is_closed": candle.is_closed,
                    "candle": candle.to_market_kline(),
                },
            ),
        )

    async def _push_stream_status(
        self,
        *,
        user_id: int,
        key: MarketStreamKey,
        health: MarketStreamHealth,
    ) -> None:
        payload = {
            "exchange": key.exchange,
            "symbol": key.symbol,
            "interval": key.interval,
            "is_testnet": key.is_testnet,
            "status": health.status,
        }
        if health.message:
            payload["message"] = health.message

        await self._ws_manager.push_to_user(
            user_id,
            build_ws_event(
                event_type="market_stream_status",
                resource_id=key.resource_id,
                flatten_payload=False,
                payload=payload,
            ),
        )

    async def _push_public_candle(self, *, connection_id: int, key: MarketStreamKey, candle: CandleCacheEntry) -> None:
        await self._send_public_payload(
            connection_id=connection_id,
            payload={
                "type": "market_candle",
                "payload": {
                    "exchange": key.exchange,
                    "market_type": key.market_type,
                    "symbol": key.symbol,
                    "interval": key.interval,
                    "is_testnet": key.is_testnet,
                    "source": candle.source,
                    "is_closed": candle.is_closed,
                    "candle": candle.to_market_kline(),
                },
            },
        )

    async def _push_public_stream_status(
        self,
        *,
        connection_id: int,
        key: MarketStreamKey,
        health: MarketStreamHealth,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "market_stream_status",
            "payload": {
                "exchange": key.exchange,
                "market_type": key.market_type,
                "symbol": key.symbol,
                "interval": key.interval,
                "is_testnet": key.is_testnet,
                "status": health.status,
            },
        }
        if health.message:
            payload["payload"]["message"] = health.message
        await self._send_public_payload(connection_id=connection_id, payload=payload)

    async def _send_public_payload(self, *, connection_id: int, payload: dict[str, Any]) -> None:
        async with self._lock:
            sender = self._public_senders.get(connection_id)
        if sender is None:
            return
        await sender(payload)

    def _ensure_manager(self, *, exchange: str, market_type: str, is_testnet: bool) -> ExchangeStreamManager:
        normalized_exchange = normalize_market_exchange(exchange)
        normalized_market_type = normalize_market_type(market_type)
        lookup_key = (normalized_exchange, normalized_market_type, bool(is_testnet))
        manager = self._managers.get(lookup_key)
        if manager is not None:
            return manager

        manager_kwargs = {
            "market_type": normalized_market_type,
            "is_testnet": bool(is_testnet),
            "idle_timeout_seconds": self._idle_timeout_seconds,
            "reconnect_base_seconds": self._reconnect_base_seconds,
            "reconnect_max_seconds": self._reconnect_max_seconds,
            "on_trade_tick": self.ingest_trade_tick,
            "on_status_change": self.update_symbol_status,
        }
        if normalized_exchange == "binance":
            manager = BinanceStreamManager(**manager_kwargs)
        elif normalized_exchange == "okx":
            manager = OkxStreamManager(**manager_kwargs)
        else:
            raise MarketDataError(f"Exchange '{exchange}' is not supported for market data")

        self._managers[lookup_key] = manager
        return manager

    def _resize_caches_locked(self) -> None:
        resized: dict[MarketStreamKey, deque[CandleCacheEntry]] = {}
        for key, cache in self._candle_caches.items():
            resized[key] = deque(
                (entry.copy() for entry in list(cache)[-self._cache_size :]),
                maxlen=self._cache_size,
            )
        self._candle_caches = resized


def normalize_market_exchange(exchange: str) -> str:
    normalized = str(exchange or "").strip().lower()
    if normalized not in {"binance", "okx"}:
        raise MarketDataError(f"Exchange '{exchange}' is not supported for market data")
    return normalized


def normalize_market_interval(interval: str) -> str:
    normalized = str(interval or "").strip().lower()
    if normalized not in SUPPORTED_MARKET_INTERVALS:
        raise MarketDataError(
            f"Interval '{interval}' is not supported. Use one of: {', '.join(SUPPORTED_MARKET_INTERVALS)}"
        )
    return normalized


def normalize_live_market_interval(interval: str) -> str:
    normalized = normalize_market_interval(interval)
    if normalized not in LIVE_MARKET_INTERVALS:
        raise MarketDataError(
            f"Live interval '{interval}' is not supported. Use one of: {', '.join(LIVE_MARKET_INTERVALS)}"
        )
    return normalized


def normalize_market_stream_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in _STREAM_HEALTH_STATES:
        raise MarketDataError(
            f"Stream status '{status}' is invalid. Use one of: {', '.join(sorted(_STREAM_HEALTH_STATES))}"
        )
    return normalized


def normalize_market_runtime_config(config: dict[str, Any] | None) -> dict[str, float | int]:
    payload = dict(config or {})
    base_seconds = max(_to_float(payload.get("market_ws_reconnect_base_seconds"), 1.0), 0.5)
    max_seconds = max(_to_float(payload.get("market_ws_reconnect_max_seconds"), 15.0), base_seconds)
    idle_timeout = max(_to_float(payload.get("market_ws_idle_timeout_seconds"), 25.0), 5.0)
    cache_size = max(_to_int(payload.get("market_candle_cache_size"), 1000), 10)
    rest_backfill_limit = max(_to_int(payload.get("market_rest_backfill_limit"), 500), 1)
    if rest_backfill_limit > cache_size:
        rest_backfill_limit = cache_size
    return {
        "market_ws_reconnect_base_seconds": float(base_seconds),
        "market_ws_reconnect_max_seconds": float(max_seconds),
        "market_ws_idle_timeout_seconds": float(idle_timeout),
        "market_candle_cache_size": int(cache_size),
        "market_rest_backfill_limit": int(rest_backfill_limit),
    }


def normalize_market_type(market_type: Any) -> str:
    if hasattr(market_type, "default"):
        market_type = getattr(market_type, "default")
    normalized = str(market_type or "").strip().lower() or "spot"
    if normalized not in {"spot", "perp"}:
        raise MarketDataError("market_type must be one of: spot, perp")
    return normalized


def normalize_market_symbol(exchange: str, symbol: str, market_type: str = "spot") -> str:
    normalized_exchange = normalize_market_exchange(exchange)
    normalized_market_type = normalize_market_type(market_type)
    raw_symbol = str(symbol or "").strip().upper().replace("/", "").replace(" ", "")
    if not raw_symbol:
        raise MarketDataError("Symbol is required")
    if normalized_exchange == "binance":
        return raw_symbol.replace("-", "")
    if normalized_market_type == "perp" and normalized_exchange == "okx" and not raw_symbol.endswith("-SWAP"):
        if "-" in raw_symbol:
            return f"{raw_symbol}-SWAP"
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if raw_symbol.endswith(quote) and len(raw_symbol) > len(quote):
                base = raw_symbol[: -len(quote)]
                return f"{base}-{quote}-SWAP"
    if "-" in raw_symbol:
        return raw_symbol
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
        if raw_symbol.endswith(quote) and len(raw_symbol) > len(quote):
            base = raw_symbol[: -len(quote)]
            return f"{base}-{quote}"
    return raw_symbol


def _binance_public_base_url(*, is_testnet: bool, market_type: str) -> str:
    normalized_market_type = normalize_market_type(market_type)
    if normalized_market_type == "perp":
        return settings.binance_futures_testnet_base_url if is_testnet else settings.binance_futures_base_url
    return settings.binance_testnet_base_url if is_testnet else settings.binance_spot_base_url


def _resolve_binance_ws_url(*, is_testnet: bool, market_type: str) -> str:
    normalized_market_type = normalize_market_type(market_type)
    if normalized_market_type == "perp":
        return _BINANCE_FUTURES_TESTNET_WS_URL if is_testnet else _BINANCE_FUTURES_WS_URL
    return _BINANCE_TESTNET_WS_URL if is_testnet else _BINANCE_SPOT_WS_URL


def _build_candle(
    *,
    time_seconds: int,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
) -> dict[str, Any]:
    return {
        "time": int(time_seconds),
        "open": float(open_price),
        "high": float(high_price),
        "low": float(low_price),
        "close": float(close_price),
        "volume": float(volume),
    }


def _deserialize_ws_message(message: Any) -> Any:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="ignore")
    if isinstance(message, str):
        stripped = message.strip()
        if not stripped:
            return ""
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
    return message


def _apply_trade_to_cache(
    *,
    cache: deque[CandleCacheEntry],
    bucket_start: int,
    price: float,
    size: float,
) -> list[CandleCacheEntry]:
    emitted: list[CandleCacheEntry] = []
    volume = max(float(size), 0.0)

    if not cache:
        entry = CandleCacheEntry(
            time=bucket_start,
            open=float(price),
            high=float(price),
            low=float(price),
            close=float(price),
            volume=volume,
            is_closed=False,
            source="ws",
        )
        cache.append(entry)
        emitted.append(entry.copy())
        return emitted

    latest = cache[-1]
    if latest.time == bucket_start:
        latest.high = max(latest.high, float(price))
        latest.low = min(latest.low, float(price))
        latest.close = float(price)
        latest.volume = max(latest.volume + volume, 0.0)
        latest.is_closed = False
        latest.source = "ws"
        emitted.append(latest.copy())
        return emitted

    if latest.time < bucket_start:
        latest.is_closed = True
        latest.source = "ws"
        emitted.append(latest.copy())
        next_entry = CandleCacheEntry(
            time=bucket_start,
            open=float(price),
            high=float(price),
            low=float(price),
            close=float(price),
            volume=volume,
            is_closed=False,
            source="ws",
        )
        cache.append(next_entry)
        emitted.append(next_entry.copy())
        return emitted

    for existing in reversed(cache):
        if existing.time == bucket_start:
            existing.high = max(existing.high, float(price))
            existing.low = min(existing.low, float(price))
            existing.close = float(price)
            existing.volume = max(existing.volume + volume, 0.0)
            existing.source = "ws"
            emitted.append(existing.copy())
            break
    return emitted


def _bucket_start_from_timestamp_ms(*, timestamp_ms: int, interval_seconds: int) -> int:
    timestamp_seconds = max(int(timestamp_ms) // 1000, 0)
    return timestamp_seconds // interval_seconds * interval_seconds


def _truncate_error_message(error: Exception) -> str:
    return str(error).strip()[:240] or error.__class__.__name__


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
