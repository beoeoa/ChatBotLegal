from __future__ import annotations

from scripts.audit_approved_golden_validity import audit_dataset


def _entry(status: str, action: str, *, effective_to: str | None = None) -> dict:
    return {
        "normalized_status": status,
        "serving_action": action,
        "source_url": "https://vbpl.vn/official",
        "verified_at": "2026-08-11T00:00:00+00:00",
        "effective_from": "2021-01-01",
        "effective_to": effective_to,
        "affected_provisions": [],
        "identity_status": "exact",
        "evidence_status": "sufficient",
    }


def test_approved_expected_source_blocked_by_snapshot_requires_revalidation():
    dataset = {
        "legal_as_of": "2026-08-11",
        "cases": [
            {
                "case_id": "active-case",
                "legal_as_of": "2026-08-11",
                "expected_sources": [
                    {"law_number": "31/2024/QH15", "article": "9"}
                ],
            },
            {
                "case_id": "expired-case",
                "legal_as_of": "2026-08-11",
                "expected_sources": [
                    {"law_number": "55/2021/TT-BCA", "article": "16"}
                ],
            },
        ],
    }
    snapshot = {
        "schema_version": "legal-validity-serving-v1",
        "mode": "protect",
        "documents": {
            "31/2024/QH15": _entry("active", "allow"),
            "55/2021/TT-BCA": _entry(
                "expired", "historical_only", effective_to="2026-07-01"
            ),
        },
    }

    report = audit_dataset(dataset, snapshot)

    assert report["status"] == "needs_revalidation"
    assert report["summary"]["expected_sources_checked"] == 2
    assert report["summary"]["blocked_case_count"] == 1
    assert report["blocked_laws"] == [
        {
            "law_number": "55/2021/TT-BCA",
            "case_count": 1,
            "case_ids": ["expired-case"],
        }
    ]
