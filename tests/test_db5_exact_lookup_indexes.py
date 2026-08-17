from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP_SQL = ROOT / "scripts" / "postgres_retrieval_indexes" / "003_exact_lookup_up.sql"
DOWN_SQL = (
    ROOT / "scripts" / "postgres_retrieval_indexes" / "003_exact_lookup_down.sql"
)


def test_exact_lookup_indexes_cover_only_normalized_metadata():
    sql = UP_SQL.read_text(encoding="utf-8")
    upper = sql.upper()

    assert "FUNCTION LEGAL_NORMALIZE_IDENTIFIER" in upper
    assert "IX_LEGAL_EXACT_DOCUMENT_LAW_NUMBER" in upper
    assert "IX_LEGAL_EXACT_ARTICLE_NUMBER" in upper
    assert "IX_LEGAL_EXACT_CHUNK_HEADING" in upper
    assert "LEGAL_NORMALIZE_IDENTIFIER(LAW_NUMBER)" in upper
    assert "LEGAL_NORMALIZE_TEXT(ARTICLE_NUMBER)" in upper
    assert "LEGAL_NORMALIZE_TEXT(HEADING)" in upper
    assert "LEGAL_NORMALIZE_TEXT(CONTENT)" not in upper
    assert " LIKE " not in upper
    for destructive_keyword in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE "):
        assert destructive_keyword not in upper


def test_exact_lookup_rollback_only_drops_db5_owned_objects():
    sql = DOWN_SQL.read_text(encoding="utf-8")
    upper = sql.upper()

    assert upper.count("DROP INDEX CONCURRENTLY IF EXISTS") == 3
    assert "DROP FUNCTION IF EXISTS LEGAL_NORMALIZE_IDENTIFIER" in upper
    assert "DROP TABLE" not in upper
    assert "DROP EXTENSION" not in upper
