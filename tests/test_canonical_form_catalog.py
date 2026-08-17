from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from api.legal_form_catalog import (
    FormCatalog,
    _verified_legacy_attestation_pairs,
    classify_requirement,
    normalize_procedure_id,
)
from scripts.build_canonical_form_catalog import build_catalogs

ROOT = Path(__file__).resolve().parents[1]
RAW_REQUIREMENTS = ROOT / "notebook_data" / "forms" / "priority_200_forms.json"


def test_f0_classifies_all_202_requirements_without_runtime_synthetic_ids(tmp_path):
    artifacts = build_catalogs(
        raw_requirements_path=RAW_REQUIREMENTS,
        output_dir=tmp_path / "forms",
        report_dir=tmp_path / "reports",
        write=False,
    )

    requirements = artifacts["requirements"]["requirements"]
    procedures = artifacts["procedures"]["procedures"]

    assert len(requirements) == 202
    assert len(procedures) == 52
    assert all(item["classification"] != "UNKNOWN" for item in requirements)
    assert sum(item["classification"] == "SYNTHETIC_PLACEHOLDER" for item in requirements) == 90
    assert all("bo_sung_" not in item["procedure_id"] for item in procedures)
    assert all(item["source_status"] in {"verified", "gap"} for item in procedures)


def test_f0_distinguishes_forms_supporting_documents_results_and_internal_forms():
    assert (
        classify_requirement("Giấy chứng sinh", "dang_ky_khai_sinh")
        == "SUPPORTING_DOCUMENT_NOT_TEMPLATE"
    )
    assert (
        classify_requirement(
            "Giấy xác nhận tình trạng hôn nhân",
            "dang_ky_ket_hon",
        )
        == "OFFICIAL_RESULT_NOT_TEMPLATE"
    )
    assert (
        classify_requirement(
            "Biên bản vi phạm trật tự xây dựng",
            "xu_phat_xay_dung_khong_phep",
        )
        == "OFFICER_INTERNAL_FORM"
    )
    assert (
        classify_requirement(
            "Tờ khai đăng ký kết hôn (mẫu điện tử)",
            "dang_ky_ket_hon",
        )
        == "ONLINE_EFORM"
    )
    assert (
        classify_requirement("Tờ khai đăng ký khai sinh", "dang_ky_khai_sinh")
        == "REAL_PROCEDURE_FORM_REQUIREMENT"
    )


def test_procedure_aliases_are_canonical_and_deterministic():
    assert normalize_procedure_id("xac_nhan_doc_than") == "xac_nhan_tinh_trang_hon_nhan"
    assert normalize_procedure_id("khieu_nai") == "khieu_nai_hanh_chinh"
    assert normalize_procedure_id("tro_cap_bao_tro_xa_hoi") == "tro_cap_xa_hoi"
    assert normalize_procedure_id("dang_ky_khai_sinh") == "dang_ky_khai_sinh"


def test_default_building_form_uses_current_decree_217_appendix():
    catalog = FormCatalog.load_default()
    resolved = catalog.resolve_forms(
        "Xin giấy phép xây dựng nhà ở riêng lẻ, cho tôi biểu mẫu đơn đề nghị chính thức",
        role="citizen",
        as_of=date(2026, 8, 8),
        limit=12,
    )

    form = next(
        item
        for item in resolved["recommended_forms"]
        if item["procedure_id"] == "cap_giay_phep_xay_dung"
    )
    assert form["download_url"] == (
        "https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/6/pl217.pdf"
    )
    assert form["source_url"] == (
        "https://vanban.chinhphu.vn/?docid=218509&orggroupid=2&pageid=27160"
    )
    assert form["legal_basis"] == ["217/2026/NĐ-CP"]
    assert form["effective_from"] == "2026-07-01"


