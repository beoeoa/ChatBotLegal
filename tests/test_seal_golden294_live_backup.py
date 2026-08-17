import json
from pathlib import Path

from scripts.seal_golden294_live_backup import build_seal, sha256


def test_backup_seal_requires_all_layers_and_matching_postgres_hash(tmp_path: Path):
    (tmp_path / "retrieval").mkdir()
    baseline = tmp_path / "pre-change-baseline.json"
    baseline.write_text('{"mutation_performed": false}', encoding="utf-8")
    (tmp_path / "surreal-open_notebook.surql").write_text(
        "DEFINE TABLE legal_crawl_candidate;", encoding="utf-8"
    )
    postgres = tmp_path / "retrieval" / "postgres.dump"
    postgres.write_bytes(b"postgres-backup")
    manifest = {
        "postgres": {"sha256": sha256(postgres)},
        "chroma": {
            "file_count": 2,
            "total_bytes": 20,
            "collections": {
                "legal_chunks_vnlegal_lal_haiphong": 10,
                "legal_chunks_vnlegal_lal": 10,
            },
        },
    }
    (tmp_path / "retrieval" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    seal = build_seal(tmp_path)
    assert seal["status"] == "pass"
    assert all(seal["checks"].values())
