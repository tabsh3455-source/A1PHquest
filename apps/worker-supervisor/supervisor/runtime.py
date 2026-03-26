from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import multiprocessing as mp
from queue import Empty
import threading
import time
from typing import Any
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StrategyLaunchContext:
    user_id: int
    strategy_id: int
    strategy_type: str
    config: dict[str, Any]
    exchange: str
    is_testnet: bool
    api_key: str
    api_secret: str
    passphrase: str | None


@dataclass(slots=True)
class Runtime:
    runtime_ref: str
    user_id: int
    strategy_id: int
    strategy_type: str
    process: mp.Process
    events: mp.Queue
    stop_event: mp.Event
    started_at: datetime
    stopped_at: datetime | None = None
    status: str = "starting"
    last_heartbeat: datetime | None = None
    last_error: str | None = None
    # Monotonic local sequence for runtime trace events.
    last_event_seq: int = 0
    last_event_type: str | None = None
    last_event_at: datetime | None = None
    # Execution observability counters.
    order_submitted_count: int = 0
    order_update_count: int = 0
    trade_fill_count: int = 0
    # Keep bounded in-memory trace history for runtime inspection.
    recent_events: list[dict[str, Any]] = field(default_factory=list)


class RuntimeRegistry:
    def __init__(self) -> None:
        self._store: dict[str, Runtime] = {}
        self._strategy_index: dict[tuple[int, int], str] = {}
        self._lock = threading.RLock()
        self._heartbeat_interval = _float_env("SUPERVISOR_HEARTBEAT_SECONDS", 2.0)
        self._action_debounce_seconds = _float_env("SUPERVISOR_ACTION_DEBOUNCE_SECONDS", 3.0)
        self._heartbeat_timeout_seconds = _float_env(
            "SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS",
            max(self._heartbeat_interval * 6, 15.0),
        )
        self._runtime_retention_seconds = _float_env("SUPERVISOR_RUNTIME_RETENTION_SECONDS", 900.0)
        self._runtime_event_history_size = _int_env("SUPERVISOR_RUNTIME_EVENT_HISTORY_SIZE", 200)

    def start(self, user_id: int, strategy_id: int, strategy_type: str, config_json: str) -> Runtime:
        self._cleanup_finished_runtimes()
        strategy_key = (user_id, strategy_id)
        with self._lock:
            existing_ref = self._strategy_index.get(strategy_key)
            existing = self._store.get(existing_ref) if existing_ref else None
        if existing and existing.status in {"starting", "running", "stopping"}:
            self._refresh_runtime(existing, wait_for_transition=False)
            # Debounce duplicate start calls from retries/race windows.
            if (_utcnow() - existing.started_at).total_seconds() <= self._action_debounce_seconds:
                return existing
            return existing

        runtime_ref = f"{user_id}-{strategy_id}-{uuid.uuid4().hex[:8]}"
        events: mp.Queue = mp.Queue()
        stop_event: mp.Event = mp.Event()
        process = mp.Process(
            target=_strategy_process,
            args=(runtime_ref, user_id, strategy_id, strategy_type, config_json, events, stop_event),
            daemon=True,
        )
        runtime = Runtime(
            runtime_ref=runtime_ref,
            user_id=user_id,
            strategy_id=strategy_id,
            strategy_type=strategy_type,
            process=process,
            events=events,
            stop_event=stop_event,
            started_at=_utcnow(),
            status="starting",
            last_heartbeat=_utcnow(),
        )
        with self._lock:
            self._store[runtime_ref] = runtime
            self._strategy_index[strategy_key] = runtime_ref
        process.start()
        self._refresh_runtime(runtime, wait_for_transition=True)
        return runtime

    def stop(self, runtime_ref: str) -> Runtime | None:
        self._cleanup_finished_runtimes()
        runtime = self.get(runtime_ref)
        if not runtime:
            return None

        if runtime.status in {"stopped", "failed"}:
            return runtime

        if runtime.status == "stopping" and runtime.last_heartbeat:
            elapsed = (_utcnow() - runtime.last_heartbeat).total_seconds()
            if elapsed <= self._action_debounce_seconds:
                return runtime

        if runtime.status not in {"stopped", "failed"}:
            runtime.status = "stopping"
            runtime.last_heartbeat = _utcnow()
            runtime.stop_event.set()
            runtime.process.join(timeout=8)
            if runtime.process.is_alive():
                runtime.process.terminate()
                runtime.process.join(timeout=2)

        self._refresh_runtime(runtime, wait_for_transition=False)
        if runtime.status != "failed":
            runtime.status = "stopped"
        runtime.stopped_at = runtime.stopped_at or _utcnow()
        runtime.last_heartbeat = _utcnow()
        return runtime

    def get(self, runtime_ref: str) -> Runtime | None:
        self._cleanup_finished_runtimes()
        with self._lock:
            runtime = self._store.get(runtime_ref)
        if runtime:
            self._refresh_runtime(runtime, wait_for_transition=False)
            self._cleanup_finished_runtimes()
        return runtime

    def _refresh_runtime(self, runtime: Runtime, *, wait_for_transition: bool) -> None:
        deadline = time.time() + 8 if wait_for_transition else time.time()
        while True:
            self._drain_events(runtime)
            if runtime.status == "running" and self._is_heartbeat_stale(runtime):
                self._mark_runtime_failed(
                    runtime,
                    "runtime_heartbeat_timeout: runtime heartbeat exceeded threshold",
                    terminate_process=True,
                )
                return
            if not runtime.process.is_alive() and runtime.status not in {"stopped", "failed"}:
                # Process may exit before queue events are drained. Give a brief grace window
                # to consume terminal events (failed/stopped) emitted right before shutdown.
                grace_deadline = time.time() + 0.2
                while time.time() < grace_deadline and runtime.status not in {"stopped", "failed"}:
                    self._drain_events(runtime)
                    if runtime.status in {"stopped", "failed"}:
                        return
                    time.sleep(0.01)
                self._drain_events(runtime)
                if runtime.status in {"stopped", "failed"}:
                    return
                self._mark_runtime_failed(runtime, "runtime_process_crash: strategy process exited unexpectedly")
                return

            if not wait_for_transition:
                return
            if runtime.status in {"running", "failed"}:
                return
            if time.time() >= deadline:
                return
            time.sleep(0.1)

    def _drain_events(self, runtime: Runtime) -> None:
        while True:
            try:
                event = runtime.events.get_nowait()
            except Empty:
                break
            _apply_runtime_event(
                runtime,
                event,
                history_size=self._runtime_event_history_size,
            )

    def _mark_runtime_failed(
        self,
        runtime: Runtime,
        error: str,
        *,
        terminate_process: bool = False,
    ) -> None:
        if terminate_process and runtime.process.is_alive():
            runtime.stop_event.set()
            runtime.process.join(timeout=2)
            if runtime.process.is_alive():
                runtime.process.terminate()
                runtime.process.join(timeout=2)
        runtime.status = "failed"
        runtime.stopped_at = runtime.stopped_at or _utcnow()
        runtime.last_error = runtime.last_error or error
        runtime.last_heartbeat = _utcnow()
        _append_runtime_trace_event(
            runtime,
            event_type="runtime_failed",
            payload={"error": runtime.last_error},
            history_size=self._runtime_event_history_size,
            event_time=runtime.last_heartbeat,
        )

    def _is_heartbeat_stale(self, runtime: Runtime) -> bool:
        if not runtime.last_heartbeat:
            return False
        elapsed = (_utcnow() - runtime.last_heartbeat).total_seconds()
        return elapsed > self._heartbeat_timeout_seconds

    def _cleanup_finished_runtimes(self) -> None:
        cutoff = _utcnow()
        expired_refs: list[str] = []
        with self._lock:
            for runtime_ref, runtime in self._store.items():
                if runtime.status not in {"stopped", "failed"}:
                    continue
                age_anchor = runtime.stopped_at or runtime.last_heartbeat or runtime.started_at
                if not age_anchor:
                    continue
                age_seconds = (cutoff - age_anchor).total_seconds()
                if age_seconds >= self._runtime_retention_seconds:
                    expired_refs.append(runtime_ref)
            for runtime_ref in expired_refs:
                runtime = self._store.pop(runtime_ref, None)
                if not runtime:
                    continue
                strategy_key = (runtime.user_id, runtime.strategy_id)
                if self._strategy_index.get(strategy_key) == runtime_ref:
                    self._strategy_index.pop(strategy_key, None)


