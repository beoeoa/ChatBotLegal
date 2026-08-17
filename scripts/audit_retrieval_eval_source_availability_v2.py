#!/usr/bin/env python3
"""Audit that every evaluation source is present in the approved V2 corpus.

This is deliberately an audit, not a mapper.  It never invents document IDs,
validity intervals or official URLs.  A source is available only when the
approved chunk manifest contains the requested law and article and the chunk
is eligible for the case temporal scope.  The report is safe to run before a
benchmark and does not mutate datasets, vectors or pointers.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from api.retrieval_release_v2_runtime import normalize_exact
from scripts.validate_retrieval_eval_suite_v1 import validate_suite


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
_NUMBER_RE = re.compile(r"\d+[A-Z]?", re.IGNORECASE)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _article_numbers(value: Any) -> set[str]:
    return {match.upper() for match in _NUMBER_RE.findall(normalize_exact(value))}


def _source_rows(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group in case.get("positive_source_groups") or []:
        rows.extend(dict(source) for source in group.get("sources") or [] if isinstance(source, Mapping))
    return rows


def _case_match(source: Mapping[str, Any], case: Mapping[str, Any], by_law: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, Any]:
    law = normalize_exact(source.get("law_number"))
    requested_articles = _article_numbers(source.get("article"))
    candidates = list(by_law.get(law) or [])
    temporal_scope = str(case.get("temporal_scope") or "current")
    as_of = str(case.get("legal_as_of") or "")[:10]
    source_from = str(source.get("validity_from") or "")[:10]
    source_to = str(source.get("validity_to") or "")[:10]

    def date_allowed(row: Mapping[str, Any]) -> bool:
        effective_from = str(row.get("effective_from") or source_from)[:10]
        effective_to = str(row.get("effective_to") or source_to)[:10]
        return (
            (not effective_from or not as_of or effective_from <= as_of)
            and (not effective_to or not as_of or as_of < effective_to)
        )

    scoped = [
        row for row in candidates
        if temporal_scope == "historical"
        or str(row.get("document_serving_state")) == "current_retrievable"
    ]
    article_candidates = scoped
    if requested_articles:
        article_candidates = [
            row for row in scoped
            if requested_articles & (
                _article_numbers(row.get("article_number"))
                | _article_numbers(row.get("structural_path"))
            )
        ]
    eligible = [row for row in article_candidates if date_allowed(row)]
    reason = "available"
    if not candidates:
        reason = "source_absent"
    elif requested_articles and not article_candidates:
        reason = "article_chunk_absent"
    elif not eligible:
        reason = "filter_rejected" if requested_articles else "temporal_or_serving_state_unavailable"
    return {
        "law_number": source.get("law_number"),
        "article": source.get("article"),
        "case_id": case.get("case_id"),
        "temporal_scope": temporal_scope,
        "candidate_chunk_count": len(candidates),
        "eligible_chunk_count": len(eligible),
        "status": reason,
        "document_ids": sorted({int(row["document_id"]) for row in eligible if row.get("document_id") is not None}),
    }


def audit(*, suite_path: Path, manifest_path: Path, output: Path) -> dict[str, Any]:
    missing = [
        name for name, path in (("suite", suite_path), ("manifest", manifest_path))
        if not path.is_file()
    ]
    if missing:
        report: dict[str, Any] = {
            "schema_version": "retrieval-eval-source-availability-v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCKED",
            "suite_path": str(suite_path.resolve()),
            "manifest_path": str(manifest_path.resolve()),
            "errors": [f"{name}_missing" for name in missing],
            "answer_required_case_count": 0,
            "positive_source_count": 0,
            "available_source_count": 0,
            "availability_rate": 0.0,
            "status_counts": {},
            "unavailable": [],
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
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
        )
        return report
    suite = _load(suite_path)
    manifest = _load(manifest_path)
    validation = validate_suite(suite, require_complete=True)
    errors: list[str] = []
    if not validation["valid"]:
        errors.append("retrieval_eval_suite_invalid")
    if manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        errors.append("v2_chunk_manifest_required")
    if manifest.get("approved") is not True or manifest.get("legal_review_attestation") is not True:
        errors.append("approved_v2_manifest_required")
    if suite.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        errors.append("source_snapshot_sha256_mismatch")
    if suite.get("manifest_sha256") != manifest.get("manifest_sha256"):
        errors.append("manifest_sha256_mismatch")

    by_law: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in manifest.get("chunks") or []:
        if isinstance(row, Mapping):
            by_law[normalize_exact(row.get("law_number"))].append(row)
    rows: list[dict[str, Any]] = []
    for case in suite.get("cases") or []:
        if not isinstance(case, Mapping) or not case.get("answer_required"):
            continue
        rows.extend(_case_match(source, case, by_law) for source in _source_rows(case))
    status_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[str(row["status"])] += 1
    answer_cases = [case for case in suite.get("cases") or [] if isinstance(case, Mapping) and case.get("answer_required")]
    positive_source_count = len(rows)
    available_source_count = status_counts.get("available", 0)
    report: dict[str, Any] = {
        "schema_version": "retrieval-eval-source-availability-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors and positive_source_count == available_source_count else "BLOCKED",
        "suite_path": str(suite_path.resolve()),
        "suite_file_sha256": file_sha256(suite_path),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_file_sha256": file_sha256(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "answer_required_case_count": len(answer_cases),
        "positive_source_count": positive_source_count,
        "available_source_count": available_source_count,
        "availability_rate": available_source_count / positive_source_count if positive_source_count else 0.0,
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
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_DIR / "retrieval-eval-suite-v1.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "retrieval-eval-source-availability-v2.json")
    args = parser.parse_args(argv)
    report = audit(suite_path=args.suite.resolve(), manifest_path=args.manifest.resolve(), output=args.output.resolve())
    print(json.dumps({
        "status": report["status"],
        "errors": report["errors"],
        "availability_rate": report["availability_rate"],
        "status_counts": report["status_counts"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
