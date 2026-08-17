#!/usr/bin/env python3
"""Audit Stage E positive sources against the V2 SQLite release index.

The older manifest audit loads a multi-gigabyte JSON chunk manifest.  This
read-only variant uses the already verified exact/lexical SQLite artifact,
which has the same approved chunk rows and fingerprints as Manifest V3.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from api.retrieval_release_v2_runtime import normalize_exact
from scripts.validate_retrieval_eval_suite_v1 import validate_development_suite


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _numbers(value: Any) -> set[str]:
    import re

    return {item.upper() for item in re.findall(r"[0-9]+[A-Z]?", normalize_exact(value))}


def _rows_for_source(connection: sqlite3.Connection, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    law = normalize_exact(source.get("law_number"))
    requested = _numbers(source.get("article"))
    if requested:
        keys = [f"{law}|{normalize_exact(number)}" for number in requested]
        placeholders = ",".join("?" for _ in keys)
        ids = [row[0] for row in connection.execute(
            f"SELECT chunk_revision_id FROM exact_lookup WHERE key_kind='law_article' "
            f"AND normalized_key IN ({placeholders})", keys
        )]
    else:
        ids = [row[0] for row in connection.execute(
            "SELECT chunk_revision_id FROM exact_lookup WHERE key_kind='law_number' "
            "AND normalized_key=?", (law,)
        )]
    rows: list[dict[str, Any]] = []
    for chunk_id in ids:
        row = connection.execute(
            "SELECT document_id, document_serving_state, article_number, structural_path, "
            "effective_from, effective_to FROM chunks WHERE chunk_revision_id=?",
            (chunk_id,),
        ).fetchone()
        if row:
            rows.append({
                "document_id": int(row[0]),
                "document_serving_state": str(row[1]),
                "article_number": str(row[2]),
                "structural_path": str(row[3]),
                "effective_from": str(row[4] or "")[:10],
                "effective_to": str(row[5] or "")[:10],
            })
    return rows


def _available(rows: list[Mapping[str, Any]], case: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[str, int]:
    scope = str(case.get("temporal_scope") or "current")
    as_of = str(case.get("legal_as_of") or "")[:10]
    requested = _numbers(source.get("article"))
    candidates = [
        row for row in rows
        if scope == "historical" or row.get("document_serving_state") == "current_retrievable"
    ]
    if requested:
        candidates = [
            row for row in candidates
            if requested & (_numbers(row.get("article_number")) | _numbers(row.get("structural_path")))
        ]
    eligible = [
        row for row in candidates
        if (not row.get("effective_from") or not as_of or row["effective_from"] <= as_of)
        and (not row.get("effective_to") or not as_of or as_of < row["effective_to"])
    ]
    if eligible:
        return "available", len(eligible)
    if not rows:
        return "source_absent", 0
    if requested and not candidates:
        return "article_chunk_absent", 0
    return "filter_rejected", 0


def audit(*, suite_path: Path, lexical_index: Path, output: Path) -> dict[str, Any]:
    suite = _load(suite_path)
    validation = validate_development_suite(suite)
    errors = [] if validation["valid"] else ["retrieval_eval_suite_invalid"]
    connection = sqlite3.connect(f"file:{lexical_index.resolve().as_posix()}?mode=ro", uri=True)
    rows: list[dict[str, Any]] = []
    try:
        for case in suite.get("cases") or []:
            if not isinstance(case, Mapping) or not case.get("answer_required"):
                continue
            for group in case.get("positive_source_groups") or []:
                for source in group.get("sources") or []:
                    source_rows = _rows_for_source(connection, source)
                    status, eligible = _available(source_rows, case, source)
                    rows.append({
                        "case_id": case.get("case_id"),
                        "law_number": source.get("law_number"),
                        "article": source.get("article"),
                        "temporal_scope": case.get("temporal_scope"),
                        "status": status,
                        "candidate_chunk_count": len(source_rows),
                        "eligible_chunk_count": eligible,
                    })
    finally:
        connection.close()
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[str(row["status"])] += 1
    manifest_sha = str(suite.get("manifest_sha256") or "")
    report: dict[str, Any] = {
        "schema_version": "retrieval-eval-source-availability-v2-sqlite-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors and len(rows) == status_counts.get("available", 0) else "BLOCKED",
        "suite_path": str(suite_path.resolve()),
        "suite_file_sha256": file_sha256(suite_path),
        "lexical_index_path": str(lexical_index.resolve()),
        "lexical_index_file_sha256": file_sha256(lexical_index),
        "source_snapshot_sha256": suite.get("source_snapshot_sha256"),
        "manifest_sha256": manifest_sha,
        "answer_required_case_count": sum(bool(case.get("answer_required")) for case in suite.get("cases") or [] if isinstance(case, Mapping)),
        "positive_source_count": len(rows),
        "available_source_count": status_counts.get("available", 0),
        "availability_rate": status_counts.get("available", 0) / len(rows) if rows else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "errors": sorted(set(errors)),
        "unavailable": [row for row in rows if row["status"] != "available"],
        "mutation": {
            "suite_mutated": False,
            "manifest_mutated": False,
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--lexical-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(suite_path=args.suite.resolve(), lexical_index=args.lexical_index.resolve(), output=args.output.resolve())
    print(json.dumps({"status": report["status"], "availability_rate": report["availability_rate"], "status_counts": report["status_counts"], "output": str(args.output.resolve())}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
