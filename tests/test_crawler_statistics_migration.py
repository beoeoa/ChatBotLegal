from pathlib import Path


def test_crawler_statistics_are_flexible_objects_in_migration_33():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "open_notebook" / "database" / "migrations" / "33.surrealql").read_text(encoding="utf-8")

    assert "statistics ON TABLE legal_crawl_run TYPE object FLEXIBLE" in migration
    assert "last_run_stats ON TABLE legal_crawl_source TYPE option<object> FLEXIBLE" in migration
