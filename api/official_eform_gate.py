"""Fail-closed technical gate for official interactive e-forms.

Passing this gate creates review evidence only.  It never grants legal approval
or runtime eligibility.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import parse_qsl, urlparse


OFFICIAL_EFORM_HOSTS = {
    "dichvucong.gov.vn",
    "dichvucong.haiphong.gov.vn",
}
SESSION_QUERY_KEYS = {
    "session",
    "sessionid",
    "sid",
    "token",
    "access_token",
    "auth",
    "jsessionid",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blocked(reason_code: str) -> dict[str, Any]:
    return {
        "status": "VERIFIED_DATA_GAP",
        "reason_code": reason_code,
        "approved": False,
        "runtime_eligible": False,
        "candidate_only": True,
    }


def evaluate_official_eform(
    requirement: Mapping[str, Any],
    probe: Mapping[str, Any],
    *,
    legal_as_of: str,
    role: str,
) -> dict[str, Any]:
    """Evaluate ownership, stable access, effectivity and role visibility."""

    source_url = _text(requirement.get("source_page_url"))
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_EFORM_HOSTS:
        return _blocked("EFORM_SOURCE_NOT_OFFICIAL")
    if any(
        key.casefold() in SESSION_QUERY_KEYS
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return _blocked("EFORM_SESSION_LINK_REJECTED")
    if int(probe.get("http_status") or 0) != 200:
        return _blocked("EFORM_ROUTE_UNAVAILABLE")
    if probe.get("requires_login") is True:
        return _blocked("EFORM_LOGIN_REQUIRED")
    if probe.get("has_captcha") is True:
        return _blocked("EFORM_CAPTCHA_BLOCKED")
    expected_procedure = _text(requirement.get("procedure_id"))
    if _text(probe.get("procedure_id")) != expected_procedure:
        return _blocked("EFORM_WRONG_PROCEDURE")
    visible_roles = {
        _text(item).casefold() for item in probe.get("eform_visible_to_roles") or []
    }
    if _text(role).casefold() not in visible_roles:
        return _blocked("EFORM_ROLE_NOT_VISIBLE")
    if _text(probe.get("effectivity")).casefold() not in {"current", "active"}:
        return _blocked("EFORM_EFFECTIVITY_UNVERIFIED")
    if not _text(requirement.get("canonical_name")):
        return _blocked("EFORM_IDENTITY_UNRESOLVED")
    return {
        "status": "READY_FOR_HUMAN_ATTESTATION",
        "reason_code": "OFFICIAL_EFORM_TECHNICALLY_VERIFIED",
        "legal_as_of": _text(legal_as_of),
        "source_page_url": source_url,
        "procedure_id": expected_procedure,
        "approved": False,
        "runtime_eligible": False,
        "candidate_only": True,
        "human_attestation_required": True,
    }
