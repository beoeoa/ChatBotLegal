from pathlib import Path

from open_notebook.database.async_migrate import AsyncMigrationManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPERATIONAL_TABLES = {
    "legal_validity_observation",
    "legal_validity_event",
    "legal_validity_decision",
    "legal_validity_sync_run",
    "legal_validity_sync_lease",
}


def test_migration_43_is_registered_after_the_reserved_optional_slot(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT)

    manager = AsyncMigrationManager()

    assert len(manager.up_migrations) == 44
    assert len(manager.down_migrations) == 44
    assert all(table in manager.up_migrations[-2].sql for table in OPERATIONAL_TABLES)
    assert all(table in manager.down_migrations[-2].sql for table in OPERATIONAL_TABLES)
    assert "form_workflow_notification" in manager.up_migrations[-1].sql
    assert "form_workflow_notification" in manager.down_migrations[-1].sql


def test_migration_up_and_down_touch_only_feature_operational_tables():
    migration_dir = PROJECT_ROOT / "open_notebook" / "database" / "migrations"
    up_sql = (migration_dir / "43.surrealql").read_text(encoding="utf-8")
    down_sql = (migration_dir / "43_down.surrealql").read_text(encoding="utf-8")
    normalized_up = " ".join(up_sql.casefold().split())
    normalized_down = " ".join(down_sql.casefold().split())

    for table in OPERATIONAL_TABLES:
        assert f"define table if not exists {table}" in normalized_up
        assert f"remove table if exists {table}" in normalized_down

    for corpus_table in ("legal_documents", "legal_articles", "legal_chunks"):
        assert corpus_table not in normalized_up
        assert corpus_table not in normalized_down
    assert "update " not in normalized_up
    assert "delete " not in normalized_up
    assert "update " not in normalized_down
    assert "delete " not in normalized_down
