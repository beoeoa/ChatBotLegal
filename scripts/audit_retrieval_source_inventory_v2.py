#!/usr/bin/env python3
"""Read-only reconciliation of the complete 12,236-document legal inventory.

The command observes PostgreSQL and Chroma only.  It writes an audit artifact
and checksum; it never updates legal metadata, vectors, collections or the
active pointer.  Provisional state is intentionally separate from legal
approval: every document remains marked ``legal_review_required`` until an
official-source review is attached by the project owner.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from api.retrieval_release_contracts import (
    INVENTORY_DOCUMENT_COUNT,
    canonical_sha256,
    classify_inventory_document,
    file_sha256,
    inventory_counts,
    validate_inventory_partition,
)
from scripts.backup_legal_retrieval import _database_url
from scripts.feature005_db1_snapshot import safe_database_target
from scripts.build_vector_serving_manifest import (
    DEFAULT_CHROMA_PATH,
    _chroma_inventory,
    _pointer,
)


DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_SNAPSHOT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-snapshot-12236-v4.json"
EXPECTED_BASELINE = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
EXPECTED_CANDIDATE = "legal_chunks_candidate_3000_v1"


def _postgres_documents(*, legal_as_of: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                scope_exists = bool(
                    connection.execute(
                        text("SELECT to_regclass('public.legal_search_scope')")
                    ).scalar()
                )
                if not scope_exists:
                    raise RuntimeError("legal_search_scope_missing")
                query = text(
                    """
                    SELECT d.id AS document_id, d.title, d.law_number,
                           d.issued_date, d.status, d.effective_date, d.expired_date,
                           d.source_url, d.issuing_agency, d.scope, d.sector,
                           count(DISTINCT a.id) AS article_count,
                           count(c.id) AS chunk_count,
                           coalesce(bool_or(s.included), false) AS scope_included,
                           max(s.domain) FILTER (WHERE s.included) AS scope_domain
                    FROM legal_documents d
                    LEFT JOIN legal_articles a ON a.document_id = d.id
                    LEFT JOIN legal_article_chunks c ON c.article_id = a.id
                    LEFT JOIN legal_search_scope s ON s.document_id = d.id
                    GROUP BY d.id, d.title, d.law_number, d.issued_date, d.status,
                             d.effective_date, d.expired_date, d.source_url,
                             d.issuing_agency, d.scope, d.sector
                    ORDER BY d.id
                    """
                )
                for row in connection.execute(query).mappings():
                    item = dict(row)
                    item["classification"] = classify_inventory_document(
                        item, legal_as_of=legal_as_of
                    )
                    rows.append(item)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return rows, safe_database_target(url)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _source_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": int(row["document_id"]),
            "title": row.get("title"),
            "law_number": row.get("law_number"),
            "issued_date": _json_safe(row.get("issued_date")),
            "status": row.get("status"),
            "effective_date": _json_safe(row.get("effective_date")),
            "expired_date": _json_safe(row.get("expired_date")),
            "source_url": row.get("source_url"),
            "issuing_agency": row.get("issuing_agency"),
            "scope": row.get("scope"),
            "sector": row.get("sector"),
        }
        for row in rows
    ]


def _chroma_summary(path: Path, active: str) -> dict[str, Any]:
    collections, warnings = _chroma_inventory(path=path, active_collection=active)
    summaries: list[dict[str, Any]] = []
    for collection in collections:
        records = collection.get("records") or []
        chunk_ids = sorted(
            str(row.get("chunk_id"))
            for row in records
            if row.get("chunk_id") not in (None, "")
        )
        summaries.append(
            {
                "name": collection.get("name"),
                "role": collection.get("role"),
                "vector_count": len(records),
                "chunk_id_count": len(chunk_ids),
                "chunk_id_sha256": canonical_sha256(chunk_ids),
                "record_sha256": canonical_sha256(records),
                "fingerprints": collection.get("fingerprints") or {},
            }
        )
    return {"collections": summaries, "warnings": sorted(set(warnings))}


def build_report(*, legal_as_of: date, chroma_path: Path) -> dict[str, Any]:
    rows, database_target = _postgres_documents(legal_as_of=legal_as_of)
    classifications = [row["classification"] for row in rows]
    counts = inventory_counts(classifications)
    partition_errors = validate_inventory_partition(classifications)
    active, pointer_source = _pointer(chroma_path)
    snapshot = _source_snapshot(rows)
    snapshot_sha = canonical_sha256(snapshot)
    chroma = _chroma_summary(chroma_path, active)
    by_status: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or "<null>")
        by_status[key] = by_status.get(key, 0) + 1
    provisional_review_count = sum(
        bool(item.get("legal_review_required")) for item in classifications
    )
    expected_collections = {
        EXPECTED_BASELINE: next(
            (item for item in chroma["collections"] if item["name"] == EXPECTED_BASELINE),
            None,
        ),
        EXPECTED_CANDIDATE: next(
            (item for item in chroma["collections"] if item["name"] == EXPECTED_CANDIDATE),
            None,
        ),
    }
    report = {
        "schema_version": "retrieval-source-inventory-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of.isoformat(),
        "database_target": database_target,
        "chroma_path": str(chroma_path.resolve()),
        "active_pointer": active,
        "active_pointer_source": pointer_source,
        "source_snapshot_sha256": snapshot_sha,
        "source_snapshot_document_count": len(snapshot),
        "source_snapshot": snapshot,
        "inventory_counts": counts,
        "status_counts": dict(sorted(by_status.items())),
        "serving_state_policy": {
            "states": [
                "current_retrievable",
                "historical_only",
                "future_effective",
                "quarantined",
            ],
            "partition_invariant": "current_retrievable + historical_only + future_effective + quarantined = 12,236",
            "legal_review_required_until_attested": True,
        },
        "partition_errors": partition_errors,
        "provisional_legal_review_required_count": provisional_review_count,
        "chroma": chroma,
        "expected_release_collections": expected_collections,
        "gates": {
            "inventory_exact_12236": len(rows) == INVENTORY_DOCUMENT_COUNT,
            "serving_partition_complete": not partition_errors,
            "source_snapshot_exact_12236": len(snapshot) == INVENTORY_DOCUMENT_COUNT,
            "active_pointer_observed_only": True,
            "legal_review_complete": provisional_review_count == 0,
        },
        "gate_passed": (
            len(rows) == INVENTORY_DOCUMENT_COUNT
            and not partition_errors
            and len(snapshot) == INVENTORY_DOCUMENT_COUNT
            and provisional_review_count == 0
        ),
        "release_ready": False,
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
        "documents": classifications,
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-snapshot-output", type=Path, default=DEFAULT_SNAPSHOT_OUTPUT)
    parser.add_argument("--chroma-path", type=Path, default=None)
    parser.add_argument("--legal-as-of", type=date.fromisoformat, default=date(2026, 8, 16))
    args = parser.parse_args(argv)
    chroma_path = args.chroma_path or Path(
        __import__("os").environ.get("LEGAL_CHROMA_PATH", str(DEFAULT_CHROMA_PATH))
    )
    report = build_report(legal_as_of=args.legal_as_of, chroma_path=chroma_path.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    snapshot_output = args.source_snapshot_output.resolve()
    snapshot_output.parent.mkdir(parents=True, exist_ok=True)
    snapshot_output.write_text(
        json.dumps(
            {
                "schema_version": "legal-source-snapshot-v2",
                "document_count": len(report["source_snapshot"]),
                "source_snapshot_sha256": report["source_snapshot_sha256"],
                "documents": report["source_snapshot"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    snapshot_checksum = file_sha256(snapshot_output)
    snapshot_output.with_suffix(snapshot_output.suffix + ".sha256").write_text(
        f"{snapshot_checksum}  {snapshot_output.name}\n", encoding="ascii"
    )
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PASS" if report["gate_passed"] else "NEEDS_REVIEW",
        "inventory": report["inventory_counts"],
        "source_snapshot_sha256": report["source_snapshot_sha256"],
        "report_sha256": report["report_sha256"],
        "active_pointer": report["active_pointer"],
        "legal_review_required": report["provisional_legal_review_required_count"],
        "output": str(output),
        "source_snapshot_output": str(snapshot_output),
        "source_snapshot_file_sha256": snapshot_checksum,
    }, ensure_ascii=False))
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