def test_resolver_matches_accent_alias_and_form_code_without_model(tmp_path):
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "sang_ten_so_do",
                "name": "Đăng ký biến động đất đai",
                "aliases": ["sang tên sổ đỏ", "đăng ký biến động"],
                "domain": "dat_dai_xay_dung",
                "review_status": "approved",
            }
        ],
        forms=[
            {
                "form_id": "mau-09-dk",
                "procedure_ids": ["sang_ten_so_do"],
                "form_code": "09/ĐK",
                "canonical_name": "Đơn đăng ký biến động đất đai",
                "aliases": ["Mẫu 09/ĐK"],
                "audience": "citizen",
                "usage": "applicant_form",
                "required_or_conditional": "required",
                "domain": "dat_dai_xay_dung",
                "jurisdiction": "national",
                "official_source_page": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html",
                "official_download_url": "https://dichvucong.gov.vn/files/mau-09-dk.pdf",
                "local_path": None,
                "file_format": "pdf",
                "sha256": None,
                "effective_from": "2025-01-01",
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
        ],
        bindings=[
            {
                "procedure_id": "sang_ten_so_do",
                "form_id": "mau-09-dk",
                "binding_status": "approved",
            }
        ],
        project_root=tmp_path,
    )

    assert catalog.resolve_procedures("Cho tôi mẫu 09/ĐK")["matches"][0]["procedure_id"] == "sang_ten_so_do"
    assert catalog.resolve_procedures("can mau sang ten so do")["matches"][0]["procedure_id"] == "sang_ten_so_do"
    forms = catalog.resolve_forms(
        "Cho tôi tải Mẫu 09/ĐK",
        role="citizen",
        as_of=date(2026, 7, 24),
    )
    assert [item["form_id"] for item in forms["recommended_forms"]] == ["mau-09-dk"]


def test_resolver_ignores_contextual_short_alias_but_keeps_explicit_multi_intent(tmp_path):
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Đăng ký khai sinh",
                "aliases": ["khai sinh"],
                "domain": "ho_tich_chung_thuc",
            },
            {
                "procedure_id": "dang_ky_tam_tru",
                "name": "Đăng ký tạm trú",
                "aliases": ["tạm trú"],
                "domain": "cu_tru_an_ninh",
            },
        ],
        forms=[],
        bindings=[],
        project_root=tmp_path,
    )

    contextual = catalog.resolve_procedures(
        "Dữ liệu khai sinh của cháu đã có; tôi cần đăng ký tạm trú."
    )
    assert [item["procedure_id"] for item in contextual["matches"]] == [
        "dang_ky_tam_tru"
    ]

    explicit = catalog.resolve_procedures(
        "Tôi cần đăng ký khai sinh và đăng ký tạm trú; cho tôi các biểu mẫu."
    )
    assert {item["procedure_id"] for item in explicit["matches"]} == {
        "dang_ky_khai_sinh",
        "dang_ky_tam_tru",
    }


def test_resolver_does_not_treat_negated_shared_form_code_as_intent(tmp_path):
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "dang_ky_tam_tru",
                "name": "Đăng ký tạm trú",
                "aliases": ["tạm trú"],
                "domain": "cu_tru_an_ninh",
            },
            {
                "procedure_id": "dang_ky_thuong_tru",
                "name": "Đăng ký thường trú",
                "aliases": ["thường trú"],
                "domain": "cu_tru_an_ninh",
            },
        ],
        forms=[
            {
                "form_id": "shared-ct01",
                "form_code": "CT01",
                "procedure_ids": ["dang_ky_tam_tru", "dang_ky_thuong_tru"],
            }
        ],
        bindings=[],
        project_root=tmp_path,
    )

    result = catalog.resolve_procedures(
        "Tôi cần đăng ký tạm trú. Không được mặc định sử dụng CT01 nếu chưa xác minh."
    )

    assert [item["procedure_id"] for item in result["matches"]] == [
        "dang_ky_tam_tru"
    ]


def test_runtime_catalog_bridges_official_numeric_procedure_to_form_19():
    catalog = FormCatalog.load_default()
    question = (
        "Cơ sở tại Hải Phòng đã có giấy chứng nhận đủ điều kiện xét nghiệm "
        "khẳng định HIV dương tính nhưng giấy bị mất, nay muốn xin cấp lại "
        "thì cần thủ tục và biểu mẫu nào, tải ở đâu?"
    )

    resolution = catalog.resolve_procedures(question)
    assert resolution["matches"]
    assert resolution["matches"][0]["procedure_id"] == "1.013873"

    forms = catalog.resolve_forms(
        question,
        role="citizen",
        as_of=date(2026, 7, 30),
        procedure_ids=["1.013873"],
    )
    assert [item["form_code"] for item in forms["recommended_forms"]] == ["19"]
    assert forms["recommended_forms"][0]["source_url"].startswith("https://vbpl.vn/")
    assert forms["recommended_forms"][0]["download_url"]


def test_explicit_official_procedure_code_beats_repeated_form_code():
    catalog = FormCatalog.load_default()
    question = (
        "Thủ tục mã 1.002693 cần đúng Mẫu số 01 nào, "
        "không gợi ý biểu mẫu của thủ tục khác?"
    )

    resolution = catalog.resolve_procedures(question)
    forms = catalog.resolve_forms(
        question,
        role="citizen",
        as_of=date(2026, 7, 30),
    )

    assert resolution["matches"][0]["procedure_id"] == "1.002693"
    assert [item["procedure_id"] for item in forms["recommended_forms"]] == [
        "1.002693"
    ]
    assert [item["form_code"] for item in forms["recommended_forms"]] == ["01"]


