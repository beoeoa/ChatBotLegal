"""Deterministic replacement-candidate projection from official evidence.

The projection deliberately stops at an Admin review queue.  It never treats
title similarity, embeddings, or model output as proof that one legal document
replaces another.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from api.legal_validity_models import normalize_law_number

_ADVERSE_STATUSES = {
    "amended",
    "expired",
    "expired_partial",
    "repealed",
    "replaced",
    "suspended",
    "suspended_partial",
}


def _official_url(value: Any) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not host:
        return None
    if host == "vbpl.vn" or host.endswith(".vbpl.vn") or host.endswith(".gov.vn"):
        return url
    return None


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def project_replacement_candidates(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project explicit candidates and keep every relation pending Admin review."""

    candidates: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    saw_adverse = False
    saw_relation = False

    def add_reason(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    for observation in observations:
        status = str(observation.get("normalized_status") or "").strip().casefold()
        if status not in _ADVERSE_STATUSES:
            continue
        saw_adverse = True
        replacement_number = normalize_law_number(
            observation.get("affecting_document_number")
        )
        if replacement_number is None:
            continue
        saw_relation = True
        source_url = _official_url(observation.get("source_url"))
        if source_url is None:
            add_reason("replacement_source_not_official")
            continue
        if str(observation.get("evidence_status") or "") == "conflicting":
            add_reason("replacement_evidence_conflicting")
            continue
        if str(observation.get("identity_status") or "") != "exact":
            add_reason("replacement_identity_not_exact")
            continue
        if str(observation.get("evidence_status") or "") != "sufficient":
            add_reason("replacement_evidence_insufficient")
            continue

        candidate = {
            "law_number": replacement_number,
            "confidence": "verified",
            "evidence_level": "explicit_official_relationship",
            "relation_status": "pending_admin_review",
            "source_url": source_url,
            "source_kind": str(observation.get("source_kind") or "official"),
            "observed_at": _timestamp(observation.get("observed_at")),
            "basis": "official_affecting_document_number",
        }
        previous = candidates.get(replacement_number)
        if previous is None or candidate["observed_at"] > previous["observed_at"]:
            candidates[replacement_number] = candidate

    if not candidates and not reasons and saw_adverse and not saw_relation:
        add_reason("replacement_relation_not_observed")
    if not candidates and not reasons and not saw_adverse:
        add_reason("replacement_not_applicable")

    ranked = sorted(
        candidates.values(),
        key=lambda item: (item["observed_at"], item["law_number"]),
        reverse=True,
    )
    return {
        "status": "candidates_found" if ranked else "no_explicit_candidate",
        "requires_admin_review": bool(ranked),
        "candidates": ranked,
        "reason_codes": reasons,
    }


__all__ = ["project_replacement_candidates"]
