from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    NormalizedValidityStatus,
)
from api.legal_validity_source import VBPLValiditySource

FIXTURE = Path(__file__).parent / "fixtures" / "vbpl_validity_responses.json"


@pytest.fixture(scope="module")
def cases():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {item["name"]: item for item in payload["cases"]}


@pytest.fixture
def source():
    return VBPLValiditySource()


def _parse(source, case, **expected):
    return source.parse_items(
        instrument=case["instrument"],
        items=case["items"],
        document_id=expected.pop("document_id", "42"),
        expected_issuing_agency=expected.pop("expected_issuing_agency", None),
        expected_issued_date=expected.pop("expected_issued_date", None),
        observed_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        as_of=date(2026, 8, 8),
        **expected,
    )


@pytest.mark.parametrize(
    ("case_name", "status", "evidence"),
    [
        ("active", NormalizedValidityStatus.ACTIVE, EvidenceStatus.SUFFICIENT),
        ("future", NormalizedValidityStatus.NOT_YET_EFFECTIVE, EvidenceStatus.SUFFICIENT),
        ("expired", NormalizedValidityStatus.EXPIRED, EvidenceStatus.SUFFICIENT),
        ("suspended", NormalizedValidityStatus.SUSPENDED, EvidenceStatus.SUFFICIENT),
        ("partial_exact", NormalizedValidityStatus.EXPIRED_PARTIAL, EvidenceStatus.SUFFICIENT),
        (
            "partial_unresolved",
            NormalizedValidityStatus.EXPIRED_PARTIAL,
            EvidenceStatus.PARTIAL_SCOPE_MISSING,
        ),
    ],
)
def test_parses_exact_official_status_and_partial_scope(source, cases, case_name, status, evidence):
    result = _parse(source, cases[case_name])

    assert result.reason_code == "EXACT_OFFICIAL_VALIDITY_OBSERVED"
    assert result.observation is not None
    assert result.observation.identity_status is IdentityStatus.EXACT
    assert result.observation.normalized_status is status
    assert result.observation.evidence_status is evidence
    assert result.observation.source_url.startswith("https://vbpl.vn/van-ban/chi-tiet/")
    if case_name == "partial_exact":
        assert [item.to_dict() for item in result.observation.affected_provisions] == [
            {"article": "5", "clause": "2", "point": "a"},
            {"article": "7", "clause": None, "point": None},
        ]


def test_zero_or_multiple_exact_matches_never_choose_first(source, cases):
    missing = source.parse_items(
        instrument="14/2020/NĐ-CP",
        items=[],
        document_id="42",
        observed_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        as_of=date(2026, 8, 8),
    )
    ambiguous = _parse(source, cases["ambiguous"])

    assert missing.reason_code == "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND"
    assert missing.observation.identity_status is IdentityStatus.MISSING
    assert ambiguous.reason_code == "OFFICIAL_DOCUMENT_IDENTITY_AMBIGUOUS"
    assert ambiguous.observation.identity_status is IdentityStatus.AMBIGUOUS
    assert ambiguous.observation.normalized_status is NormalizedValidityStatus.UNKNOWN


def test_issuer_or_issue_date_mismatch_is_not_applied(source, cases):
    issuer = _parse(
        source,
        cases["active"],
        expected_issuing_agency="Chính phủ",
    )
    issued = _parse(
        source,
        cases["active"],
        expected_issued_date="2024-01-19",
    )

    assert issuer.observation.identity_status is IdentityStatus.MISMATCH
    assert issuer.reason_code == "OFFICIAL_DOCUMENT_IDENTITY_MISMATCH"
    assert issued.observation.identity_status is IdentityStatus.MISMATCH


def test_malformed_payload_returns_structured_reason_without_guessing(source, cases):
    result = _parse(source, cases["malformed"])

    assert result.reason_code == "OFFICIAL_VALIDITY_PAYLOAD_MALFORMED"
    assert result.observation.identity_status is IdentityStatus.MISSING
    assert result.observation.evidence_status is EvidenceStatus.MALFORMED
    assert result.observation.normalized_status is NormalizedValidityStatus.UNKNOWN


def test_source_configuration_is_https_allowlisted_and_bounded():
    source = VBPLValiditySource(timeout_seconds=12, page_size=500)

    assert source.search_url == "https://vbpl.vn/van-ban/trung-uong"
    assert source.page_size == 100
    assert source.timeout_seconds == 12


def test_untrusted_detail_url_is_replaced_with_canonical_vbpl_link(source, cases):
    item = dict(cases["active"]["items"][0])
    item["detailUrl"] = "https://evil.example/collect"

    result = source.parse_items(
        instrument=item["docNum"],
        items=[item],
        document_id="42",
        observed_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        as_of=date(2026, 8, 8),
    )

    assert result.observation is not None
    assert result.observation.source_url.startswith("https://vbpl.vn/van-ban/chi-tiet/")
    assert "evil.example" not in result.observation.source_url
