from __future__ import annotations

from scripts.benchmark_db5_shadow import (
    _classify_issue,
    _expected_source_observations,
    _ratio_gate,
    _source_identity_gap,
)
from scripts.legal_search_server import (
    LegalRetriever,
    _apply_law_domain_override,
    _domain_matches,
    _exact_override_scope_join,
    _reviewed_domain_override_laws,
    _rewrite_query,
    _select_exact_lookup_plan,
)
from api.legal_exact_retrieval import plan_exact_lookup


def test_step1_classifies_available_expected_source_in_top_10_as_retrieved():
    expected = [
        {
            "kind": "document",
            "document_id": 37879,
            "law_number": "60/2014/QH13",
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
            "checks": {
                "effectivity": True,
                "scope": True,
                "hierarchy": True,
                "tier": "primary",
            },
        }
    ]
    rows = [
        {
            "document_id": 999,
            "article_number": "1",
            "law_number": "unrelated",
        },
        {
            "document_id": 37879,
            "article_number": "13",
            "law_number": "60/2014/QH13",
        },
    ]

    observations = _expected_source_observations(expected, rows)

    assert observations[0]["classification"] == "FOUND_AND_RETRIEVED"
    assert observations[0]["rank"] == 2
    assert observations[0]["in_top_5"] is True
    assert observations[0]["in_top_10"] is True
    assert observations[0]["selection_reason"] == "expected_source_ranked_top_10"
    assert _classify_issue(observations, has_live_results=True) == (
        "FOUND_AND_RETRIEVED"
    )


def test_step1_classifies_available_provision_outside_top_10_as_retrieval_error():
    expected = [
        {
            "kind": "provision",
            "document_id": 37879,
            "provision": "16",
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
            "document_checks": {
                "effectivity": True,
                "scope": True,
                "hierarchy": True,
                "tier": "primary",
            },
        }
    ]
    rows = [
        {
            "document_id": 37879,
            "article_number": str(index),
            "law_number": "60/2014/QH13",
        }
        for index in range(1, 11)
    ]

    observations = _expected_source_observations(expected, rows)

    assert observations[0]["classification"] == "FOUND_NOT_RETRIEVED"
    assert observations[0]["rank"] is None
    assert observations[0]["exclusion_reason"] == (
        "source_exists_but_provision_not_ranked_top_10"
    )
    assert _classify_issue(observations, has_live_results=True) == (
        "FOUND_NOT_RETRIEVED"
    )


def test_step1_keeps_verified_data_gap_distinct_from_retrieval_error():
    expected = [
        {
            "kind": "document",
            "document_id": None,
            "expected_law_number": "68/2020/QH14",
            "outcome": "VERIFIED_DATA_GAP",
            "reason_code": "expected_document_not_in_corpus",
            "corpus_match_count": 0,
        }
    ]

    observations = _expected_source_observations(expected, [])

    assert observations[0]["classification"] == "VERIFIED_DATA_GAP"
    assert observations[0]["source_exists_in_corpus"] is False
    assert observations[0]["exclusion_reason"] == (
        "expected_document_not_in_corpus"
    )
    assert _classify_issue(observations, has_live_results=False) == (
        "VERIFIED_DATA_GAP"
    )


def test_step1_gap_only_slice_does_not_fail_ratio_gate():
    assert _ratio_gate(0.0, 0, 0.95) is True
    assert _ratio_gate(0.94, 1, 0.95) is False
    assert _ratio_gate(0.95, 1, 0.95) is True


def test_step1_does_not_invent_expected_source_for_uncontracted_issue():
    observations = _expected_source_observations([], [{"document_id": 1}])

    assert observations == []
    assert _classify_issue(observations, has_live_results=True) == (
        "FOUND_AND_RETRIEVED"
    )


def test_step1_rejects_law_number_collision_with_wrong_legal_subject():
    gap = _source_identity_gap(
        case_id="golden_urban_003",
        document_id=55930,
    )

    assert gap is not None
    assert gap["reason_code"] == "law_number_collision_wrong_legal_subject"
    assert gap["correct_source_match_count"] == 0


def test_composite_urban_and_administrative_domains_remain_strict_but_complete():
    assert _domain_matches(
        "noi_vu_hanh_chinh",
        "khieu_nai_to_cao_xu_phat",
    )
    assert _domain_matches("trat_tu_do_thi", "trat_tu_do_thi")
    assert _domain_matches("trat_tu_do_thi", "xay_dung_do_thi")
    assert _domain_matches(
        "trat_tu_do_thi",
        "khieu_nai_to_cao_xu_phat",
    )
    assert not _domain_matches("trat_tu_do_thi", "cu_tru_an_ninh")
    assert not _domain_matches("trat_tu_do_thi", "lao_dong")