def test_named_social_pension_procedure_beats_unrelated_form_01():
    catalog = FormCatalog.load_default()
    question = (
        "Toi muon huong tro cap huu tri xa hoi va can Mau so 01 de nop ho so."
    )

    resolution = catalog.resolve_procedures(question)
    forms = catalog.resolve_forms(
        question,
        role="citizen",
        as_of=date(2026, 8, 8),
    )

    assert resolution["matches"][0]["procedure_id"] == "1.014027"
    assert [item["form_id"] for item in forms["recommended_forms"]] == [
        "form-three-tier-516b5cf2b4a912e9dc227076"
    ]
    assert all(
        item["procedure_id"] != "1.002693"
        for item in forms["recommended_forms"]
    )


def test_generic_numeric_form_code_without_procedure_is_fail_closed():
    catalog = FormCatalog.load_default()
    question = "Cho toi tai Mau so 01."

    resolution = catalog.resolve_procedures(question)
    forms = catalog.resolve_forms(
        question,
        role="citizen",
        as_of=date(2026, 8, 8),
    )

    assert resolution["ambiguous"] is True
    assert forms["recommended_forms"] == []


def test_named_temporary_residence_procedure_beats_shared_ct01_code():
    catalog = FormCatalog.load_default()
    question = "Toi can dang ky tam tru bang Mau CT01."

    resolution = catalog.resolve_procedures(question)
    forms = catalog.resolve_forms(
        question,
        role="citizen",
        as_of=date(2026, 8, 8),
    )

    assert resolution["matches"][0]["procedure_id"] == "1.004194"
    assert [item["form_id"] for item in forms["recommended_forms"]] == [
        "form-three-tier-8b3269ed3ec4e70eebf33b84"
    ]


def test_bare_date_number_never_matches_a_numeric_form_code():
    catalog = FormCatalog.load_default()

    result = catalog.resolve_procedures(
        "Tại ngày 30/07/2026, cho tôi biết hồ sơ và biểu mẫu phù hợp."
    )

    assert all(
        item.get("match_type") != "form_code"
        for item in result["matches"]
    )


def test_pending_form_code_cannot_influence_procedure_resolution(tmp_path):
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "pending-procedure",
                "name": "Thủ tục thử nghiệm",
                "domain": "an_sinh_y_te_giao_duc",
            }
        ],
        forms=[
            {
                "form_id": "pending-form",
                "form_code": "X99",
                "procedure_ids": ["pending-procedure"],
                "review_status": "pending",
                "approved": False,
                "runtime_eligible": False,
            }
        ],
        bindings=[
            {
                "procedure_id": "pending-procedure",
                "form_id": "pending-form",
                "binding_status": "pending",
                "review_status": "pending",
                "approved": False,
            }
        ],
        project_root=tmp_path,
    )

    assert catalog.resolve_procedures("Cho tôi mẫu X99")["matches"] == []


