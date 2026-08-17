from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from api.legal_validity_registry import (
    apply_validity_overlay,
    current_answer_validity_readiness,
)


def _entry(
    *,
    status: str,
    effective_to: str | None = None,
    affected: list[dict] | None = None,
) -> dict:
    return {
        "normalized_status": status,
        "serving_action": "allow",
        "source_url": "https://vbpl.vn/official",
        "verified_at": "2026-08-10T00:00:00+00:00",
        "effective_from": "2015-01-01",
        "effective_to": effective_to,
        "affected_provisions": affected or [],
        "identity_status": "exact",
        "evidence_status": "sufficient",
    }


def _snapshot() -> dict:
    now = "2026-08-10T00:00:00+00:00"
    return {
        "schema_version": "legal-validity-serving-v1",
        "last_success_at": now,
        "mode": "protect",
        "coverage": {"eligible": 2, "observed": 2, "fresh": 2},
        "documents": {
            "88/2001/NĐ-CP": _entry(
                status="expired", effective_to="2015-11-14"
            ),
            "70/2015/NĐ-CP": _entry(
                status="expired_partial",
                affected=[
                    {"article": "13", "clause": None, "point": None},
                    {"article": "14", "clause": None, "point": None},
                ],
            ),
        },
        "document_ids": {},
    }


def _row(law_number: str, article: str) -> dict:
    return {
        "chunk_id": f"{law_number}-{article}",
        "law_number": law_number,
        "article_number": article,
        "document_status": "active",
        "content": "Nội dung kiểm thử",
    }


def test_known_expired_document_is_blocked_from_current_answer():
    result = apply_validity_overlay(
        {"results": [_row("88/2001/NĐ-CP", "1")]},
        snapshot=_snapshot(),
        as_of=date(2026, 8, 10),
        mode="strict",
    )
    assert result["results"] == []
    assert result["validity_sync"]["filtered_reasons"] == {"expired": 1}


def test_partial_validity_blocks_only_articles_13_and_14():
    result = apply_validity_overlay(
        {
            "results": [
                _row("70/2015/NĐ-CP", "12"),
                _row("70/2015/NĐ-CP", "13"),
                _row("70/2015/NĐ-CP", "14"),
            ]
        },
        snapshot=_snapshot(),
        as_of=date(2026, 8, 10),
        mode="strict",
    )
    assert [item["article_number"] for item in result["results"]] == ["12"]
    assert result["validity_sync"]["filtered_count"] == 2


def test_strict_readiness_requires_full_fresh_coverage_and_provision_scope():
    snapshot = _snapshot()
    healthy = current_answer_validity_readiness(
        snapshot,
        now=datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
    )
    assert healthy["ready"] is True
    assert healthy["coverage_ratio"] == 1.0

    stale = current_answer_validity_readiness(
        snapshot,
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )
    assert stale["ready"] is False
    assert "validity_snapshot_stale" in stale["reason_codes"]

    incomplete = _snapshot()
    incomplete["coverage"]["observed"] = 1
    incomplete["documents"]["70/2015/NĐ-CP"]["affected_provisions"] = []
    readiness = current_answer_validity_readiness(
        incomplete,
        now=datetime(2026, 8, 10, 1, tzinfo=timezone.utc),
    )
    assert readiness["ready"] is False
    assert "validity_coverage_incomplete" in readiness["reason_codes"]
    assert "partial_scope_unresolved" in readiness["reason_codes"]
