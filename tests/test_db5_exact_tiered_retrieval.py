from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from api.legal_exact_retrieval import (
    PRIMARY_BUDGET,
    SUPPORT_BUDGET,
    ExactLookupPlan,
    filter_exact_candidates,
    normalize_exact_identifier,
    plan_exact_lookup,
    retrieve_primary_then_support,
)
from api.legal_retrieval_quality import diversify_ranked_candidates
from scripts.legal_search_server import LegalRetriever
from scripts.legal_search_server import _exact_metadata_relevance, _rewrite_query
from api.legal_exact_article import is_single_exact_article_plan


def test_exact_law_number_article_and_clause_are_parsed_without_broad_text_search():
    plan = plan_exact_lookup(
        "Áp dụng khoản 2 Điều 16 Luật số 60/2014/QH13 như thế nào?"
    )

    assert plan.law_number == "60/2014/QH13"
    assert plan.article_number == "16"
    assert plan.clause_number == "2"
    assert plan.requires_exact_metadata_lookup is True


def test_multiple_exact_law_numbers_are_preserved_for_one_multi_source_issue():
    plan = plan_exact_lookup(
        "Áp dụng Luật 02/2011/QH13 và Luật 15/2012/QH13"
    )

    assert plan.law_numbers == ("02/2011/QH13", "15/2012/QH13")
    assert plan.law_number == "02/2011/QH13"


def test_single_article_is_paired_with_adjacent_current_law_after_old_law():
    plan = plan_exact_lookup(
        "Huong dan cu vien dan 45/2013/QH13; Dieu 9 31/2024/QH15 hien quy dinh gi "
        "ve Chuong I > Dieu 9?"
    )

    assert plan.law_numbers == ("45/2013/QH13", "31/2024/QH15")
    assert plan.article_numbers == ("9",)
    assert plan.law_number == "31/2024/QH15"
    assert plan.article_law_pairs == (("31/2024/QH15", "9"),)
    assert is_single_exact_article_plan(plan) is True


def test_adjacent_pair_wins_when_later_text_contains_a_cross_reference():
    plan = plan_exact_lookup(
        "Văn bản cũ 45/2013/QH13; Điều 1 62/2020/QH14 hiện quy định gì? "
        "Dấu hiệu cần đối chiếu là Điều 58 của Luật này."
    )

    assert plan.article_law_pairs == (("62/2020/QH14", "1"),)
    assert plan.law_number == "62/2020/QH14"
    assert is_single_exact_article_plan(plan) is True


def test_adjacent_article_law_pair_bounds_exact_sql_to_current_instrument():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []
    plan = plan_exact_lookup(
        "Huong dan cu vien dan 45/2013/QH13; Dieu 1 62/2020/QH14 hien quy dinh gi?"
    )

    retriever._fetch_exact_chunks(
        plan,
        "dat_dai_xay_dung",
        date(2026, 8, 11),
        "core",
    )

    statement = str(connection.execute.call_args.args[0])
    params = connection.execute.call_args.args[1]
    assert params["law_numbers"] == ["62/2020/QH14"]
    assert params["article_numbers"] == ["1", "dieu 1"]
    assert "legal_normalize_text(a.article_number) IN" in statement
    assert "g.group_slug IN" not in statement


def test_exact_law_and_article_bypass_inferred_domain_but_keep_scope_and_dates():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []

    plan = plan_exact_lookup("Theo Dieu 18a 88/2025/QH15 quy dinh gi?")
    retriever._fetch_exact_chunks(
        plan,
        "lao_dong",
        date(2026, 8, 11),
        "core",
    )

    statement = str(connection.execute.call_args.args[0])
    params = connection.execute.call_args.args[1]
    assert "search_scope.included = TRUE" in statement
    assert "d.effective_date" in statement
    assert "a.effective_from" in statement
    assert "g.group_slug IN" not in statement
    assert "domains" not in params


def test_alphanumeric_article_is_bound_in_sql_normalized_text_case():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []

    plan = plan_exact_lookup("Theo Dieu 18a 88/2025/QH15 quy dinh gi?")
    assert plan.article_number == "18A"

    retriever._fetch_exact_chunks(
        plan,
        "noi_vu_hanh_chinh",
        date(2026, 8, 11),
        "core",
    )

    params = connection.execute.call_args.args[1]
    assert params["article_numbers"] == ["18a", "dieu 18a"]


