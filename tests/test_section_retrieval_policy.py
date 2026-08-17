"""Feature 005 regression requirements for section retrieval provenance.

These are deliberately pure-policy tests: no corpus, model, or network is
needed to show whether a candidate is eligible for the current legal issue.
"""

from api.legal_section_grounding import (
    LegalIssue,
    evaluate_evidence_eligibility,
    issue_requires_expanded_support,
    plan_legal_issues,
    retrieval_domain_slug,
    select_eligible_evidence,
)


def test_semicolon_rule_clause_remains_its_own_issue_before_later_facets():
    issues = plan_legal_issues(
        "Xử phạt không lập biên bản khi nào hợp pháp; "
        "khi khiếu nại thì ai giải quyết và thời hạn ra sao?"
    )

    assert len(issues) == 3
    assert "không lập biên bản" in issues[0].query_text
    assert "khiếu nại" not in issues[0].query_text
    assert any("khiếu nại" in issue.query_text for issue in issues[1:])


LAND_ISSUE = LegalIssue(
    issue_id="issue-land",
    request_id="request-land",
    text="Thủ tục cấp giấy chứng nhận quyền sử dụng đất thế nào?",
    domain="dat_dai_moi_truong",
    intent="procedure",
)


def _evidence(**overrides):
    source = {
        "chunk_id": 101,
        "request_id": "request-land",
        "issue_id": "issue-land",
        "domain_slug": "dat_dai_moi_truong",
        "document_status": "active",
        "article_status": "active",
        "effective_date": "2025-01-01",
        "source_url": "https://vbpl.vn/land",
        "official_level": "central",
        "scope": "central",
        "issuing_agency": "Quốc hội",
        "law_number": "31/2024/QH15",
    }
    source.update(overrides)
    return source


def test_land_issue_rejects_labour_evidence_even_when_keywords_overlap():
    decision = evaluate_evidence_eligibility(
        _evidence(domain_slug="lao_dong", law_number="45/2019/QH14"),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )

    assert decision.eligible is False
    assert decision.reason == "wrong_domain"


def test_unknown_domain_accepts_only_the_exact_law_number_named_by_user():
    issue = LegalIssue(
        issue_id="issue-exact-law",
        request_id="request-exact-law",
        text="Nghị quyết 22/2025/NQ-HĐND có hiệu lực từ ngày nào?",
        query_text="Nghị quyết 22/2025/NQ-HĐND có hiệu lực từ ngày nào?",
        domain="unknown",
        intent="rule",
    )
    exact = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        official_level="haiphong",
        scope="haiphong",
        issuing_agency="HĐND thành phố Hải Phòng",
        law_number="22/2025/NQ-HĐND",
        content="Nghị quyết có hiệu lực từ ngày 26 tháng 10 năm 2025.",
    )
    wrong_law = {**exact, "law_number": "39/2025/NQ-HĐND"}

    assert evaluate_evidence_eligibility(exact, issue).eligible is True
    rejected = evaluate_evidence_eligibility(wrong_law, issue)
    assert rejected.eligible is False
    assert rejected.reason == "wrong_domain"


def test_unknown_domain_without_exact_law_number_still_fails_closed():
    issue = LegalIssue(
        issue_id="issue-unknown",
        request_id="request-unknown",
        text="Văn bản này có hiệu lực từ ngày nào?",
        query_text="Văn bản này có hiệu lực từ ngày nào?",
        domain="unknown",
        intent="rule",
    )
    source = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        content="Văn bản có hiệu lực từ ngày 26 tháng 10 năm 2025.",
    )

    decision = evaluate_evidence_eligibility(source, issue)

    assert decision.eligible is False
    assert decision.reason == "wrong_domain"


def test_known_domain_exact_law_rejects_unrelated_higher_authority_source():
    issue = LegalIssue(
        issue_id="issue-exact-local-law",
        request_id="request-exact-local-law",
        text="Theo Nghị quyết 22/2025/NQ-HĐND, diện tích tối thiểu là bao nhiêu?",
        query_text=(
            "Theo Nghị quyết 22/2025/NQ-HĐND, diện tích tối thiểu là bao nhiêu?"
        ),
        domain="dat_dai_xay_dung",
        intent="condition",
    )
    unrelated_national = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        law_number="254/2025/QH15",
        content="Điều kiện bồi thường bằng đất ở tại chỗ.",
    )

    decision = evaluate_evidence_eligibility(unrelated_national, issue)

    assert decision.eligible is False
    assert decision.reason == "wrong_legal_identity"


