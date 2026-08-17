import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = ROOT / "scripts" / "postgres_retrieval_indexes"
UP_SQL = MIGRATION_DIR / "001_retrieval_indexes_up.sql"
DOWN_SQL = MIGRATION_DIR / "001_retrieval_indexes_down.sql"

TARGET_INDEXES = {
    "ix_legal_retrieval_chunks_content_trgm",
    "ix_legal_retrieval_chunks_heading_trgm",
    "ix_legal_retrieval_articles_title_trgm",
    "ix_legal_retrieval_documents_title_trgm",
    "ix_legal_retrieval_documents_law_number_trgm",
    "ix_legal_retrieval_chunks_article_id",
    "ix_legal_retrieval_articles_active_document_id",
    "ix_legal_retrieval_relationships_source_document_id",
    "ix_legal_retrieval_relationships_target_document_id",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _created_functions(sql: str) -> set[str]:
    return {
        match.casefold()
        for match in re.findall(
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z_][a-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )
    }


def _dropped_functions(sql: str) -> set[str]:
    return {
        match.casefold()
        for match in re.findall(
            r"\bDROP\s+FUNCTION\s+(?:IF\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )
    }


def test_up_migration_adds_only_concurrent_indexes_for_current_query_expressions():
    sql = _read(UP_SQL)
    upper = sql.upper()

    assert "CREATE EXTENSION IF NOT EXISTS PG_TRGM" in upper
    assert "CREATE EXTENSION IF NOT EXISTS UNACCENT" in upper
    assert "FUNCTION LEGAL_NORMALIZE_TEXT" in upper
    assert upper.count("CREATE INDEX CONCURRENTLY IF NOT EXISTS") == len(
        TARGET_INDEXES
    )
    for index_name in TARGET_INDEXES:
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name}" in sql

    # These expressions mirror the existing leading-wildcard lexical query.
    assert "legal_normalize_text(content)" in sql
    assert "legal_normalize_text(heading)" in sql
    assert "legal_normalize_text(title)" in sql
    assert "legal_normalize_text(law_number)" in sql
    assert upper.count("GIN_TRGM_OPS") == 5

    for destructive_keyword in ("INSERT ", "UPDATE ", "DELETE ", "TRUNCATE "):
        assert destructive_keyword not in upper


def test_down_migration_only_removes_objects_owned_by_this_migration():
    up_sql = _read(UP_SQL)
    down_sql = _read(DOWN_SQL)
    down_upper = down_sql.upper()

    for index_name in TARGET_INDEXES:
        assert f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}" in down_sql

    assert down_upper.count("DROP INDEX CONCURRENTLY IF EXISTS") == len(
        TARGET_INDEXES
    )
    assert _dropped_functions(down_sql) <= _created_functions(up_sql)
    assert "DROP EXTENSION" not in down_upper
    for forbidden in (
        "DROP TABLE",
        "ALTER TABLE",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "TRUNCATE ",
    ):
        assert forbidden not in down_upper
