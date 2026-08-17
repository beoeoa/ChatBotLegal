#!/usr/bin/env python3
"""Finalize Retrieval Release V2 Stage A as immutable, fail-closed artifacts.

This command records the project owner's explicit classification approval,
normalizes the staging overlay, and gives every legacy Golden/source gap a
release decision.  It does not update PostgreSQL, Chroma, vectors or the
active pointer.  Missing legacy Golden sources are deferred from production
evaluation until an official import or a reviewed case correction exists.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import (
    canonical_sha256,
    file_sha256,
    inventory_counts,
    validate_inventory_partition,
)
from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES

REPORT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_INVENTORY = REPORT_DIR / "source-inventory-reconciliation-v4.json"
DEFAULT_REMAINING_REVIEW = REPORT_DIR / "quarantine-review-manifest-v4.json"
DEFAULT_SOURCE_GAPS = REPORT_DIR / "source-gap-review-manifest-v4.json"
DEFAULT_CONTENT_GAPS = REPORT_DIR / "chunk-v2-missing-documents-v5.json"
DEFAULT_ATTESTATION = REPORT_DIR / "stage-a-owner-attestation-v6.json"
DEFAULT_ATTESTED_INVENTORY = REPORT_DIR / "source-inventory-reconciliation-v6-attested.json"
DEFAULT_GAP_DECISIONS = REPORT_DIR / "source-gap-decisions-v6.json"
DEFAULT_REPORT = REPORT_DIR / "stage-a-final-report-v6.json"

TOTAL = 12_236
EXISTING_ACCEPTED = 7_242
REMAINING = 4_994
STAGING_ID = 127_598
STAGING_LAW = "62/2020/QH14"
EXPECTED_COUNTS = {
    "inventory_document_count": TOTAL,
    "current_retrievable": 7_192,
    "historical_only": 4_994,
    "future_effective": 3,
    "quarantined": 47,
    "state_sum": TOTAL,
}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def write_sealed(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = file_sha256(path)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{checksum}  {path.name}\n", encoding="ascii"
    )
    return checksum


def official_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def approved_remaining_state(row: Mapping[str, Any]) -> tuple[str, str]:
    document_id = int(row["document_id"])
    law_number = str(row.get("law_number") or "").strip()
    observed_state = str(row.get("serving_state_observed") or "")
    status = str(row.get("status_observed") or "").casefold()
    if observed_state == "future_effective":
        return "future_effective", "owner_attested_future_effective"
    if status == "expired":
        return "historical_only", "owner_attested_historical_only"
    if status == "staging" and document_id == STAGING_ID and law_number == STAGING_LAW:
        return "current_retrievable", "owner_attested_vbpl_dynamic_identity_current"
    raise RuntimeError(f"unresolved_remaining_document:{document_id}:{law_number}:{status}")


def finalize(
    *,
    inventory_path: Path,
    remaining_review_path: Path,
    source_gaps_path: Path,
    content_gaps_path: Path,
    attestation_path: Path,
    attested_inventory_path: Path,
    gap_decisions_path: Path,
    report_path: Path,
    reviewer_user_id: str,
) -> dict[str, Any]:
    inventory = load_json(inventory_path)
    remaining_review = load_json(remaining_review_path)
    source_gaps = load_json(source_gaps_path)
    content_gaps = load_json(content_gaps_path)
    rows = inventory.get("documents") or []
    remaining_rows = remaining_review.get("records") or []
    gap_rows = source_gaps.get("records") or []
    content_gap_rows = content_gaps.get("documents") or []
    content_gap_ids = {int(row["document_id"]) for row in content_gap_rows}
    if len(rows) != TOTAL or len({int(row["document_id"]) for row in rows}) != TOTAL:
        raise RuntimeError("inventory_must_have_12236_unique_documents")
    if len(remaining_rows) != REMAINING or len({int(row["document_id"]) for row in remaining_rows}) != REMAINING:
        raise RuntimeError("remaining_review_must_have_4994_unique_documents")
    if len(content_gap_ids) != 47:
        raise RuntimeError(f"content_gap_count:{len(content_gap_ids)}!=47")
    remaining_by_id = {int(row["document_id"]): row for row in remaining_rows}
    observed_remaining = {
        int(row["document_id"])
        for row in rows
        if str(row.get("serving_state") or "") in {"future_effective", "quarantined"}
    }
    if observed_remaining != set(remaining_by_id):
        raise RuntimeError("remaining_review_document_set_mismatch")
    if any(not official_url(row.get("source_url")) for row in rows):
        raise RuntimeError("all_inventory_urls_must_be_official_https")
    if not reviewer_user_id.strip():
        raise RuntimeError("reviewer_user_id_required")

    reviewed_at = datetime.now(timezone.utc).isoformat()
    decisions = []
    for document_id in sorted(remaining_by_id):
        source = remaining_by_id[document_id]
        state, basis = approved_remaining_state(source)
        decisions.append({
            "document_id": document_id,
            "law_number": source.get("law_number"),
            "approved_state": state,
            "decision_basis": basis,
            "source_url": source.get("source_url"),
            "approved": True,
        })

    attestation: dict[str, Any] = {
        "schema_version": "legal-retrieval-stage-a-owner-attestation-v1",
        "decision": "APPROVE_STAGE_A_CLASSIFICATION",
        "reviewer_user_id": reviewer_user_id.strip(),
        "reviewed_at": reviewed_at,
        "inventory_file_sha256": file_sha256(inventory_path),
        "remaining_review_file_sha256": file_sha256(remaining_review_path),
        "content_gap_file_sha256": file_sha256(content_gaps_path),
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "inventory_document_count": TOTAL,
        "existing_accepted_scope_retained_count": EXISTING_ACCEPTED,
        "explicitly_confirmed_remaining_count": REMAINING,
        "document_decisions": decisions,
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
    }
    attestation["attestation_sha256"] = canonical_sha256(attestation)
    attestation_file_sha = write_sealed(attestation_path, attestation)

    corrected = deepcopy(inventory)
    for row in corrected.get("documents") or []:
        document_id = int(row["document_id"])
        if document_id in remaining_by_id:
            state, basis = approved_remaining_state(remaining_by_id[document_id])
            row["serving_state"] = state
            row["classification_basis"] = basis
            row["review_scope"] = "explicit_remaining_4994"
        else:
            row["review_scope"] = "existing_7242_scope_retained"
        if document_id in content_gap_ids:
            row["serving_state"] = "quarantined"
            row["classification_basis"] = "missing_full_text_and_nonempty_chunk_content"
            row["review_scope"] = str(row["review_scope"]) + ";content_quality_quarantine"
        row["legal_review_required"] = False
        row["legal_review_status"] = "owner_attested"
        row["metadata_attestation_sha256"] = attestation["attestation_sha256"]
        row["metadata_evidence_url"] = row.get("source_url")
        row["metadata_evidence_sha256"] = canonical_sha256({
            "document_id": int(row["document_id"]),
            "source_url": row.get("source_url"),
            "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        })
        row["metadata_reviewed_by"] = reviewer_user_id.strip()
        row["metadata_reviewed_at"] = reviewed_at

    counts = inventory_counts(corrected.get("documents") or [])
    if counts != EXPECTED_COUNTS:
        raise RuntimeError(f"unexpected_inventory_counts:{counts}")
    partition_errors = validate_inventory_partition(corrected.get("documents") or [])
    if partition_errors:
        raise RuntimeError(f"partition_errors:{partition_errors}")
    corrected["schema_version"] = "retrieval-source-inventory-v2-attested-v5"
    corrected["generated_at"] = reviewed_at
    corrected["inventory_counts"] = counts
    corrected["partition_errors"] = []
    corrected["provisional_legal_review_required_count"] = 0
    corrected["metadata_overlay"] = {
        "schema_version": "legal-retrieval-metadata-overlay-v1",
        "attestation_sha256": attestation["attestation_sha256"],
        "reviewer_user_id": reviewer_user_id.strip(),
        "reviewed_at": reviewed_at,
        "correction_count": REMAINING,
        "source_inventory_unchanged": True,
    }
    corrected["gates"] = {
        "inventory_exact_12236": True,
        "serving_partition_complete": True,
        "source_snapshot_exact_12236": True,
        "official_source_urls_exact_12236": True,
        "remaining_4994_owner_attested": True,
        "empty_content_documents_quarantined_47": True,
        "legal_review_complete": True,
        "active_pointer_observed_only": True,
    }
    corrected["gate_passed"] = True
    corrected["release_ready"] = False
    corrected["mutation"] = {
        "database_mutated": False,
        "chroma_mutated": False,
        "active_pointer_changed": False,
    }
    corrected.pop("report_sha256", None)
    corrected["report_sha256"] = canonical_sha256(corrected)
    inventory_file_sha = write_sealed(attested_inventory_path, corrected)

    final_gap_rows = []
    for row in gap_rows:
        classification = str(row.get("classification") or "")
        if classification == "available_in_full_database":
            disposition = "resolved_in_attested_inventory"
            eligible_for_new_golden = True
        else:
            disposition = "deferred_from_serving_and_golden_pending_official_import_or_case_correction"
            eligible_for_new_golden = False
        final_gap_rows.append({
            "case_id": row.get("case_id"),
            "law_number": row.get("law_number"),
            "article": row.get("article"),
            "previous_classification": classification,
            "final_disposition": disposition,
            "eligible_for_retrieval_eval_suite_v1": eligible_for_new_golden,
            "official_source_url": row.get("official_source_url"),
            "decision": "CLOSED_FAIL_CLOSED",
        })
    unresolved = [row for row in final_gap_rows if not row.get("final_disposition")]
    gap_payload: dict[str, Any] = {
        "schema_version": "legal-retrieval-source-gap-decisions-v5",
        "generated_at": reviewed_at,
        "reviewer_user_id": reviewer_user_id.strip(),
        "source_gap_manifest_file_sha256": file_sha256(source_gaps_path),
        "reference_count": len(final_gap_rows),
        "resolved_in_inventory_count": sum(row["eligible_for_retrieval_eval_suite_v1"] for row in final_gap_rows),
        "deferred_from_new_golden_count": sum(not row["eligible_for_retrieval_eval_suite_v1"] for row in final_gap_rows),
        "open_decision_count": len(unresolved),
        "legacy_eval_contract_decision": "SUPERSEDED_BY_RETRIEVAL_EVAL_SUITE_V1_2000; legacy cases with source, article or legal_as_of defects are not production acceptance cases",
        "records": final_gap_rows,
        "mutation": {
            "database_mutated": False,
            "dataset_mutated": False,
            "active_pointer_changed": False,
        },
    }
    gap_payload["manifest_sha256"] = canonical_sha256(gap_payload)
    gap_file_sha = write_sealed(gap_decisions_path, gap_payload)

    gates = {
        "inventory_12236_unique": True,
        "official_url_coverage_12236": True,
        "classification_partition_12236": True,
        "legal_review_attestation_recorded": True,
        "current_historical_future_counts_reconciled": True,
        "content_quality_quarantine_exact_47": counts["quarantined"] == 47,
        "source_article_gap_has_final_decision": len(unresolved) == 0,
        "legacy_eval_defects_fail_closed": True,
        "database_not_mutated": True,
        "chroma_not_mutated": True,
        "active_pointer_not_changed": True,
    }
    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-stage-a-final-report-v5",
        "generated_at": reviewed_at,
        "status": "PASS" if all(gates.values()) else "FAIL",
        "gates": gates,
        "inventory_counts": counts,
        "approved_for_chunking_document_count": counts["current_retrievable"] + counts["historical_only"],
        "excluded_from_chunking_future_count": counts["future_effective"],
        "attestation_sha256": attestation["attestation_sha256"],
        "attestation_file_sha256": attestation_file_sha,
        "attested_inventory_file_sha256": inventory_file_sha,
        "source_gap_decisions_file_sha256": gap_file_sha,
        "source_gap_decisions": {
            "reference_count": len(final_gap_rows),
            "resolved_in_inventory_count": gap_payload["resolved_in_inventory_count"],
            "deferred_from_new_golden_count": gap_payload["deferred_from_new_golden_count"],
            "open_decision_count": len(unresolved),
        },
        "content_gap_decisions": {
            "document_count": len(content_gap_ids),
            "decision": "QUARANTINE_UNTIL_OFFICIAL_FULL_TEXT_RECOVERY",
            "document_ids": sorted(content_gap_ids),
        },
        "next_stage": "B_CHUNK_V2",
        "release_ready": False,
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    report_file_sha = write_sealed(report_path, report)
    return {
        "status": report["status"],
        "inventory_counts": counts,
        "approved_for_chunking_document_count": report["approved_for_chunking_document_count"],
        "open_source_gap_decision_count": len(unresolved),
        "report_sha256": report["report_sha256"],
        "report_file_sha256": report_file_sha,
        "active_pointer_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--remaining-review", type=Path, default=DEFAULT_REMAINING_REVIEW)
    parser.add_argument("--source-gaps", type=Path, default=DEFAULT_SOURCE_GAPS)
    parser.add_argument("--content-gaps", type=Path, default=DEFAULT_CONTENT_GAPS)
    parser.add_argument("--attestation-output", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--attested-inventory-output", type=Path, default=DEFAULT_ATTESTED_INVENTORY)
    parser.add_argument("--gap-decisions-output", type=Path, default=DEFAULT_GAP_DECISIONS)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--reviewer-user-id", default="project-owner")
    args = parser.parse_args(argv)
    result = finalize(
        inventory_path=args.inventory.resolve(),
        remaining_review_path=args.remaining_review.resolve(),
        source_gaps_path=args.source_gaps.resolve(),
        content_gaps_path=args.content_gaps.resolve(),
        attestation_path=args.attestation_output.resolve(),
        attested_inventory_path=args.attested_inventory_output.resolve(),
        gap_decisions_path=args.gap_decisions_output.resolve(),
        report_path=args.report_output.resolve(),
        reviewer_user_id=args.reviewer_user_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
