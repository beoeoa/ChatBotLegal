from datetime import date

import pytest

from scripts.apply_golden294_residence_expiry import (
    EXPIRED_ON,
    TARGETS,
    validate_database_rows,
    validate_observation,
)


def _observation(law_number: str) -> dict:
    expected = TARGETS[law_number]
    return {
        "law_number": law_number,
        "identity_status": "exact",
        "evidence_status": "sufficient",
        "normalized_status": "expired",
        "effective_to": EXPIRED_ON.isoformat(),
        "source_url": expected["source_url"],
    }


@pytest.mark.parametrize("law_number", list(TARGETS))
def test_official_expiry_requires_exact_sufficient_evidence(law_number):
    validate_observation(law_number, _observation(law_number))


def test_official_expiry_rejects_wrong_date():
    value = _observation("55/2021/TT-BCA")
    value["effective_to"] = "2026-07-02"
    with pytest.raises(ValueError, match="effective_to"):
        validate_observation("55/2021/TT-BCA", value)


def test_database_guard_preserves_historical_content():
    rows = []
    for law_number, expected in TARGETS.items():
        rows.append(
            {
                "id": expected["document_id"],
                "law_number": law_number,
                "status": "active",
                "expired_date": None,
                "article_count": 2,
                "chunk_count": 3,
            }
        )
    validate_database_rows(rows)


def test_database_guard_rejects_missing_chunks():
    rows = []
    for law_number, expected in TARGETS.items():
        rows.append(
            {
                "id": expected["document_id"],
                "law_number": law_number,
                "status": "active",
                "expired_date": date(2026, 7, 1),
                "article_count": 2,
                "chunk_count": 0 if law_number == "66/2023/TT-BCA" else 3,
            }
        )
    with pytest.raises(RuntimeError, match="HISTORY_MISSING"):
        validate_database_rows(rows)
