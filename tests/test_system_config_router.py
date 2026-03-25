from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.routers.system_config import (
    get_market_data_config,
    reset_market_data_config_route,
    update_market_data_config,
)
from app.schemas import MarketDataConfigRequest


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _create_user(db: Session, username: str, role: str = "user") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


class _FakeMarketDataService:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    async def apply_runtime_config(self, config: dict):
        self.applied.append(dict(config))
        return config


def test_get_market_data_config_returns_defaults(async_runner):
    with _build_session() as db:
        user = _create_user(db, "config-reader")

        response = get_market_data_config(db=db, current_user=user)

        assert response.has_overrides is False
        assert response.market_ws_reconnect_base_seconds >= 0.5
        assert response.default_values["market_candle_cache_size"] >= 100


def test_update_market_data_config_persists_override_and_applies_runtime(async_runner):
    with _build_session() as db:
        user = _create_user(db, "config-writer")
        market_data_service = _FakeMarketDataService()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data_service=market_data_service)))

        response = async_runner(
            update_market_data_config(
                payload=MarketDataConfigRequest(
                    market_ws_reconnect_base_seconds=2,
                    market_ws_reconnect_max_seconds=9,
                    market_ws_idle_timeout_seconds=18,
                    market_candle_cache_size=1200,
                    market_rest_backfill_limit=600,
                ),
                request=request,
                db=db,
                current_user=user,
            )
        )

        assert response.has_overrides is True
        assert response.updated_by_user_id == user.id
        assert market_data_service.applied[-1]["market_ws_reconnect_base_seconds"] == 2

        fresh = get_market_data_config(db=db, current_user=user)
        assert fresh.has_overrides is True
        assert fresh.market_rest_backfill_limit == 600


def test_reset_market_data_config_clears_override_and_applies_defaults(async_runner):
    with _build_session() as db:
        user = _create_user(db, "config-reset")
        market_data_service = _FakeMarketDataService()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(market_data_service=market_data_service)))

        async_runner(
            update_market_data_config(
                payload=MarketDataConfigRequest(
                    market_ws_reconnect_base_seconds=2,
                    market_ws_reconnect_max_seconds=9,
                    market_ws_idle_timeout_seconds=18,
                    market_candle_cache_size=1200,
                    market_rest_backfill_limit=600,
                ),
                request=request,
                db=db,
                current_user=user,
            )
        )

        response = async_runner(
            reset_market_data_config_route(
                request=request,
                db=db,
                current_user=user,
            )
        )

        assert response.has_overrides is False
        assert market_data_service.applied[-1]["market_candle_cache_size"] == response.market_candle_cache_size