def test_reviewed_exact_title_alias_binds_construction_amendment():
    plan = plan_exact_lookup(
        "Luật sửa đổi, bổ sung một số điều của Luật Xây dựng quy định thế nào "
        "về dự án đầu tư xây dựng khu đô thị?"
    )

    assert plan.law_number == "62/2020/QH14"
    assert plan.article_number == "1"
    assert plan.article_law_pairs == (("62/2020/QH14", "1"),)
    shortened = plan_exact_lookup(
        "Sửa đổi, bổ sung một số điều của Luật Xây dựng (phần 61: dự án khu đô thị)"
    )
    assert shortened.article_law_pairs == (("62/2020/QH14", "1"),)
    with_cross_reference = plan_exact_lookup(
        "Sửa đổi, bổ sung một số điều của Luật Xây dựng "
        "(phần 65: thẩm định theo Điều 58 của Luật này)"
    )
    assert with_cross_reference.article_law_pairs == (("62/2020/QH14", "1"),)


def test_reviewed_civil_status_database_chapter_keeps_requested_article():
    plan = plan_exact_lookup(
        "Cơ sở dữ liệu hộ tịch, cấp trích lục hộ tịch > Điều 59 quy định gì?"
    )

    assert plan.article_law_pairs == (("60/2014/QH13", "59"),)


def test_reviewed_health_and_teacher_chapters_bind_their_current_instruments():
    teacher = plan_exact_lookup(
        "Hoạt động nghề nghiệp, quyền và nghĩa vụ > Điều 11. Những việc không được làm"
    )
    health = plan_exact_lookup(
        "Thẻ bảo hiểm y tế > Điều 13. Thời điểm thẻ có giá trị sử dụng"
    )

    assert teacher.article_law_pairs == (("73/2025/QH15", "11"),)
    assert health.article_law_pairs == (("188/2025/ND-CP", "13"),)


def test_reviewed_enforcement_form_fallback_title_binds_article_seventy():
    plan = plan_exact_lookup(
        "Trường hợp không thể thực hiện được các hình thức quy định tại các điểm a, b "
        "(phần 3: thực hiện niêm yết)"
    )

    assert plan.article_law_pairs == (("88/2025/QH15", "70"),)


def test_reviewed_chapter_alias_keeps_explicit_civil_status_article():
    plan = plan_exact_lookup(
        "Đăng ký hộ tịch tại Ủy ban nhân dân cấp huyện, Điều 48 quy định gì?"
    )

    assert plan.law_number == "60/2014/QH13"
    assert plan.article_number == "48"
    assert plan.article_law_pairs == (("60/2014/QH13", "48"),)


def test_reviewed_provision_title_alias_binds_land_article_six():
    plan = plan_exact_lookup(
        "Người chịu trách nhiệm trước Nhà nước đối với việc sử dụng đất là ai?"
    )

    assert plan.law_number == "31/2024/QH15"
    assert plan.article_number == "6"


def test_real_vietnamese_decree_identifier_matches_ascii_query_form():
    assert normalize_exact_identifier("175/2024/NĐ-CP") == "175/2024/ND-CP"
    plan = plan_exact_lookup("Áp dụng Nghị định 175/2024/NĐ-CP")
    assert plan.law_number == "175/2024/ND-CP"


def test_bare_article_number_never_triggers_global_exact_lookup():
    retriever = LegalRetriever.__new__(LegalRetriever)
    plan = plan_exact_lookup("Điều 13 quy định thế nào?")

    assert retriever._fetch_exact_chunks(
        plan,
        "ho_tich_chung_thuc",
        date(2026, 7, 25),
        "core",
    ) == []


def test_exact_document_rows_are_ordered_by_question_relevance_before_cap():
    relevant = {
        "chunk_id": 2,
        "article_number": "18",
        "article_title": "Dừng xe, đỗ xe",
        "content": "Không được đỗ xe trên vỉa hè có biển cấm.",
        "document_title": "Luật Trật tự, an toàn giao thông đường bộ",
    }
    generic = {
        "chunk_id": 1,
        "article_number": "1",
        "article_title": "Phạm vi điều chỉnh",
        "content": "Luật này quy định chung.",
        "document_title": "Luật Trật tự, an toàn giao thông đường bộ",
    }

    assert _exact_metadata_relevance(
        "Tại sao bị cấm đỗ xe ở vỉa hè?",
        relevant,
    ) > _exact_metadata_relevance(
        "Tại sao bị cấm đỗ xe ở vỉa hè?",
        generic,
    )


