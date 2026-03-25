from pathlib import Path
from types import SimpleNamespace

from app import main as app_main


def test_run_alembic_upgrade_sets_runtime_options(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAlembicConfig:
        def __init__(self, ini_path: str):
            captured["ini_path"] = ini_path
            captured["options"] = {}

        def set_main_option(self, key: str, value: str) -> None:
            options = captured.get("options")
            assert isinstance(options, dict)
            options[key] = value

    def _fake_upgrade(config, revision: str) -> None:
        captured["upgrade_revision"] = revision
        captured["upgrade_config"] = config

    monkeypatch.setattr(app_main, "AlembicConfig", _FakeAlembicConfig, raising=False)
    monkeypatch.setattr(
        app_main,
        "alembic_command",
        SimpleNamespace(upgrade=_fake_upgrade),
        raising=False,
    )
    monkeypatch.setattr(
        app_main.settings,
        "database_url",
        "postgresql+psycopg2://user:p%40ss@db:5432/a1phquest",
        raising=False,
    )
    monkeypatch.setattr(app_main.settings, "migration_pg_advisory_lock_enabled", False, raising=False)

    app_main._run_alembic_upgrade()

    ini_path = Path(str(captured["ini_path"]))
    assert ini_path.name == "alembic.ini"
    assert "apps" in str(ini_path) or "/app/" in str(ini_path).replace("\\", "/")

    options = captured["options"]
    assert isinstance(options, dict)
    script_location = str(options["script_location"])
    assert script_location.endswith("apps\\api\\migrations") or script_location.endswith("/app/migrations")
    # `%` must be escaped before putting into Alembic config parser.
    assert "p%%40ss" in str(options["sqlalchemy.url"])
    assert captured["upgrade_revision"] == "head"


def test_run_alembic_upgrade_uses_postgres_advisory_lock(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAlembicConfig:
        def __init__(self, ini_path: str):
            captured["ini_path"] = ini_path
            captured["options"] = {}
            self.attributes: dict[str, object] = {}

        def set_main_option(self, key: str, value: str) -> None:
            options = captured.get("options")
            assert isinstance(options, dict)
            options[key] = value

    class _FakeResult:
        def __init__(self, scalar_value: object):
            self._scalar_value = scalar_value

        def scalar(self) -> object:
            return self._scalar_value

    class _FakeConn:
        def __init__(self) -> None:
            self.sql_calls: list[tuple[str, dict | None]] = []

        def execute(self, statement, params=None):
            sql = str(statement)
            payload = dict(params or {})
            self.sql_calls.append((sql, payload))
            if "pg_try_advisory_lock" in sql:
                return _FakeResult(True)
            return _FakeResult(None)

    class _FakeEngine:
        def __init__(self, conn: _FakeConn):
            self.conn = conn

        def begin(self):
            conn = self.conn

            class _Ctx:
                def __enter__(self):
                    return conn

                def __exit__(self, exc_type, exc, tb):
                    return False

            return _Ctx()

    fake_conn = _FakeConn()

    def _fake_upgrade(config, revision: str) -> None:
        captured["upgrade_revision"] = revision
        captured["upgrade_connection"] = config.attributes.get("connection")

    monkeypatch.setattr(app_main, "AlembicConfig", _FakeAlembicConfig, raising=False)
    monkeypatch.setattr(
        app_main,
        "alembic_command",
        SimpleNamespace(upgrade=_fake_upgrade),
        raising=False,
    )
    monkeypatch.setattr(app_main, "engine", _FakeEngine(fake_conn), raising=False)
    monkeypatch.setattr(
        app_main.settings,
        "database_url",
        "postgresql+psycopg2://user:pass@db:5432/a1phquest",
        raising=False,
    )
    monkeypatch.setattr(app_main.settings, "migration_pg_advisory_lock_enabled", True, raising=False)
    monkeypatch.setattr(app_main.settings, "migration_pg_advisory_lock_key", 9031101, raising=False)
    monkeypatch.setattr(app_main.settings, "migration_pg_advisory_lock_timeout_seconds", 5, raising=False)

    app_main._run_alembic_upgrade("head")
    assert captured["upgrade_revision"] == "head"
    assert captured["upgrade_connection"] is fake_conn
    sql_texts = [item[0] for item in fake_conn.sql_calls]
    assert any("pg_try_advisory_lock" in sql for sql in sql_texts)
    assert any("pg_advisory_unlock" in sql for sql in sql_texts)


def test_on_startup_skips_migration_when_disabled(monkeypatch):
    class _FakeBeginCtx:
        def __enter__(self):
            return SimpleNamespace(execute=lambda *_args, **_kwargs: None)

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(app_main.settings, "migrations_run_on_startup", False, raising=False)
    monkeypatch.setattr(app_main.settings, "db_startup_max_retries", 1, raising=False)

    def _fail_migration(*_args, **_kwargs):
        raise AssertionError("migration should not run")

    monkeypatch.setattr(app_main, "_run_alembic_upgrade", _fail_migration, raising=False)
    monkeypatch.setattr(app_main, "engine", SimpleNamespace(begin=lambda: _FakeBeginCtx()), raising=False)
    monkeypatch.setattr(app_main, "ensure_bootstrap_admin", lambda: None, raising=False)

    app_main.on_startup()