def test_expired_conflicting_and_metadata_incomplete_evidence_do_not_support_issue():
    expired = evaluate_evidence_eligibility(
        _evidence(document_status="expired", expired_date="2025-12-31"),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )
    incomplete = evaluate_evidence_eligibility(
        _evidence(source_url="", official_level="", issuing_agency=""),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )
    conflict = _evidence(
        chunk_id=102,
        relationships=[{"relation_type": "superseded_by", "status": "active"}],
    )

    assert expired.eligible is False
    assert expired.reason == "not_effective"
    assert incomplete.eligible is False
    assert incomplete.reason == "missing_required_metadata"
    assert select_eligible_evidence(
        [conflict], LAND_ISSUE, legal_as_of="2026-07-18"
    ) == []


def test_as_of_gate_rejects_date_expired_source_even_if_status_is_stale_active():
    stale_active = _evidence(
        document_status="active",
        effective_status="active",
        expired_date="2025-12-31",
    )
    future_source = _evidence(
        document_status="active",
        effective_status="active",
        effective_date="2027-01-01",
    )

    expired = evaluate_evidence_eligibility(
        stale_active,
        LAND_ISSUE,
        legal_as_of="2026-08-09",
    )
    not_yet_effective = evaluate_evidence_eligibility(
        future_source,
        LAND_ISSUE,
        legal_as_of="2026-08-09",
    )

    assert expired.eligible is False
    assert expired.reason == "not_effective"
    assert not_yet_effective.eligible is False
    assert not_yet_effective.reason == "not_effective"


def test_test_or_unapproved_source_url_cannot_support_a_legal_issue():
    test_source = _evidence(
        source_url="http://test.local/9999-b20",
        law_number="9999/2099/QD-TEST-UTF8",
        collection_source="manual_vnlegal_lal_import",
    )

    decision = evaluate_evidence_eligibility(
        test_source,
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )

    assert decision.eligible is False
    assert decision.reason == "unapproved_source_provenance"


def test_central_source_is_retained_before_compatible_hai_phong_implementation():
    central = _evidence(chunk_id=101, official_level="central", scope="central")
    local = _evidence(
        chunk_id=102,
        official_level="haiphong",
        scope="haiphong",
        issuing_agency="UBND thành phố Hải Phòng",
    )

    selected = select_eligible_evidence(
        [local, central], LAND_ISSUE, legal_as_of="2026-07-18"
    )

    assert [item["chunk_id"] for item in selected] == [101, 102]


def test_runtime_nationwide_scope_is_treated_as_central_law():
    nationwide = _evidence(
        official_level="",
        scope="Toàn quốc",
        issuing_agency="Chính phủ",
    )

    decision = evaluate_evidence_eligibility(
        nationwide,
        LAND_ISSUE,
        legal_as_of="2026-07-22",
    )

    assert decision.eligible is True
    assert decision.reason == "eligible_current_issue_source"


def test_selected_runtime_domain_wins_over_broad_planner_alias():
    assert retrieval_domain_slug("civil_status", "cu_tru_an_ninh") == "cu_tru_an_ninh"
    assert retrieval_domain_slug("land", "khieu_nai_to_cao_xu_phat") == "khieu_nai_to_cao_xu_phat"


def test_exact_article_identity_is_not_rejected_by_topic_heuristic():
    issue = LegalIssue(
        issue_id="issue-exact-article-topic",
        request_id="request-exact-article-topic",
        query_text="Luật 73/2025/QH15 Điều 11 quy định gì?",
        domain="an_sinh_y_te_giao_duc",
        intent="rule",
    )
    decision = evaluate_evidence_eligibility(
        _evidence(
            request_id=issue.request_id,
            issue_id=issue.issue_id,
            domain_slug="an_sinh_y_te_giao_duc",
            law_number="73/2025/QH15",
            article_number="11",
            content="Nội dung điều khoản dùng thuật ngữ chuyên ngành khác.",
            article_title="Điều 11",
        ),
        issue,
        legal_as_of="2026-07-18",
    )
    assert decision.eligible is True
    assert decision.reason == "eligible_current_issue_source"


def test_fee_issue_rejects_same_domain_source_without_fee_language():
    issue = LegalIssue(
        issue_id="issue-fee",
        request_id="request-fee",
        text="dang ky khai sinh le phi",
        domain="civil_status",
        intent="fee",
    )
    source = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        content="Uy ban nhan dan cap xa thuc hien dang ky khai sinh.",
    )

    decision = evaluate_evidence_eligibility(source, issue)

    assert decision.status == "excluded"
    assert decision.reason == "intent_not_supported"