def test_exact_document_phrase_match_keeps_threshold_rule_ahead_of_preamble():
    threshold_rule = {
        "chunk_id": 3,
        "article_number": "3",
        "article_title": "Tiêu chí thu hồi đất",
        "content": (
            "Khu đất có diện tích từ 1000m2 trở lên tại các phường, từ "
            "3000m2 trở lên tại các xã. Trường hợp khu đất nhỏ hơn diện tích "
            "quy định thì giao Ủy ban nhân thành phố xem xét, quyết định."
        ),
        "document_title": "Nghị quyết 22/2025/NQ-HĐND",
    }
    preamble = {
        "chunk_id": 1,
        "article_number": "1",
        "article_title": "Phạm vi điều chỉnh",
        "content": (
            "Nghị quyết quy định trường hợp thu hồi khu đất xen kẹt trong khu "
            "dân cư để tạo quỹ đất đấu giá quyền sử dụng đất."
        ),
        "document_title": "Nghị quyết 22/2025/NQ-HĐND",
    }
    query = (
        "Khu đất tại phường và xã phải đạt diện tích tối thiểu bao nhiêu; "
        "trường hợp diện tích nhỏ hơn ngưỡng thì cơ quan nào xem xét?"
    )

    assert _exact_metadata_relevance(query, threshold_rule) > (
        _exact_metadata_relevance(query, preamble)
    )


def test_rewritten_expected_article_outweighs_generic_article() -> None:
    expected = {
        "chunk_id": 50,
        "article_number": "13",
        "article_title": "Thẩm quyền đăng ký khai sinh",
        "content": "Ủy ban nhân dân cấp xã nơi cư trú của cha hoặc mẹ.",
    }
    generic = {
        "chunk_id": 1,
        "article_number": "1",
        "article_title": "Phạm vi điều chỉnh",
        "content": "Quy định chung về hộ tịch.",
    }
    rewritten = (
        "Đăng ký khai sinh ở đâu? điều 13 điều 35 "
        "60/2014/QH13 123/2015/ND-CP"
    )

    assert _exact_metadata_relevance(
        rewritten,
        expected,
    ) > _exact_metadata_relevance(
        rewritten,
        generic,
    )


def test_procedure_id_and_form_code_are_exact_identifiers():
    plan = plan_exact_lookup(
        "procedure_id:dang_ky_khai_sinh cần biểu mẫu Mẫu 09/ĐK và CT01"
    )

    assert plan.procedure_id == "dang_ky_khai_sinh"
    assert plan.form_codes == ("09/DK", "CT01")


def test_db5_candidate_budgets_are_locked():
    assert PRIMARY_BUDGET.vector_candidates == 150
    assert PRIMARY_BUDGET.lexical_candidates == 60
    assert PRIMARY_BUDGET.rerank_window == 40
    assert SUPPORT_BUDGET.vector_candidates == 100
    assert SUPPORT_BUDGET.lexical_candidates == 40
    assert SUPPORT_BUDGET.rerank_window == 40


def test_wrong_field_and_expired_or_superseded_candidates_are_rejected():
    plan = ExactLookupPlan(
        law_number="60/2014/QH13",
        article_number="16",
        clause_number=None,
        procedure_id=None,
        form_codes=(),
    )
    candidates = [
        {
            "chunk_id": 1,
            "law_number": "60/2014/QH13",
            "article_number": "16",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "expired_date": None,
        },
        {
            "chunk_id": 2,
            "law_number": "60/2014/QH13",
            "article_number": "16",
            "domain_slug": "dat_dai_xay_dung",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "expired_date": None,
        },
        {
            "chunk_id": 3,
            "law_number": "60/2014/QH13",
            "article_number": "16",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "expired",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "expired_date": date(2020, 1, 1),
        },
        {
            "chunk_id": 4,
            "law_number": "60/2014/QH13",
            "article_number": "16",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "expired_date": None,
            "is_superseded": True,
        },
    ]

    accepted, rejected = filter_exact_candidates(
        candidates,
        plan=plan,
        domain="tu_phap_ho_tich",
        as_of=date(2026, 7, 23),
    )

    assert [row["chunk_id"] for row in accepted] == [1]
    assert {row["reason"] for row in rejected} == {
        "wrong_field",
        "expired_or_not_yet_effective",
        "superseded",
    }


def test_domestic_civil_status_does_not_use_foreign_representation_source():
    plan = plan_exact_lookup(
        "Đăng ký khai sinh trong nước tại UBND phường theo Điều 13 Luật 60/2014/QH13"
    )
    candidates = [
        {
            "chunk_id": 10,
            "law_number": "60/2014/QH13",
            "article_number": "13",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "source_jurisdiction": "domestic",
        },
        {
            "chunk_id": 11,
            "law_number": "60/2014/QH13",
            "article_number": "13",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
            "source_jurisdiction": "foreign_representation",
        },
    ]

    accepted, rejected = filter_exact_candidates(
        candidates,
        plan=plan,
        domain="tu_phap_ho_tich",
        as_of=date(2026, 7, 23),
        jurisdiction="domestic",
    )

    assert [row["chunk_id"] for row in accepted] == [10]
    assert rejected == [{"chunk_id": 11, "reason": "wrong_jurisdiction"}]


