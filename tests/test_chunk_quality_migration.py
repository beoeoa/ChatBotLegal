from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "scripts" / "postgres_retrieval_indexes" / "002_chunk_quality_up.sql"
DOWN = ROOT / "scripts" / "postgres_retrieval_indexes" / "002_chunk_quality_down.sql"


def test_chunk_quality_sidecar_is_non_destructive_and_auditable():
    sql = UP.read_text(encoding="utf-8")
    upper = sql.upper()

    assert "CREATE TABLE IF NOT EXISTS LEGAL_CHUNK_QUALITY" in upper
    for column in (
        "chunk_id", "quality_version", "eligible", "canonical_chunk_id",
        "content_hash", "cleaned_article_title", "quality_reasons", "assessed_at",
    ):
        assert column in sql
    assert "REFERENCES legal_article_chunks(id)" in sql
    assert not any(word in upper for word in ("DELETE FROM", "TRUNCATE", "DROP TABLE"))


def test_chunk_quality_rollback_removes_only_the_sidecar():
    sql = DOWN.read_text(encoding="utf-8").strip()
    assert sql == "DROP TABLE IF EXISTS legal_chunk_quality;"
