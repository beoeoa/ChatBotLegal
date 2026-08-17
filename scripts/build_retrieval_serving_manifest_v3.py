#!/usr/bin/env python3
"""Create the immutable V3 serving pointer for Retrieval Release V2.

The pointer is assembled only after the approved chunk manifest, the current
and temporal Chroma build reports, and the exact/lexical report all bind to the
same release and source snapshot.  It is a staging artifact: this command does
not change the M2 pointer, PostgreSQL, Chroma collections, or live config.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256

DEFAULT_MANIFEST = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-approved-passage-v4.json"
DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_CURRENT_REPORT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-v2-current-shadow.report.json"
DEFAULT_TEMPORAL_REPORT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-v2-temporal-shadow.report.json"
DEFAULT_LEXICAL_REPORT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-v2-exact-lexical.sqlite3.report.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "legal-serving-manifest-v3.json"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _artifact(*, kind: str, path: Path, release_id: str, source_snapshot_sha256: str, count: int) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"artifact_missing:{path}")
    return {
        "kind": kind,
        "path": _relative(path),
        "sha256": file_sha256(path),
        "release_id": release_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "count": int(count),
    }


def _collection_artifact(*, report: dict[str, Any], release_id: str, source_snapshot_sha256: str, count: int) -> dict[str, Any]:
    collection = str(report.get("target_collection") or "")
    vector_sha = str(report.get("vector_content_sha256") or "")
    if not collection or len(vector_sha) != 64:
        raise RuntimeError("collection_vector_content_fingerprint_missing")
    return {
        "kind": "chroma_collection",
        "path": f"chroma://{collection}",
        "sha256": vector_sha,
        "release_id": release_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "count": int(count),
    }


def _require_report(
    path: Path,
    *,
    release_id: str,
    source_snapshot_sha256: str,
    manifest_sha256: str,
    expected_count: int,
    label: str,
    expected_document_state: str,
    allow_provisional_staging: bool = False,
) -> dict[str, Any]:
    report = _load(path)
    if report.get("release_id") != release_id:
        raise RuntimeError(f"{label}_release_id_mismatch")
    if report.get("source_snapshot_sha256") != source_snapshot_sha256:
        raise RuntimeError(f"{label}_source_snapshot_mismatch")
    if report.get("manifest_sha256") != manifest_sha256:
        raise RuntimeError(f"{label}_manifest_sha256_mismatch")
    if report.get("valid") is not True:
        raise RuntimeError(f"{label}_not_valid")
    if int(report.get("expected_vector_count") or 0) != expected_count:
        raise RuntimeError(f"{label}_count_mismatch")
    if int(report.get("actual_vector_count") or 0) != expected_count:
        raise RuntimeError(f"{label}_actual_count_mismatch")
    if str(report.get("document_state") or "") != expected_document_state:
        raise RuntimeError(f"{label}_document_state_mismatch")
    if report.get("missing_ids") or report.get("orphan_ids"):
        raise RuntimeError(f"{label}_manifest_id_mismatch")
    if report.get("active_pointer_unchanged") is not True:
        raise RuntimeError(f"{label}_active_pointer_changed")
    if allow_provisional_staging:
        if report.get("provisional_staging") is not True or report.get("release_eligible") is not False:
            raise RuntimeError(f"{label}_provisional_marker_missing")
        if "provisional" not in str(report.get("target_collection") or "").lower():
            raise RuntimeError(f"{label}_target_not_provisional")
    elif report.get("provisional_staging") is True or report.get("release_eligible") is False:
        raise RuntimeError(f"{label}_provisional_artifact_not_allowed")
    return report


def _chunk_ids_sha256(ids: Iterable[str]) -> str:
    return canonical_sha256(sorted(str(value) for value in ids))


def _validate_schema(payload: dict[str, Any]) -> None:
    # The release pointer contract is distinct from the older per-collection
    # ``legal-serving-manifest-v3.schema.json`` compatibility contract.
    schema_path = ROOT / "specs" / "018-production-release-readiness" / "contracts" / "retrieval-serving-manifest-v3.schema.json"
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        raise RuntimeError("jsonschema_dependency_missing") from exc
    schema = _load(schema_path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise RuntimeError(f"serving_manifest_schema_invalid:{location}:{errors[0].message}")


def build(
    *,
    manifest_path: Path,
    inventory_path: Path,
    current_report_path: Path,
    temporal_report_path: Path,
    lexical_report_path: Path,
    output: Path,
    allow_provisional_staging: bool = False,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    if manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        raise RuntimeError("v2_chunk_manifest_required")
    manifest_is_approved = manifest.get("approved") is True and manifest.get("legal_review_attestation") is True
    if not manifest_is_approved:
        if not allow_provisional_staging:
            raise RuntimeError("approved_v2_manifest_required")
        if manifest.get("approved") is not False or manifest.get("legal_review_attestation") is not False:
            raise RuntimeError("provisional_manifest_must_be_unapproved")
        if not str(manifest.get("approval_blocker") or "").strip():
            raise RuntimeError("provisional_manifest_approval_blocker_required")
        if "provisional" not in output.name.lower():
            raise RuntimeError("provisional_output_name_required")
    provisional_staging = not manifest_is_approved
    release_id = str(manifest.get("release_id") or "")
    source_snapshot_sha256 = str(manifest.get("source_snapshot_sha256") or "")
    manifest_sha256 = str(manifest.get("manifest_sha256") or "")
    if not release_id or not source_snapshot_sha256 or not manifest_sha256:
        raise RuntimeError("manifest_fingerprints_required")

    inventory = _load(inventory_path)
    documents = list(inventory.get("documents") or [])
    if int(inventory.get("source_snapshot_document_count") or 0) != 12_236 or len(documents) != 12_236:
        raise RuntimeError("inventory_must_contain_12236_documents")
    chunk_rows = list(manifest.get("chunks") or [])
    chunks_by_document: dict[int, list[str]] = {}
    for row in chunk_rows:
        if row.get("eligible") is not True or row.get("serving_state") != "retrievable":
            raise RuntimeError(f"ineligible_chunk_in_manifest:{row.get('chunk_revision_id')}")
        document_id = int(row["document_id"])
        chunks_by_document.setdefault(document_id, []).append(str(row["chunk_revision_id"]))

    current_count = sum(
        str(row.get("document_serving_state") or "") == "current_retrievable"
        for row in chunk_rows
    )
    temporal_count = len(chunk_rows)
    current_report = _require_report(
        current_report_path,
        release_id=release_id,
        source_snapshot_sha256=source_snapshot_sha256,
        manifest_sha256=manifest_sha256,
        expected_count=current_count,
        label="current_vector_report",
        expected_document_state="current_retrievable",
        allow_provisional_staging=provisional_staging,
    )
    temporal_report = _require_report(
        temporal_report_path,
        release_id=release_id,
        source_snapshot_sha256=source_snapshot_sha256,
        manifest_sha256=manifest_sha256,
        expected_count=temporal_count,
        label="temporal_vector_report",
        expected_document_state="all",
        allow_provisional_staging=provisional_staging,
    )
    lexical_report = _load(lexical_report_path)
    expected_lexical_status = "PROVISIONAL_BUILT" if provisional_staging else "STAGING_BUILT"
    if lexical_report.get("status") != expected_lexical_status:
        raise RuntimeError("lexical_index_not_staging_built")
    if bool(lexical_report.get("provisional_staging")) != provisional_staging:
        raise RuntimeError("lexical_index_provisional_marker_mismatch")
    if bool(lexical_report.get("release_eligible")) != (not provisional_staging):
        raise RuntimeError("lexical_index_release_eligibility_mismatch")
    if provisional_staging and "provisional" not in lexical_report_path.name.lower():
        raise RuntimeError("provisional_lexical_report_name_required")
    if lexical_report.get("release_id") != release_id or lexical_report.get("manifest_sha256") != manifest_sha256:
        raise RuntimeError("lexical_index_manifest_mismatch")
    lexical_output = Path(str(lexical_report.get("output") or ""))
    if not lexical_output.is_file():
        raise RuntimeError("lexical_index_artifact_missing")

    state_counts = {
        "current_retrievable": 0,
        "historical_only": 0,
        "future_effective": 0,
        "quarantined": 0,
    }
    manifest_documents: list[dict[str, Any]] = []
    seen_documents: set[int] = set()
    for source_row in documents:
        document_id = int(source_row["document_id"])
        if document_id in seen_documents:
            raise RuntimeError(f"duplicate_inventory_document:{document_id}")
        seen_documents.add(document_id)
        state = str(source_row.get("serving_state") or "quarantined")
        if state not in state_counts:
            raise RuntimeError(f"unknown_document_state:{document_id}:{state}")
        state_counts[state] += 1
        ids = sorted(chunks_by_document.get(document_id, []))
        if state in {"future_effective", "quarantined"} and ids:
            raise RuntimeError(f"{state}_document_has_chunks:{document_id}")
        if state in {"current_retrievable", "historical_only"} and not ids:
            raise RuntimeError(f"retrievable_document_has_no_chunks:{document_id}")
        manifest_documents.append({
            "document_id": document_id,
            "serving_state": state,
            "chunk_count": len(ids),
            "chunk_ids_sha256": _chunk_ids_sha256(ids),
        })
    if sum(state_counts.values()) != 12_236:
        raise RuntimeError("document_state_partition_mismatch")

    payload: dict[str, Any] = {
        "schema_version": "legal-serving-manifest-v3",
        "kind": "release_pointer",
        "manifest_version": (
            "legal-serving-manifest-v3-provisional-20260816"
            if provisional_staging
            else "legal-serving-manifest-v3-20260816"
        ),
        "dataset_version": str(manifest.get("dataset_version") or "retrieval-release-v2"),
        "release_id": release_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "chunk_manifest_sha256": manifest_sha256,
        "chunk_manifest_file_sha256": file_sha256(manifest_path),
        "chunk_manifest_path": _relative(manifest_path),
        "source_inventory_file_sha256": file_sha256(inventory_path),
        "legal_as_of": str(manifest.get("legal_as_of") or date.today().isoformat()),
        "inventory_document_count": 12_236,
        "document_state_counts": state_counts,
        "current_collection": _collection_artifact(
            report=current_report,
            release_id=release_id,
            source_snapshot_sha256=source_snapshot_sha256,
            count=current_count,
        ),
        "temporal_collection": _collection_artifact(
            report=temporal_report,
            release_id=release_id,
            source_snapshot_sha256=source_snapshot_sha256,
            count=temporal_count,
        ),
        "exact_lexical_index": _artifact(
            kind="sqlite_exact_fts5",
            path=lexical_output,
            release_id=release_id,
            source_snapshot_sha256=source_snapshot_sha256,
            count=int((lexical_report.get("counts") or {}).get("chunk_count") or 0),
        ),
        "provenance": {
            **{
                key: manifest.get(key)
                for key in (
                    "model_artifact_fingerprint",
                    "tokenizer_fingerprint",
                    "embedding_recipe_fingerprint",
                    "passage_recipe_fingerprint",
                    "splitter_fingerprint",
                    "dependency_lock_fingerprint",
                )
            },
            "quality_policy_version": str(manifest.get("quality_policy_version") or "legal-chunk-quality-v2"),
        },
        "quality_policy_version": str(manifest.get("quality_policy_version") or "legal-chunk-quality-v2"),
        "documents": manifest_documents,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activation_performed": False,
        "active_pointer_changed": False,
        "source_inventory_path": _relative(inventory_path),
        "vector_reports": {
            "current": _relative(current_report_path),
            "temporal": _relative(temporal_report_path),
        },
        "vector_report_checksums": {
            "current": file_sha256(current_report_path),
            "temporal": file_sha256(temporal_report_path),
        },
        "lexical_report_path": _relative(lexical_report_path),
        "lexical_report_sha256": file_sha256(lexical_report_path),
        "provisional_staging": provisional_staging,
        "release_eligible": not provisional_staging,
    }
    # The checksum excludes itself and the generated timestamp is deliberately
    # retained: a regenerated pointer is a new immutable artifact.
    payload["manifest_sha256"] = canonical_sha256(payload)
    _validate_schema(payload)

    if output.exists():
        existing = _load(output)
        if existing != payload:
            raise RuntimeError("serving_manifest_version_already_exists")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_sha = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{output_sha}  {output.name}\n", encoding="ascii")
    report = {
        "schema_version": "legal-retrieval-serving-manifest-v3-build-v1",
        "status": "PROVISIONAL_BUILT" if provisional_staging else "STAGING_BUILT",
        "manifest_path": str(output.resolve()),
        "manifest_file_sha256": output_sha,
        "manifest_sha256": payload["manifest_sha256"],
        "release_id": release_id,
        "source_snapshot_sha256": source_snapshot_sha256,
        "document_count": len(manifest_documents),
        "chunk_count": len(chunk_rows),
        "current_vector_count": current_count,
        "temporal_vector_count": temporal_count,
        "active_pointer_changed": False,
        "activation_performed": False,
        "provisional_staging": provisional_staging,
        "release_eligible": not provisional_staging,
    }
    report_path = output.with_suffix(output.suffix + ".report.json")
    report["report_path"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
    )
    report["report_file_sha256"] = file_sha256(report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--current-report", type=Path, default=DEFAULT_CURRENT_REPORT)
    parser.add_argument("--temporal-report", type=Path, default=DEFAULT_TEMPORAL_REPORT)
    parser.add_argument("--lexical-report", type=Path, default=DEFAULT_LEXICAL_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-provisional-staging",
        action="store_true",
        help="Build an explicitly non-release-eligible pointer from an unapproved draft manifest.",
    )
    args = parser.parse_args(argv)
    report = build(
        manifest_path=args.manifest.resolve(),
        inventory_path=args.inventory.resolve(),
        current_report_path=args.current_report.resolve(),
        temporal_report_path=args.temporal_report.resolve(),
        lexical_report_path=args.lexical_report.resolve(),
        output=args.output.resolve(),
        allow_provisional_staging=args.allow_provisional_staging,
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
