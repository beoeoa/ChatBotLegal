"""Truthful, read-only quality projection for the Direct RAG public contract.

Citation membership, possible supporting references, answer completeness and
recorded source validity are separate observations. None of these heuristics
performs semantic claim verification or changes source eligibility.
"""

from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from typing import Any, Mapping, Sequence


_CITATION = re.compile(r"\[E(\d{1,3})\]", re.IGNORECASE)


def _fold(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
        if not unicodedata.combining(char)
    )


def _packet_ids(evidence_by_id: Mapping[str, Mapping[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for index, (key, row) in enumerate(evidence_by_id.items(), 1):
        match = re.fullmatch(r"evidence-(\d+)", str(key), re.IGNORECASE)
        marker = str(row.get("direct_evidence_id") or row.get("evidence_id") or "")
        marker_match = re.fullmatch(r"E?(\d+)", marker, re.IGNORECASE)
        number = match.group(1) if match else marker_match.group(1) if marker_match else index
        ids.add(f"E{int(number)}")
    return ids


def reference_usage(answer: str, allowed_ids: set[str]) -> dict[str, Any]:
    """Count potential support mentions without calling them verified support.

    A citation mentioned solely to say its source is irrelevant/unsupported
    does not contribute even to this candidate count. A source cited elsewhere
    in a substantive sentence remains a candidate. Negated legal conclusions
    such as 'không phải nộp' do not mean their citations are irrelevant.
    """

    candidate: set[str] = set()
    negative: set[str] = set()
    segments = re.split(r"[.!?;\n]+|,\s*(?:nhưng|còn|trong khi)\s+", answer, flags=re.I)
    for segment in segments:
        markers = {f"E{int(number)}" for number in _CITATION.findall(segment)} & allowed_ids
        if not markers:
            continue
        folded = _fold(segment)
        excludes_support = any(
            phrase in folded
            for phrase in (
                "khong lien quan", "khong ho tro", "khong chung minh",
                "khong phai can cu", "chi de doi chieu", "chi de tham khao",
            )
        )
        (negative if excludes_support else candidate).update(markers)
    excluded = negative - candidate
    return {
        "support_reference_count": len(candidate),
        "excluded_reference_count": len(excluded),
        "support_reference_kind": "candidate_mentions_only",
    }


def _validity_summary(citations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses: list[str] = []
    for citation in citations:
        sync = citation.get("validity_sync")
        sync = sync if isinstance(sync, Mapping) else {}
        status = str(sync.get("status") or "unknown").strip().casefold()
        # An imported 'effective' label or an identity-unverified observation
        # cannot become an independently verified legal effectivity claim.
        if (
            not sync.get("verified_at")
            or not sync.get("source_url")
            or "unverified" in str(sync.get("warning_code") or "").casefold()
            or status not in {
                "active", "not_yet_effective", "expired", "expired_partial",
                "suspended", "suspended_partial", "amended", "replaced", "repealed",
            }
        ):
            status = "unknown"
        statuses.append(status)
    unique = set(statuses)
    overall = (
        "not_available" if not statuses
        else "unknown" if "unknown" in unique
        else next(iter(unique)) if len(unique) == 1
        else "mixed"
    )
    return {
        "validity_status": overall,
        "validity_assessment": "registry_observations_only",
        "validity_unknown_source_count": statuses.count("unknown"),
        "validity_observed_source_count": len(statuses) - statuses.count("unknown"),
        "validity_scope": "displayed_sources",
    }


def project_direct_answer_quality(
    *,
    answer: str,
    outcome: str,
    reason_code: str,
    citations: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    """Project observations, preserving unknowns and existing delivery outcome."""

    allowed_ids = _packet_ids(evidence_by_id)
    cited_ids = {f"E{int(number)}" for number in _CITATION.findall(answer)}
    check = trace.get("citation_check")
    check = check if isinstance(check, Mapping) else {}
    invalid_output = reason_code in {"CITATION_OUTSIDE_PACKET", "OUTPUT_NOT_MARKDOWN"}
    unknown_ids = cited_ids - allowed_ids
    membership_valid = (
        check.get("valid") is True
        if "valid" in check
        else bool(cited_ids) and not unknown_ids
    )
    if invalid_output or unknown_ids or check.get("valid") is False and check.get("status") == "invalid":
        citation_status = "invalid"
    elif reason_code == "CITATION_MISSING":
        citation_status = "missing"
    elif membership_valid and citations:
        citation_status = "valid"
    else:
        citation_status = "present" if citations else "missing"

    packet_coverage = trace.get("packet_coverage")
    packet_coverage = dict(packet_coverage) if isinstance(packet_coverage, Mapping) else {}
    has_packet_gap = bool(packet_coverage.get("missing_issue_ids") or packet_coverage.get("partial_issue_ids"))
    whole_refusal = outcome == "source_only" and reason_code == "INSUFFICIENT_EVIDENCE"
    partial = outcome == "partial" or has_packet_gap
    answer_status = (
        "cannot_verify" if outcome not in {"answered", "partial"}
        else "partial" if partial
        else "unverified"
    )
    quality: dict[str, Any] = {
        "contract_version": "direct-quality-v2",
        "citation_count": len(citations),
        "citation_status": citation_status,
        "citation_assessment": "packet_membership_only",
        "support_status": "not_assessed",
        "coverage": 0.0 if whole_refusal else None,
        "coverage_status": "insufficient_evidence" if whole_refusal else "partial" if partial else "not_assessed",
        **reference_usage(answer, allowed_ids),
        **_validity_summary(citations),
    }
    if outcome not in {"answered", "partial"}:
        quality["accepted_claim_count"] = 0
    # Unknown claim counts are omitted; neither citation count nor an invalid
    # marker establishes how many legal propositions passed/failed review.
    if packet_coverage:
        quality["packet_coverage"] = deepcopy(packet_coverage)
    if answer_status == "cannot_verify":
        grounding_status = "source_view_only"
        label = "Chưa có kết luận pháp lý được kiểm chứng từ lượt này."
    else:
        grounding_status = "citation_bound" if citation_status == "valid" else "not_assessed"
        label = "Trích dẫn đã được đối chiếu với gói nguồn; nội dung kết luận chưa được kiểm chứng độc lập."
    return {
        "answer_status": answer_status,
        "grounding_status": grounding_status,
        "verification_label": label,
        "quality": quality,
    }


__all__ = ["project_direct_answer_quality", "reference_usage"]
