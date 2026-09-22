"""Checksum-bound owner approval for article-mapping review candidates."""

from __future__ import annotations

from typing import Any, Mapping

from api.legal_basis_auto_review import verification_sha256


def build_article_owner_acceptance(
    mapping: Mapping[str, Any],
    *,
    expected_mapping_sha256: str,
    accepted_at: str,
    owner_id: str,
) -> dict[str, Any]:
    if str(mapping.get("mapping_sha256") or "") != expected_mapping_sha256:
        raise ValueError("article_mapping_checksum_mismatch")
    accepted = sorted(
        str(item.get("basis_id") or "")
        for item in mapping.get("mappings", [])
        if item.get("decision") == "MAPPED_CANDIDATE"
    )
    blocked = [
        item
        for item in mapping.get("mappings", [])
        if item.get("decision") == "BLOCKED_NO_DIRECT_SUPPORT"
    ]
    if not accepted or any(not value for value in accepted):
        raise ValueError("accepted_article_mapping_scope_empty")
    receipt = {
        "schema_version": "legal-basis-article-owner-acceptance-v1",
        "mapping_sha256": expected_mapping_sha256,
        "owner_id": owner_id,
        "accepted_at": accepted_at,
        "accepted_basis_ids": accepted,
        "summary": {
            "accepted_mapping_count": len(accepted),
            "blocked_mapping_count": len(blocked),
        },
        "owner_approved_for_shadow": True,
        "independent_human_legal_qa_complete": False,
        "production_runtime_eligible": False,
    }
    receipt["acceptance_sha256"] = verification_sha256(receipt, "acceptance_sha256")
    return receipt


__all__ = ["build_article_owner_acceptance"]
