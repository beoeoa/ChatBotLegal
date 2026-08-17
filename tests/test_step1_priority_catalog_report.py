from __future__ import annotations

import json
from pathlib import Path

from scripts.build_step1_priority_catalog_report import build_report


def test_priority_report_surfaces_foreign_marriage_candidate_without_approving_it():
    report = build_report()
    assert len(report["groups"]) == 7
    assert report["auto_approved_count"] == 0
    assert report["runtime_policy"] == "hard_gate_only"
    foreign = next(
        item
        for item in report["groups"]
        if item["label"] == "Đăng ký kết hôn có yếu tố nước ngoài"
    )
    assert foreign["procedure_id"] == "dang_ky_ket_hon_nuoc_ngoai"
    assert foreign["status"] == "LEGAL_REVIEW_REQUIRED"
    assert foreign["forms_unavailable"] is True
    assert "HUMAN_LEGAL_REVIEW_REQUIRED" in foreign["data_gap_reasons"]
    assert len(foreign["forms"]) == 1
    assert foreign["forms"][0]["approved"] is False
    assert foreign["forms"][0]["runtime_eligible"] is False
    assert foreign["forms"][0]["procedure_id"] == "dang_ky_ket_hon_nuoc_ngoai"


def test_priority_report_serves_only_hard_gate_approved_runtime_forms():
    report = build_report()
    assert report["overall_status"] == "BLOCKED_EXTERNAL"
    for group in report["groups"]:
        for form in group["forms"]:
            if form["runtime_eligible"]:
                assert group["forms_unavailable"] is False
                assert form["approved"] is True
                assert form["legal_review_status"] == "approved"
                assert form["binding_status"] == "approved"
            else:
                assert form["hard_gate_reason"] != "ELIGIBLE"


def test_catalog_keeps_candidate_evidence_namespaced_and_unapproved():
    root = Path(__file__).resolve().parents[1]
    procedure = json.loads(
        (root / "notebook_data/forms/canonical_procedures_v1.json").read_text(
            encoding="utf-8"
        )
    )["procedures"][0]
    form = json.loads(
        (root / "notebook_data/forms/canonical_forms_catalog_v1.json").read_text(
            encoding="utf-8"
        )
    )["forms"][0]
    assert "candidate_source_evidence" in procedure
    assert "candidate_source_evidence" in form
    assert procedure["approved"] is False
    assert form["approved"] is False
    assert procedure["runtime_eligible"] is False
