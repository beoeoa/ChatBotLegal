from __future__ import annotations

from api.three_tier_form_inventory import (
    build_privacy_safe_summary,
    canonicalize_form_candidates,
    classify_component_kind,
    classify_domain,
    classify_source_tier,
    evaluate_candidate,
    extract_form_code,
    infer_executing_level,
)
from scripts.build_three_tier_form_inventory import select_scoped_procedures


def _candidate(**overrides):
    value = {
        "candidate_id": "central-a",
        "procedure_id": "1.001193",
        "procedure_code": "1.001193",
        "procedure_name": "Thủ tục đăng ký khai sinh",
        "domain": "ho_tich_chung_thuc",
        "source_tier": "central",
        "form_name": "Tờ khai đăng ký khai sinh theo Mẫu số 01",
        "form_code": "Mẫu số 01",
        "component_kind": "applicant_form",
        "official_source_page": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/a",
        "official_download_url": "https://dichvucong.gov.vn/files/a.pdf",
        "local_path": "data/uploads/three_tier_form_candidates/a.pdf",
        "sha256": "a" * 64,
        "issuing_instruments": ["04/2020/TT-BTP"],
        "effective_from": "2020-07-16",
        "effective_to": None,
        "portal_state": "UPDATED",
        "review_status": "candidate_pending_review",
        "approved": False,
        "is_seed": False,
        "is_demo": False,
        "is_quarantined": False,
        "provenance": [{"publisher": "Bộ Tư pháp"}],
    }
    value.update(overrides)
    return value


def test_classifies_central_hai_phong_override_and_le_chan_local():
    assert (
        classify_source_tier(
            formality_type="STANDARD",
            publisher="Bộ Tư pháp",
            executing_level="commune",
        )
        == "central"
    )
    assert (
        classify_source_tier(
            formality_type="SPECIFIC",
            publisher="Ủy ban nhân dân thành phố Hải Phòng",
            executing_level="province",
        )
        == "hai_phong_override"
    )
    assert (
        classify_source_tier(
            formality_type="SPECIFIC",
            publisher="Ủy ban nhân dân phường Lê Chân",
            executing_level="commune",
        )
        == "le_chan_local"
    )


def test_same_checksum_across_three_tiers_collapses_to_central_with_mirrors():
    records = [
        _candidate(),
        _candidate(
            candidate_id="hp-a",
            source_tier="hai_phong_override",
            official_source_page="https://haiphong.gov.vn/a",
        ),
        _candidate(
            candidate_id="lc-a",
            source_tier="le_chan_local",
            official_source_page="https://lechan.haiphong.gov.vn/a",
        ),
    ]

    result = canonicalize_form_candidates(records, legal_as_of="2026-07-27")

    assert len(result["canonical_forms"]) == 1
    assert result["canonical_forms"][0]["source_tier"] == "central"
    assert len(result["canonical_forms"][0]["source_mirrors"]) == 2
    assert result["summary"]["duplicates_collapsed"] == 2


def test_different_hai_phong_instrument_is_kept_as_override():
    result = canonicalize_form_candidates(
        [
            _candidate(),
            _candidate(
                candidate_id="hp-b",
                source_tier="hai_phong_override",
                form_code="Mẫu HP-01",
                sha256="b" * 64,
                issuing_instruments=["12/2026/QĐ-UBND"],
                official_source_page="https://haiphong.gov.vn/b",
                official_download_url="https://haiphong.gov.vn/files/b.pdf",
                local_path="data/uploads/three_tier_form_candidates/b.pdf",
            ),
        ],
        legal_as_of="2026-07-27",
    )

    assert len(result["canonical_forms"]) == 2
    assert {item["source_tier"] for item in result["canonical_forms"]} == {
        "central",
        "hai_phong_override",
    }


def test_same_package_checksum_does_not_merge_distinct_forms():
    result = canonicalize_form_candidates(
        [
            _candidate(),
            _candidate(
                candidate_id="central-b",
                procedure_id="2.000001",
                form_name="Đơn đề nghị trợ giúp xã hội",
                form_code="Mẫu số 02",
            ),
        ],
        legal_as_of="2026-07-27",
    )

    assert len(result["canonical_forms"]) == 2
    assert result["summary"]["duplicates_collapsed"] == 0


def test_excludes_result_support_seed_and_expired_records():
    records = [
        _candidate(candidate_id="result", component_kind="official_result"),
        _candidate(candidate_id="support", component_kind="supporting_document"),
        _candidate(candidate_id="seed", is_seed=True),
        _candidate(candidate_id="expired", effective_to="2025-12-31"),
        _candidate(candidate_id="superseded", portal_state="SUPERSEDED"),
    ]

    result = canonicalize_form_candidates(records, legal_as_of="2026-07-27")

    assert result["canonical_forms"] == []
    assert result["summary"]["excluded_count"] == 5
    assert set(result["summary"]["exclusion_reason_counts"]) == {
        "OFFICIAL_RESULT_NOT_TEMPLATE",
        "SUPPORTING_DOCUMENT_NOT_TEMPLATE",
        "SEED_OR_DEMO_BLOCKED",
        "FORM_EXPIRED",
        "FORM_SUPERSEDED",
    }


