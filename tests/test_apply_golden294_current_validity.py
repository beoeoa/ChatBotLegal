from datetime import datetime, timezone

import pytest

from scripts.apply_golden294_current_validity import (
    OFFICIAL_EVIDENCE,
    observation_for,
    validate_official_evidence,
)


def test_manual_official_evidence_is_exact_bounded_and_official():
    validate_official_evidence(OFFICIAL_EVIDENCE)

    assert set(OFFICIAL_EVIDENCE) == {"31/2024/QH15", "73/2025/QH15"}
    assert all(
        item["official_status"] == "Còn hiệu lực"
        for item in OFFICIAL_EVIDENCE.values()
    )


def test_manual_observation_projects_current_exact_evidence():
    observation = observation_for(
        "73/2025/QH15",
        observed_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )

    value = observation.to_dict()
    assert value["normalized_status"] == "active"
    assert value["identity_status"] == "exact"
    assert value["evidence_status"] == "sufficient"
    assert value["source_kind"] == "vbpl_manual_official_review"


def test_evidence_gate_rejects_non_official_or_incomplete_sources():
    invalid = {law: dict(item) for law, item in OFFICIAL_EVIDENCE.items()}
    invalid["73/2025/QH15"]["source_url"] = "https://example.com/law"
    with pytest.raises(ValueError, match="NOT_OFFICIAL"):
        validate_official_evidence(invalid)

    invalid = {law: dict(item) for law, item in OFFICIAL_EVIDENCE.items()}
    invalid["31/2024/QH15"]["reviewed_amendment_sources"] = []
    with pytest.raises(ValueError, match="EVIDENCE_MISSING"):
        validate_official_evidence(invalid)
