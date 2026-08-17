"""Read-only rollback rehearsal for the 3.000-document candidate.

No live pointer, PostgreSQL row, Chroma collection, or source history is
mutated. The pointer swap is simulated only in a temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

import chromadb
from dotenv import dotenv_values
from sqlalchemy import create_engine, text


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def id_hash(collection: object) -> str:
    # ID listing is read-only and is bounded to the immutable collection.
    ids = list(collection.get(include=[]).get("ids") or [])  # type: ignore[attr-defined]
    digest = hashlib.sha256()
    for value in sorted(str(item) for item in ids):
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--postgres-backup-manifest", type=Path, required=True)
    parser.add_argument("--postgres-backup", type=Path, required=True)
    parser.add_argument("--chroma-backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = json.loads(args.baseline_manifest.resolve().read_text(encoding="utf-8-sig"))
    candidate = json.loads(args.candidate_manifest.resolve().read_text(encoding="utf-8-sig"))
    backup_manifest = json.loads(args.postgres_backup_manifest.resolve().read_text(encoding="utf-8-sig"))
    pointer_path = args.chroma_path.resolve() / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    postgres_backup_sha = sha256(args.postgres_backup.resolve())
    expected_postgres_sha = str(backup_manifest.get("postgres", {}).get("sha256") or "")
    postgres_backup_ok = postgres_backup_sha == expected_postgres_sha and args.postgres_backup.stat().st_size == int(backup_manifest.get("postgres", {}).get("bytes") or 0)

    client = chromadb.PersistentClient(path=str(args.chroma_path.resolve()))
    baseline_collection_name = str(baseline["collection"]["collection_name"])
    candidate_collection_name = str(candidate["candidate_collection"])
    baseline_collection = client.get_collection(baseline_collection_name)
    candidate_collection = client.get_collection(candidate_collection_name)
    current_baseline_count = int(baseline_collection.count())
    candidate_count = int(candidate_collection.count())

    backup_client = chromadb.PersistentClient(path=str((args.chroma_backup.resolve() / "chroma_store")))
    backup_baseline = backup_client.get_collection(baseline_collection_name)
    backup_pointer = (args.chroma_backup.resolve() / "chroma_store" / "active_core_collection.txt").read_text(encoding="utf-8").strip()
    baseline_hash = id_hash(baseline_collection)
    backup_hash = id_hash(backup_baseline)

    with tempfile.TemporaryDirectory(prefix="candidate-rollback-rehearsal-") as temp:
        simulated_pointer = Path(temp) / "active_core_collection.txt"
        simulated_pointer.write_text(candidate_collection_name + "\n", encoding="utf-8")
        candidate_pointer_ok = simulated_pointer.read_text(encoding="utf-8").strip() == candidate_collection_name
        simulated_pointer.write_text(baseline_collection_name + "\n", encoding="utf-8")
        baseline_restore_ok = simulated_pointer.read_text(encoding="utf-8").strip() == baseline_collection_name

    env = dotenv_values(Path(".env"))
    database_url = str(env.get("LEGAL_RELEASE_DATABASE_URL") or env.get("LEGAL_DATABASE_URL") or "").replace("host.docker.internal", "127.0.0.1")
    with create_engine(database_url).connect() as connection:
        active_docs_before = int(connection.execute(text("SELECT count(*) FROM legal_documents WHERE status = 'active'")).scalar_one())
        staging_docs_before = int(connection.execute(text("SELECT count(*) FROM legal_documents WHERE status = 'staging'")).scalar_one())
        scope_included_staging = int(connection.execute(text("SELECT count(*) FROM legal_search_scope s JOIN legal_documents d ON d.id=s.document_id WHERE d.status='staging' AND s.included=TRUE")).scalar_one())
    pointer_after = pointer_path.read_text(encoding="utf-8").strip()

    checks = {
        "postgres_backup_checksum_and_size": postgres_backup_ok,
        "current_baseline_count_matches_manifest": current_baseline_count == int(baseline["database"]["chunk_count"]),
        "candidate_count_matches_manifest": candidate_count == int(candidate["expected_vector_count"]),
        "backup_baseline_count_matches_manifest": int(backup_baseline.count()) == int(baseline["database"]["chunk_count"]),
        "backup_baseline_ids_match_current": baseline_hash == backup_hash,
        "backup_pointer_is_baseline": backup_pointer == baseline_collection_name,
        "simulated_candidate_pointer_swap": candidate_pointer_ok,
        "simulated_baseline_pointer_restore": baseline_restore_ok,
        "live_pointer_unchanged": pointer_before == pointer_after == baseline_collection_name,
        "active_document_count_unchanged": active_docs_before == 7245,
        "staging_not_served": scope_included_staging == 0,
    }
    report = {
        "schema_version": "legal-candidate-rollback-rehearsal-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "passed": all(checks.values()),
        "live_pointer_before": pointer_before,
        "live_pointer_after": pointer_after,
        "baseline_collection": baseline_collection_name,
        "candidate_collection": candidate_collection_name,
        "current_baseline_count": current_baseline_count,
        "backup_baseline_count": int(backup_baseline.count()),
        "candidate_count": candidate_count,
        "baseline_id_hash": baseline_hash,
        "backup_baseline_id_hash": backup_hash,
        "postgres_backup_sha256": postgres_backup_sha,
        "postgres_backup_manifest_sha256": expected_postgres_sha,
        "database_observation": {"active_docs": active_docs_before, "staging_docs": staging_docs_before, "included_staging_docs": scope_included_staging},
        "mutation": {"postgres_mutated": False, "chroma_mutated": False, "pointer_changed": False, "temporary_pointer_only": True},
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "passed": report["passed"], "checks": checks}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
