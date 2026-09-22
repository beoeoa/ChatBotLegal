"""Automated, source-level legal-basis verification for owner review.

This is not independent human Legal QA. It only verifies document identity and
effectivity against official observations and never invents article bindings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlparse


OFFICIAL_HOSTS = {"vbpl.vn", "www.vbpl.vn", "vanban.chinhphu.vn", "congbao.chinhphu.vn"}


def source_review_recommendation(observation: Mapping[str, Any] | None) -> str:
    if not observation:
        return "MANUAL_SOURCE_REQUIRED"
    host = (urlparse(str(observation.get("source_url") or "")).hostname or "").casefold()
    identity = str(observation.get("identity_status") or "").casefold()
    status = str(observation.get("normalized_status") or "").casefold()
    evidence = str(observation.get("evidence_status") or "").casefold()
    if host not in OFFICIAL_HOSTS or identity != "exact":
        return "MANUAL_IDENTITY_REVIEW"
    if status in {"expired", "not_yet_effective", "repealed", "suspended_full"}:
        return "REJECT_NOT_EFFECTIVE"
    if status in {"expired_partial", "suspended_partial", "amended", "unknown"}:
        return "MANUAL_EFFECTIVITY_REVIEW"
    if evidence not in {"sufficient"}:
        return "MANUAL_EVIDENCE_REVIEW"
    if status == "active":
        return "PROPOSE_SOURCE_APPROVAL"
    return "MANUAL_EFFECTIVITY_REVIEW"


def verification_sha256(
    value: Mapping[str, Any], hash_field: str = "verification_sha256"
) -> str:
    payload = dict(value)
    payload.pop(hash_field, None)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def owner_proposal_decision(
    basis: Mapping[str, Any], verification: Mapping[str, Any] | None
) -> str:
    if basis.get("resolution_status") == "resolved_official":
        if (
            basis.get("serving_state") == "current_retrievable"
            and basis.get("official_url")
            and basis.get("available_article_numbers")
        ):
            return "PROPOSE_APPROVE_INDEXED"
        if basis.get("serving_state") == "current_retrievable" and basis.get("official_url"):
            return "PROPOSE_APPROVE_SOURCE_ONLY"
        return "HOLD_NOT_CURRENT_OR_INCOMPLETE"
    recommendation = str((verification or {}).get("recommendation") or "")
    if recommendation == "PROPOSE_SOURCE_APPROVAL":
        return "PROPOSE_APPROVE_SOURCE_ONLY"
    if recommendation == "REJECT_NOT_EFFECTIVE":
        return "PROPOSE_REJECT_NOT_EFFECTIVE"
    return "HOLD_FOR_MANUAL_REVIEW"


__all__ = ["owner_proposal_decision", "source_review_recommendation", "verification_sha256"]
