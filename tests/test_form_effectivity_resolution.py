from __future__ import annotations

import pytest

from api.form_source_resolution import classify_form_effectivity


def _partial(evidence: list[dict] | None = None) -> dict:
    return {
        "docNum": "01/2025/TT-TEST",
        "effStatus": "Còn hiệu lực một phần",
        "effFrom": "2025-01-01",
        "appendix_effectivity": evidence or [],
    }


@pytest.mark.parametrize(
    ("evidence", "expected_reason", "eligible"),
    [
        (
            {
                "appendix_identifier": "PHỤ LỤC II",
                "status": "active",
                "verified_as_of": "2026-07-28",
                "official_source_url": "https://vbpl.vn/evidence/1",
            },
            "APPENDIX_EFFECTIVITY_VERIFIED",
            True,
        ),
        (
            {
                "appendix_identifier": "PHỤ LỤC II",
                "status": "replaced",
                "verified_as_of": "2026-07-28",
                "official_source_url": "https://vbpl.vn/evidence/2",
                "replacement_document_number": "02/2026/TT-TEST",
            },
            "FORM_APPENDIX_SUPERSEDED",
            False,
        ),
        (
            {
                "appendix_identifier": "PHỤ LỤC II",
                "status": "active",
                "effective_to": "2026-06-30",
                "verified_as_of": "2026-07-28",
                "official_source_url": "https://vbpl.vn/evidence/3",
            },
            "FORM_APPENDIX_SUPERSEDED",
            False,
        ),
    ],
)
def test_exact_appendix_effectivity_edges(evidence, expected_reason, eligible) -> None:
    result = classify_form_effectivity(
        _partial([evidence]),
        legal_as_of="2026-07-29",
        appendix_identifier="Phụ lục II",
    )
    assert result["reason_code"] == expected_reason
    assert result["eligible"] is eligible


def test_partial_or_wrong_appendix_remains_fail_closed() -> None:
    result = classify_form_effectivity(
        _partial(
            [
                {
                    "appendix_identifier": "PHỤ LỤC I",
                    "status": "active",
                    "verified_as_of": "2026-07-28",
                    "official_source_url": "https://vbpl.vn/evidence/4",
                }
            ]
        ),
        legal_as_of="2026-07-29",
        appendix_identifier="Phụ lục II",
    )
    assert result["reason_code"] == "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW"
    assert result["eligible"] is False


def test_expired_instrument_is_ineligible() -> None:
    result = classify_form_effectivity(
        {
            "effStatus": "Hết hiệu lực",
            "effFrom": "2020-01-01",
            "effTo": "2025-12-31",
        },
        legal_as_of="2026-07-29",
        appendix_identifier=None,
    )
    assert result["reason_code"] == "ISSUING_INSTRUMENT_EXPIRED"
    assert result["eligible"] is False