def test_documents_issue_rejects_internal_agency_file_processing_sentence():
    issue = LegalIssue(
        issue_id="issue-documents",
        request_id="request-birth",
        text="Người dân cần nộp giấy tờ gì để đăng ký khai sinh?",
        query_text="Hồ sơ đăng ký khai sinh gồm những giấy tờ nào?",
        domain="civil_status",
        intent="documents",
    )
    internal_processing = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="126/2014/NĐ-CP",
        document_title="Quy định chi tiết thi hành Luật Hộ tịch",
        article_title="Thủ tục đăng ký khai sinh",
        content=(
            "Trong thời hạn 10 ngày, Sở Tư pháp thẩm tra tính hợp lệ của "
            "hồ sơ và văn bản kèm theo hồ sơ."
        ),
    )

    decision = evaluate_evidence_eligibility(internal_processing, issue)

    assert decision.status == "excluded"
    assert decision.reason == "intent_not_supported"


def test_documents_issue_accepts_direct_applicant_submission_sentence():
    issue = LegalIssue(
        issue_id="issue-documents",
        request_id="request-birth",
        text="Người dân cần nộp giấy tờ gì để đăng ký khai sinh?",
        query_text="Hồ sơ đăng ký khai sinh gồm những giấy tờ nào?",
        domain="civil_status",
        intent="documents",
    )
    direct_submission = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="60/2014/QH13",
        content=(
            "Người đi đăng ký khai sinh nộp Tờ khai theo mẫu quy định và "
            "giấy chứng sinh cho cơ quan đăng ký hộ tịch."
        ),
    )

    decision = evaluate_evidence_eligibility(direct_submission, issue)

    assert decision.status == "accepted"


def test_authority_issue_rejects_internal_processing_without_submission_or_power():
    issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-residence",
        text="Đăng ký tạm trú nộp ở đâu?",
        query_text="Cơ quan tiếp nhận đăng ký tạm trú",
        domain="cu_tru_an_ninh",
        intent="authority",
    )
    internal_processing = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="cu_tru_an_ninh",
        law_number="68/2020/QH14",
        document_title="Luật Cư trú",
        content=(
            "Trong thủ tục đăng ký tạm trú, cơ quan đăng ký cư trú có "
            "trách nhiệm thẩm định, cập nhật "
            "thông tin về nơi tạm trú mới của người đăng ký."
        ),
    )

    decision = evaluate_evidence_eligibility(internal_processing, issue)

    assert decision.status == "excluded"
    assert decision.reason == "intent_not_supported"


def test_authority_issue_accepts_direct_submission_destination():
    issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-residence",
        text="Đăng ký tạm trú nộp ở đâu?",
        query_text="Cơ quan tiếp nhận đăng ký tạm trú",
        domain="cu_tru_an_ninh",
        intent="authority",
    )
    direct_destination = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="cu_tru_an_ninh",
        law_number="68/2020/QH14",
        document_title="Luật Cư trú",
        content=(
            "Người đăng ký tạm trú nộp hồ sơ đến cơ quan đăng ký cư trú "
            "nơi mình dự kiến tạm trú."
        ),
    )

    decision = evaluate_evidence_eligibility(direct_destination, issue)

    assert decision.status == "accepted"


def test_authority_issue_accepts_direct_assignment_to_review_and_decide():
    issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-land-threshold",
        text="Trường hợp khu đất nhỏ hơn ngưỡng thì cơ quan nào xem xét?",
        query_text=(
            "Trường hợp khu đất nhỏ hơn ngưỡng thì cơ quan nào xem xét; "
            "22/2025/NQ-HĐND"
        ),
        domain="dat_dai_xay_dung",
        intent="authority",
    )
    direct_assignment = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        law_number="22/2025/NQ-HĐND",
        official_level="haiphong",
        scope="haiphong",
        issuing_agency="HĐND thành phố Hải Phòng",
        content=(
            "Trường hợp khu đất nhỏ hơn diện tích quy định thì giao Ủy ban nhân dân "
            "thành phố Hải Phòng xem xét, quyết định từng trường hợp cụ thể."
        ),
    )

    decision = evaluate_evidence_eligibility(direct_assignment, issue)

    assert decision.status == "accepted"


