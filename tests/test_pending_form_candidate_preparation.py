from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from api.form_candidate_preparation import (
    CandidatePreparationError,
    prepare_candidate_payload,
)


def _candidate() -> dict:
    return {
        "id": "candidate-1",
        "detected_form_name": "Câu hỏi được crawler phát hiện",
        "review_status": "candidate_pending_review",
        "is_approved": False,
        "file_path": None,
    }


def _spec(file_path: str) -> dict:
    return {
        "candidate_id": "candidate-1",
        "canonical_form_name": "Tờ khai đăng ký kết hôn",
        "procedure_id": "dang_ky_ket_hon",
        "official_procedure_code": "1.000894",
        "domain": "ho_tich_chung_thuc",
        "file_path": file_path,
        "source_page_url": (
            "https://sotp.haiphong.gov.vn/van-ban-chi-dao-dieu-hanh-68017/"
            "cong-bo-thu-tuc-hanh-chinh-linh-vuc-ho-tich-769866"
        ),
        "source_download_url": (
            "https://cdn.haiphong.gov.vn/gov-hpg/6804/tintuc/2025/11/"
            "tthc-linh-vucho-tich638990521771580708.pdf"
        ),
        "publisher": "Sở Tư pháp thành phố Hải Phòng",
        "legal_basis": ["60/2014/QH13", "123/2015/NĐ-CP"],
        "effective_status": "official_source_current_pending_legal_review",
    }


def _write_pdf(root: Path, relative_path: str) -> str:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    content = b"%PDF-1.7\n" + (b"candidate-form\n" * 200)
    target.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_preparation_attaches_verified_file_without_approving(tmp_path: Path) -> None:
    relative = "data/uploads/forms/official_candidates/verified/form.pdf"
    digest = _write_pdf(tmp_path, relative)

    result = prepare_candidate_payload(
        {"summary": {}, "records": [_candidate()]},
        [_spec(relative)],
        project_root=tmp_path,
        allowed_procedure_ids={"dang_ky_ket_hon"},
        prepared_at="2026-07-27T00:00:00+00:00",
    )

    record = result["records"][0]
    assert record["file_path"] == relative
    assert record["sha256"] == digest
    assert record["suggested_procedure_id"] == "dang_ky_ket_hon"
    assert record["official_procedure_code"] == "1.000894"
    assert record["preparation_status"] == "ready_for_human_review"
    assert record["review_status"] == "candidate_pending_review"
    assert record["legal_review_status"] == "candidate_pending_review"
    assert record["is_approved"] is False
    assert record["is_canonical"] is False
    assert result["summary"]["ready_for_human_review"] == 1


def test_preparation_reproduces_missing_candidate_file_error(tmp_path: Path) -> None:
    relative = "data/uploads/forms/official_candidates/verified/missing.pdf"

    with pytest.raises(CandidatePreparationError, match="CANDIDATE_FILE_MISSING"):
        prepare_candidate_payload(
            {"summary": {}, "records": [_candidate()]},
            [_spec(relative)],
            project_root=tmp_path,
            allowed_procedure_ids={"dang_ky_ket_hon"},
        )


def test_preparation_rejects_non_official_source_url(tmp_path: Path) -> None:
    relative = "data/uploads/forms/official_candidates/verified/form.pdf"
    _write_pdf(tmp_path, relative)
    spec = _spec(relative)
    spec["source_page_url"] = "https://local.invalid/admin-reviewed-form"

    with pytest.raises(CandidatePreparationError, match="SOURCE_NOT_OFFICIAL"):
        prepare_candidate_payload(
            {"summary": {}, "records": [_candidate()]},
            [spec],
            project_root=tmp_path,
            allowed_procedure_ids={"dang_ky_ket_hon"},
        )


def test_preparation_rejects_unknown_procedure_mapping(tmp_path: Path) -> None:
    relative = "data/uploads/forms/official_candidates/verified/form.pdf"
    _write_pdf(tmp_path, relative)

    with pytest.raises(CandidatePreparationError, match="UNKNOWN_PROCEDURE_ID"):
        prepare_candidate_payload(
            {"summary": {}, "records": [_candidate()]},
            [_spec(relative)],
            project_root=tmp_path,
            allowed_procedure_ids={"xac_nhan_tinh_trang_hon_nhan"},
        )


def test_preparation_never_changes_an_already_reviewed_candidate(tmp_path: Path) -> None:
    relative = "data/uploads/forms/official_candidates/verified/form.pdf"
    _write_pdf(tmp_path, relative)
    candidate = _candidate()
    candidate["review_status"] = "approved"
    candidate["is_approved"] = True

    with pytest.raises(CandidatePreparationError, match="CANDIDATE_ALREADY_REVIEWED"):
        prepare_candidate_payload(
            {"summary": {}, "records": [candidate]},
            [_spec(relative)],
            project_root=tmp_path,
            allowed_procedure_ids={"dang_ky_ket_hon"},
        )


def test_live_assignment_covers_all_pending_candidates_once() -> None:
    from scripts.prepare_pending_form_candidates import CANDIDATE_ASSIGNMENTS, PDF_FORMS

    expected_ids = {
        "web-809586a0caa4c471e157",
        "web-2e73a1f62661c3582e62",
        "web-26596cccdc6dba9ae65c",
        "web-44e61234f4d9e7d87301",
        "web-1065dc4f26071bf648f4",
        "web-72bcdef4b1d34c60ec86",
        "web-5d5c108600fe97c36c95",
        "web-6c4325e89506a3ab5668",
        "web-db45facfff5559219d9d",
        "web-38bfff37b8069ca01b68",
        "web-5ce9f6c96895e07cf875",
        "web-b97d343433fd4e2986c7",
        "web-f6ad888bc6336b47927a",
        "web-3ef00244d4da39cb84d1",
    }

    ids = [item["candidate_id"] for item in CANDIDATE_ASSIGNMENTS]
    assert len(ids) == len(set(ids)) == 14
    assert set(ids) == expected_ids
    assert all(item["procedure_id"] for item in CANDIDATE_ASSIGNMENTS)
    assert all(item["official_procedure_code"] for item in CANDIDATE_ASSIGNMENTS)
    assert PDF_FORMS["marriage"]["pages"] == [121, 122]


def test_console_report_is_safe_for_legacy_windows_encoding() -> None:
    from scripts.prepare_pending_form_candidates import serialize_report_for_console

    report = {"form_name": "Tờ khai đăng ký khai sinh", "status": "PASS"}
    serialized = serialize_report_for_console(report)

    serialized.encode("cp1252")
    assert "\\u1edd" in serialized