def test_hard_gate_blocks_pending_expired_officer_and_broken_forms(tmp_path):
    valid_pdf = tmp_path / "valid.pdf"
    valid_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 512)
    common = {
        "procedure_ids": ["dang_ky_khai_sinh"],
        "form_code": None,
        "aliases": [],
        "required_or_conditional": "required",
        "domain": "ho_tich_chung_thuc",
        "jurisdiction": "national",
        "official_source_page": "https://dichvucong.gov.vn/p/home/procedure",
        "official_download_url": None,
        "local_path": str(valid_pdf.relative_to(tmp_path)),
        "file_format": "pdf",
        "sha256": hashlib.sha256(valid_pdf.read_bytes()).hexdigest(),
        "effective_from": "2020-01-01",
        "effective_to": None,
        "supersedes_form_id": None,
        "source_classification": "official_file",
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "legal_basis": ["Nghị định hiện hành"],
        "provenance": {"source_url": "https://dichvucong.gov.vn/p/home/procedure"},
    }
    forms = [
        {
            **common,
            "form_id": "citizen-current",
            "canonical_name": "Tờ khai đăng ký khai sinh",
            "audience": "citizen",
            "usage": "applicant_form",
        },
        {
            **common,
            "form_id": "officer-only",
            "canonical_name": "Sổ đăng ký khai sinh",
            "audience": "officer",
            "usage": "officer_internal",
        },
        {
            **common,
            "form_id": "expired",
            "canonical_name": "Tờ khai khai sinh cũ",
            "audience": "citizen",
            "usage": "applicant_form",
            "effective_to": "2024-12-31",
        },
        {
            **common,
            "form_id": "pending",
            "canonical_name": "Tờ khai chưa duyệt",
            "audience": "citizen",
            "usage": "applicant_form",
            "review_status": "candidate_pending_review",
        },
        {
            **common,
            "form_id": "broken",
            "canonical_name": "Tờ khai mất file",
            "audience": "citizen",
            "usage": "applicant_form",
            "local_path": "missing.pdf",
        },
    ]
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Đăng ký khai sinh",
                "aliases": ["khai sinh"],
                "domain": "ho_tich_chung_thuc",
                "review_status": "approved",
            }
        ],
        forms=forms,
        bindings=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "form_id": row["form_id"],
                "binding_status": "approved",
            }
            for row in forms
        ],
        project_root=tmp_path,
    )

    citizen = catalog.resolve_forms(
        "Tải tờ khai đăng ký khai sinh",
        role="citizen",
        as_of=date(2026, 7, 24),
    )
    assert [item["form_id"] for item in citizen["recommended_forms"]] == [
        "citizen-current"
    ]
    assert {item["reason_code"] for item in citizen["rejected_forms"]} >= {
        "ROLE_NOT_ALLOWED",
        "FORM_EXPIRED",
        "FORM_NOT_APPROVED",
        "FORM_FILE_INVALID",
    }

    officer = catalog.resolve_forms(
        "Tải tờ khai đăng ký khai sinh",
        role="officer",
        as_of=date(2026, 7, 24),
    )
    assert {item["form_id"] for item in officer["recommended_forms"]} == {
        "citizen-current",
        "officer-only",
    }


def test_specific_reissue_procedure_does_not_fall_back_to_base_permit_form():
    catalog = FormCatalog.load_default()

    result = catalog.resolve_forms(
        "Cấp lại/điều chỉnh giấy phép xây dựng",
        role="citizen",
        as_of=date(2026, 7, 24),
    )

    assert all(
        item["procedure_id"] == "cap_lai_giay_phep_xay_dung"
        for item in result["recommended_forms"]
    )
    assert not any(
        item["procedure_id"] == "cap_giay_phep_xay_dung"
        for item in result["recommended_forms"]
    )


def test_approved_form_without_runtime_eligibility_is_fail_closed(tmp_path):
    valid_pdf = tmp_path / "valid.pdf"
    valid_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 512)
    catalog = FormCatalog(
        procedures=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Đăng ký khai sinh",
                "aliases": ["dang ky khai sinh"],
                "domain": "ho_tich_chung_thuc",
                "review_status": "approved",
            }
        ],
        forms=[
            {
                "form_id": "approved-not-runtime",
                "procedure_ids": ["dang_ky_khai_sinh"],
                "canonical_name": "Tờ khai đăng ký khai sinh",
                "audience": "citizen",
                "usage": "applicant_form",
                "required_or_conditional": "required",
                "domain": "ho_tich_chung_thuc",
                "jurisdiction": "national",
                "official_source_page": "https://dichvucong.gov.vn/p/home/procedure",
                "official_download_url": None,
                "local_path": "valid.pdf",
                "file_format": "pdf",
                "sha256": hashlib.sha256(valid_pdf.read_bytes()).hexdigest(),
                "effective_from": "2020-01-01",
                "effective_to": None,
                "supersedes_form_id": None,
                "source_classification": "official_file",
                "review_status": "approved",
                "approved": True,
                "runtime_eligible": False,
                "legal_basis": ["Nghị định hiện hành"],
                "provenance": {
                    "source_url": "https://dichvucong.gov.vn/p/home/procedure"
                },
            }
        ],
        bindings=[
            {
                "procedure_id": "dang_ky_khai_sinh",
                "form_id": "approved-not-runtime",
                "binding_status": "approved",
            }
        ],
        project_root=tmp_path,
    )

    result = catalog.resolve_forms(
        "Tải tờ khai đăng ký khai sinh",
        role="citizen",
        as_of=date(2026, 7, 24),
        procedure_ids=["dang_ky_khai_sinh"],
    )

    assert result["recommended_forms"] == []
    assert "FORM_NOT_RUNTIME_ELIGIBLE" in result["data_gap_reasons"]