def _strategy_process(
    runtime_ref: str,
    user_id: int,
    strategy_id: int,
    strategy_type: str,
    _config_json_hint: str,
    events: mp.Queue,
    stop_event: mp.Event,
) -> None:
    _push_event(events, status="starting", event_kind="state")
    try:
        context = _load_launch_context(user_id=user_id, strategy_id=strategy_id)
        # The API guarantees only live-enabled runtime startup, but worker still
        # defends against stale strategy rows or mismatched template state.
        if context.strategy_type not in {"grid", "futures_grid", "dca", "combo_grid_dca"}:
            raise RuntimeError(f"strategy_type '{context.strategy_type}' is not enabled for live runtime")
        if context.exchange not in {"binance", "okx"}:
            raise RuntimeError(f"exchange '{context.exchange}' is not supported for live runtime")
        _run_vnpy_runtime(runtime_ref, context, events, stop_event)
    except Exception as exc:
        _push_event(
            events,
            status="failed",
            error=_classify_runtime_error(exc),
            event_kind="state",
        )


def _run_vnpy_runtime(
    runtime_ref: str,
    context: StrategyLaunchContext,
    events: mp.Queue,
    stop_event: mp.Event,
) -> None:
    from vnpy.event import EventEngine
    from vnpy.trader.engine import MainEngine

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    gateway_name = ""
    cta_engine = None
    strategy_name = ""
    runtime_started_at = _utcnow()

    def _emit_trace(event_type: str, payload: dict[str, Any] | None = None) -> None:
        _push_trace_event(
            events,
            event_type=event_type,
            payload={
                "runtime_ref": runtime_ref,
                "strategy_id": context.strategy_id,
                "strategy_type": context.strategy_type,
                **(payload or {}),
            },
        )

    try:
        gateway_cls = _resolve_gateway_class(context.exchange)
        gateway = main_engine.add_gateway(gateway_cls)
        gateway_name = getattr(gateway, "gateway_name", getattr(gateway_cls, "default_name", context.exchange.upper()))
        gateway_setting = _build_gateway_setting(gateway_cls, context)

        try:
            main_engine.connect(gateway_setting, gateway_name)
        except Exception as exc:
            raise RuntimeError(f"gateway_connect_failed: {exc}") from exc

        cta_engine = _bootstrap_cta_engine(main_engine)
        strategy_name = _setup_runtime_strategy(
            cta_engine=cta_engine,
            context=context,
            gateway_name=gateway_name,
            gateway=gateway,
            runtime_ref=runtime_ref,
            emit_trace=_emit_trace,
        )
        _push_event(events, status="running", event_kind="state")
        _emit_trace(
            "strategy_runtime_started",
            {
                "gateway_name": gateway_name,
                "strategy_name": strategy_name,
                "started_at": _to_iso_z(runtime_started_at),
            },
        )

        heartbeat_interval = _float_env("SUPERVISOR_HEARTBEAT_SECONDS", 2.0)
        while not stop_event.is_set():
            _push_event(events, status="running", event_kind="heartbeat")
            time.sleep(heartbeat_interval)
    finally:
        _emit_trace(
            "strategy_runtime_stopping",
            {
                "gateway_name": gateway_name,
                "strategy_name": strategy_name,
            },
        )
        _stop_runtime_strategy(cta_engine, strategy_name)
        try:
            main_engine.close()
        except Exception as exc:
            logger.warning("runtime_main_engine_close_failed: %s", exc)
        _push_event(events, status="stopped", event_kind="state")