def test_missing_procedure_file_or_effectivity_stays_out_of_review_shortlist():
    records = [
        _candidate(candidate_id="no-procedure", procedure_id=None),
        _candidate(
            candidate_id="no-file",
            official_download_url=None,
            local_path=None,
            sha256=None,
        ),
        _candidate(candidate_id="no-effectivity", effective_from=None),
    ]

    result = canonicalize_form_candidates(records, legal_as_of="2026-07-27")

    assert result["review_shortlist"] == []
    assert result["summary"]["verified_data_gap_count"] == 3
    assert {
        item["reason_code"] for item in result["verified_data_gaps"]
    } == {
        "MISSING_PROCEDURE_ID",
        "MISSING_OFFICIAL_FILE",
        "MISSING_EFFECTIVITY",
    }


def test_candidate_is_never_auto_approved_and_requires_all_hard_gates():
    decision = evaluate_candidate(_candidate(), legal_as_of="2026-07-27")

    assert decision["disposition"] == "READY_FOR_HUMAN_ATTESTATION"
    assert decision["approved"] is False
    assert decision["runtime_eligible"] is False


def test_aggregate_report_is_privacy_safe():
    result = canonicalize_form_candidates(
        [_candidate()],
        legal_as_of="2026-07-27",
    )

    report = build_privacy_safe_summary(result, run_id="run-1")
    serialized = str(report)

    assert report["contains_question_text"] is False
    assert report["contains_answer_text"] is False
    assert report["contains_credentials"] is False
    assert "Tờ khai đăng ký khai sinh" not in serialized
    assert "https://dichvucong.gov.vn/files/a.pdf" not in serialized


def test_official_catalog_categories_map_only_to_the_five_system_domains():
    assert classify_domain(["Hộ tịch"]) == "ho_tich_chung_thuc"
    assert classify_domain(["Đăng ký, quản lý cư trú"]) == "cu_tru_an_ninh"
    assert classify_domain(["Hoạt động xây dựng"]) == "dat_dai_xay_dung"
    assert classify_domain(["Khiếu nại, tố cáo"]) == "khieu_nai_to_cao_xu_phat"
    assert classify_domain(["Bảo trợ xã hội"]) == "an_sinh_y_te_giao_duc"
    assert classify_domain(["Hải quan"]) is None


def test_execution_level_and_form_component_are_deterministic():
    assert infer_executing_level(["Ủy ban nhân dân cấp xã"]) == "commune"
    assert infer_executing_level(["Sở Tư pháp"]) == "province"
    assert infer_executing_level(["Bộ Tư pháp"]) == "central"

    assert (
        classify_component_kind(
            {"name": "Tờ khai đăng ký khai sinh theo Mẫu số 01"}
        )
        == "applicant_form"
    )
    assert (
        classify_component_kind(
            {"name": "Giấy chứng sinh", "isProcessingResult": False}
        )
        == "supporting_document"
    )
    assert (
        classify_component_kind(
            {"name": "Giấy khai sinh", "isProcessingResult": True}
        )
        == "official_result"
    )
    assert (
        extract_form_code("Tờ khai đăng ký khai sinh theo Mẫu số 01")
        == "Mẫu số 01"
    )


def test_form_component_classifier_excludes_dossier_evidence_false_positives():
    for name in (
        "Mẫu nhãn sản phẩm",
        "Ảnh chân dung theo mẫu",
        "Bản sao Giấy chứng nhận đã cấp theo mẫu",
        "Giấy chứng nhận quyền sử dụng đất theo mẫu",
        "Bản đồ hiện trạng theo mẫu",
        "Danh sách, hồ sơ trang thiết bị (phù hợp báo cáo theo mẫu)",
    ):
        assert classify_component_kind({"name": name}) == "supporting_document"

    for name in (
        "Đơn đề nghị cấp lại Giấy chứng nhận theo Mẫu số 01",
        "Phiếu khai báo tạm trú (mẫu NA17)",
        "Biểu mẫu điện tử tương tác đăng ký kết hôn",
        "Mẫu hộ tịch điện tử tương tác đăng ký khai sinh",
    ):
        assert classify_component_kind({"name": name}) == "applicant_form"

    for name in (
        "Đơn đề nghị cấp lại giấy phép",
        "Văn bản đề nghị giải quyết thủ tục",
        "Danh sách người lao động",
    ):
        assert classify_component_kind({"name": name}) == "applicant_submission"

    assert (
        classify_component_kind(
            {"name": "Phiếu lý lịch tư pháp", "hasElectronicForm": True}
        )
        == "supporting_document"
    )
    assert (
        classify_component_kind(
            {"name": "Giấy chứng sinh", "hasElectronicForm": True}
        )
        == "supporting_document"
    )
    assert (
        classify_component_kind(
            {
                "name": (
                    "Lưu ý: người yêu cầu khai báo thông tin theo biểu mẫu điện tử "
                    "được cung cấp sẵn"
                )
            }
        )
        == "supporting_document"
    )


def test_catalog_scope_keeps_standard_and_hai_phong_but_not_other_province():
    common = {
        "state": "UPDATED",
        "categories": ["Hộ tịch"],
        "departments": ["Ủy ban nhân dân cấp xã"],
    }
    selected, exclusions = select_scoped_procedures(
        [
            {
                **common,
                "id": "central",
                "type": "STANDARD",
                "departmentPromulgate": "Bộ Tư pháp",
            },
            {
                **common,
                "id": "hai-phong",
                "type": "SPECIFIC",
                "departmentPromulgate": "UBND Thành phố Hải Phòng",
            },
            {
                **common,
                "id": "other",
                "type": "SPECIFIC",
                "departmentPromulgate": "UBND Thành phố Hồ Chí Minh",
            },
        ]
    )

    assert [item["id"] for item in selected] == ["central", "hai-phong"]
    assert exclusions["OTHER_PROVINCE_OR_UNVERIFIED_TIER"] == 1