def test_authority_issue_accepts_source_text_missing_dan_in_direct_assignment():
    issue = LegalIssue(
        issue_id="issue-authority-source-artifact",
        request_id="request-land-threshold-artifact",
        text="co quan nao xem xet khu dat nho hon nguong?",
        query_text="tham quyen xem xet; 22/2025/NQ-HDND",
        domain="dat_dai_xay_dung",
        intent="authority",
    )
    direct_assignment = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        law_number="22/2025/NQ-HDND",
        official_level="haiphong",
        scope="haiphong",
        content=(
            "Truong hop khu dat nho hon dien tich quy dinh thi giao Uy ban "
            "nhan thanh pho Hai Phong xem xet, quyet dinh tung truong hop cu the."
        ),
    )

    decision = evaluate_evidence_eligibility(direct_assignment, issue)

    assert decision.status == "accepted"


def test_authority_issue_rejects_notice_sent_to_agency_as_submission_place():
    issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-building",
        text="Xin giấy phép xây dựng nộp ở đâu?",
        query_text="Cấp giấy phép xây dựng nhà ở riêng lẻ; thẩm quyền và nơi nộp",
        domain="dat_dai_xay_dung",
        intent="authority",
    )
    notice = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        law_number="50/2014/QH13",
        document_title="Luật Xây dựng",
        content=(
            "Sau khi được cấp giấy phép xây dựng, chủ đầu tư thông báo ngày khởi công "
            "xây dựng bằng văn bản cho Ủy ban "
            "nhân dân cấp xã nơi xây dựng công trình."
        ),
    )

    decision = evaluate_evidence_eligibility(notice, issue)

    assert decision.status == "excluded"
    assert decision.reason == "intent_not_supported"


def test_domestic_civil_status_rejects_foreign_representation_only_source():
    domestic_issue = LegalIssue(
        issue_id="issue-deadline",
        request_id="request-birth",
        text="Đăng ký khai sinh tại Hải Phòng mất bao lâu?",
        domain="civil_status",
        intent="deadline",
    )
    foreign_source = _evidence(
        request_id=domestic_issue.request_id,
        issue_id=domestic_issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        document_title=(
            "Hướng dẫn hộ tịch tại Cơ quan đại diện ngoại giao, "
            "Cơ quan đại diện lãnh sự Việt Nam ở nước ngoài"
        ),
        content="Thời hạn giải quyết là 02 ngày làm việc.",
    )

    decision = evaluate_evidence_eligibility(
        foreign_source,
        domestic_issue,
    )

    assert decision.status == "excluded"
    assert decision.reason == "wrong_jurisdiction"


def test_domestic_birth_rejects_foreign_element_authority_provision():
    domestic_issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-birth",
        text="Đăng ký khai sinh cho con tại UBND phường thì nộp ở đâu?",
        domain="civil_status",
        intent="authority",
    )
    foreign_element_authority = _evidence(
        request_id=domestic_issue.request_id,
        issue_id=domestic_issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="60/2014/QH13",
        article_number="35",
        document_title="Luật Hộ tịch",
        article_title="Thẩm quyền đăng ký khai sinh có yếu tố nước ngoài",
        content=(
            "Ủy ban nhân dân cấp huyện nơi cư trú của người cha hoặc người mẹ "
            "thực hiện đăng ký khai sinh cho trẻ em có yếu tố nước ngoài."
        ),
    )

    decision = evaluate_evidence_eligibility(
        foreign_element_authority,
        domestic_issue,
    )

    assert decision.status == "excluded"
    assert decision.reason == "wrong_jurisdiction"


def test_explicit_article_13_35_comparison_keeps_foreign_element_provision():
    comparison_issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-foreign-birth",
        text=(
            "Trẻ sinh ở nước ngoài, phân biệt thẩm quyền Điều 13 và Điều 35 "
            "Luật Hộ tịch"
        ),
        domain="civil_status",
        intent="authority",
    )
    article_35 = _evidence(
        request_id=comparison_issue.request_id,
        issue_id=comparison_issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="60/2014/QH13",
        article_number="35",
        document_title="Luật Hộ tịch",
        content="Ủy ban nhân dân cấp huyện thực hiện đăng ký khai sinh.",
    )

    decision = evaluate_evidence_eligibility(article_35, comparison_issue)

    assert decision.status == "accepted"