def _bootstrap_cta_engine(main_engine):
    """Initialize CTA engine and fail fast when plugin/runtime is not available."""
    try:
        from vnpy_ctastrategy import CtaStrategyApp
    except Exception as exc:
        raise RuntimeError(f"cta_engine_init_failed: {exc}") from exc

    cta_engine = main_engine.add_app(CtaStrategyApp)
    if hasattr(cta_engine, "init_engine"):
        try:
            cta_engine.init_engine()
        except Exception as exc:
            raise RuntimeError(f"cta_engine_init_failed: {exc}") from exc
    return cta_engine


def _setup_runtime_strategy(
    *,
    cta_engine,
    context: StrategyLaunchContext,
    gateway_name: str,
    gateway,
    runtime_ref: str,
    emit_trace,
) -> str:
    """
    Register and start runtime strategy instance for grid, dca, or combo grid+dca.

    We keep strategy logic minimal but wire the full CTA lifecycle:
    add_strategy -> init_strategy -> start_strategy.
    """
    strategy_cls = _build_runtime_strategy_class(context.strategy_type, emit_trace=emit_trace)
    class_name = strategy_cls.__name__
    if hasattr(cta_engine, "classes") and isinstance(getattr(cta_engine, "classes"), dict):
        cta_engine.classes[class_name] = strategy_cls

    strategy_name = f"{context.strategy_type}_{context.strategy_id}_{runtime_ref[-6:]}"
    vt_symbol = _build_vt_symbol(
        str(context.config.get("symbol", "")).upper(),
        gateway_name,
        gateway=gateway,
    )
    setting = _build_runtime_strategy_setting(context)

    if not hasattr(cta_engine, "add_strategy"):
        return ""
    try:
        cta_engine.add_strategy(class_name, strategy_name, vt_symbol, setting)
    except Exception as exc:
        raise RuntimeError(f"strategy_init_failed: {exc}") from exc
    # Some CTA engine builds register strategies asynchronously, so wait briefly
    # instead of reporting a false-positive running state.
    if not _wait_for_strategy_registration(cta_engine, strategy_name):
        emit_trace(
            "strategy_runtime_registered_pending",
            {
                "strategy_name": strategy_name,
                "vt_symbol": vt_symbol,
            },
        )
        raise RuntimeError("strategy_init_failed: strategy registration did not become ready")
    try:
        init_future = None
        if hasattr(cta_engine, "init_strategy"):
            # vnpy-ctastrategy initializes strategies asynchronously and returns a Future.
            # We must wait for that Future to settle before attempting start_strategy,
            # otherwise the runtime can report a false start while `strategy.inited` is still False.
            init_future = cta_engine.init_strategy(strategy_name)
        _wait_for_strategy_initialization(cta_engine, strategy_name, init_future)
        if hasattr(cta_engine, "start_strategy"):
            cta_engine.start_strategy(strategy_name)
    except KeyError:
        raise RuntimeError("strategy_init_failed: strategy registration disappeared before start")
    except Exception as exc:
        raise RuntimeError(f"strategy_init_failed: {exc}") from exc
    return strategy_name


def _stop_runtime_strategy(cta_engine, strategy_name: str) -> None:
    if not cta_engine or not strategy_name:
        return
    try:
        if hasattr(cta_engine, "stop_strategy"):
            cta_engine.stop_strategy(strategy_name)
        if hasattr(cta_engine, "remove_strategy"):
            cta_engine.remove_strategy(strategy_name)
    except Exception as exc:
        # Stop path should not block process shutdown; error is reflected by final runtime state.
        logger.warning("runtime_stop_strategy_cleanup_failed: %s", exc)


def _wait_for_strategy_registration(cta_engine, strategy_name: str, *, timeout_seconds: float = 3.0) -> bool:
    strategies_map = getattr(cta_engine, "strategies", None)
    if not isinstance(strategies_map, dict):
        # Older CTA implementations may not expose a public strategies map. In that
        # case we cannot verify registration here and fall back to normal start path.
        return True
    deadline = time.time() + max(timeout_seconds, 0.1)
    while time.time() < deadline:
        strategies_map = getattr(cta_engine, "strategies", None)
        if isinstance(strategies_map, dict) and strategy_name in strategies_map:
            return True
        time.sleep(0.05)
    strategies_map = getattr(cta_engine, "strategies", None)
    return isinstance(strategies_map, dict) and strategy_name in strategies_map


