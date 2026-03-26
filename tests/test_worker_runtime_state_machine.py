import itertools
from datetime import datetime, timezone
import threading
import time
from types import SimpleNamespace

from supervisor import runtime as worker_runtime


def _fake_running_strategy_process(
    runtime_ref: str,
    user_id: int,
    strategy_id: int,
    strategy_type: str,
    config_json_hint: str,
    events,
    stop_event,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # Emit running event quickly so RuntimeRegistry.start can observe transition.
    events.put({"status": "running", "last_heartbeat": now_iso})
    while not stop_event.is_set():
        time.sleep(0.01)
    stop_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    events.put(
        {
            "status": "stopped",
            "last_heartbeat": stop_iso,
            "stopped_at": stop_iso,
        }
    )


def _fake_failed_strategy_process(
    runtime_ref: str,
    user_id: int,
    strategy_id: int,
    strategy_type: str,
    config_json_hint: str,
    events,
    stop_event,
) -> None:
    events.put(
        {
            "status": "failed",
            "error": "bootstrap failed",
            "last_heartbeat": "2026-03-22T12:10:00Z",
            "stopped_at": "2026-03-22T12:10:00Z",
        }
    )


def _fake_stalled_strategy_process(
    runtime_ref: str,
    user_id: int,
    strategy_id: int,
    strategy_type: str,
    config_json_hint: str,
    events,
    stop_event,
) -> None:
    # Emit a stale heartbeat timestamp once and then stall.
    events.put({"status": "running", "last_heartbeat": "2020-01-01T00:00:00Z"})
    while not stop_event.is_set():
        time.sleep(0.01)


class _ThreadProcess:
    _pid_counter = itertools.count(2000)

    def __init__(self, target, args, daemon=True):
        self._target = target
        self._args = args
        self.daemon = daemon
        self.pid = next(self._pid_counter)
        self._alive = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._alive = True

        def _runner() -> None:
            try:
                self._target(*self._args)
            finally:
                self._alive = False

        self._thread = threading.Thread(target=_runner, daemon=self.daemon)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._alive = False


def test_runtime_registry_transitions_from_starting_to_running_to_stopped(monkeypatch):
    monkeypatch.setattr(worker_runtime.mp, "Process", _ThreadProcess)
    monkeypatch.setattr(worker_runtime, "_strategy_process", _fake_running_strategy_process)

    registry = worker_runtime.RuntimeRegistry()
    runtime = registry.start(
        user_id=1,
        strategy_id=101,
        strategy_type="grid",
        config_json='{"exchange_account_id":1,"symbol":"BTCUSDT"}',
    )
    assert runtime.status == "running"
    assert runtime.last_heartbeat is not None

    stopped = registry.stop(runtime.runtime_ref)
    assert stopped is not None
    assert stopped.status == "stopped"
    assert stopped.stopped_at is not None


def test_runtime_registry_transitions_to_failed_and_records_last_error(monkeypatch):
    monkeypatch.setattr(worker_runtime.mp, "Process", _ThreadProcess)
    monkeypatch.setattr(worker_runtime, "_strategy_process", _fake_failed_strategy_process)

    registry = worker_runtime.RuntimeRegistry()
    runtime = registry.start(
        user_id=2,
        strategy_id=202,
        strategy_type="dca",
        config_json='{"exchange_account_id":2,"symbol":"ETHUSDT"}',
    )
    assert runtime.status == "failed"
    assert runtime.last_error == "bootstrap failed"
    assert runtime.stopped_at is not None


def test_runtime_registry_start_is_idempotent_for_same_strategy(monkeypatch):
    monkeypatch.setattr(worker_runtime.mp, "Process", _ThreadProcess)
    monkeypatch.setattr(worker_runtime, "_strategy_process", _fake_running_strategy_process)

    registry = worker_runtime.RuntimeRegistry()
    first = registry.start(
        user_id=3,
        strategy_id=303,
        strategy_type="grid",
        config_json='{"exchange_account_id":3,"symbol":"BTCUSDT"}',
    )
    second = registry.start(
        user_id=3,
        strategy_id=303,
        strategy_type="grid",
        config_json='{"exchange_account_id":3,"symbol":"BTCUSDT"}',
    )
    assert first.runtime_ref == second.runtime_ref
    registry.stop(first.runtime_ref)


def test_runtime_registry_marks_failed_on_heartbeat_timeout(monkeypatch):
    monkeypatch.setattr(worker_runtime.mp, "Process", _ThreadProcess)
    monkeypatch.setattr(worker_runtime, "_strategy_process", _fake_stalled_strategy_process)
    monkeypatch.setattr(worker_runtime, "_float_env", lambda name, default: 0.01 if name == "SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS" else default)

    registry = worker_runtime.RuntimeRegistry()
    runtime = registry.start(
        user_id=4,
        strategy_id=404,
        strategy_type="grid",
        config_json='{"exchange_account_id":4,"symbol":"BTCUSDT"}',
    )
    # First start can report running before timeout check, so refresh once more.
    refreshed = registry.get(runtime.runtime_ref)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.last_error and "heartbeat_timeout" in refreshed.last_error


def test_runtime_registry_cleanup_removes_expired_stopped_runtimes(monkeypatch):
    monkeypatch.setattr(worker_runtime.mp, "Process", _ThreadProcess)
    monkeypatch.setattr(worker_runtime, "_strategy_process", _fake_running_strategy_process)
    monkeypatch.setattr(
        worker_runtime,
        "_float_env",
        lambda name, default: 0.0 if name == "SUPERVISOR_RUNTIME_RETENTION_SECONDS" else default,
    )

    registry = worker_runtime.RuntimeRegistry()
    runtime = registry.start(
        user_id=5,
        strategy_id=505,
        strategy_type="grid",
        config_json='{"exchange_account_id":5,"symbol":"BTCUSDT"}',
    )
    stopped = registry.stop(runtime.runtime_ref)
    assert stopped is not None
    assert stopped.status == "stopped"
    registry._cleanup_finished_runtimes()
    assert registry.get(runtime.runtime_ref) is None


def test_build_gateway_setting_uses_okx_demo_for_testnet():
    class _Gateway:
        default_setting = {
            "API Key": "",
            "Secret Key": "",
            "Passphrase": "",
            "Server": ["REAL", "DEMO"],
        }

    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=1,
        strategy_type="grid",
        config={"symbol": "BTC-USDT-SWAP"},
        exchange="okx",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase="p",
    )
    setting = worker_runtime._build_gateway_setting(_Gateway, context)
    assert setting["Server"] == "DEMO"
    assert setting["API Key"] == "k"
    assert setting["Secret Key"] == "s"
    assert setting["Passphrase"] == "p"


