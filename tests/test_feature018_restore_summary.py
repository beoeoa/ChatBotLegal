from scripts.summarize_feature018_restore_rehearsal import summarize


def test_real_restore_summary_requires_every_store_and_fingerprint():
    fingerprint = "a" * 64
    report = summarize(
        release_fingerprint=fingerprint,
        copy_report={
            "passed": True,
            "inventory_match": True,
            "components": ["postgres", "objects", "vectors", "surreal"],
            "release_fingerprint": fingerprint,
            "file_count": 4,
            "backup_inventory_sha256": "b" * 64,
            "restored_inventory_sha256": "b" * 64,
        },
        retrieval_manifest={
            "postgres": {"sha256": "c" * 64},
            "chroma": {"collections": {"primary": 1}},
        },
        postgres_report={
            "status": "PASS",
            "release_fingerprint": fingerprint,
            "dump_sha256": "c" * 64,
        },
        vector_report={
            "status": "PASS",
            "release_fingerprint": fingerprint,
        },
        surreal_report={
            "status": "PASS",
            "comparison": {"schema_match": True},
            "raw_copy": {"match": True},
        },
    )
    assert report["passed"] is True

    wrong = dict(postgres_report={
        "status": "PASS",
        "release_fingerprint": "d" * 64,
        "dump_sha256": "c" * 64,
    })
    failed = summarize(
        release_fingerprint=fingerprint,
        copy_report={
            "passed": True,
            "inventory_match": True,
            "components": ["postgres", "objects", "vectors", "surreal"],
            "release_fingerprint": fingerprint,
        },
        retrieval_manifest={"postgres": {"sha256": "c" * 64}},
        postgres_report=wrong["postgres_report"],
        vector_report={"status": "PASS", "release_fingerprint": fingerprint},
        surreal_report={
            "status": "PASS",
            "comparison": {"schema_match": True},
            "raw_copy": {"match": True},
        },
    )
    assert failed["passed"] is False
    assert failed["checks"]["release_fingerprint_reconciled"] is False