def _wait_for_strategy_initialization(
    cta_engine,
    strategy_name: str,
    init_future,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    """
    Wait for vn.py CTA init_strategy() to complete.

    The upstream CTA engine uses a ThreadPoolExecutor and returns a Future. Without
    waiting here we can race into start_strategy() before the strategy becomes `inited`.
    """
    if init_future is not None and hasattr(init_future, "result"):
        try:
            init_future.result(timeout=max(timeout_seconds, 0.1))
        except TimeoutError as exc:
            raise RuntimeError("strategy_init_failed: strategy initialization timed out") from exc
        except KeyError as exc:
            raise RuntimeError("strategy_init_failed: strategy registration disappeared before init") from exc
        except Exception as exc:
            raise RuntimeError(f"strategy_init_failed: {exc}") from exc

    deadline = time.time() + max(timeout_seconds, 0.1)
    while time.time() < deadline:
        strategy = _get_registered_strategy(cta_engine, strategy_name)
        if strategy is None:
            raise RuntimeError("strategy_init_failed: strategy registration disappeared before init")
        if bool(getattr(strategy, "inited", False)):
            return
        time.sleep(0.05)

    strategy = _get_registered_strategy(cta_engine, strategy_name)
    if strategy is None:
        raise RuntimeError("strategy_init_failed: strategy registration disappeared before init")
    if bool(getattr(strategy, "inited", False)):
        return
    raise RuntimeError("strategy_init_failed: strategy initialization did not complete")


def _get_registered_strategy(cta_engine, strategy_name: str):
    strategies_map = getattr(cta_engine, "strategies", None)
    if not isinstance(strategies_map, dict):
        return None
    return strategies_map.get(strategy_name)


def _build_runtime_strategy_class(strategy_type: str, *, emit_trace):
    from vnpy_ctastrategy import CtaTemplate

    class _BaseRuntimeStrategy(CtaTemplate):
        """
        Shared runtime strategy hooks for live CTA process.

        The subclasses below implement concrete runtime behaviors so runtime
        process is not only "connected" but can also exercise real CTA order APIs.
        """

        author = "A1phquest"
        parameters: list[str] = []
        variables: list[str] = ["executed_order_count", "last_order_at", "last_error"]

        def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict[str, Any]):
            super().__init__(cta_engine, strategy_name, vt_symbol, setting)
            self.executed_order_count: int = 0
            self.last_order_at: str = ""
            self.last_error: str = ""
            self._emit_runtime_trace = emit_trace

        def on_init(self) -> None:
            self.write_log("runtime strategy initialized")

        def on_start(self) -> None:
            self.write_log("runtime strategy started")
            self._emit_trace(
                "strategy_triggered",
                {
                    "executed_order_count": self.executed_order_count,
                },
            )

        def on_stop(self) -> None:
            self.write_log("runtime strategy stopped")
            self._emit_trace(
                "strategy_stopped",
                {
                    "executed_order_count": self.executed_order_count,
                    "last_order_at": self.last_order_at,
                    "last_error": self.last_error or None,
                },
            )

        def on_tick(self, tick) -> None:  # pragma: no cover - runtime callback
            return None

        def on_bar(self, bar) -> None:  # pragma: no cover - runtime callback
            return None

        def on_trade(self, trade) -> None:  # pragma: no cover - runtime callback
            payload = {
                "trade_id": _to_text(getattr(trade, "tradeid", None) or getattr(trade, "vt_tradeid", None)),
                "order_id": _to_text(getattr(trade, "orderid", None) or getattr(trade, "vt_orderid", None)),
                "direction": _to_text(getattr(trade, "direction", None)),
                "offset": _to_text(getattr(trade, "offset", None)),
                "price": _to_float(getattr(trade, "price", None)),
                "volume": _to_float(getattr(trade, "volume", None)),
                "trade_time": _to_text(getattr(trade, "datetime", None)),
            }
            self._emit_trace("trade_filled", payload)

        def on_order(self, order) -> None:  # pragma: no cover - runtime callback
            payload = {
                "order_id": _to_text(getattr(order, "orderid", None) or getattr(order, "vt_orderid", None)),
                "status": _to_text(getattr(order, "status", None)),
                "direction": _to_text(getattr(order, "direction", None)),
                "offset": _to_text(getattr(order, "offset", None)),
                "price": _to_float(getattr(order, "price", None)),
                "volume": _to_float(getattr(order, "volume", None)),
                "traded": _to_float(getattr(order, "traded", None)),
            }
            self._emit_trace("order_status_update", payload)

        def _record_order_submission(
            self,
            *,
            side: str,
            price: float,
            volume: float,
            order_refs: Any = None,
        ) -> None:
            self.executed_order_count += 1
            self.last_order_at = _to_iso_z(_utcnow()) or ""
            self._emit_trace(
                "order_submitted",
                {
                    "side": side,
                    "price": price,
                    "volume": volume,
                    "order_refs": _normalize_order_refs(order_refs),
                    "executed_order_count": self.executed_order_count,
                },
            )
            self.put_event()

        def _record_error(self, error: Exception) -> None:
            self.last_error = str(error)
            self.write_log(f"runtime strategy error: {self.last_error}")
            self._emit_trace("strategy_runtime_error", {"error": self.last_error})
            self.put_event()

        def _emit_trace(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
            safe_payload = _sanitize_event_payload(payload or {})
            safe_payload.setdefault("strategy_name", self.strategy_name)
            safe_payload.setdefault("vt_symbol", self.vt_symbol)
            self._emit_runtime_trace(event_type, safe_payload)

    class RuntimeGridStrategy(_BaseRuntimeStrategy):
        parameters = ["grid_count", "grid_step_pct", "base_order_size", "max_grid_levels"]
        variables = [
            "grid_count",
            "grid_step_pct",
            "base_order_size",
            "max_grid_levels",
            "grid_seeded",
            "planned_order_count",
            "executed_order_count",
            "last_order_at",
            "last_error",
        ]

        def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict[str, Any]):
            super().__init__(cta_engine, strategy_name, vt_symbol, setting)
            self.grid_seeded: bool = False
            self.planned_order_count: int = 0

        def on_start(self) -> None:
            super().on_start()
            self.grid_seeded = False
            self.last_error = ""
            self.put_event()

        def on_tick(self, tick) -> None:  # pragma: no cover - runtime callback
            # Grid initialization is one-shot per start. Order refresh/cancel cycle
            # can be layered later without changing runtime API contracts.
            if self.grid_seeded:
                return
            last_price = float(getattr(tick, "last_price", 0.0) or 0.0)
            if last_price <= 0:
                return
            try:
                self.planned_order_count = _seed_grid_orders_for_runtime(self, reference_price=last_price)
                if not self.planned_order_count:
                    return
                self.grid_seeded = True
                self.put_event()
            except Exception as exc:
                self._record_error(exc)

    class RuntimeDcaStrategy(_BaseRuntimeStrategy):
        parameters = ["cycle_seconds", "amount_per_cycle", "price_offset_pct", "min_order_volume"]
        variables = [
            "cycle_seconds",
            "amount_per_cycle",
            "price_offset_pct",
            "min_order_volume",
            "next_run_ts",
            "executed_order_count",
            "last_order_at",
            "last_error",
        ]

        def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict[str, Any]):
            super().__init__(cta_engine, strategy_name, vt_symbol, setting)
            self.next_run_ts: float = 0.0

        def on_start(self) -> None:
            super().on_start()
            # First run fires as soon as the first valid tick arrives.
            self.next_run_ts = 0.0
            self.last_error = ""
            self.put_event()

        def on_tick(self, tick) -> None:  # pragma: no cover - runtime callback
            last_price = float(getattr(tick, "last_price", 0.0) or 0.0)
            if last_price <= 0:
                return

            try:
                next_run_ts = _run_dca_cycle_for_runtime(self, last_price=last_price)
                if not next_run_ts:
                    return
                self.next_run_ts = next_run_ts
                self.put_event()
            except Exception as exc:
                self._record_error(exc)

    class RuntimeComboGridDcaStrategy(_BaseRuntimeStrategy):
        parameters = [
            "grid_count",
            "grid_step_pct",
            "base_order_size",
            "max_grid_levels",
            "cycle_seconds",
            "amount_per_cycle",
            "price_offset_pct",
            "min_order_volume",
        ]
        variables = [
            "grid_count",
            "grid_step_pct",
            "base_order_size",
            "max_grid_levels",
            "cycle_seconds",
            "amount_per_cycle",
            "price_offset_pct",
            "min_order_volume",
            "grid_seeded",
            "planned_order_count",
            "next_run_ts",
            "executed_order_count",
            "last_order_at",
            "last_error",
        ]

        def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict[str, Any]):
            super().__init__(cta_engine, strategy_name, vt_symbol, setting)
            self.grid_seeded: bool = False
            self.planned_order_count: int = 0
            self.next_run_ts: float = 0.0

        def on_start(self) -> None:
            super().on_start()
            self.grid_seeded = False
            self.planned_order_count = 0
            self.next_run_ts = 0.0
            self.last_error = ""
            self.put_event()

        def on_tick(self, tick) -> None:  # pragma: no cover - runtime callback
            last_price = float(getattr(tick, "last_price", 0.0) or 0.0)
            if last_price <= 0:
                return

            if not self.grid_seeded:
                try:
                    self.planned_order_count = _seed_grid_orders_for_runtime(self, reference_price=last_price)
                    self.grid_seeded = self.planned_order_count > 0
                    if self.grid_seeded:
                        self.put_event()
                except Exception as exc:
                    self._record_error(exc)
                    return

            try:
                next_run_ts = _run_dca_cycle_for_runtime(self, last_price=last_price)
                if next_run_ts:
                    self.next_run_ts = next_run_ts
                    self.put_event()
            except Exception as exc:
                self._record_error(exc)

    class RuntimeFuturesGridStrategy(RuntimeGridStrategy):
        parameters = ["grid_count", "grid_step_pct", "base_order_size", "max_grid_levels", "leverage", "direction"]
        variables = [
            "grid_count",
            "grid_step_pct",
            "base_order_size",
            "max_grid_levels",
            "leverage",
            "direction",
            "grid_seeded",
            "planned_order_count",
            "executed_order_count",
            "last_order_at",
            "last_error",
        ]

        def on_start(self) -> None:
            super().on_start()
            self._emit_trace(
                "futures_grid_profile",
                {
                    "direction": str(getattr(self, "direction", "neutral")).lower(),
                    "leverage": _to_float(getattr(self, "leverage", None)),
                },
            )

    if strategy_type == "grid":
        return RuntimeGridStrategy
    if strategy_type == "futures_grid":
        return RuntimeFuturesGridStrategy
    if strategy_type == "dca":
        return RuntimeDcaStrategy
    if strategy_type == "combo_grid_dca":
        return RuntimeComboGridDcaStrategy
    raise RuntimeError(f"strategy_init_failed: unsupported runtime strategy_type={strategy_type}")