def test_legacy_runtime_bridge_requires_complete_matching_attestation():
    form = {
        "form_id": "legacy-form",
        "procedure_ids": ["legacy-procedure"],
        "sha256": "abc123",
        "review_status": "approved",
        "legal_review_status": "approved",
        "approved": True,
        "provenance": {
            "candidate_id": "candidate-1",
            "attestation_id": "attestation-1",
            "reviewer_id": "reviewer-1",
            "reviewed_at": "2026-07-27T04:00:52Z",
        },
    }
    binding = {
        "procedure_id": "legacy-procedure",
        "form_id": "legacy-form",
        "binding_status": "approved",
        "review_status": "approved",
        "approved": True,
        "attestation_id": "attestation-1",
        "reviewer_id": "reviewer-1",
        "reviewed_at": "2026-07-27T04:00:52Z",
    }
    payload = {
        "attestations": [
            {
                "attestation_id": "attestation-1",
                "reviewer_id": "reviewer-1",
                "reviewed_at": "2026-07-27T04:00:52Z",
                "decision": "approved",
                "items": [
                    {
                        "candidate_id": "candidate-1",
                        "canonical_form_id": "legacy-form",
                        "procedure_id": "legacy-procedure",
                        "sha256": "abc123",
                        "decision": "approved",
                    }
                ],
            }
        ]
    }

    assert _verified_legacy_attestation_pairs(
        forms=[form], bindings=[binding], attestations_payload=payload
    ) == {("legacy-procedure", "legacy-form")}

    mismatched = json.loads(json.dumps(payload))
    mismatched["attestations"][0]["items"][0]["sha256"] = "tampered"
    assert _verified_legacy_attestation_pairs(
        forms=[form], bindings=[binding], attestations_payload=mismatched
    ) == set()

    explicitly_disabled = {**form, "runtime_eligible": False}
    assert _verified_legacy_attestation_pairs(
        forms=[explicitly_disabled],
        bindings=[binding],
        attestations_payload=payload,
    ) == set()


def test_default_catalog_releases_existing_checksum_attested_priority_forms():
    catalog = FormCatalog.load_default()
    cases = {
        "Toi can xin Giay xac nhan tinh trang hon nhan va tai to khai.": (
            "xac_nhan_tinh_trang_hon_nhan",
            "form-25abac4752b4690f8f08",
        ),
        "Toi xin cap giay phep xay dung nha o rieng le va can don de nghi.": (
            "cap_giay_phep_xay_dung",
            "form-82e0f920688d5a6a5da0",
        ),
        "Toi can khieu nai hanh chinh va tai don khieu nai.": (
            "khieu_nai_hanh_chinh",
            "form-2fd149a36c5485525ab7",
        ),
    }

    for question, (procedure_id, form_id) in cases.items():
        forms = catalog.resolve_forms(
            question,
            role="citizen",
            as_of=date(2026, 8, 8),
        )
        assert forms["procedure_matches"][0]["procedure_id"] == procedure_id
        assert [item["form_id"] for item in forms["recommended_forms"]] == [
            form_id
        ]

    officer_wording = catalog.resolve_forms(
        "Khi tiep nhan thu tuc cap Giay xac nhan tinh trang hon nhan, "
        "can dung bieu mau chinh thuc nao?",
        role="officer",
        as_of=date(2026, 8, 8),
    )
    assert officer_wording["procedure_matches"][0]["procedure_id"] == (
        "xac_nhan_tinh_trang_hon_nhan"
    )
    assert [item["form_id"] for item in officer_wording["recommended_forms"]] == [
        "form-25abac4752b4690f8f08"
    ]


def test_complaint_form_uses_reviewed_current_decree_124_package():
    catalog = FormCatalog.load_default()
    result = catalog.resolve_forms(
        "Tôi cần khiếu nại hành chính và tải đơn khiếu nại.",
        role="citizen",
        as_of=date(2026, 8, 9),
    )

    form = result["recommended_forms"][0]
    assert form["form_id"] == "form-2fd149a36c5485525ab7"
    assert form["form_code"] == "01"
    assert form["download_url"].endswith("/124.signed.pdf")
    assert "vanban.chinhphu.vn" in form["source_url"]
    assert form["legal_basis"] == ["02/2011/QH13", "124/2020/NĐ-CP"]


def test_generated_artifacts_are_valid_json_and_contain_no_mojibake(tmp_path):
    artifacts = build_catalogs(
        raw_requirements_path=RAW_REQUIREMENTS,
        output_dir=tmp_path / "forms",
        report_dir=tmp_path / "reports",
        write=True,
    )

    for path in artifacts["written_paths"]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "Ã" not in json.dumps(payload, ensure_ascii=False)
        assert "Ä‘" not in json.dumps(payload, ensure_ascii=False)