def test_build_gateway_setting_maps_api_secret_key_variant():
    class _Gateway:
        default_setting = {"API Key": "", "API Secret": "", "Server": ["REAL", "TESTNET"]}

    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=2,
        strategy_type="grid",
        config={"symbol": "BTCUSDT"},
        exchange="binance",
        is_testnet=True,
        api_key="binance-key",
        api_secret="binance-secret",
        passphrase=None,
    )
    setting = worker_runtime._build_gateway_setting(_Gateway, context)
    assert setting["Server"] == "TESTNET"
    assert setting["API Key"] == "binance-key"
    assert setting["API Secret"] == "binance-secret"


def test_setup_runtime_strategy_fails_when_registration_never_becomes_ready(monkeypatch):
    monkeypatch.setattr(worker_runtime, "_wait_for_strategy_registration", lambda cta_engine, strategy_name: False)
    monkeypatch.setattr(
        worker_runtime,
        "_build_runtime_strategy_class",
        lambda strategy_type, emit_trace: type("FakeStrategy", (), {}),
    )

    class _FakeCtaEngine:
        def __init__(self):
            self.classes = {}
            self.strategies = {}

        def add_strategy(self, class_name, strategy_name, vt_symbol, setting):
            return None

        def init_strategy(self, strategy_name):
            return None

        def start_strategy(self, strategy_name):
            return None

    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=9,
        strategy_type="grid",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 4,
            "grid_step_pct": 0.3,
            "base_order_size": 0.001,
        },
        exchange="binance",
        is_testnet=True,
        api_key="key",
        api_secret="secret",
        passphrase=None,
    )
    try:
        worker_runtime._setup_runtime_strategy(
            cta_engine=_FakeCtaEngine(),
            context=context,
            gateway_name="BINANCE",
            gateway=SimpleNamespace(exchanges=[SimpleNamespace(name="GLOBAL")]),
            runtime_ref="rt-fail-1",
            emit_trace=lambda *args, **kwargs: None,
        )
        raise AssertionError("Expected RuntimeError when strategy registration stays pending")
    except RuntimeError as exc:
        assert "registration did not become ready" in str(exc)


