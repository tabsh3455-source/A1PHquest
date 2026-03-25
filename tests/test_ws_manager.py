from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User
from app.ws_manager import WsManager


class _FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.accepted = False
        self.messages: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.messages.append(payload)


def test_push_to_user_assigns_monotonic_event_sequence(async_runner):
    manager = WsManager(backend="memory")
    ws_a = _FakeWebSocket()
    ws_b = _FakeWebSocket()

    async_runner(manager.connect(11, ws_a))
    async_runner(manager.connect(11, ws_b))
    async_runner(manager.push_to_user(11, {"type": "strategy_runtime_update"}))
    async_runner(manager.push_to_user(11, {"type": "trade_filled"}))

    assert ws_a.accepted is True
    assert ws_b.accepted is True
    assert [event["event_seq"] for event in ws_a.messages] == [1, 2]
    assert [event["event_seq"] for event in ws_b.messages] == [1, 2]
    assert all(event["user_id"] == 11 for event in ws_a.messages)


def test_event_sequence_is_isolated_per_user(async_runner):
    manager = WsManager(backend="memory")
    ws_user_a = _FakeWebSocket()
    ws_user_b = _FakeWebSocket()

    async_runner(manager.connect(21, ws_user_a))
    async_runner(manager.connect(22, ws_user_b))
    async_runner(manager.push_to_user(21, {"type": "a1"}))
    async_runner(manager.push_to_user(22, {"type": "b1"}))
    async_runner(manager.push_to_user(21, {"type": "a2"}))

    assert [event["event_seq"] for event in ws_user_a.messages] == [1, 2]
    assert [event["event_seq"] for event in ws_user_b.messages] == [1]


def test_push_disconnects_failed_websocket_and_keeps_delivery_for_active_ones(async_runner):
    manager = WsManager(backend="memory")
    ws_ok = _FakeWebSocket()
    ws_fail = _FakeWebSocket(fail_send=True)

    async_runner(manager.connect(31, ws_ok))
    async_runner(manager.connect(31, ws_fail))

    async_runner(manager.push_to_user(31, {"type": "first"}))
    async_runner(manager.push_to_user(31, {"type": "second"}))

    # Failed websocket should be removed after first send failure, while active one keeps receiving.
    assert [event["type"] for event in ws_ok.messages] == ["first", "second"]
    assert [event["event_seq"] for event in ws_ok.messages] == [1, 2]


def test_event_history_replay_supports_after_seq_filter(async_runner):
    manager = WsManager(backend="memory")
    ws = _FakeWebSocket()
    async_runner(manager.connect(41, ws))
    async_runner(manager.push_to_user(41, {"type": "one", "timestamp": "2026-01-01T00:00:00+00:00"}))
    async_runner(manager.push_to_user(41, {"type": "two", "timestamp": "2026-01-01T00:00:01+00:00"}))
    async_runner(manager.push_to_user(41, {"type": "three", "timestamp": "2026-01-01T00:00:02+00:00"}))

    replay = manager.get_user_event_history(41, after_seq=1, limit=10)
    assert [item["type"] for item in replay] == ["two", "three"]


def test_push_to_user_dedupe_key_prevents_duplicate_delivery(async_runner):
    manager = WsManager(backend="memory")
    ws = _FakeWebSocket()
    async_runner(manager.connect(42, ws))

    async_runner(manager.push_to_user(42, {"type": "runtime", "dedupe_key": "rt-1"}))
    async_runner(manager.push_to_user(42, {"type": "runtime", "dedupe_key": "rt-1"}))
    async_runner(manager.push_to_user(42, {"type": "runtime", "dedupe_key": "rt-2"}))

    assert [event["event_seq"] for event in ws.messages] == [1, 2]
    assert [event["type"] for event in ws.messages] == ["runtime", "runtime"]


def test_event_history_default_limit_returns_latest_window(async_runner):
    manager = WsManager(backend="memory")
    ws = _FakeWebSocket()
    async_runner(manager.connect(43, ws))
    for index in range(1, 6):
        async_runner(manager.push_to_user(43, {"type": f"evt-{index}"}))

    replay = manager.get_user_event_history(43, limit=2)
    assert [item["type"] for item in replay] == ["evt-4", "evt-5"]
    assert [item["event_seq"] for item in replay] == [4, 5]


def test_connection_count_tracks_online_sockets(async_runner):
    manager = WsManager(backend="memory")
    ws_a = _FakeWebSocket()
    ws_b = _FakeWebSocket()
    async_runner(manager.connect(51, ws_a))
    async_runner(manager.connect(52, ws_b))

    assert manager.connection_count() == 2
    assert manager.online_user_count() == 2


def test_db_replay_backend_persists_monotonic_history(async_runner):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        db.add(
            User(
                id=61,
                username="ws-db-user",
                email="ws-db-user@example.com",
                password_hash="x",
                role="user",
                is_active=True,
            )
        )
        db.commit()

    manager = WsManager(backend="db", session_factory=session_factory, history_size=3)
    ws = _FakeWebSocket()
    async_runner(manager.connect(61, ws))

    async_runner(manager.push_to_user(61, {"type": "evt-1", "dedupe_key": "dup-1"}))
    async_runner(manager.push_to_user(61, {"type": "evt-1", "dedupe_key": "dup-1"}))
    async_runner(manager.push_to_user(61, {"type": "evt-2"}))
    async_runner(manager.push_to_user(61, {"type": "evt-3"}))
    async_runner(manager.push_to_user(61, {"type": "evt-4"}))

    assert [event["event_seq"] for event in ws.messages] == [1, 2, 3, 4]
    replay = manager.get_user_event_history(61, after_seq=None, limit=3)
    assert [item["type"] for item in replay] == ["evt-2", "evt-3", "evt-4"]
    assert [item["event_seq"] for item in replay] == [2, 3, 4]
