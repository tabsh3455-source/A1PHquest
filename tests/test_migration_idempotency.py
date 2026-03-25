from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types


ROOT_DIR = Path(__file__).resolve().parents[1]
VERSIONS_DIR = ROOT_DIR / "apps" / "api" / "migrations" / "versions"


class _FakeInspector:
    def __init__(
        self,
        columns_by_table: dict[str, list[str]] | None = None,
        tables: list[str] | None = None,
        indexes_by_table: dict[str, list[str]] | None = None,
    ) -> None:
        self.columns_by_table = columns_by_table or {}
        self.tables = tables or []
        self.indexes_by_table = indexes_by_table or {}

    def get_columns(self, table_name: str) -> list[dict[str, str]]:
        return [{"name": value} for value in self.columns_by_table.get(table_name, [])]

    def get_table_names(self) -> list[str]:
        return list(self.tables)

    def get_indexes(self, table_name: str) -> list[dict[str, str]]:
        return [{"name": value} for value in self.indexes_by_table.get(table_name, [])]


def _load_revision_module(filename: str, module_name: str):
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = object()
    previous_alembic = sys.modules.get("alembic")
    sys.modules["alembic"] = fake_alembic
    spec = importlib.util.spec_from_file_location(module_name, VERSIONS_DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_alembic is not None:
            sys.modules["alembic"] = previous_alembic
        else:
            sys.modules.pop("alembic", None)


def test_runtime_observability_migration_skips_columns_already_present(monkeypatch):
    revision = _load_revision_module(
        "20260323_0002_strategy_runtime_observability.py",
        "migration_20260323_0002",
    )
    added_columns: list[str] = []

    class _FakeOp:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def add_column(table_name: str, column) -> None:
            assert table_name == "strategy_runtimes"
            added_columns.append(str(column.name))

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(
        revision.sa,
        "inspect",
        lambda _bind: _FakeInspector(
            {
                "strategy_runtimes": [
                    "last_event_seq",
                    "last_audited_event_seq",
                    "order_submitted_count",
                ]
            }
        ),
    )

    revision.upgrade()
    assert added_columns == ["order_update_count", "trade_fill_count"]


def test_user_token_version_migration_is_idempotent(monkeypatch):
    revision = _load_revision_module(
        "20260324_0003_user_token_version.py",
        "migration_20260324_0003",
    )
    added_columns: list[str] = []

    class _FakeOp:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def add_column(table_name: str, column) -> None:
            assert table_name == "users"
            added_columns.append(str(column.name))

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(
        revision.sa,
        "inspect",
        lambda _bind: _FakeInspector({"users": ["id", "username", "token_version"]}),
    )

    revision.upgrade()
    assert added_columns == []


def test_user_event_replay_store_migration_skips_existing_tables(monkeypatch):
    revision = _load_revision_module(
        "20260324_0004_user_event_replay_store.py",
        "migration_20260324_0004",
    )
    created_tables: list[str] = []
    created_indexes: list[str] = []

    class _FakeOp:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def create_table(table_name: str, *args, **kwargs) -> None:
            created_tables.append(table_name)

        @staticmethod
        def create_index(index_name: str, table_name: str, columns, unique: bool = False) -> None:
            created_indexes.append(index_name)

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(
        revision.sa,
        "inspect",
        lambda _bind: _FakeInspector(
            tables=["users", "user_event_sequences", "user_events"],
            indexes_by_table={"user_events": ["ix_user_events_user_id", "ix_user_events_created_at"]},
        ),
    )

    revision.upgrade()
    assert created_tables == []
    assert created_indexes == []


def test_ai_autopilot_migration_skips_existing_tables_and_indexes(monkeypatch):
    revision = _load_revision_module(
        "20260325_0006_ai_autopilot.py",
        "migration_20260325_0006",
    )
    created_tables: list[str] = []
    created_indexes: list[str] = []

    class _FakeOp:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def create_table(table_name: str, *args, **kwargs) -> None:
            created_tables.append(table_name)

        @staticmethod
        def create_index(index_name: str, table_name: str, columns, unique: bool = False) -> None:
            created_indexes.append(index_name)

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(
        revision.sa,
        "inspect",
        lambda _bind: _FakeInspector(
            tables=[
                "users",
                "exchange_accounts",
                "strategies",
                "ai_provider_credentials",
                "ai_autopilot_policies",
                "ai_autopilot_decision_runs",
            ],
            indexes_by_table={
                "ai_provider_credentials": [
                    "ix_ai_provider_credentials_id",
                    "ix_ai_provider_credentials_user_id",
                ],
                "ai_autopilot_policies": [
                    "ix_ai_autopilot_policies_id",
                    "ix_ai_autopilot_policies_user_id",
                    "ix_ai_autopilot_policies_provider_id",
                    "ix_ai_autopilot_policies_exchange_account_id",
                ],
                "ai_autopilot_decision_runs": [
                    "ix_ai_autopilot_decision_runs_id",
                    "ix_ai_autopilot_decision_runs_user_id",
                    "ix_ai_autopilot_decision_runs_policy_id",
                    "ix_ai_autopilot_decision_runs_provider_id",
                    "ix_ai_autopilot_decision_runs_exchange_account_id",
                    "ix_ai_autopilot_decision_runs_created_at",
                ],
            },
        ),
    )

    revision.upgrade()
    assert created_tables == []
    assert created_indexes == []


def test_ai_policy_allowed_actions_migration_skips_existing_column(monkeypatch):
    revision = _load_revision_module(
        "20260325_0007_ai_policy_allowed_actions.py",
        "migration_20260325_0007",
    )
    added_columns: list[str] = []

    class _FakeOp:
        @staticmethod
        def get_bind():
            return object()

        @staticmethod
        def add_column(table_name: str, column) -> None:
            assert table_name == "ai_autopilot_policies"
            added_columns.append(str(column.name))

    monkeypatch.setattr(revision, "op", _FakeOp())
    monkeypatch.setattr(
        revision.sa,
        "inspect",
        lambda _bind: _FakeInspector(
            {"ai_autopilot_policies": ["id", "name", "allowed_actions_json"]},
            tables=["ai_autopilot_policies"],
        ),
    )

    revision.upgrade()
    assert added_columns == []