def test_land_transfer_rejects_unrelated_registration_change_subtype():
    transfer_issue = LegalIssue(
        issue_id="issue-documents",
        request_id="request-transfer",
        text="Sang tên nhà đất do mua bán cần hồ sơ và Mẫu 09/ĐK nào?",
        domain="land",
        intent="documents",
    )
    wrong_subtype = _evidence(
        request_id=transfer_issue.request_id,
        issue_id=transfer_issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        document_title="Thông tư về hồ sơ địa chính",
        content=(
            "Hồ sơ thay đổi số căn cước công dân gồm Đơn đăng ký biến động "
            "theo Mẫu 09/ĐK và bản gốc Giấy chứng nhận."
        ),
    )
    direct_transfer = {
        **wrong_subtype,
        "chunk_id": 202,
        "content": (
            "Hồ sơ đăng ký biến động do chuyển nhượng quyền sử dụng đất gồm "
            "hợp đồng chuyển nhượng và bản gốc Giấy chứng nhận."
        ),
    }
    current_transfer_procedure = {
        **wrong_subtype,
        "chunk_id": 204,
        "law_number": "101/2024/NĐ-CP",
        "article_number": "42",
        "content": (
            "Trình tự đăng ký biến động đối với trường hợp đã chuyển quyền "
            "sử dụng đất nhưng chưa thực hiện thủ tục đăng ký."
        ),
    }

    rejected = evaluate_evidence_eligibility(wrong_subtype, transfer_issue)
    accepted = evaluate_evidence_eligibility(direct_transfer, transfer_issue)
    current_accepted = evaluate_evidence_eligibility(
        current_transfer_procedure, transfer_issue
    )

    assert rejected.status == "excluded"
    assert rejected.reason == "wrong_procedure_topic"
    assert accepted.status == "accepted"
    assert current_accepted.status == "accepted"


def test_unlicensed_construction_rejects_general_license_revocation_source():
    issue = LegalIssue(
        issue_id="issue-procedure",
        request_id="request-construction",
        text="Nhà ở đang xây dựng không phép thì lập biên bản và xử lý thế nào?",
        domain="land",
        intent="procedure",
    )
    revocation = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        document_title="Luật Xây dựng",
        content=(
            "Ủy ban nhân dân cấp tỉnh quyết định thu hồi giấy phép xây dựng "
            "đã cấp không đúng quy định."
        ),
    )
    direct_violation = {
        **revocation,
        "chunk_id": 203,
        "content": (
            "Đối với công trình xây dựng không có giấy phép, người có thẩm quyền "
            "lập biên bản vi phạm hành chính và áp dụng biện pháp khắc phục."
        ),
    }

    rejected = evaluate_evidence_eligibility(revocation, issue)
    accepted = evaluate_evidence_eligibility(direct_violation, issue)

    assert rejected.status == "excluded"
    assert rejected.reason == "wrong_procedure_topic"
    assert accepted.status == "accepted"


def test_unlicensed_construction_accepts_reviewed_cross_cutting_sanction_sources():
    issue = LegalIssue(
        issue_id="issue-procedure",
        request_id="request-construction-laws",
        text="Nhà ở đang xây dựng không phép thì lập biên bản và xử lý thế nào?",
        domain="land",
        intent="procedure",
    )
    general_procedure = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug=["khieu_nai_to_cao_xu_phat", "dat_dai_xay_dung"],
        law_number="15/2012/QH13",
        article_number="58",
        content=(
            "Khi phát hiện vi phạm hành chính thuộc lĩnh vực quản lý, người có "
            "thẩm quyền phải kịp thời lập biên bản vi phạm hành chính."
        ),
    )
    direct_construction = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="dat_dai_xay_dung",
        law_number="16/2022/NĐ-CP",
        article_number="16",
        content=(
            "Xử phạt hành vi tổ chức thi công xây dựng công trình không có "
            "giấy phép xây dựng mà theo quy định phải có giấy phép."
        ),
    )

    assert evaluate_evidence_eligibility(general_procedure, issue).status == "accepted"
    assert evaluate_evidence_eligibility(direct_construction, issue).status == "accepted"
    assert issue_requires_expanded_support(issue, [general_procedure]) is True
    assert issue_requires_expanded_support(
        issue, [general_procedure, direct_construction]
    ) is False


