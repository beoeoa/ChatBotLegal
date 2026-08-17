from api.legal_data_manifest import build_read_only_manifest


def test_manifest_partitions_postgres_chunks_and_reports_vector_orphans():
    rows = [
        {
            "chunk_id": 1,
            "document_status": "active",
            "article_status": "active",
            "included": True,
            "eligible": True,
            "has_content": True,
        },
        {
            "chunk_id": 2,
            "document_status": "active",
            "article_status": "active",
            "included": True,
            "eligible": True,
            "has_content": True,
        },
        {
            "chunk_id": 3,
            "document_status": "expired",
            "article_status": "active",
            "included": False,
            "eligible": True,
            "has_content": True,
        },
        {
            "chunk_id": 4,
            "document_status": "staging",
            "article_status": "staging",
            "included": False,
            "eligible": True,
            "has_content": True,
        },
        {
            "chunk_id": 5,
            "document_status": "active",
            "article_status": "active",
            "included": True,
            "eligible": False,
            "canonical_chunk_id": 1,
            "has_content": True,
        },
    ]

    manifest = build_read_only_manifest(
        postgres_rows=rows,
        vector_chunk_ids={1, 99},
        collection_name="legal-core",
        validity_snapshot={"schema_version": "legal-validity-serving-v1", "documents": {}},
    )

    assert manifest["read_only"] is True
    assert manifest["corpus_mutated"] is False
    assert manifest["counts"] == {
        "postgres_total": 5,
        "vector_total": 2,
        "active_vectorized": 1,
        "missing_vector": 1,
        "historical": 1,
        "staging": 1,
        "duplicate": 1,
        "inactive_other": 0,
        "orphan_vector": 1,
    }
    assert manifest["missing_chunk_ids"] == [2]
    assert manifest["orphan_vector_chunk_ids"] == [99]
    assert manifest["partition_complete"] is True


def test_manifest_fingerprint_is_deterministic():
    row = {
        "chunk_id": 1,
        "document_status": "active",
        "article_status": "active",
        "included": True,
        "eligible": True,
        "has_content": True,
    }
    first = build_read_only_manifest(
        postgres_rows=[row],
        vector_chunk_ids={1},
        collection_name="legal-core",
        validity_snapshot=None,
    )
    second = build_read_only_manifest(
        postgres_rows=[dict(reversed(list(row.items())))],
        vector_chunk_ids={1},
        collection_name="legal-core",
        validity_snapshot=None,
    )

    assert first["manifest_fingerprint"] == second["manifest_fingerprint"]


def test_manifest_binds_embedding_and_pipeline_fingerprints_without_mutation():
    manifest = build_read_only_manifest(
        postgres_rows=[],
        vector_chunk_ids=set(),
        collection_name="legal-core-active",
        validity_snapshot=None,
        embedding_fingerprint="vnlegal-lal-sha256",
        pipeline_version="answer-pipeline-v3",
    )

    assert manifest["embedding_fingerprint"] == "vnlegal-lal-sha256"
    assert manifest["pipeline_version"] == "answer-pipeline-v3"
    assert manifest["read_only"] is True
    assert manifest["vectors_mutated"] is False