def test_current_instrument_wins_and_old_instrument_is_rejected():
    plan = plan_exact_lookup("Điều 13 Luật 60/2014/QH13")
    candidates = [
        {
            "chunk_id": 20,
            "law_number": "60/2014/QH13",
            "article_number": "13",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "active",
            "article_status": "active",
            "effective_date": date(2016, 1, 1),
        },
        {
            "chunk_id": 21,
            "law_number": "60/2014/QH13",
            "article_number": "13",
            "domain_slug": "tu_phap_ho_tich",
            "document_status": "expired",
            "article_status": "active",
            "effective_date": date(2000, 1, 1),
            "expired_date": date(2015, 12, 31),
        },
    ]

    accepted, rejected = filter_exact_candidates(
        candidates,
        plan=plan,
        domain="tu_phap_ho_tich",
        as_of=date(2026, 7, 23),
    )

    assert [row["chunk_id"] for row in accepted] == [20]
    assert rejected == [
        {"chunk_id": 21, "reason": "expired_or_not_yet_effective"}
    ]


def test_support_is_called_once_only_for_missing_facets():
    calls = {"primary": 0, "support": 0}

    def primary():
        calls["primary"] += 1
        return [{"chunk_id": 1, "facets": ["legal_basis"]}]

    def support(missing_facets):
        calls["support"] += 1
        assert missing_facets == ("form", "time_limit")
        return [{"chunk_id": 2, "facets": list(missing_facets)}]

    result = retrieve_primary_then_support(
        required_facets=("legal_basis", "form", "time_limit"),
        primary_retrieve=primary,
        support_retrieve=support,
    )

    assert calls == {"primary": 1, "support": 1}
    assert result.support_called is True
    assert result.missing_facets == ()


def test_support_is_not_called_when_primary_has_full_coverage():
    calls = {"support": 0}

    def support(_missing_facets):
        calls["support"] += 1
        return []

    result = retrieve_primary_then_support(
        required_facets=("legal_basis",),
        primary_retrieve=lambda: [{"chunk_id": 1, "facets": ["legal_basis"]}],
        support_retrieve=support,
    )

    assert calls["support"] == 0
    assert result.support_called is False


def test_diversity_is_two_chunks_per_article_and_three_per_document():
    rows = [
        {
            "chunk_id": index,
            "document_id": 1 if index <= 5 else 2,
            "article_id": 10 if index <= 3 else index,
            "score": 100 - index,
        }
        for index in range(1, 8)
    ]

    selected = diversify_ranked_candidates(
        rows,
        limit=7,
        max_per_article=2,
        max_per_document=3,
    )

    assert len([row for row in selected if row["document_id"] == 1]) == 3
    assert len(
        [
            row
            for row in selected
            if row["document_id"] == 1 and row["article_id"] == 10
        ]
    ) == 2


def test_shadow_serving_rejects_lexical_and_neighbor_chunks_outside_tier():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._shadow_allowed_chunk_ids = {
        "core": {10, 11},
        "expanded": {20},
    }

    assert retriever._shadow_filter_rows(
        [{"chunk_id": 10}, {"chunk_id": 99}],
        "core",
    ) == [{"chunk_id": 10}]
    assert retriever._shadow_filter_ids([20, 21], "expanded") == [20]


def test_foreign_birth_registration_query_expands_to_both_competent_articles():
    rewritten = _rewrite_query(
        "Trẻ sinh ở nước ngoài, cha cư trú Hải Phòng, đăng ký khai sinh ở đâu?"
    )
    normalized = rewritten.casefold()
    assert "dieu 35" in normalized
    assert "dieu 13" in normalized
    assert "123/2015/nd-cp" in normalized
    plan = plan_exact_lookup(rewritten)
    assert plan.article_numbers == ("13", "35")


def test_domestic_marriage_registration_expands_to_current_framework_sources():
    rewritten = _rewrite_query(
        "Thá»§ tá»¥c Ä‘Äƒng kÃ½ káº¿t hÃ´n táº¡i Háº£i PhÃ²ng cáº§n gÃ¬?"
    )
    normalized = rewritten.casefold()

    assert "60/2014/qh13" in normalized
    assert "123/2015/nd-cp" in normalized
    assert "dieu 17" in normalized
    plan = plan_exact_lookup(rewritten)
    assert plan.law_numbers == ("60/2014/QH13", "123/2015/ND-CP")
    assert plan.article_numbers == ("17", "18")
