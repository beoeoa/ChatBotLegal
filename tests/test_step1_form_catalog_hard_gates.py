from __future__ import annotations

import hashlib
from datetime import date

from api.legal_form_catalog import FormCatalog


def _catalog(tmp_path, form_overrides=None, *, binding_status="approved"):
    payload = {
        "form_id": "birth-form",
        "procedure_ids": ["dang_ky_khai_sinh"],
        "form_code": "01",
        "canonical_name": "Tờ khai đăng ký khai sinh",
        "audience": "citizen",
        "usage": "applicant_form",
        "required_or_conditional": "required",
        "domain": "ho_tich_chung_thuc",
        "jurisdiction": "national",
        "official_source_page": "https://dichvucong.gov.vn/p/home/procedure",
        "official_download_url": None,
        "local_path": None,
        "file_format": "pdf",
        "sha256": None,
        "effective_from": "2020-01-01",
        "effective_to": None,
        "supersedes_form_id": None,
        "source_classification": "official_file",
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "legal_basis": ["Nghị định hiện hành"],
        "provenance": {"source_url": "https://dichvucong.gov.vn/p/home/procedure"},
        "url_status": "verified",
    }
    payload.update(form_overrides or {})
    return FormCatalog(
        procedures=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Đăng ký khai sinh",
                "aliases": ["khai sinh"],
                "domain": "ho_tich_chung_thuc",
                "review_status": "approved",
            }
        ],
        forms=[payload],
        bindings=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "form_id": payload["form_id"],
                "binding_status": binding_status,
            }
        ],
        project_root=tmp_path,
    )


def _resolve(catalog):
    return catalog.resolve_forms(
        "Tải tờ khai đăng ký khai sinh",
        role="citizen",
        as_of=date(2026, 7, 24),
        procedure_ids=["dang_ky_khai_sinh"],
    )


def test_missing_procedure_id_is_fail_closed(tmp_path):
    result = _resolve(_catalog(tmp_path, {"procedure_ids": []}))
    assert result["recommended_forms"] == []
    assert "FORM_PROCEDURE_ID_MISSING" in result["data_gap_reasons"]


def test_missing_file_is_fail_closed(tmp_path):
    result = _resolve(_catalog(tmp_path, {"local_path": "missing.pdf"}))
    assert result["recommended_forms"] == []
    assert "FORM_FILE_INVALID" in result["data_gap_reasons"]


def test_unreachable_official_url_is_fail_closed(tmp_path):
    result = _resolve(
        _catalog(
            tmp_path,
            {
                "official_download_url": "https://dichvucong.gov.vn/files/birth.pdf",
                "url_status": "unreachable",
            },
        )
    )
    assert result["recommended_forms"] == []
    assert "FORM_URL_UNVERIFIED" in result["data_gap_reasons"]


def test_expired_form_is_fail_closed(tmp_path):
    result = _resolve(_catalog(tmp_path, {"effective_to": "2024-12-31"}))
    assert result["recommended_forms"] == []
    assert "FORM_EXPIRED" in result["data_gap_reasons"]


def test_wrong_procedure_mapping_is_fail_closed(tmp_path):
    result = _resolve(
        _catalog(tmp_path, {"procedure_ids": ["dang_ky_ket_hon"]})
    )
    assert result["recommended_forms"] == []
    assert "WRONG_PROCEDURE" in result["data_gap_reasons"]


def test_seed_record_cannot_be_displayed_as_official(tmp_path):
    result = _resolve(_catalog(tmp_path, {"source_classification": "seed"}))
    assert result["recommended_forms"] == []
    assert result["data_gap_status"] == "VERIFIED_DATA_GAP"
    assert "SOURCE_NOT_DOWNLOADABLE" in result["data_gap_reasons"]


def test_unapproved_form_cannot_be_returned_outside_runtime(tmp_path):
    result = _resolve(
        _catalog(
            tmp_path,
            {"review_status": "candidate_pending_review", "approved": False},
        )
    )
    assert result["recommended_forms"] == []
    assert result["data_gap_status"] == "LEGAL_REVIEW_REQUIRED"
    assert "FORM_NOT_APPROVED" in result["data_gap_reasons"]


def test_approved_but_runtime_ineligible_form_cannot_be_returned(tmp_path):
    result = _resolve(_catalog(tmp_path, {"runtime_eligible": False}))
    assert result["recommended_forms"] == []
    assert result["data_gap_status"] == "LEGAL_REVIEW_REQUIRED"
    assert "FORM_NOT_RUNTIME_ELIGIBLE" in result["data_gap_reasons"]


def test_quarantined_form_cannot_be_returned_even_if_approval_flags_remain(tmp_path):
    result = _resolve(
        _catalog(
            tmp_path,
            {
                "review_status": "approved",
                "approved": True,
                "runtime_eligible": True,
                "is_quarantined": True,
            },
        )
    )
    assert result["recommended_forms"] == []
    assert "FORM_NOT_RUNTIME_ELIGIBLE" in result["data_gap_reasons"]


def test_form_excluded_because_no_official_state_form_can_never_be_served(
    tmp_path,
):
    result = _resolve(
        _catalog(
            tmp_path,
            {
                "catalog_disposition": "excluded_no_official_form",
                "catalog_status": "excluded_no_official_form",
            },
        )
    )
    assert result["recommended_forms"] == []
    assert result["forms_unavailable"] is True
    assert "NO_OFFICIAL_STATE_FORM" in result["data_gap_reasons"]


def test_valid_local_form_passes_all_hard_gates(tmp_path):
    path = tmp_path / "birth.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"0" * 512)
    result = _resolve(
        _catalog(
            tmp_path,
            {
                "local_path": "birth.pdf",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        )
    )
    assert [item["form_id"] for item in result["recommended_forms"]] == ["birth-form"]
    assert result["forms_unavailable"] is False
