from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import User
from app.routers.events import replay_events
from app.ws_manager import WsManager


def test_replay_events_returns_user_scoped_history(async_runner):
    ws_manager = WsManager(backend="memory")
    async_runner(ws_manager.push_to_user(101, {"type": "strategy_runtime_update"}))
    async_runner(ws_manager.push_to_user(101, {"type": "trade_filled"}))
    async_runner(ws_manager.push_to_user(102, {"type": "other-user-event"}))

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws_manager)))
    user = User(
        id=101,
        username="replay-user",
        email="replay-user@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    response = replay_events(
        request=request,
        after_seq=0,
        since_seconds=None,
        limit=50,
        current_user=user,
    )
    assert [event.type for event in response.events] == ["strategy_runtime_update", "trade_filled"]
    assert response.next_after_seq == 2


def test_replay_events_default_window_returns_latest_items(async_runner):
    ws_manager = WsManager(backend="memory")
    for index in range(1, 6):
        async_runner(ws_manager.push_to_user(201, {"type": f"evt-{index}"}))

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws_manager)))
    user = User(
        id=201,
        username="replay-user-2",
        email="replay-user-2@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    response = replay_events(
        request=request,
        after_seq=None,
        since_seconds=None,
        limit=2,
        current_user=user,
    )
    assert [event.type for event in response.events] == ["evt-4", "evt-5"]
    assert response.next_after_seq == 5


def test_replay_events_supports_db_backend(async_runner):
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
                id=301,
                username="replay-user-db",
                email="replay-user-db@example.com",
                password_hash="x",
                role="user",
                is_active=True,
            )
        )
        db.commit()

    ws_manager = WsManager(backend="db", session_factory=session_factory, history_size=10)
    async_runner(ws_manager.push_to_user(301, {"type": "strategy_runtime_update"}))
    async_runner(ws_manager.push_to_user(301, {"type": "trade_filled"}))
    async_runner(ws_manager.push_to_user(302, {"type": "other-user-event"}))

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(ws_manager=ws_manager)))
    user = User(
        id=301,
        username="replay-user-db",
        email="replay-user-db@example.com",
        password_hash="x",
        role="user",
        is_active=True,
    )
    response = replay_events(
        request=request,
        after_seq=0,
        since_seconds=None,
        limit=50,
        current_user=user,
    )
    assert [event.type for event in response.events] == ["strategy_runtime_update", "trade_filled"]
    assert [event.event_seq for event in response.events] == [1, 2]
    assert response.next_after_seq == 2
