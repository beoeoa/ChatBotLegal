"""Checksum-bound owner acceptance without impersonating independent Legal QA."""

from __future__ import annotations

from typing import Any, Mapping

from api.legal_basis_auto_review import verification_sha256


def build_owner_acceptance(
    adjudication: Mapping[str, Any],
    *,
    accepted_sha256: str,
    owner_id: str,
    accepted_at: str,
    active_pointer_sha256: str,
) -> dict[str, Any]:
    actual = str(adjudication.get("adjudication_sha256") or "")
    if actual != verification_sha256(adjudication, "adjudication_sha256"):
        raise ValueError("adjudication_checksum_mismatch")
    if accepted_sha256 != actual:
        raise ValueError("accepted_adjudication_checksum_mismatch")
    indexed_ids = sorted(
        str(basis["basis_id"])
        for entry in adjudication.get("entries", [])
        for basis in entry.get("legal_bases", [])
        if basis.get("final_decision") == "APPROVE_INDEXED"
    )
    receipt = {
        "schema_version": "legal-basis-owner-acceptance-v1",
        "adjudication_sha256": actual,
        "owner_id": owner_id,
        "accepted_at": accepted_at,
        "acceptance_scope": "owner_review_of_automated_adjudication",
        "indexed_candidate_basis_ids": indexed_ids,
        "summary": {"indexed_candidate_count": len(indexed_ids)},
        "active_pointer_sha256_before_replay": active_pointer_sha256.casefold(),
        "owner_accepted": True,
        "independent_human_legal_qa_complete": False,
        "runtime_eligible": False,
    }
    receipt["acceptance_sha256"] = verification_sha256(receipt, "acceptance_sha256")
    return receipt


__all__ = ["build_owner_acceptance"]