def test_documents_issue_expands_when_core_only_mentions_post_receipt_processing():
    issue = LegalIssue(
        issue_id="issue-documents",
        request_id="request-birth-documents",
        text="Hồ sơ đăng ký khai sinh gồm những giấy tờ gì?",
        domain="ho_tich_chung_thuc",
        intent="documents",
    )
    post_receipt = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="60/2014/QH13",
        article_number="16",
        content=(
            "Ngay sau khi nhận đủ giấy tờ theo quy định, công chức tư pháp "
            "ghi nội dung khai sinh vào Sổ hộ tịch."
        ),
    )
    dossier_clause = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        law_number="60/2014/QH13",
        article_number="16",
        content=(
            "Người đi đăng ký khai sinh nộp tờ khai theo mẫu quy định và "
            "giấy chứng sinh."
        ),
    )

    assert issue_requires_expanded_support(issue, [post_receipt]) is True
    assert issue_requires_expanded_support(
        issue, [post_receipt, dossier_clause]
    ) is False


def test_broad_meritorious_support_rejects_arbitrary_benefit_subtype():
    issue = LegalIssue(
        issue_id="issue-documents",
        request_id="request-meritorious",
        text="Người có công xin trợ cấp cần hồ sơ gì?",
        domain="an_sinh_y_te_giao_duc",
        intent="documents",
    )
    education_subtype = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="an_sinh_y_te_giao_duc",
        document_title="Nghị định ưu đãi người có công",
        content=(
            "Hồ sơ hưởng ưu đãi trong giáo dục gồm Mẫu số 20 và giấy xác nhận "
            "của cơ sở giáo dục."
        ),
    )

    decision = evaluate_evidence_eligibility(education_subtype, issue)

    assert decision.status == "excluded"
    assert decision.reason == "wrong_procedure_topic"


def test_birth_issue_rejects_same_domain_parentage_or_marriage_source():
    birth_issue = LegalIssue(
        issue_id="issue-authority",
        request_id="request-birth",
        text=(
            "Phân biệt thẩm quyền Điều 13 và Điều 35 Luật Hộ tịch "
            "khi đăng ký khai sinh"
        ),
        domain="civil_status",
        intent="authority",
    )
    wrong_topic = _evidence(
        request_id=birth_issue.request_id,
        issue_id=birth_issue.issue_id,
        domain_slug="ho_tich_chung_thuc",
        document_title="Quy định chi tiết Luật Hôn nhân và gia đình",
        content=(
            "Người đứng đầu Cơ quan đại diện ký Quyết định công nhận "
            "việc nhận cha, mẹ, con."
        ),
    )

    decision = evaluate_evidence_eligibility(wrong_topic, birth_issue)

    assert decision.status == "excluded"
    assert decision.reason == "wrong_procedure_topic"


def test_labour_issue_accepts_reviewed_broad_social_services_scope():
    issue = LegalIssue(
        issue_id="issue-labour",
        request_id="request-labour",
        text="Điều 1 Bộ luật Lao động quy định gì?",
        domain="labour",
        intent="rule",
    )
    source = _evidence(
        request_id=issue.request_id,
        issue_id=issue.issue_id,
        domain_slug="an_sinh_y_te_giao_duc",
        law_number="45/2019/QH14",
    )

    assert evaluate_evidence_eligibility(source, issue).status == "accepted"


def test_abstract_issue_domain_maps_to_runtime_retrieval_slug():
    assert retrieval_domain_slug("civil_status", None) == "ho_tich_chung_thuc"
    assert retrieval_domain_slug("land", None) == "dat_dai_xay_dung"
    assert retrieval_domain_slug("labour", None) == "lao_dong"
    assert retrieval_domain_slug("unknown", "khieu_nai_to_cao_xu_phat") == "khieu_nai_to_cao_xu_phat"
    assert retrieval_domain_slug("administrative", None, "cấp giấy phép xây dựng nhà ở") == "dat_dai_xay_dung"
    assert retrieval_domain_slug(
        "administrative", None, "công nhận ban quản trị nhà chung cư"
    ) == "dat_dai_xay_dung"
    assert retrieval_domain_slug("administrative", None, "khiếu nại quyết định xử phạt") == "khieu_nai_to_cao_xu_phat"
    assert retrieval_domain_slug(
        "administrative",
        None,
        "bị xử phạt vi phạm hành chính mà không có biên bản",
    ) == "khieu_nai_to_cao_xu_phat"
    assert retrieval_domain_slug(
        "administrative",
        None,
        "Bi phat vi pham hanh chinh ma khong co bien ban thi co dung luat khong?",
    ) == "khieu_nai_to_cao_xu_phat"
    assert retrieval_domain_slug("administrative", None, "trợ cấp người có công") == "an_sinh_y_te_giao_duc"
    assert retrieval_domain_slug("administrative", None, "xác nhận tình trạng hôn nhân") == "ho_tich_chung_thuc"
