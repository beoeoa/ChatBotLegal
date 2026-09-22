"""Read-only integration gate for an owner-accepted legal-basis catalog."""

from __future__ import annotations

from typing import Any, Mapping

from api.legal_basis_auto_review import verification_sha256


def assess_legal_basis_integration(
    adjudication: Mapping[str, Any],
    receipt: Mapping[str, Any],
    subset: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    article_mapping: Mapping[str, Any] | None = None,
    active_pointer_unchanged: bool,
) -> dict[str, Any]:
    if receipt.get("acceptance_sha256") != verification_sha256(receipt, "acceptance_sha256"):
        raise ValueError("acceptance_checksum_mismatch")
    if receipt.get("adjudication_sha256") != adjudication.get("adjudication_sha256"):
        raise ValueError("acceptance_adjudication_checksum_mismatch")
    indexed = [
        basis
        for entry in adjudication.get("entries", [])
        for basis in entry.get("legal_bases", [])
        if basis.get("final_decision") == "APPROVE_INDEXED"
    ]
    ids = {str(item.get("basis_id") or "") for item in indexed}
    if ids != set(receipt.get("indexed_candidate_basis_ids") or []):
        raise ValueError("indexed_candidate_scope_mismatch")
    missing_articles = sum(1 for basis in indexed if not basis.get("article_numbers"))
    mapping = article_mapping or {"mappings": []}
    if mapping.get("mappings") and mapping.get("adjudication_sha256") != adjudication.get(
        "adjudication_sha256"
    ):
        raise ValueError("article_mapping_adjudication_checksum_mismatch")
    mapped_candidate_ids = {
        str(item.get("basis_id") or "")
        for item in mapping.get("mappings", [])
        if item.get("decision") == "MAPPED_CANDIDATE"
    }
    blocked_mapping_ids = {
        str(item.get("basis_id") or "")
        for item in mapping.get("mappings", [])
        if item.get("decision") == "BLOCKED_NO_DIRECT_SUPPORT"
    }
    blockers: list[str] = []
    if not receipt.get("independent_human_legal_qa_complete"):
        blockers.append("independent_legal_qa_missing")
    if missing_articles:
        blockers.append("approved_article_bindings_absent")
    if subset.get("status") != "PASS" or int(subset.get("failure_count") or 0):
        blockers.append("subset_replay_not_100_percent")
    if inventory.get("status") != "PASS":
        blockers.append("source_inventory_metadata_incomplete")
    if not active_pointer_unchanged:
        blockers.append("active_pointer_changed")
    report = {
        "schema_version": "legal-basis-integration-assessment-v1",
        "adjudication_sha256": adjudication["adjudication_sha256"],
        "owner_acceptance_sha256": receipt["acceptance_sha256"],
        "indexed_candidate_count": len(indexed),
        "indexed_candidates_missing_approved_articles": missing_articles,
        "article_mapping_candidate_count": len(mapped_candidate_ids),
        "article_mapping_blocked_count": len(blocked_mapping_ids),
        "article_mapping_runtime_eligible": bool(mapping.get("runtime_eligible")),
        "subset": {
            "status": subset.get("status"),
            "case_count": int(subset.get("subset_case_count") or 0),
            "pass_count": int(subset.get("pass_count") or 0),
            "failure_count": int(subset.get("failure_count") or 0),
        },
        "source_inventory_status": inventory.get("status"),
        "source_inventory_classification_counts": inventory.get("classification_counts") or {},
        "active_pointer_unchanged": active_pointer_unchanged,
        "blockers": blockers,
        "recommendation": "NO_GO" if blockers else "ELIGIBLE_FOR_SHADOW_INTEGRATION_REVIEW",
        "runtime_mutated": False,
    }
    report["assessment_sha256"] = verification_sha256(report, "assessment_sha256")
    return report


__all__ = ["assess_legal_basis_integration"]
