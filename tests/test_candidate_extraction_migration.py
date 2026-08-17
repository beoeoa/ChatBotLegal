from pathlib import Path


def test_candidate_extraction_migration_keeps_review_evidence_fields():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "open_notebook" / "database" / "migrations" / "34.surrealql").read_text(encoding="utf-8")
    rollback = (root / "open_notebook" / "database" / "migrations" / "34_down.surrealql").read_text(encoding="utf-8")

    assert "extraction_result ON TABLE legal_crawl_candidate" in migration
    assert "review_recommendation ON TABLE legal_crawl_candidate" in migration
    assert "content_hash" in migration
    assert "idx_legal_crawl_candidate_content_hash" in rollback


def test_candidate_review_metadata_migration_preserves_nested_objects():
    root = Path(__file__).resolve().parents[1]
    migration = (
        root / "open_notebook" / "database" / "migrations" / "39.surrealql"
    ).read_text(encoding="utf-8")
    normalized = " ".join(migration.casefold().split())

    for field in (
        "raw_metadata",
        "uploaded_file",
        "extraction_result",
        "legal_validity_flags",
        "review_recommendation",
    ):
        assert (
            f"define field overwrite {field} on table legal_crawl_candidate "
            "type option<object> flexible;"
        ) in normalized

    assert "delete " not in normalized
    assert "update " not in normalized