def test_build_vt_symbol_prefers_gateway_exchange_suffix_over_gateway_name():
    gateway = SimpleNamespace(exchanges=[SimpleNamespace(name="GLOBAL")])
    assert worker_runtime._build_vt_symbol("BTCUSDT", "BINANCE_LINEAR", gateway=gateway) == "BTCUSDT.GLOBAL"


def test_wait_for_strategy_initialization_waits_for_future_completion():
    strategy = SimpleNamespace(inited=False)
    future_called = {"value": False}

    class _FakeFuture:
        def result(self, timeout=None):
            future_called["value"] = True
            strategy.inited = True
            return None

    cta_engine = SimpleNamespace(strategies={"grid_1": strategy})
    worker_runtime._wait_for_strategy_initialization(cta_engine, "grid_1", _FakeFuture(), timeout_seconds=0.5)
    # Call again with an already-inited strategy to verify the polling path is stable.
    worker_runtime._wait_for_strategy_initialization(cta_engine, "grid_1", None, timeout_seconds=0.1)
    assert future_called["value"] is True
    assert strategy.inited is True


def test_setup_runtime_strategy_waits_for_async_init_before_start(monkeypatch):
    monkeypatch.setattr(
        worker_runtime,
        "_build_runtime_strategy_class",
        lambda strategy_type, emit_trace: type("FakeStrategy", (), {}),
    )

    class _FakeFuture:
        def __init__(self, strategy):
            self.strategy = strategy

        def result(self, timeout=None):
            self.strategy.inited = True
            return None

    class _FakeStrategy:
        def __init__(self):
            self.inited = False
            self.trading = False

    class _FakeCtaEngine:
        def __init__(self):
            self.classes = {}
            self.strategies = {}
            self.started: list[str] = []

        def add_strategy(self, class_name, strategy_name, vt_symbol, setting):
            self.strategies[strategy_name] = _FakeStrategy()

        def init_strategy(self, strategy_name):
            return _FakeFuture(self.strategies[strategy_name])

        def start_strategy(self, strategy_name):
            strategy = self.strategies[strategy_name]
            assert strategy.inited is True
            strategy.trading = True
            self.started.append(strategy_name)

    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=10,
        strategy_type="grid",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 4,
            "grid_step_pct": 0.3,
            "base_order_size": 0.001,
        },
        exchange="binance",
        is_testnet=True,
        api_key="key",
        api_secret="secret",
        passphrase=None,
    )
    cta_engine = _FakeCtaEngine()
    strategy_name = worker_runtime._setup_runtime_strategy(
        cta_engine=cta_engine,
        context=context,
        gateway_name="BINANCE_LINEAR",
        gateway=SimpleNamespace(exchanges=[SimpleNamespace(name="GLOBAL")]),
        runtime_ref="rt-pass-1",
        emit_trace=lambda *args, **kwargs: None,
    )
    assert strategy_name in cta_engine.started
    assert cta_engine.strategies[strategy_name].trading is True


def test_build_runtime_strategy_setting_rejects_invalid_grid_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=3,
        strategy_type="grid",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 1,
            "grid_step_pct": 0.2,
            "base_order_size": 0.001,
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    try:
        worker_runtime._build_runtime_strategy_setting(context)
        raise AssertionError("Expected RuntimeError for invalid grid config")
    except RuntimeError as exc:
        assert "strategy_param_failed" in str(exc)