def test_social_services_domain_accepts_exact_and_reviewed_child_scopes():
    assert _domain_matches(
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te_giao_duc",
    )
    assert _domain_matches("an_sinh_y_te_giao_duc", "an_sinh_y_te")
    assert _domain_matches("an_sinh_y_te_giao_duc", "giao_duc_van_hoa")
    assert _domain_matches("an_sinh_y_te_giao_duc", "lao_dong")
    assert _domain_matches("lao_dong", "an_sinh_y_te_giao_duc")
    assert not _domain_matches("an_sinh_y_te_giao_duc", "cu_tru_an_ninh")


def test_domain_override_identifiers_are_only_bound_for_reviewed_domains():
    assert "43/2025/NQ-HDND" in _reviewed_domain_override_laws(
        "ho_tich_chung_thuc"
    )
    assert "15/2012/QH13" in _reviewed_domain_override_laws(
        "khieu_nai_to_cao_xu_phat"
    )
    assert "16/2022/ND-CP" in _reviewed_domain_override_laws(
        "dat_dai_xay_dung"
    )
    assert "140/2025/ND-CP" in _reviewed_domain_override_laws(
        "dat_dai_xay_dung"
    )
    assert _domain_matches(
        "dat_dai_xay_dung",
        ["khieu_nai_to_cao_xu_phat", "dat_dai_xay_dung"],
    )
    assert _domain_matches(
        "dat_dai_xay_dung",
        "khieu_nai_to_cao_xu_phat,dat_dai_xay_dung",
    )


def test_exact_reviewed_instrument_can_bypass_legacy_excluded_scope_join():
    plan = plan_exact_lookup("Nghị định 16/2022/NĐ-CP về xử phạt xây dựng")

    assert _exact_override_scope_join(plan, "dat_dai_xay_dung") is True
    # A direct exact-law lookup must remain deterministic even when the
    # caller has not supplied a UI domain; it is still restricted by law_number.
    assert _exact_override_scope_join(plan, None) is True
    unrelated = plan_exact_lookup("Luật Hộ tịch 60/2014/QH13")
    assert _exact_override_scope_join(unrelated, "dat_dai_xay_dung") is False


def test_urban_order_exact_lookup_keeps_reviewed_decree_167_in_scope():
    repaired = _apply_law_domain_override(
        {
            "law_number": "167/2013/NĐ-CP",
            "domain_slug": "cu_tru_an_ninh",
            "document_title": (
                "Quy định xử phạt vi phạm hành chính trong lĩnh vực an ninh, "
                "trật tự, an toàn xã hội"
            ),
        }
    )

    assert _domain_matches("trat_tu_do_thi", repaired["domain_slug"])
    assert _domain_matches("cu_tru_an_ninh", repaired["domain_slug"])
    unrelated = _apply_law_domain_override(
        {"law_number": "106/2025/NĐ-CP", "domain_slug": "cu_tru_an_ninh"}
    )
    assert not _domain_matches("trat_tu_do_thi", unrelated["domain_slug"])


def test_two_tier_construction_authority_instrument_keeps_both_reviewed_scopes():
    repaired = _apply_law_domain_override(
        {
            "law_number": "140/2025/NĐ-CP",
            "domain_slug": "hanh_chinh_cong",
            "document_title": "Phân định thẩm quyền của chính quyền địa phương 02 cấp",
        }
    )

    assert _domain_matches("dat_dai_xay_dung", repaired["domain_slug"])
    assert _domain_matches("hanh_chinh_cong", repaired["domain_slug"])


def test_explicit_document_article_route_is_not_widened_by_query_rewrite():
    query = (
        "Luật Xây dựng 50/2014/QH13 Điều 93 điều kiện cấp giấy phép "
        "xây dựng đối với nhà ở riêng lẻ"
    )
    rewritten = _rewrite_query(query)

    assert "175/2024/ND-CP" in rewritten
    plan = _select_exact_lookup_plan(query, rewritten)

    assert plan.law_numbers == ("50/2014/QH13",)
    assert plan.article_numbers == ("93",)


