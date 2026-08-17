from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    LegalValidityObservation,
    NormalizedValidityStatus,
    normalize_law_number,
    normalize_provisions,
    normalize_validity_status,
    serving_decision,
)


@pytest.mark.parametrize(
    ("raw", "effective_from", "effective_to", "expected"),
    [
        ("Còn hiệu lực", "2024-01-01", None, NormalizedValidityStatus.ACTIVE),
        ("Chờ hiệu lực", "2027-01-01", None, NormalizedValidityStatus.NOT_YET_EFFECTIVE),
        ("Hết hiệu lực", "2020-01-01", "2025-01-01", NormalizedValidityStatus.EXPIRED),
        ("Hết hiệu lực một phần", "2020-01-01", None, NormalizedValidityStatus.EXPIRED_PARTIAL),
        ("Ngưng hiệu lực một phần", "2020-01-01", None, NormalizedValidityStatus.SUSPENDED_PARTIAL),
        ("Bị thay thế", "2020-01-01", None, NormalizedValidityStatus.REPLACED),
        ("Được sửa đổi, bổ sung", "2020-01-01", None, NormalizedValidityStatus.AMENDED),
    ],
)
def test_normalizes_official_status_without_guessing(raw, effective_from, effective_to, expected):
    assert normalize_validity_status(
        raw,
        effective_from=effective_from,
        effective_to=effective_to,
        as_of=date(2026, 8, 8),
    ) is expected


def test_dates_can_prove_future_or_expired_but_missing_evidence_stays_unknown():
    assert normalize_validity_status(
        "",
        effective_from="2027-01-01",
        effective_to=None,
        as_of=date(2026, 8, 8),
    ) is NormalizedValidityStatus.NOT_YET_EFFECTIVE
    assert normalize_validity_status(
        "",
        effective_from="2020-01-01",
        effective_to="2026-08-01",
        as_of=date(2026, 8, 8),
    ) is NormalizedValidityStatus.EXPIRED
    assert normalize_validity_status(
        "",
        effective_from="2020-01-01",
        effective_to=None,
        as_of=date(2026, 8, 8),
    ) is NormalizedValidityStatus.UNKNOWN


def test_identity_and_provision_normalization_are_exact_and_bounded():
    assert normalize_law_number(" 31 / 2024 / QH15 ") == "31/2024/QH15"
    assert normalize_law_number("Luật Đất đai 2024") is None
    provisions = normalize_provisions(
        [
            {"article": "Điều 5", "clause": "Khoản 2", "point": "Điểm a"},
            {"article": "5", "clause": "2", "point": "a"},
            {"article": "7"},
            {"article": "toàn bộ nội dung không xác định"},
        ]
    )
    assert [item.to_dict() for item in provisions] == [
        {"article": "5", "clause": "2", "point": "a"},
        {"article": "7", "clause": None, "point": None},
    ]


def _observation(
    status: NormalizedValidityStatus,
    *,
    effective_from: str | None = "2020-01-01",
    effective_to: str | None = None,
    provisions=(),
    evidence_status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
) -> LegalValidityObservation:
    return LegalValidityObservation(
        document_id="42",
        law_number="12/2020/NĐ-CP",
        issuing_agency="Chính phủ",
        issued_date="2020-01-01",
        source_url="https://vbpl.vn/van-ban/chi-tiet/example--42",
        source_kind="vbpl",
        raw_status=status.value,
        normalized_status=status,
        effective_from=effective_from,
        effective_to=effective_to,
        affecting_document_number=None,
        affected_provisions=tuple(provisions),
        identity_status=IdentityStatus.EXACT,
        evidence_status=evidence_status,
        observed_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        source_updated_at=None,
    )


def test_serving_matrix_blocks_adverse_and_handles_historical_interval():
    expired = _observation(
        NormalizedValidityStatus.EXPIRED,
        effective_to="2026-08-01",
    )
    current = serving_decision(expired, as_of=date(2026, 8, 8), mode="protect")
    assert current.blocked is True
    assert current.reason_code == "expired"

    historical = serving_decision(expired, as_of=date(2025, 1, 1), mode="protect")
    assert historical.blocked is False
    assert historical.warning_code == "historical_validity"

    shadow = serving_decision(expired, as_of=date(2026, 8, 8), mode="observe")
    assert shadow.blocked is False
    assert shadow.would_block is True


def test_partial_scope_is_exact_or_fails_closed():
    provisions = normalize_provisions([{"article": "5", "clause": "2"}])
    exact = _observation(
        NormalizedValidityStatus.EXPIRED_PARTIAL,
        provisions=provisions,
    )
    assert serving_decision(
        exact,
        as_of=date(2026, 8, 8),
        mode="protect",
        article_number="5",
        clause_number="2",
    ).blocked is True
    assert serving_decision(
        exact,
        as_of=date(2026, 8, 8),
        mode="protect",
        article_number="6",
    ).blocked is False

    unresolved = _observation(
        NormalizedValidityStatus.EXPIRED_PARTIAL,
        evidence_status=EvidenceStatus.PARTIAL_SCOPE_MISSING,
    )
    decision = serving_decision(unresolved, as_of=date(2026, 8, 8), mode="protect")
    assert decision.blocked is True
    assert decision.reason_code == "partial_scope_unresolved"


def test_unknown_is_warning_in_protect_and_blocked_only_in_strict():
    unknown = _observation(NormalizedValidityStatus.UNKNOWN)
    protect = serving_decision(unknown, as_of=date(2026, 8, 8), mode="protect")
    strict = serving_decision(unknown, as_of=date(2026, 8, 8), mode="strict")
    assert protect.blocked is False
    assert protect.warning_code == "validity_unverified"
    assert strict.blocked is True
