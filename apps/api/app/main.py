from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .bootstrap import ensure_bootstrap_admin
from .config import get_settings
from .csrf import CSRFMiddleware
from .db import SessionLocal, engine
from .routers import ai, auth, events, exchange_accounts, market, ops, orders, risk, strategies, system_config, ws
from .services.ai_autopilot import AiAutopilotService
from .services.market_data import MarketDataService
from .services.system_config import get_market_data_config_values
from .ws_manager import WsManager

settings = get_settings()
logger = logging.getLogger(__name__)

try:
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal local envs
    alembic_command = None
    AlembicConfig = None

def on_startup() -> None:
    for attempt in range(1, settings.db_startup_max_retries + 1):
        try:
            if settings.migrations_run_on_startup:
                _run_alembic_upgrade()
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
            ensure_bootstrap_admin()
            logger.info("Database is ready%s", ", migrations applied" if settings.migrations_run_on_startup else "")
            return
        except Exception as exc:
            logger.warning(
                "Database not ready (attempt %s/%s): %s",
                attempt,
                settings.db_startup_max_retries,
                exc,
            )
            time.sleep(settings.db_startup_retry_seconds)

    raise RuntimeError("Database startup retry limit reached")


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    on_startup()
    market_data_service = MarketDataService(ws_manager=app.state.ws_manager)
    ai_autopilot_service = AiAutopilotService(
        market_data_service=market_data_service,
        ws_manager=app.state.ws_manager,
    )
    db = SessionLocal()
    try:
        await market_data_service.apply_runtime_config(get_market_data_config_values(db))
    finally:
        db.close()
    app.state.market_data_service = market_data_service
    app.state.ai_autopilot_service = ai_autopilot_service
    await market_data_service.start()
    await ai_autopilot_service.start()
    try:
        yield
    finally:
        await ai_autopilot_service.stop()
        await market_data_service.stop()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=app_lifespan)
app.state.ws_manager = WsManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origin_list(),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CSRFMiddleware)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": settings.app_name}


def _run_alembic_upgrade(revision: str = "head") -> None:
    """
    Run schema migrations to head before serving traffic.

    Using Alembic as startup gate keeps schema versioning explicit and avoids
    drift from ad-hoc `create_all` mutations in long-lived environments.
    """
    project_root = Path(__file__).resolve().parents[1]
    alembic_ini = project_root / "alembic.ini"
    if not alembic_ini.exists():
        raise RuntimeError(f"alembic.ini not found at {alembic_ini}")
    if AlembicConfig is None or alembic_command is None:
        raise RuntimeError("alembic package is not installed in current runtime")

    config = AlembicConfig(str(alembic_ini))
    config.set_main_option("script_location", str(project_root / "migrations"))
    # ConfigParser treats % as interpolation marker, so percent signs in URL
    # must be escaped before setting runtime option.
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    if _is_postgres_url(settings.database_url) and settings.migration_pg_advisory_lock_enabled:
        _run_migration_with_postgres_lock(config, revision)
        return
    alembic_command.upgrade(config, revision)


def _run_migration_with_postgres_lock(config: AlembicConfig, revision: str) -> None:
    lock_key = int(settings.migration_pg_advisory_lock_key)
    lock_timeout_seconds = max(int(settings.migration_pg_advisory_lock_timeout_seconds), 1)
    # Use transactional connection so alembic upgrade is committed when lock path
    # is enabled. Plain engine.connect() can rollback changes on close.
    with engine.begin() as conn:
        _acquire_postgres_advisory_lock(
            conn,
            lock_key=lock_key,
            timeout_seconds=lock_timeout_seconds,
        )
        try:
            # Reuse the locked connection so migration and lock share one session.
            config.attributes["connection"] = conn
            alembic_command.upgrade(config, revision)
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:lock_key)"), {"lock_key": lock_key})
            except Exception as exc:
                # On migration failure PostgreSQL keeps the transaction in aborted
                # state, and the connection close will release the advisory lock
                # anyway. We log instead of masking the original migration error.
                logger.warning("Failed to release migration advisory lock cleanly: %s", exc)


def _acquire_postgres_advisory_lock(
    conn: Connection,
    *,
    lock_key: int,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": lock_key},
        ).scalar()
        if bool(acquired):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"failed to acquire migration advisory lock within {timeout_seconds}s"
            )
        time.sleep(0.2)


def _is_postgres_url(database_url: str) -> bool:
    normalized = str(database_url).lower().strip()
    return normalized.startswith("postgresql")


app.include_router(auth.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(exchange_accounts.router)
app.include_router(market.router)
app.include_router(ops.router)
app.include_router(orders.router)
app.include_router(strategies.router)
app.include_router(system_config.router)
app.include_router(risk.router)
app.include_router(ws.router)