def test_common_step1_topics_add_only_deterministic_exact_legal_identifiers():
    rewritten = _rewrite_query(
        "Bị phạt vi phạm hành chính mà không có biên bản thì có đúng luật không?"
    )

    assert "15/2012/QH13" in rewritten

    certified_copy = _rewrite_query(
        "Tôi cần chứng thực bản sao giấy tờ tại phường thì chuẩn bị gì?"
    )
    assert "23/2015/ND-CP" in certified_copy

    civil_correction = _rewrite_query(
        "Tôi muốn cải chính ngày sinh trên giấy khai sinh thì cần làm gì?"
    )
    assert "60/2014/QH13" in civil_correction
    assert "123/2015/ND-CP" in civil_correction
    assert "118/2021/ND-CP" in rewritten

    marital = _rewrite_query(
        "Xin giấy xác nhận tình trạng hôn nhân thì nộp ở đâu?"
    )
    assert "60/2014/QH13" in marital
    assert all(f"dieu {number}" in marital.casefold() for number in ("21", "22", "23"))

    domestic_birth = _rewrite_query(
        "Tôi muốn đăng ký khai sinh cho con ở phường thì cần chuẩn bị gì?"
    )
    assert "60/2014/QH13" in domestic_birth
    assert all(
        f"dieu {number}" in domestic_birth.casefold()
        for number in ("13", "16")
    )

    foreign_birth = _rewrite_query(
        "Trẻ sinh ở nước ngoài chưa đăng ký khai sinh thì làm ở đâu?"
    )
    assert "123/2015/ND-CP" in foreign_birth
    assert all(
        f"dieu {number}" in foreign_birth.casefold()
        for number in ("13", "35")
    )

    foreign_birth_comparison = _rewrite_query(
        "Trẻ sinh ở nước ngoài thì phân biệt Điều 13 và Điều 35 Luật Hộ tịch"
    )
    assert "60/2014/QH13" in foreign_birth_comparison
    assert "123/2015/ND-CP" in foreign_birth_comparison

    land_transfer = _rewrite_query(
        "Sang tên nhà đất do chuyển nhượng đã có Giấy chứng nhận"
    )
    assert "31/2024/QH15" in land_transfer
    assert "101/2024/ND-CP" in land_transfer

    unlicensed_construction = _rewrite_query(
        "Nhà ở đô thị đang xây không có giấy phép thì xử lý thế nào?"
    )
    assert "15/2012/QH13" in unlicensed_construction
    assert "16/2022/ND-CP" in unlicensed_construction
    assert "50/2014/QH13" in unlicensed_construction

    building_permit = _rewrite_query(
        "Cấp giấy phép xây dựng nhà ở riêng lẻ tại Hải Phòng"
    )
    assert "50/2014/QH13" in building_permit
    assert "175/2024/ND-CP" in building_permit

    complaint_without_record = _rewrite_query(
        "Khiếu nại quyết định xử phạt hành chính không lập biên bản"
    )
    assert "dieu 56" in complaint_without_record.casefold()
    assert "15/2012/QH13" in complaint_without_record

    first_instance_complaint = _rewrite_query(
        "Ai có thẩm quyền giải quyết khiếu nại lần đầu đối với quyết định hành chính?"
    )
    assert "02/2011/QH13" in first_instance_complaint
    assert all(
        f"dieu {number}" in first_instance_complaint.casefold()
        for number in ("17", "18")
    )

    identity_card = _rewrite_query("Làm thẻ CCCD mất bao lâu?")
    assert "26/2023/QH15" in identity_card
    assert "17/2024/TT-BCA" in identity_card

    parking = _rewrite_query(
        "Tại sao tôi bị cấm đỗ xe ở vỉa hè trước nhà?"
    )
    assert "36/2024/QH15" in parking
    assert "168/2024/ND-CP" in parking
    assert "100/2019/ND-CP" not in parking


def test_reviewed_identity_exception_requires_clean_title_and_nonempty_content():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = True

    _, predicate, _ = retriever._quality_sql()

    assert "15/2012/QH13" in predicate
    assert "16/2022/ND-CP" in predicate
    assert "quality.quality_reasons <@" in predicate
    assert "missing_domain" in predicate
    assert "noisy_article_title" in predicate
    assert "cleaned_article_title" in predicate
    assert "BTRIM(c.content)" in predicate


def test_exact_article_quality_allows_only_missing_domain_with_clean_content():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = True

    _, default_predicate, _ = retriever._quality_sql()
    _, exact_predicate, _ = retriever._quality_sql(
        allow_exact_missing_domain=True
    )

    exact_rule = "quality.quality_reasons = ARRAY['missing_domain']::TEXT[]"
    assert exact_rule not in default_predicate
    assert exact_rule in exact_predicate
    assert "cleaned_article_title" in exact_predicate
    assert "BTRIM(c.content)" in exact_predicate
