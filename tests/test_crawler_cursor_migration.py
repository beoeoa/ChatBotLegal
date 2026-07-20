from pathlib import Path


def test_cursor_repair_migration_declares_both_missing_fields():
    migration = Path("open_notebook/database/migrations/34.surrealql").read_text(encoding="utf-8")

    assert "listing_cursor" in migration
    assert "max_listing_pages_per_run" in migration
