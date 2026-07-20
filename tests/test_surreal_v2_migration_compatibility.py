from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "open_notebook" / "database" / "migrations" / "1.surrealql"


def test_initial_full_text_indexes_use_surrealdb_v2_syntax():
    migration = MIGRATION.read_text(encoding="utf-8")

    assert "SEARCH ANALYZER my_analyzer BM25 HIGHLIGHTS" in migration
    assert "FULLTEXT ANALYZER my_analyzer" not in migration
