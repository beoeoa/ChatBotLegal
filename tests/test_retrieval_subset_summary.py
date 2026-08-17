from __future__ import annotations

import json
from pathlib import Path

from scripts.summarize_retrieval_subset import summarize


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_summarize_retrieval_subset_requires_post_correction_validity_pass(
    tmp_path: Path,
) -> None:
    case_ids = [f"golden-{index:04d}" for index in range(1, 295)]
    report = _write(
        tmp_path / "report.json",
        {
            "cases": [
                {
                    "case_id": case_id,
                    "classification": "FOUND_AND_RETRIEVED",
                    "timing_ms": 100,
                    "wrong_field_count": 0,
                    "expired_selection_count": 0,
                }
                for case_id in case_ids
            ]
        },
    )
    selector = _write(
        tmp_path / "selector.json",
        {
            "blocked_expected_sources": [
                {
                    "case_id": case_id,
                    "law_number": "31/2024/QH15",
                    "article_number": "3",
                    "reason_code": "partial_scope_unresolved",
                }
                for case_id in case_ids
            ]
        },
    )
    validity = _write(
        tmp_path / "validity.json",
        {
            "status": "pass",
            "summary": {"blocked_expected_source_count": 0},
        },
    )

    result = summarize(
        report_path=report,
        selector_path=selector,
        validity_audit_path=validity,
    )

    assert result["status"] == "PASS"
    assert result["case_count"] == 294
    assert result["expired_selection_count"] == 0


def test_summarize_retrieval_subset_fails_when_validity_still_blocks(
    tmp_path: Path,
) -> None:
    case_ids = [f"golden-{index:04d}" for index in range(1, 295)]
    report = _write(
        tmp_path / "report.json",
        {
            "cases": [
                {
                    "case_id": case_id,
                    "classification": "FOUND_AND_RETRIEVED",
                    "timing_ms": 100,
                    "wrong_field_count": 0,
                    "expired_selection_count": 0,
                }
                for case_id in case_ids
            ]
        },
    )
    selector = _write(
        tmp_path / "selector.json",
        {
            "blocked_expected_sources": [
                {"case_id": case_id, "law_number": "31/2024/QH15"}
                for case_id in case_ids
            ]
        },
    )
    validity = _write(
        tmp_path / "validity.json",
        {
            "status": "needs_revalidation",
            "summary": {"blocked_expected_source_count": 1},
        },
    )

    result = summarize(
        report_path=report,
        selector_path=selector,
        validity_audit_path=validity,
    )

    assert result["status"] == "FAIL"
    assert result["post_correction_validity_audit_passed"] is False
