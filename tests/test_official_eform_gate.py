from __future__ import annotations

import pytest

from api.official_eform_gate import evaluate_official_eform


def _requirement(**overrides):
    value = {
        "requirement_identity_id": "eform-1",
        "procedure_id": "procedure-1",
        "canonical_name": "Biểu mẫu điện tử thử nghiệm",
        "source_page_url": (
            "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1"
        ),
        "audience": "citizen",
    }
    value.update(overrides)
    return value


def _probe(**overrides):
    value = {
        "http_status": 200,
        "final_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1",
        "requires_login": False,
        "has_captcha": False,
        "procedure_id": "procedure-1",
        "eform_visible_to_roles": ["citizen", "officer", "admin"],
        "effectivity": "current",
    }
    value.update(overrides)
    return value


def test_stable_official_eform_is_candidate_only() -> None:
    result = evaluate_official_eform(
        _requirement(), _probe(), legal_as_of="2026-07-29", role="citizen"
    )
    assert result["status"] == "READY_FOR_HUMAN_ATTESTATION"
    assert result["approved"] is False
    assert result["runtime_eligible"] is False


@pytest.mark.parametrize(
    ("requirement", "probe", "role", "reason"),
    [
        (
            _requirement(
                source_page_url=(
                    "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1?session=secret"
                )
            ),
            _probe(),
            "citizen",
            "EFORM_SESSION_LINK_REJECTED",
        ),
        (_requirement(), _probe(requires_login=True), "citizen", "EFORM_LOGIN_REQUIRED"),
        (_requirement(), _probe(has_captcha=True), "citizen", "EFORM_CAPTCHA_BLOCKED"),
        (
            _requirement(),
            _probe(procedure_id="procedure-2"),
            "citizen",
            "EFORM_WRONG_PROCEDURE",
        ),
        (
            _requirement(),
            _probe(eform_visible_to_roles=["officer", "admin"]),
            "citizen",
            "EFORM_ROLE_NOT_VISIBLE",
        ),
    ],
)
def test_eform_gate_fails_closed(requirement, probe, role, reason) -> None:
    result = evaluate_official_eform(
        requirement, probe, legal_as_of="2026-07-29", role=role
    )
    assert result["reason_code"] == reason
    assert result["approved"] is False
    assert result["runtime_eligible"] is False

