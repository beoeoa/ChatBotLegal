from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from api.form_completion_campaign import (
    apply_candidate_technical_validation,
    build_form_completion_jobs,
    build_privacy_safe_campaign_report,
    is_form_completion_campaign_due,
    launch_form_completion_campaign,
    merge_source_findings_preserving_stronger_evidence,
)


def test_campaign_enqueues_only_active_forms_with_official_source_seed() -> None:
    forms = {
        "forms": [
            {
                "form_id": "form-ready-source",
                "canonical_name": "Mẫu CT01",
                "form_code": "CT01",
                "procedure_ids": ["dang_ky_cu_tru"],
                "domain": "cu_tru",
                "approved": False,
            },
            {
                "form_id": "form-no-source",
                "canonical_name": "Mẫu chưa có nguồn",
                "procedure_ids": ["thu_tuc_khac"],
                "approved": False,
            },
            {
                "form_id": "form-excluded",
                "canonical_name": "Mẫu không do Nhà nước quy định",
                "procedure_ids": ["thu_tuc_loai"],
                "catalog_disposition": "excluded_no_official_form",
                "approved": False,
            },
            {
                "form_id": "form-approved",
                "canonical_name": "Mẫu đã duyệt",
                "procedure_ids": ["thu_tuc_da_duyet"],
                "approved": True,
                "review_status": "approved",
                "legal_review_status": "approved",
            },
        ]
    }
    findings = {
        "findings": [
            {
                "form_id": "form-ready-source",
                "procedure_id": "dang_ky_cu_tru",
                "status": "AVAILABLE_OFFICIAL_FILE",
                "source_page": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc.html",
                "download_url": "https://dichvucong.gov.vn/files/ct01.pdf",
                "official_procedure_code": "1.004194",
                "legal_basis": ["53/2025/TT-BCA"],
            },
            {
                "form_id": "form-no-source",
                "procedure_id": "thu_tuc_khac",
                "status": "NEEDS_SOURCE_MAPPING",
            },
            {
                "form_id": "form-excluded",
                "procedure_id": "thu_tuc_loai",
                "status": "NO_PUBLIC_DOWNLOAD_VERIFIED",
                "source_page": "https://dichvucong.gov.vn/excluded",
            },
        ]
    }
    procedures = {
        "procedures": [
            {
                "procedure_id": "dang_ky_cu_tru",
                "official_procedure_code": "1.004194",
                "official_procedure_url": "https://dichvucong.gov.vn/procedure/1.004194",
            }
        ]
    }

    result = build_form_completion_jobs(
        canonical_forms_payload=forms,
        form_source_findings_payload=findings,
        procedure_sources_payload=procedures,
        legal_as_of="2026-07-27",
    )

    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["case_id"] == "form-ready-source"
    assert result["jobs"][0]["gap_type"] == "MISSING_FORM_SOURCE"
    assert result["jobs"][0]["official_metadata"]["confirmed_official_source"] is True
    assert result["jobs"][0]["official_metadata"]["legal_basis"] == ["53/2025/TT-BCA"]
    assert result["unresolved_reason_counts"] == {
        "NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH": 1
    }
    assert result["auto_approved_count"] == 0


def test_campaign_report_is_aggregate_only_and_never_claims_legal_approval() -> None:
    report = build_privacy_safe_campaign_report(
        run_id="form-campaign-opaque",
        legal_as_of="2026-07-27",
        status="waiting_for_human_attestation",
        counts={
            "active_catalog_forms": 65,
            "already_approved": 10,
            "ready_for_attestation": 4,
            "verified_data_gap": 8,
            "blocked_external": 2,
            "remaining_unresolved": 41,
        },
        reason_counts={"NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH": 29},
    )

    assert report["automated_approval"] is False
    assert report["human_attestation_required"] is True
    assert "question" not in str(report).casefold()
    assert "answer" not in str(report).casefold()
    assert "source_url" not in str(report).casefold()
    assert "form_name" not in str(report).casefold()
    assert report["counts"]["ready_for_attestation"] == 4


def test_campaign_job_identity_is_stable_for_repeated_run() -> None:
    payload = {
        "forms": [
            {
                "form_id": "form-1",
                "canonical_name": "Mẫu CT01",
                "form_code": "CT01",
                "procedure_ids": ["dang_ky_cu_tru"],
                "domain": "cu_tru",
            }
        ]
    }
    findings = {
        "findings": [
            {
                "form_id": "form-1",
                "procedure_id": "dang_ky_cu_tru",
                "status": "OFFICIAL_PACKAGE_PAGE",
                "source_page": "https://dichvucong.gov.vn/procedure/1.004194",
                "official_procedure_code": "1.004194",
                "legal_basis": ["53/2025/TT-BCA"],
            }
        ]
    }

    first = build_form_completion_jobs(
        canonical_forms_payload=payload,
        form_source_findings_payload=findings,
        procedure_sources_payload={"procedures": []},
        legal_as_of="2026-07-27",
    )
    second = build_form_completion_jobs(
        canonical_forms_payload=payload,
        form_source_findings_payload=findings,
        procedure_sources_payload={"procedures": []},
        legal_as_of="2026-07-27",
    )

    assert first["jobs"][0]["job_id"] == second["jobs"][0]["job_id"]