def _build_runtime_strategy_setting(context: StrategyLaunchContext) -> dict[str, Any]:
    config = context.config
    if context.strategy_type == "grid":
        grid_count = int(config.get("grid_count", 0))
        grid_step_pct = float(config.get("grid_step_pct", 0))
        base_order_size = float(config.get("base_order_size", 0))
        max_grid_levels = int(config.get("max_grid_levels", min(max(grid_count, 2), 40)))
        if grid_count < 2 or grid_step_pct <= 0 or base_order_size <= 0:
            raise RuntimeError("strategy_param_failed: invalid grid config")
        return {
            "grid_count": grid_count,
            "grid_step_pct": grid_step_pct,
            "base_order_size": base_order_size,
            "max_grid_levels": min(max(max_grid_levels, 2), 100),
        }
    if context.strategy_type == "dca":
        cycle_seconds = int(config.get("cycle_seconds", 0))
        amount_per_cycle = float(config.get("amount_per_cycle", 0))
        if cycle_seconds <= 0 or amount_per_cycle <= 0:
            raise RuntimeError("strategy_param_failed: invalid dca config")
        return {
            "cycle_seconds": cycle_seconds,
            "amount_per_cycle": amount_per_cycle,
            "price_offset_pct": float(config.get("price_offset_pct", 0.15)),
            "min_order_volume": float(config.get("min_order_volume", 0)),
        }
    if context.strategy_type == "futures_grid":
        grid_count = int(config.get("grid_count", 0))
        grid_step_pct = float(config.get("grid_step_pct", 0))
        base_order_size = float(config.get("base_order_size", 0))
        max_grid_levels = int(config.get("max_grid_levels", min(max(grid_count, 2), 40)))
        leverage = int(config.get("leverage", 0))
        direction = str(config.get("direction", "")).strip().lower()
        if grid_count < 2 or grid_step_pct <= 0 or base_order_size <= 0:
            raise RuntimeError("strategy_param_failed: invalid futures grid config")
        if leverage < 1 or leverage > 50:
            raise RuntimeError("strategy_param_failed: invalid futures leverage")
        if direction not in {"neutral", "long", "short"}:
            raise RuntimeError("strategy_param_failed: invalid futures direction")
        return {
            "grid_count": grid_count,
            "grid_step_pct": grid_step_pct,
            "base_order_size": base_order_size,
            "max_grid_levels": min(max(max_grid_levels, 2), 100),
            "leverage": leverage,
            "direction": direction,
        }
    if context.strategy_type == "combo_grid_dca":
        grid_count = int(config.get("grid_count", 0))
        grid_step_pct = float(config.get("grid_step_pct", 0))
        base_order_size = float(config.get("base_order_size", 0))
        cycle_seconds = int(config.get("cycle_seconds", 0))
        amount_per_cycle = float(config.get("amount_per_cycle", 0))
        max_grid_levels = int(config.get("max_grid_levels", min(max(grid_count, 2), 40)))
        if grid_count < 2 or grid_step_pct <= 0 or base_order_size <= 0:
            raise RuntimeError("strategy_param_failed: invalid combo grid config")
        if cycle_seconds <= 0 or amount_per_cycle <= 0:
            raise RuntimeError("strategy_param_failed: invalid combo dca config")
        return {
            "grid_count": grid_count,
            "grid_step_pct": grid_step_pct,
            "base_order_size": base_order_size,
            "max_grid_levels": min(max(max_grid_levels, 2), 100),
            "cycle_seconds": cycle_seconds,
            "amount_per_cycle": amount_per_cycle,
            "price_offset_pct": float(config.get("price_offset_pct", 0.15)),
            "min_order_volume": float(config.get("min_order_volume", 0)),
        }
    return {}