def test_build_runtime_strategy_setting_rejects_invalid_dca_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=4,
        strategy_type="dca",
        config={
            "symbol": "BTCUSDT",
            "cycle_seconds": 0,
            "amount_per_cycle": 100,
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    try:
        worker_runtime._build_runtime_strategy_setting(context)
        raise AssertionError("Expected RuntimeError for invalid dca config")
    except RuntimeError as exc:
        assert "strategy_param_failed" in str(exc)


def test_build_runtime_strategy_setting_accepts_combo_grid_dca_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=41,
        strategy_type="combo_grid_dca",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 8,
            "grid_step_pct": 0.4,
            "base_order_size": 0.001,
            "max_grid_levels": 10,
            "cycle_seconds": 300,
            "amount_per_cycle": 25,
            "price_offset_pct": 0.2,
            "min_order_volume": 0.0001,
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    setting = worker_runtime._build_runtime_strategy_setting(context)
    assert setting["grid_count"] == 8
    assert setting["max_grid_levels"] == 10
    assert setting["cycle_seconds"] == 300
    assert setting["amount_per_cycle"] == 25
    assert setting["price_offset_pct"] == 0.2
    assert setting["min_order_volume"] == 0.0001


def test_build_runtime_strategy_setting_rejects_invalid_combo_grid_dca_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=42,
        strategy_type="combo_grid_dca",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 8,
            "grid_step_pct": 0.4,
            "base_order_size": 0.001,
            "cycle_seconds": 0,
            "amount_per_cycle": 25,
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    try:
        worker_runtime._build_runtime_strategy_setting(context)
        raise AssertionError("Expected RuntimeError for invalid combo DCA config")
    except RuntimeError as exc:
        assert "strategy_param_failed" in str(exc)


def test_build_runtime_strategy_setting_accepts_futures_grid_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=43,
        strategy_type="futures_grid",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 8,
            "grid_step_pct": 0.4,
            "base_order_size": 0.001,
            "max_grid_levels": 10,
            "leverage": 5,
            "direction": "short",
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    setting = worker_runtime._build_runtime_strategy_setting(context)
    assert setting["grid_count"] == 8
    assert setting["max_grid_levels"] == 10
    assert setting["leverage"] == 5
    assert setting["direction"] == "short"


def test_build_runtime_strategy_setting_rejects_invalid_futures_grid_config():
    context = worker_runtime.StrategyLaunchContext(
        user_id=1,
        strategy_id=44,
        strategy_type="futures_grid",
        config={
            "symbol": "BTCUSDT",
            "grid_count": 8,
            "grid_step_pct": 0.4,
            "base_order_size": 0.001,
            "leverage": 0,
            "direction": "neutral",
        },
        exchange="binance",
        is_testnet=True,
        api_key="k",
        api_secret="s",
        passphrase=None,
    )
    try:
        worker_runtime._build_runtime_strategy_setting(context)
        raise AssertionError("Expected RuntimeError for invalid futures grid config")
    except RuntimeError as exc:
        assert "strategy_param_failed" in str(exc)


class _FakeGridSeederStrategy:
    def __init__(self, direction: str) -> None:
        self.grid_count = 6
        self.grid_step_pct = 1.0
        self.max_grid_levels = 6
        self.base_order_size = 0.01
        self.direction = direction
        self.leverage = 3
        self.buy_calls: list[tuple[float, float]] = []
        self.sell_calls: list[tuple[float, float]] = []

    def buy(self, price: float, volume: float):
        self.buy_calls.append((price, volume))
        return [f"BUY-{len(self.buy_calls)}"]

    def sell(self, price: float, volume: float):
        self.sell_calls.append((price, volume))
        return [f"SELL-{len(self.sell_calls)}"]

    def _record_order_submission(self, **_: object):
        return None

    def write_log(self, _: str):
        return None

    def _emit_trace(self, _: str, __: dict):
        return None


def test_seed_grid_orders_for_runtime_neutral_keeps_both_sides():
    strategy = _FakeGridSeederStrategy("neutral")
    planned = worker_runtime._seed_grid_orders_for_runtime(strategy, reference_price=100.0)
    assert planned == len(strategy.buy_calls) + len(strategy.sell_calls)
    assert len(strategy.buy_calls) > 0
    assert len(strategy.sell_calls) > 0


def test_seed_grid_orders_for_runtime_long_keeps_buy_side_only():
    strategy = _FakeGridSeederStrategy("long")
    planned = worker_runtime._seed_grid_orders_for_runtime(strategy, reference_price=100.0)
    assert planned == len(strategy.buy_calls)
    assert len(strategy.buy_calls) > 0
    assert len(strategy.sell_calls) == 0


def test_seed_grid_orders_for_runtime_short_keeps_sell_side_only():
    strategy = _FakeGridSeederStrategy("short")
    planned = worker_runtime._seed_grid_orders_for_runtime(strategy, reference_price=100.0)
    assert planned == len(strategy.sell_calls)
    assert len(strategy.sell_calls) > 0
    assert len(strategy.buy_calls) == 0


def test_compute_grid_order_prices_returns_symmetric_ladder_and_caps_levels():
    buy_prices, sell_prices = worker_runtime._compute_grid_order_prices(
        reference_price=100,
        grid_count=20,
        grid_step_pct=1.0,
        max_levels=6,
    )
    # max_levels=6 means 3 buy + 3 sell levels.
    assert len(buy_prices) == 3
    assert len(sell_prices) == 3
    assert buy_prices[0] < 100
    assert sell_prices[0] > 100
    assert buy_prices == sorted(buy_prices, reverse=True)
    assert sell_prices == sorted(sell_prices)


def test_compute_dca_order_volume_respects_minimum_volume():
    assert worker_runtime._compute_dca_order_volume(
        last_price=100,
        amount_per_cycle=10,
        min_order_volume=0.05,
    ) == 0.1
    assert worker_runtime._compute_dca_order_volume(
        last_price=100,
        amount_per_cycle=1,
        min_order_volume=0.05,
    ) == 0.0


def test_classify_runtime_error_maps_gateway_auth_failures():
    error = RuntimeError("gateway_connect_failed: invalid api key")
    classified = worker_runtime._classify_runtime_error(error)
    assert classified.startswith("gateway_auth_failed:")


def test_apply_runtime_event_tracks_trace_sequence_and_counters():
    runtime = worker_runtime.Runtime(
        runtime_ref="rt-trace-1",
        user_id=1,
        strategy_id=1,
        strategy_type="grid",
        process=_ThreadProcess(target=lambda *_: None, args=()),
        events=None,
        stop_event=threading.Event(),
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    heartbeat = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    worker_runtime._apply_runtime_event(
        runtime,
        {
            "event_kind": "trace",
            "event_type": "order_submitted",
            "status": "running",
            "last_heartbeat": heartbeat,
            "payload": {"order_id": "ord-1"},
        },
        history_size=2,
    )
    worker_runtime._apply_runtime_event(
        runtime,
        {
            "event_kind": "trace",
            "event_type": "order_status_update",
            "status": "running",
            "last_heartbeat": heartbeat,
            "payload": {"order_id": "ord-1", "status": "FILLED"},
        },
        history_size=2,
    )
    worker_runtime._apply_runtime_event(
        runtime,
        {
            "event_kind": "trace",
            "event_type": "trade_filled",
            "status": "running",
            "last_heartbeat": heartbeat,
            "payload": {"trade_id": "tr-1"},
        },
        history_size=2,
    )
    assert runtime.last_event_seq == 3
    assert runtime.order_submitted_count == 1
    assert runtime.order_update_count == 1
    assert runtime.trade_fill_count == 1
    # History is bounded by provided size.
    assert len(runtime.recent_events) == 2
    assert runtime.recent_events[0]["type"] == "order_status_update"
    assert runtime.recent_events[1]["type"] == "trade_filled"