def test_technical_validation_prepares_but_never_approves_candidate() -> None:
    candidate = {
        "id": "candidate-ct01",
        "procedure_id": "dang_ky_cu_tru",
        "official_procedure_code": "1.004194",
        "source_page_url": "https://dichvucong.gov.vn/procedure/1.004194",
        "source_download_url": "https://dichvucong.gov.vn/files/ct01.pdf",
        "sha256": "a" * 64,
        "legal_basis": ["53/2025/TT-BCA"],
    }

    result = apply_candidate_technical_validation(
        candidate,
        form={"procedure_ids": ["dang_ky_cu_tru"]},
        finding={"status": "AVAILABLE_OFFICIAL_FILE"},
        effectivity_rules=[
            {
                "rule_id": "residence-current",
                "official_procedure_codes": ["1.004194"],
                "effective_from": "2025-07-01",
                "official_source_url": "https://vanban.bocongan.gov.vn/current",
                "verified_as_of": "2026-07-27",
            }
        ],
        validated_at="2026-07-27T08:00:00+00:00",
    )

    assert result["technical_validation"]["status"] == "passed"
    assert result["preparation_status"] == "ready_for_human_review"
    assert result["review_status"] == "candidate_pending_review"
    assert result["approved"] is False
    assert result["runtime_eligible"] is False


def test_technical_validation_accepts_only_exact_dvc_attachment_rule() -> None:
    procedure_url = (
        "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/"
        "019d2bff-2d2f-73b6-a226-b9bad3aca884"
    )
    decision_url = (
        "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
        "019eb146-cf41-72c8-9f55-1d3df27dd74a"
    )
    candidate = {
        "id": "candidate-dvc-07",
        "procedure_id": "1.013870",
        "official_procedure_code": "1.013870",
        "form_code": "07",
        "source_page_url": decision_url,
        "source_download_url": decision_url,
        "sha256": "a" * 64,
        "legal_basis": ["91/2016/NĐ-CP"],
        "effective_from": "2016-07-01",
        "effective_to": None,
        "provenance_kind": "official_dvc_attachment",
        "source_attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
        "source_package_sha256": "b" * 64,
        "provenance": {
            "kind": "official_dvc_attachment",
            "source_attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
            "source_package_sha256": "b" * 64,
        },
        "extraction": {
            "kind": "structural_docx_form_boundary",
            "complete": True,
            "element_range": [12, 31],
        },
    }
    rule = {
        "evidence_id": "nd-91-2016-appendix-i-form-07-dvc-current",
        "evidence_basis": "official_current_dvc_attachment",
        "issuing_instrument": "91/2016/NĐ-CP",
        "target_form_code": "07",
        "effective_from": "2026-07-01",
        "effective_to": "2027-02-28",
        "verified_as_of": "2026-07-29",
        "official_source_url": decision_url,
        "publication_decision_number": "1684/QĐ-BYT",
        "procedure_bindings": [
            {
                "procedure_id": "1.013870",
                "official_source_url": procedure_url,
            }
        ],
        "canonical_artifact_attachment": {
            "attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
            "sha256": "b" * 64,
            "source_page_url": decision_url,
        },
    }

    result = apply_candidate_technical_validation(
        candidate,
        form={"procedure_ids": ["1.013870"]},
        finding={"status": "OFFICIAL_PACKAGE_PAGE"},
        effectivity_rules=[rule],
        validated_at="2026-07-29T08:00:00+00:00",
    )

    assert result["technical_validation"]["status"] == "passed"
    assert result["effective_from"] == "2016-07-01"
    assert result["effective_to"] is None
    assert result["effectivity_provenance"]["rule_id"] == rule["evidence_id"]
    assert (
        result["effectivity_provenance"]["source_attachment_id"]
        == "019eb4af-a3af-7143-b664-6dd27b38b9de"
    )
    assert result["approved"] is False
    assert result["runtime_eligible"] is False


def test_campaign_launcher_is_idempotent_while_queued(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict]] = []

    def launch(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=123)

    status_path = tmp_path / "data/form_completion_campaign/status_v1.json"
    first = launch_form_completion_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-27",
        status_path=status_path,
        launcher=launch,
    )
    second = launch_form_completion_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-27",
        status_path=status_path,
        launcher=launch,
    )

    assert first["launch_status"] == "started"
    assert second["launch_status"] == "already_running"
    assert len(calls) == 1
    assert first["automated_approval"] is False


def test_daily_campaign_due_policy_does_not_repeat_same_legal_date() -> None:
    assert is_form_completion_campaign_due(
        {"status": "not_started", "legal_as_of": ""},
        legal_as_of="2026-07-27",
    )
    assert not is_form_completion_campaign_due(
        {"status": "completed_fail_closed", "legal_as_of": "2026-07-27"},
        legal_as_of="2026-07-27",
    )
    assert not is_form_completion_campaign_due(
        {"status": "failed_fail_closed", "legal_as_of": "2026-07-27"},
        legal_as_of="2026-07-27",
    )
    assert is_form_completion_campaign_due(
        {"status": "completed_fail_closed", "legal_as_of": "2026-07-26"},
        legal_as_of="2026-07-27",
    )


def test_new_weak_discovery_cannot_reopen_verified_no_official_form() -> None:
    previous = {
        "findings": [
            {
                "form_id": "form-no-state-template",
                "status": "NO_PUBLIC_DOWNLOAD_VERIFIED",
                "reason_code": "FORM_NOT_LISTED_IN_OFFICIAL_PROCEDURE",
                "source_page": "https://dichvucong.gov.vn/procedure/official",
            }
        ]
    }
    refreshed = {
        "findings": [
            {
                "form_id": "form-no-state-template",
                "status": "NEEDS_SOURCE_MAPPING",
                "reason_code": "NO_UNAMBIGUOUS_OFFICIAL_MATCH",
            }
        ]
    }

    merged = merge_source_findings_preserving_stronger_evidence(
        previous,
        refreshed,
    )

    assert merged["findings"][0]["status"] == "NO_PUBLIC_DOWNLOAD_VERIFIED"
    assert (
        merged["findings"][0]["reason_code"]
        == "FORM_NOT_LISTED_IN_OFFICIAL_PROCEDURE"
    )
