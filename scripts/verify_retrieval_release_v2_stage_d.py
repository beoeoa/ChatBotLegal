#!/usr/bin/env python3
"""Verify Stage D shadow collections and the V3 serving manifest.

This is a read-only release gate.  It checks the persisted Chroma IDs and
metadata through SQLite, the separate exact/FTS artifact, the V3 pointer and
the unchanged M2 pointer.  It never imports vectors, changes a collection or
updates the live pointer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.build_retrieval_release_v2_lexical_index import normalize_exact


EXPECTED_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
EXPECTED_RELEASE = "retrieval-v2-20260816-v6"
EXPECTED_MANIFEST_SHA = "20072befc93dc5f66401c392cd174a278fda9efeb2ca8307c37f7fc7cf517b9f"
EXPECTED_SOURCE_SHA = "d4040ff69a0da62db0227fdfa28a0aa198d9cb385118b0aa381eae00fd108ca6"
EXPECTED_CURRENT = 391_588
EXPECTED_TEMPORAL = 639_129


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _pointer(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _bool(value: bool, *, reason: str, details: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"pass": bool(value), "reason": reason}
    if details is not None:
        result["details"] = details
    return result


def _collection_ids(connection: sqlite3.Connection, segment_id: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT embedding_id FROM embeddings WHERE segment_id = ?",
            (segment_id,),
        )
    }


def _exact_law_article_contract(connection: sqlite3.Connection) -> dict[str, Any]:
    """Validate every indexed chunk with both law and article metadata.

    The temporary table is created in SQLite's temp store; the staging index
    itself remains opened read-only and is never modified.
    """

    connection.execute(
        "CREATE TEMP TABLE expected_law_article ("
        "normalized_key TEXT NOT NULL, chunk_revision_id TEXT NOT NULL, "
        "PRIMARY KEY(normalized_key, chunk_revision_id))"
    )
    rows = connection.execute(
        "SELECT chunk_revision_id, law_number, article_number FROM chunks "
        "WHERE trim(law_number) <> '' AND trim(article_number) <> ''"
    )
    batch: list[tuple[str, str]] = []
    expected_rows = 0
    for chunk_id, law_number, article_number in rows:
        law = normalize_exact(law_number)
        article = normalize_exact(article_number)
        if not law or not article:
            continue
        batch.append((f"{law}|{article}", str(chunk_id)))
        expected_rows += 1
        if len(batch) >= 10_000:
            connection.executemany(
                "INSERT OR IGNORE INTO expected_law_article VALUES (?, ?)", batch
            )
            batch.clear()
    if batch:
        connection.executemany(
            "INSERT OR IGNORE INTO expected_law_article VALUES (?, ?)", batch
        )
    missing = connection.execute(
        "SELECT COUNT(*) FROM expected_law_article e "
        "WHERE NOT EXISTS (SELECT 1 FROM exact_lookup x "
        "WHERE x.key_kind = 'law_article' AND x.normalized_key = e.normalized_key "
        "AND x.chunk_revision_id = e.chunk_revision_id)"
    ).fetchone()[0]
    return {
        "pass": missing == 0,
        "expected_rows": expected_rows,
        "unique_expected_rows": connection.execute(
            "SELECT COUNT(*) FROM expected_law_article"
        ).fetchone()[0],
        "missing_rows": int(missing),
    }


def verify(
    *,
    import_report_path: Path,
    current_report_path: Path,
    temporal_report_path: Path,
    ann_readiness_path: Path,
    serving_manifest_path: Path,
    lexical_report_path: Path,
    lexical_index_path: Path,
    chroma_path: Path,
    active_pointer_path: Path,
    output: Path,
    expected_pointer: str = EXPECTED_POINTER,
) -> dict[str, Any]:
    import_report = _load(import_report_path)
    current_report = _load(current_report_path)
    temporal_report = _load(temporal_report_path)
    ann_readiness = _load(ann_readiness_path)
    serving = _load(serving_manifest_path)
    lexical_report = _load(lexical_report_path)
    pointer_before = _pointer(active_pointer_path)

    checks: dict[str, Any] = {}
    blockers: list[str] = []

    checks["import"] = _bool(
        import_report.get("status") == "PASS"
        and import_report.get("valid") is True
        and import_report.get("verification_valid") is True
        and import_report.get("provisional_staging") is False
        and import_report.get("release_eligible") is True
        and import_report.get("active_pointer_unchanged") is True
        and import_report.get("mutation", {}).get("baseline_mutated") is False
        and import_report.get("current_target", "").endswith(("_shadow", "_staging"))
        and import_report.get("temporal_target", "").endswith(("_shadow", "_staging")),
        reason="Kaggle vectors imported into isolated shadow collections",
        details={
            "current_target": import_report.get("current_target"),
            "temporal_target": import_report.get("temporal_target"),
            "actual_current_vectors": import_report.get("actual_current_vectors"),
            "actual_temporal_vectors": import_report.get("actual_temporal_vectors"),
        },
    )
    checks["release_binding"] = _bool(
        import_report.get("release_id") == EXPECTED_RELEASE
        and current_report.get("release_id") == EXPECTED_RELEASE
        and temporal_report.get("release_id") == EXPECTED_RELEASE
        and lexical_report.get("release_id") == EXPECTED_RELEASE
        and serving.get("release_id") == EXPECTED_RELEASE
        and current_report.get("source_snapshot_sha256") == EXPECTED_SOURCE_SHA
        and temporal_report.get("source_snapshot_sha256") == EXPECTED_SOURCE_SHA
        and serving.get("source_snapshot_sha256") == EXPECTED_SOURCE_SHA
        and lexical_report.get("manifest_sha256") == EXPECTED_MANIFEST_SHA,
        reason="All Stage D artifacts bind to one release and source snapshot",
    )
    checks["current_collection"] = _bool(
        current_report.get("valid") is True
        and current_report.get("document_state") == "current_retrievable"
        and current_report.get("expected_vector_count") == EXPECTED_CURRENT
        and current_report.get("actual_vector_count") == EXPECTED_CURRENT
        and not current_report.get("missing_ids")
        and not current_report.get("orphan_ids")
        and not current_report.get("metadata_failures")
        and not current_report.get("metadata_contract_failures"),
        reason="Current shadow IDs, counts and hydration metadata are exact",
        details={
            "expected": current_report.get("expected_vector_count"),
            "actual": current_report.get("actual_vector_count"),
        },
    )
    checks["temporal_collection"] = _bool(
        temporal_report.get("valid") is True
        and temporal_report.get("document_state") == "all"
        and temporal_report.get("expected_vector_count") == EXPECTED_TEMPORAL
        and temporal_report.get("actual_vector_count") == EXPECTED_TEMPORAL
        and not temporal_report.get("missing_ids")
        and not temporal_report.get("orphan_ids")
        and not temporal_report.get("metadata_failures")
        and not temporal_report.get("metadata_contract_failures"),
        reason="Temporal shadow IDs, counts and hydration metadata are exact",
        details={
            "expected": temporal_report.get("expected_vector_count"),
            "actual": temporal_report.get("actual_vector_count"),
        },
    )
    checks["ann_readiness"] = _bool(
        ann_readiness.get("status") == "PASS"
        and ann_readiness.get("valid") is True
        and ann_readiness.get("release_id") == EXPECTED_RELEASE
        and ann_readiness.get("source_snapshot_sha256") == EXPECTED_SOURCE_SHA
        and ann_readiness.get("active_pointer_unchanged") is True
        and ann_readiness.get("current", {}).get("collection")
        == import_report.get("current_target")
        and ann_readiness.get("temporal", {}).get("collection")
        == import_report.get("temporal_target")
        and ann_readiness.get("current", {}).get("actual_count") == EXPECTED_CURRENT
        and ann_readiness.get("temporal", {}).get("actual_count") == EXPECTED_TEMPORAL
        and ann_readiness.get("current", {}).get("checks", {}).get("bounded_replay_tail")
        is True
        and ann_readiness.get("temporal", {}).get("checks", {}).get("bounded_replay_tail")
        is True,
        reason="Both shadow collections have persisted, queryable HNSW indexes",
        details={
            "report_path": str(ann_readiness_path.resolve()),
            "report_file_sha256": file_sha256(ann_readiness_path),
            "current_persisted_count": ann_readiness.get("current", {}).get("persisted_count"),
            "temporal_persisted_count": ann_readiness.get("temporal", {}).get("persisted_count"),
            "current_warm_p95_ms": ann_readiness.get("current", {}).get("warm_query_ms", {}).get("p95"),
            "temporal_warm_p95_ms": ann_readiness.get("temporal", {}).get("warm_query_ms", {}).get("p95"),
        },
    )

    chroma_db = chroma_path / "chroma.sqlite3"
    connection = sqlite3.connect(f"file:{chroma_db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        current_ids = _collection_ids(connection, str(current_report["segment_id"]))
        temporal_ids = _collection_ids(connection, str(temporal_report["segment_id"]))
        current_outside_temporal = sorted(current_ids - temporal_ids)
        checks["current_subset_temporal"] = _bool(
            current_ids.issubset(temporal_ids),
            reason="Every current chunk is present in temporal shadow",
            details={
                "current_id_count": len(current_ids),
                "temporal_id_count": len(temporal_ids),
                "outside_temporal_count": len(current_outside_temporal),
                "outside_temporal_sample": current_outside_temporal[:10],
            },
        )
    finally:
        connection.close()

    lexical = sqlite3.connect(f"file:{lexical_index_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        metadata = dict(lexical.execute("SELECT key, value FROM release_metadata"))
        counts = json.loads(metadata.get("counts", "{}"))
        state_counts = dict(
            lexical.execute(
                "SELECT document_serving_state, COUNT(*) FROM chunks "
                "GROUP BY document_serving_state"
            ).fetchall()
        )
        fts_rows = int(lexical.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0])
        exact_rows = int(lexical.execute("SELECT COUNT(*) FROM exact_lookup").fetchone()[0])
        lexical_contract = _exact_law_article_contract(lexical)
        checks["exact_lexical_index"] = _bool(
            lexical_report.get("status") == "STAGING_BUILT"
            and lexical_report.get("provisional_staging") is False
            and lexical_report.get("release_eligible") is True
            and metadata.get("release_id") == EXPECTED_RELEASE
            and metadata.get("source_snapshot_sha256") == EXPECTED_SOURCE_SHA
            and metadata.get("manifest_sha256") == EXPECTED_MANIFEST_SHA
            and metadata.get("provisional_staging") == "false"
            and metadata.get("release_eligible") == "true"
            and metadata.get("filter_contract")
            == "document_serving_state + effective interval + eligible + manifest IDs"
            and int(counts.get("chunk_count", 0)) == EXPECTED_TEMPORAL
            and int(counts.get("current_chunk_count", 0)) == EXPECTED_CURRENT
            and int(counts.get("historical_chunk_count", 0)) == EXPECTED_TEMPORAL - EXPECTED_CURRENT
            and state_counts == {
                "current_retrievable": EXPECTED_CURRENT,
                "historical_only": EXPECTED_TEMPORAL - EXPECTED_CURRENT,
            }
            and fts_rows == EXPECTED_TEMPORAL
            and exact_rows > 0
            and lexical_contract["pass"],
            reason="Exact law/article and lexical index are complete and release-bound",
            details={
                "chunk_count": counts.get("chunk_count"),
                "current_chunk_count": counts.get("current_chunk_count"),
                "historical_chunk_count": counts.get("historical_chunk_count"),
                "fts_rows": fts_rows,
                "exact_rows": exact_rows,
                "law_article_contract": lexical_contract,
                "state_counts": state_counts,
            },
        )
    finally:
        lexical.close()

    checks["serving_manifest"] = _bool(
        str(serving.get("manifest_version") or "").startswith("legal-serving-manifest-v3")
        and serving.get("provisional_staging") is False
        and serving.get("release_eligible") is True
        and serving.get("active_pointer_changed") is False
        and serving.get("inventory_document_count") == 12_236
        and serving.get("document_state_counts", {}).get("current_retrievable") == 7_192
        and serving.get("document_state_counts", {}).get("historical_only") == 4_994
        and serving.get("document_state_counts", {}).get("future_effective") == 3
        and serving.get("document_state_counts", {}).get("quarantined") == 47
        and serving.get("current_collection", {}).get("count") == EXPECTED_CURRENT
        and serving.get("temporal_collection", {}).get("count") == EXPECTED_TEMPORAL
        and serving.get("exact_lexical_index", {}).get("count") == EXPECTED_TEMPORAL,
        reason="Manifest V3 is the single shadow serving scope",
        details={
            "manifest_sha256": serving.get("manifest_sha256"),
            "current_collection": serving.get("current_collection", {}).get("path"),
            "temporal_collection": serving.get("temporal_collection", {}).get("path"),
            "exact_lexical_index": serving.get("exact_lexical_index", {}).get("path"),
        },
    )

    pointer_values = [
        pointer_before,
        import_report.get("active_pointer_before"),
        import_report.get("active_pointer_after"),
        current_report.get("active_pointer_before"),
        current_report.get("active_pointer_after"),
        temporal_report.get("active_pointer_before"),
        temporal_report.get("active_pointer_after"),
    ]
    checks["active_pointer"] = _bool(
        pointer_before == expected_pointer
        and all(value == expected_pointer for value in pointer_values)
        and serving.get("active_pointer_changed") is False,
        reason="M2 active pointer remains unchanged",
        details={"observed": pointer_values, "expected": expected_pointer},
    )

    for name, check in checks.items():
        if not check.get("pass"):
            blockers.append(name)

    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-release-v2-stage-d-gate-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not blockers else "BLOCKED",
        "release_id": serving.get("release_id"),
        "serving_manifest_path": str(serving_manifest_path.resolve()),
        "serving_manifest_file_sha256": file_sha256(serving_manifest_path),
        "serving_manifest_sha256": serving.get("manifest_sha256"),
        "lexical_index_path": str(lexical_index_path.resolve()),
        "lexical_index_file_sha256": file_sha256(lexical_index_path),
        "checks": checks,
        "blockers": sorted(set(blockers)),
        "active_pointer_before": pointer_before,
        "active_pointer_after": _pointer(active_pointer_path),
        "active_pointer_changed": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    directory = ROOT / "reports" / "retrieval-release-v2"
    parser.add_argument("--import-report", type=Path, default=directory / "stage-d-shadow-import-v6r1-bulk.json")
    parser.add_argument("--current-report", type=Path, default=directory / "stage-d-current-shadow-v6r1.json")
    parser.add_argument("--temporal-report", type=Path, default=directory / "stage-d-temporal-shadow-v6r1.json")
    parser.add_argument("--ann-readiness", type=Path, default=directory / "stage-d-ann-readiness-v6r1.json")
    parser.add_argument("--serving-manifest", type=Path, default=directory / "legal-serving-manifest-v3-v6r1.json")
    parser.add_argument("--lexical-report", type=Path, default=directory / "legal-retrieval-v2-exact-lexical-v6r1.sqlite3.report.json")
    parser.add_argument("--lexical-index", type=Path, default=directory / "legal-retrieval-v2-exact-lexical-v6r1.sqlite3")
    parser.add_argument("--chroma-path", type=Path, default=ROOT / "release-data" / "legal" / "chroma_store")
    parser.add_argument("--active-pointer", type=Path, default=ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt")
    parser.add_argument("--expected-pointer", default=EXPECTED_POINTER)
    parser.add_argument("--output", type=Path, default=directory / "stage-d-final-report-v6r1.json")
    args = parser.parse_args(argv)
    report = verify(
        import_report_path=args.import_report.resolve(),
        current_report_path=args.current_report.resolve(),
        temporal_report_path=args.temporal_report.resolve(),
        ann_readiness_path=args.ann_readiness.resolve(),
        serving_manifest_path=args.serving_manifest.resolve(),
        lexical_report_path=args.lexical_report.resolve(),
        lexical_index_path=args.lexical_index.resolve(),
        chroma_path=args.chroma_path.resolve(),
        active_pointer_path=args.active_pointer.resolve(),
        output=args.output.resolve(),
        expected_pointer=args.expected_pointer,
    )
    print(json.dumps({"status": report["status"], "blockers": report["blockers"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