def _seed_grid_orders_for_runtime(strategy, *, reference_price: float) -> int:
    buy_prices, sell_prices = _compute_grid_order_prices(
        reference_price=reference_price,
        grid_count=int(strategy.grid_count),
        grid_step_pct=float(strategy.grid_step_pct),
        max_levels=int(strategy.max_grid_levels),
    )
    if not buy_prices and not sell_prices:
        return 0
    direction = str(getattr(strategy, "direction", "neutral") or "neutral").strip().lower()
    if direction not in {"neutral", "long", "short"}:
        raise RuntimeError("strategy_param_failed: invalid futures direction")
    selected_buy_prices = buy_prices if direction in {"neutral", "long"} else []
    selected_sell_prices = sell_prices if direction in {"neutral", "short"} else []

    for price in selected_buy_prices:
        order_refs = strategy.buy(price, float(strategy.base_order_size))
        strategy._record_order_submission(
            side="BUY",
            price=price,
            volume=float(strategy.base_order_size),
            order_refs=order_refs,
        )
    for price in selected_sell_prices:
        order_refs = strategy.sell(price, float(strategy.base_order_size))
        strategy._record_order_submission(
            side="SELL",
            price=price,
            volume=float(strategy.base_order_size),
            order_refs=order_refs,
        )

    planned_order_count = len(selected_buy_prices) + len(selected_sell_prices)
    strategy.write_log(f"grid seeded around {reference_price:.8f}, orders={planned_order_count}")
    strategy._emit_trace(
        "grid_seeded",
        {
            "reference_price": reference_price,
            "planned_order_count": planned_order_count,
            "buy_levels": len(selected_buy_prices),
            "sell_levels": len(selected_sell_prices),
            "direction": direction,
            "leverage": _to_float(getattr(strategy, "leverage", None)),
        },
    )
    return planned_order_count


def _run_dca_cycle_for_runtime(strategy, *, last_price: float) -> float:
    now_ts = time.time()
    if getattr(strategy, "next_run_ts", 0.0) and now_ts < float(strategy.next_run_ts):
        return 0.0

    volume = _compute_dca_order_volume(
        last_price=last_price,
        amount_per_cycle=float(strategy.amount_per_cycle),
        min_order_volume=float(strategy.min_order_volume),
    )
    next_run_ts = now_ts + max(int(strategy.cycle_seconds), 1)
    if volume <= 0:
        return next_run_ts

    price_offset = max(float(strategy.price_offset_pct), 0.0) / 100.0
    target_price = last_price * (1 + price_offset)
    order_refs = strategy.buy(target_price, volume)
    strategy._record_order_submission(
        side="BUY",
        price=target_price,
        volume=volume,
        order_refs=order_refs,
    )
    strategy.write_log(f"dca order submitted price={target_price:.8f} volume={volume:.8f}")
    strategy._emit_trace(
        "dca_cycle_executed",
        {
            "price_offset_pct": float(strategy.price_offset_pct),
            "target_price": target_price,
            "volume": volume,
            "next_run_ts": next_run_ts,
        },
    )
    return next_run_ts


