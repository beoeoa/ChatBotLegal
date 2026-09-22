"""Fail-closed final adjudication for every legal-basis binding.

The output is an owner-review artifact.  Source-only and blocked decisions are
never made answer-runtime eligible, and this module does not mutate retrieval,
the legal corpus, vectors, manifests, or active pointers.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping
from urllib.parse import urlparse

from api.legal_basis_auto_review import OFFICIAL_HOSTS, verification_sha256
from api.legal_basis_catalog import canonical_sha256


FINAL_DECISIONS = {
    "APPROVE_INDEXED",
    "APPROVE_SOURCE_ONLY",
    "REJECT_NOT_EFFECTIVE",
    "REJECT_HISTORICAL_FOR_CURRENT",
    "BLOCKED_EFFECTIVITY_SCOPE",
    "BLOCKED_IDENTITY",
    "BLOCKED_OFFICIAL_SOURCE",
    "BLOCKED_EVIDENCE",
}


def _validate_inputs(
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
    proposal: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> None:
    candidate_sha = str(candidate.get("catalog_sha256") or "")
    if candidate_sha != canonical_sha256(candidate, "catalog_sha256"):
        raise ValueError("candidate_checksum_mismatch")
    if verification.get("candidate_catalog_sha256") != candidate_sha:
        raise ValueError("verification_candidate_checksum_mismatch")
    if verification.get("verification_sha256") != verification_sha256(verification):
        raise ValueError("verification_checksum_mismatch")
    if proposal.get("candidate_catalog_sha256") != candidate_sha:
        raise ValueError("proposal_candidate_checksum_mismatch")
    if proposal.get("proposal_sha256") != verification_sha256(proposal, "proposal_sha256"):
        raise ValueError("proposal_checksum_mismatch")
    if overlay.get("candidate_catalog_sha256") != candidate_sha:
        raise ValueError("overlay_candidate_checksum_mismatch")
    if overlay.get("overlay_sha256") != verification_sha256(overlay, "overlay_sha256"):
        raise ValueError("overlay_checksum_mismatch")


def _overlay_decision(record: Mapping[str, Any] | None) -> str | None:
    if not record:
        return None
    host = (urlparse(str(record.get("source_url") or "")).hostname or "").casefold()
    if host not in OFFICIAL_HOSTS or record.get("identity_status") != "exact":
        return None
    status = str(record.get("normalized_status") or "").casefold()
    if status in {"expired", "repealed", "suspended_full", "not_yet_effective"}:
        return "REJECT_NOT_EFFECTIVE"
    if status == "active" and record.get("evidence_status") == "sufficient":
        return "APPROVE_SOURCE_ONLY"
    if status in {"expired_partial", "suspended_partial", "amended"}:
        return "BLOCKED_EFFECTIVITY_SCOPE"
    return "BLOCKED_EVIDENCE"


def _final_decision(
    basis: Mapping[str, Any],
    verification_record: Mapping[str, Any] | None,
    overlay_record: Mapping[str, Any] | None,
) -> str:
    proposal = str(basis.get("owner_proposal") or "")
    if proposal == "PROPOSE_APPROVE_INDEXED":
        return "APPROVE_INDEXED"
    if proposal == "PROPOSE_APPROVE_SOURCE_ONLY":
        return "APPROVE_SOURCE_ONLY"
    if proposal == "PROPOSE_REJECT_NOT_EFFECTIVE":
        return "REJECT_NOT_EFFECTIVE"
    if proposal == "HOLD_NOT_CURRENT_OR_INCOMPLETE":
        return "REJECT_HISTORICAL_FOR_CURRENT"
    overlaid = _overlay_decision(overlay_record)
    if overlaid:
        return overlaid
    recommendation = str((verification_record or {}).get("recommendation") or "")
    return {
        "MANUAL_EFFECTIVITY_REVIEW": "BLOCKED_EFFECTIVITY_SCOPE",
        "MANUAL_EVIDENCE_REVIEW": "BLOCKED_EVIDENCE",
        "MANUAL_IDENTITY_REVIEW": "BLOCKED_IDENTITY",
        "MANUAL_SOURCE_REQUIRED": "BLOCKED_OFFICIAL_SOURCE",
    }.get(recommendation, "BLOCKED_EVIDENCE")


def finalize_legal_basis_catalog(
    candidate: Mapping[str, Any],
    verification: Mapping[str, Any],
    proposal: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_inputs(candidate, verification, proposal, overlay)
    verification_by_law = {str(row["law_number"]): row for row in verification.get("records", [])}
    overlay_by_law = {str(row["law_number"]): row for row in overlay.get("records", [])}
    entries = copy.deepcopy(proposal["entries"])
    counts: dict[str, int] = {}
    for entry in entries:
        for basis in entry.get("legal_bases", []):
            law_number = str(basis.get("law_number") or "")
            decision = _final_decision(
                basis, verification_by_law.get(law_number), overlay_by_law.get(law_number)
            )
            if decision not in FINAL_DECISIONS:
                raise ValueError(f"unknown_final_decision:{decision}")
            basis["final_decision"] = decision
            basis["runtime_eligible"] = decision == "APPROVE_INDEXED"
            if law_number in overlay_by_law:
                basis["final_official_source"] = overlay_by_law[law_number].get("source_url")
                basis["final_source_status"] = overlay_by_law[law_number].get("normalized_status")
            counts[decision] = counts.get(decision, 0) + 1
    held = sum(
        1 for entry in entries for basis in entry.get("legal_bases", [])
        if str(basis.get("final_decision") or "").startswith("HOLD")
    )
    result = {
        "schema_version": "legal-basis-final-adjudication-v1",
        "candidate_catalog_sha256": candidate["catalog_sha256"],
        "automated_verification_sha256": verification["verification_sha256"],
        "owner_proposal_sha256": proposal["proposal_sha256"],
        "official_source_overlay_sha256": overlay["overlay_sha256"],
        "entries": entries,
        "summary": {
            "entry_count": len(entries),
            "basis_count": sum(counts.values()),
            "decision_counts": counts,
            "unresolved_hold_count": held,
        },
        "owner_accepted": False,
        "independent_human_legal_qa_complete": False,
        "runtime_eligible": False,
    }
    result["adjudication_sha256"] = verification_sha256(result, "adjudication_sha256")
    return result


__all__ = ["FINAL_DECISIONS", "finalize_legal_basis_catalog"]
