from pathlib import Path

from open_notebook.database.async_migrate import AsyncMigrationManager


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_migration_37_is_registered_last_for_up_and_down(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)

    manager = AsyncMigrationManager()

    assert len(manager.up_migrations) == 37
    assert len(manager.down_migrations) == 37
    assert "user_ask_history" in manager.up_migrations[-1].sql
    assert "user_ask_history" in manager.down_migrations[-1].sql


def test_migration_37_keeps_legacy_strings_and_structured_snapshots_compatible():
    migration_dir = PROJECT_ROOT / "open_notebook" / "database" / "migrations"
    up_sql = (migration_dir / "37.surrealql").read_text(encoding="utf-8")
    down_sql = (migration_dir / "37_down.surrealql").read_text(encoding="utf-8")

    for sql in (up_sql, down_sql):
        normalized = " ".join(sql.casefold().split())
        assert "option<array<any>>" in normalized
        assert "array<string>" not in normalized
        assert "array<object>" not in normalized
        assert "delete " not in normalized
        assert "update " not in normalized