def _compute_grid_order_prices(
    *,
    reference_price: float,
    grid_count: int,
    grid_step_pct: float,
    max_levels: int,
) -> tuple[list[float], list[float]]:
    """
    Build symmetric grid ladder prices around current reference price.

    Returns tuple: (buy_prices, sell_prices). The function is pure so we can
    test pricing behavior without loading vn.py runtime dependencies.
    """
    if reference_price <= 0 or grid_count < 2 or grid_step_pct <= 0:
        return [], []
    level_cap = min(max(max_levels, 2), 100)
    total_levels = min(max(grid_count, 2), level_cap)
    buy_levels = max(total_levels // 2, 1)
    sell_levels = max(total_levels - buy_levels, 1)
    step_ratio = grid_step_pct / 100.0

    buy_prices: list[float] = []
    sell_prices: list[float] = []
    for level in range(1, buy_levels + 1):
        price = reference_price * (1 - step_ratio * level)
        if price > 0:
            buy_prices.append(round(price, 12))
    for level in range(1, sell_levels + 1):
        price = reference_price * (1 + step_ratio * level)
        sell_prices.append(round(price, 12))
    return buy_prices, sell_prices


def _compute_dca_order_volume(*, last_price: float, amount_per_cycle: float, min_order_volume: float) -> float:
    """
    Convert quote amount into base volume for a DCA order.

    Invalid price/amount returns zero so caller can safely skip order submit.
    """
    if last_price <= 0 or amount_per_cycle <= 0:
        return 0.0
    volume = amount_per_cycle / last_price
    if min_order_volume > 0 and volume < min_order_volume:
        return 0.0
    return float(f"{volume:.12f}")


def _build_vt_symbol(symbol: str, gateway_name: str, *, gateway=None) -> str:
    if "." in symbol:
        return symbol
    exchange_suffix = _resolve_vt_exchange_suffix(gateway, gateway_name=gateway_name)
    return f"{symbol}.{exchange_suffix}"


def _resolve_vt_exchange_suffix(gateway, *, gateway_name: str) -> str:
    """
    Build CTA-compatible vt_symbol exchange suffix.

    vn.py CTA expects the exchange enum suffix (for Binance/OKX gateways this is
    `GLOBAL`), not the gateway runtime name such as `BINANCE_LINEAR`.
    """
    exchanges = getattr(gateway, "exchanges", None)
    if isinstance(exchanges, (list, tuple)) and exchanges:
        suffix = _enum_like_name(exchanges[0])
        if suffix:
            return suffix

    normalized_gateway = str(gateway_name or "").upper().strip()
    if normalized_gateway in {"BINANCE_LINEAR", "BINANCE_SPOT", "BINANCE_INVERSE", "BINANCE_PORTFOLIO", "OKX"}:
        return "GLOBAL"
    return normalized_gateway or "UNKNOWN"


def _enum_like_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().upper()
    raw = getattr(value, "value", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return None


def _classify_runtime_error(exc: Exception) -> str:
    message = str(exc).strip()
    if message.startswith("gateway_connect_failed:"):
        lowered = message.lower()
        auth_markers = (
            "auth",
            "signature",
            "invalid api",
            "api key",
            "permission",
            "forbidden",
            "unauthorized",
            "passphrase",
        )
        if any(marker in lowered for marker in auth_markers):
            return f"gateway_auth_failed: {message}"
        return message
    known_prefixes = ("gateway_", "strategy_", "strategy_param_failed", "cta_engine_")
    if message.startswith(known_prefixes):
        return message
    return f"runtime_process_crash: {message}"


def _resolve_gateway_class(exchange: str):
    if exchange == "binance":
        try:
            from vnpy_binance import BinanceLinearGateway

            return BinanceLinearGateway
        except Exception:
            from vnpy_binance import BinanceSpotGateway

            return BinanceSpotGateway
    if exchange == "okx":
        from vnpy_okx import OkxGateway

        return OkxGateway
    raise RuntimeError(f"unsupported exchange: {exchange}")


def _build_gateway_setting(gateway_cls, context: StrategyLaunchContext) -> dict[str, Any]:
    defaults = getattr(gateway_cls, "default_setting", {})
    result = dict(defaults) if isinstance(defaults, dict) else {}
    server_value = _server_value_for_exchange(context.exchange, context.is_testnet)
    if not result:
        result = {
            "API Key": context.api_key,
            "Secret Key": context.api_secret,
            "Passphrase": context.passphrase or "",
            "Server": server_value,
        }

    for key in list(result.keys()):
        lower_key = key.lower()
        if "api" in lower_key and "key" in lower_key and "secret" not in lower_key:
            result[key] = context.api_key
        elif "secret" in lower_key:
            result[key] = context.api_secret
        elif "passphrase" in lower_key or "password" in lower_key:
            result[key] = context.passphrase or ""
        elif "server" in lower_key:
            result[key] = server_value
    return result


def _server_value_for_exchange(exchange: str, is_testnet: bool) -> str:
    normalized = str(exchange).lower().strip()
    if not is_testnet:
        return "REAL"
    # OKX gateway expects DEMO instead of TESTNET.
    if normalized == "okx":
        return "DEMO"
    return "TESTNET"


def _load_launch_context(user_id: int, strategy_id: int) -> StrategyLaunchContext:
    engine = _build_engine()
    decrypter = _build_aes_decrypter()
    try:
        with engine.connect() as conn:
            strategy_row = conn.execute(
                text(
                    """
                    SELECT strategy_type, config_json
                    FROM strategies
                    WHERE id = :strategy_id AND user_id = :user_id
                    """
                ),
                {"strategy_id": strategy_id, "user_id": user_id},
            ).mappings().first()
            if not strategy_row:
                raise RuntimeError(f"strategy {strategy_id} not found for user {user_id}")

            config = json.loads(strategy_row["config_json"] or "{}")
            if not isinstance(config, dict):
                raise RuntimeError("strategy config_json must be an object")
            exchange_account_id = int(config.get("exchange_account_id") or 0)
            if exchange_account_id <= 0:
                raise RuntimeError("strategy config requires exchange_account_id")

            account_row = conn.execute(
                text(
                    """
                    SELECT exchange, is_testnet, api_key_encrypted, api_secret_encrypted, passphrase_encrypted
                    FROM exchange_accounts
                    WHERE id = :account_id AND user_id = :user_id
                    """
                ),
                {"account_id": exchange_account_id, "user_id": user_id},
            ).mappings().first()
            if not account_row:
                raise RuntimeError(
                    f"exchange account {exchange_account_id} not found for user {user_id}"
                )

            return StrategyLaunchContext(
                user_id=user_id,
                strategy_id=strategy_id,
                strategy_type=str(strategy_row["strategy_type"]),
                config=config,
                exchange=str(account_row["exchange"]).lower(),
                is_testnet=bool(account_row["is_testnet"]),
                api_key=decrypter.decrypt(str(account_row["api_key_encrypted"])),
                api_secret=decrypter.decrypt(str(account_row["api_secret_encrypted"])),
                passphrase=(
                    decrypter.decrypt(str(account_row["passphrase_encrypted"]))
                    if account_row["passphrase_encrypted"]
                    else None
                ),
            )
    except (SQLAlchemyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to load strategy launch context: {exc}") from exc


def _build_engine():
    database_url = _str_env("DATABASE_URL", "sqlite:///./a1phquest_worker_dev.db")
    if database_url.startswith("sqlite"):
        return create_engine(database_url, future=True, connect_args={"check_same_thread": False})
    return create_engine(database_url, future=True, pool_pre_ping=True)


@dataclass(slots=True)
class _LocalAesDecrypter:
    key_material: bytes

    def decrypt(self, ciphertext: str) -> str:
        payload = json.loads(base64.b64decode(ciphertext).decode("utf-8"))
        nonce = base64.b64decode(payload["nonce"])
        cipher = base64.b64decode(payload["cipher"])
        aes = AESGCM(self.key_material)
        plain = aes.decrypt(nonce, cipher, None)
        return plain.decode("utf-8")


def _build_aes_decrypter() -> _LocalAesDecrypter:
    raw_key = _str_env("AES_MASTER_KEY", _str_env("KMS_MASTER_KEY", "")).strip()
    if not raw_key:
        raise RuntimeError("AES_MASTER_KEY is required for worker decryption")
    key = raw_key.encode("utf-8")
    if len(key) != 32:
        raise RuntimeError("AES_MASTER_KEY must be exactly 32 bytes")
    return _LocalAesDecrypter(key_material=key)


def _apply_runtime_event(
    runtime: Runtime,
    event: dict[str, Any],
    *,
    history_size: int,
) -> None:
    event_kind = str(event.get("event_kind") or "state").strip().lower()
    status = str(event.get("status", runtime.status))
    heartbeat_at = _parse_dt(event.get("last_heartbeat")) or _utcnow()
    runtime.last_heartbeat = heartbeat_at

    if event_kind != "trace":
        runtime.status = status

    error = event.get("error")
    if error:
        runtime.last_error = str(error)
    elif event_kind == "state" and status == "running":
        runtime.last_error = None

    if status in {"stopped", "failed"}:
        runtime.stopped_at = _parse_dt(event.get("stopped_at")) or runtime.stopped_at or _utcnow()

    # Heartbeat packets only refresh liveness timestamps and should not pollute
    # event sequence/history.
    if event_kind == "heartbeat":
        return

    raw_payload = event.get("payload")
    payload = _sanitize_event_payload(raw_payload if isinstance(raw_payload, dict) else {})
    if error and "error" not in payload:
        payload["error"] = str(error)
    if event_kind == "state":
        payload.setdefault("status", status)

    event_type = _resolve_runtime_event_type(
        event_kind=event_kind,
        status=status,
        explicit_type=event.get("event_type"),
        error=error,
    )
    _append_runtime_trace_event(
        runtime,
        event_type=event_type,
        payload=payload,
        history_size=history_size,
        event_time=heartbeat_at,
    )
    if event_type == "order_submitted":
        runtime.order_submitted_count += 1
    elif event_type == "order_status_update":
        runtime.order_update_count += 1
    elif event_type == "trade_filled":
        runtime.trade_fill_count += 1


def _append_runtime_trace_event(
    runtime: Runtime,
    *,
    event_type: str,
    payload: dict[str, Any],
    history_size: int,
    event_time: datetime | None = None,
) -> None:
    runtime.last_event_seq += 1
    normalized_time = event_time or _utcnow()
    runtime.last_event_type = event_type
    runtime.last_event_at = normalized_time
    runtime.recent_events.append(
        {
            "seq": runtime.last_event_seq,
            "type": event_type,
            "timestamp": _to_iso_z(normalized_time),
            "payload": payload,
        }
    )
    if history_size > 0 and len(runtime.recent_events) > history_size:
        del runtime.recent_events[:-history_size]


def _resolve_runtime_event_type(
    *,
    event_kind: str,
    status: str,
    explicit_type: Any,
    error: Any,
) -> str:
    if explicit_type not in (None, ""):
        return str(explicit_type)
    if event_kind == "trace":
        return "strategy_runtime_trace"
    if status == "starting":
        return "runtime_starting"
    if status == "running":
        return "runtime_running"
    if status == "stopping":
        return "runtime_stopping"
    if status == "stopped":
        return "runtime_stopped"
    if status == "failed":
        return "runtime_failed"
    if error:
        return "runtime_failed"
    return "runtime_status"


def _push_event(
    events: mp.Queue,
    *,
    status: str,
    error: str | None = None,
    event_kind: str = "state",
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    now = _utcnow()
    event_payload: dict[str, Any] = {
        "event_kind": event_kind,
        "status": status,
        "last_heartbeat": _to_iso_z(now),
    }
    if event_type:
        event_payload["event_type"] = event_type
    if payload:
        event_payload["payload"] = _sanitize_event_payload(payload)
    if error:
        event_payload["error"] = error
    if status in {"stopped", "failed"} and event_kind != "heartbeat":
        event_payload["stopped_at"] = _to_iso_z(now)
    events.put(event_payload)


def _push_trace_event(
    events: mp.Queue,
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    _push_event(
        events,
        status="running",
        event_kind="trace",
        event_type=event_type,
        payload=payload,
    )


def _to_iso_z(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat() + "Z"


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _str_env(name: str, default: str) -> str:
    import os

    return os.getenv(name, default)


def _float_env(name: str, default: float) -> float:
    import os

    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _normalize_order_refs(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


def _sanitize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _serialize_event_value(value) for key, value in payload.items()}


def _serialize_event_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return _to_iso_z(value)
    if isinstance(value, dict):
        return {str(key): _serialize_event_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_event_value(item) for item in value]
    return str(value)


def _to_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _to_iso_z(value)
    return str(value)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
