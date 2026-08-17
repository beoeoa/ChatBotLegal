from api.legal_evidence_relevance import rank_issue_evidence


def _row(content: str, *, score: float = 1.0, authority_rank: int = 80):
    return {
        "chunk_id": content[:12],
        "content": content,
        "score": score,
        "authority_rank": authority_rank,
        "authority_confidence": "verified",
        "effective_status": "active",
    }


def test_individual_land_issue_rejects_community_and_house_only_passages():
    rows = [
        _row("Cong dong dan cu dang su dung dat co dinh, den, mieu, am."),
        _row("Truong hop ca nhan co nha o thi xac lap quyen so huu nha o."),
        _row("Ho gia dinh, ca nhan su dung dat duoc xem xet cap Giay chung nhan quyen su dung dat."),
    ]

    ranked, decisions = rank_issue_evidence(
        "Ca nhan xin cap Giay chung nhan quyen su dung dat cho thua dat",
        rows,
    )

    assert [row["content"] for row in ranked] == [rows[2]["content"]]
    assert {item["reason"] for item in decisions if item["decision"] == "reject"} == {
        "subject_mismatch_community",
        "relation_mismatch_house_only",
    }


def test_relevance_rules_keep_explicit_community_or_house_questions():
    community = _row("Cong dong dan cu dang su dung dat co dinh, den, mieu, am.")
    house = _row("Ca nhan co nha o duoc xac lap quyen so huu nha o.")

    ranked_community, _ = rank_issue_evidence(
        "Cong dong dan cu xin cap giay cho dinh, den, mieu", [community]
    )
    ranked_house, _ = rank_issue_evidence(
        "Dieu kien xac lap quyen so huu nha o cua ca nhan", [house]
    )

    assert ranked_community == [community]
    assert ranked_house == [house]


def test_soft_penalty_never_moves_lower_authority_ahead_of_higher_authority():
    superior = _row("Quy dinh co lien quan gian tiep den ho so.", score=0.1, authority_rank=90)
    lower = _row("Thanh phan ho so truc tiep gom cac giay to sau.", score=5.0, authority_rank=60)

    ranked, _ = rank_issue_evidence("Ho so can nop gom nhung gi?", [lower, superior])

    assert ranked[0] is superior


def test_individual_first_issue_rejects_organization_only_and_replacement_procedure():
    organization = _row(
        "To chuc trong nuoc, to chuc kinh te co von dau tu nuoc ngoai "
        "dau tu xay dung nha o duoc cap Giay chung nhan quyen su dung dat."
    )
    replacement = _row(
        "Nguoi su dung dat yeu cau cap doi, cap lai Giay chung nhan."
    )
    direct = _row(
        "Ho gia dinh, ca nhan chua duoc cap Giay chung nhan "
        "duoc dang ky dat dai lan dau khi du dieu kien."
    )

    ranked, decisions = rank_issue_evidence(
        "Ca nhan chua co so do lam thu tuc cap Giay chung nhan lan dau",
        [organization, replacement, direct],
    )

    assert ranked == [direct]
    assert {item["reason"] for item in decisions if item["decision"] == "reject"} == {
        "subject_mismatch_organization",
        "procedure_mismatch_first_vs_replacement",
    }


def test_transaction_year_rejects_inapplicable_old_cutoff_but_keeps_2014_cutoff():
    old_cutoff = _row(
        "Giay to chuyen nhuong quyen su dung dat duoc lap truoc ngay "
        "15 thang 10 nam 1993."
    )
    applicable = _row(
        "Truong hop nhan chuyen quyen su dung dat truoc ngay "
        "01 thang 7 nam 2014 ma chua thuc hien thu tuc chuyen quyen."
    )

    ranked, decisions = rank_issue_evidence(
        "Ca nhan mua dat bang giay viet tay nam 2009, cap lan dau",
        [old_cutoff, applicable],
    )

    assert ranked == [applicable]
    assert decisions[0]["reason"] == "time_condition_mismatch"


def test_special_fact_issue_requires_direct_deceased_transferor_support():
    unrelated = _row(
        "Nguoi su dung dat nop don dang ky dat dai lan dau."
    )
    direct = _row(
        "Truong hop ben chuyen nhuong da chet thi nguoi thua ke "
        "thuc hien theo quy dinh."
    )

    ranked, decisions = rank_issue_evidence(
        "Xu ly nguoi chuyen quyen da chet khi cap lan dau",
        [unrelated, direct],
        relevance_topics=["deceased_transferor"],
    )

    assert ranked == [direct]
    assert decisions[0]["reason"] == "missing_deceased_transferor_support"


def test_special_fact_gap_is_not_promoted_to_direct_branch_evidence():
    generic_but_eligible = _row(
        "Nguoi su dung dat nop don dang ky dat dai lan dau tai co quan tiep nhan."
    )

    ranked, decisions = rank_issue_evidence(
        "Xu ly nguoi chuyen quyen da chet khi cap lan dau",
        [generic_but_eligible],
        relevance_topics=["deceased_transferor"],
    )

    assert ranked == []
    assert decisions[0]["decision"] == "reject"
    assert decisions[0]["reason"] == "missing_deceased_transferor_support"
    assert decisions[0]["penalty"] == 0


def test_handwritten_purchase_rejects_unrequested_agricultural_transfer_limit_case():
    limit_case = _row(
        "Hộ gia đình, cá nhân sử dụng đất nông nghiệp vượt hạn mức nhận "
        "chuyển quyền thì phần diện tích vượt hạn mức được tiếp tục sử dụng."
    )
    direct = _row(
        "Trường hợp nhận chuyển quyền sử dụng đất trước ngày 01 tháng 7 năm "
        "2014 mà chưa thực hiện thủ tục chuyển quyền thì thực hiện đăng ký lần đầu."
    )

    ranked, decisions = rank_issue_evidence(
        "Mua đất bằng giấy viết tay năm 2009, chưa có sổ đỏ, cấp Giấy chứng nhận",
        [limit_case, direct],
    )

    assert ranked == [direct]
    assert decisions[0]["reason"] == (
        "transaction_case_mismatch_agricultural_transfer_limit"
    )


def test_hard_reject_uses_matched_child_not_unrelated_parent_siblings():
    row = _row(
        "Ho gia dinh, ca nhan su dung dat duoc xem xet cap Giay chung nhan."
    )
    row.update(
        {
            "matched_child_content": (
                "To chuc trong nuoc dau tu xay dung nha o duoc cap "
                "Giay chung nhan quyen su dung dat."
            ),
            "parent_context": (
                "Khoan 1 ve to chuc. Khoan 2 ve ho gia dinh, ca nhan."
            ),
        }
    )

    ranked, decisions = rank_issue_evidence(
        "Ca nhan xin cap Giay chung nhan quyen su dung dat lan dau",
        [row],
    )

    assert ranked == []
    assert decisions[0]["reason"] == "subject_mismatch_organization"


def test_individual_transaction_rejects_investor_project_and_post_2014_branch():
    project = _row(
        "Du an dau tu da lua chon nha dau tu tu ngay 01 thang 7 nam 2014 "
        "thi tiep tuc thu tuc giao dat cho chu dau tu."
    )

    ranked, decisions = rank_issue_evidence(
        "Ca nhan mua dat nam 2009; nhan chuyen quyen truoc nam 2014",
        [project],
    )

    assert ranked == []
    assert decisions[0]["reason"] in {
        "subject_mismatch_investor_project",
        "time_condition_mismatch",
    }


def test_noncooperation_branch_rejects_generic_old_dispute_guidance():
    generic = _row(
        "Toa an nghien cuu quy dinh de giai quyet tot cac tranh chap dat dai."
    )
    direct = _row(
        "Sau 30 ngay ma khong co don tranh chap thi co quan dang ky tiep tuc xu ly."
    )

    ranked, decisions = rank_issue_evidence(
        "Nguoi thua ke khong hop tac; phan biet voi tranh chap chinh thuc",
        [generic, direct],
        relevance_topics=["noncooperation"],
    )

    assert ranked == [direct]
    assert decisions[0]["reason"] == "missing_noncooperation_branch_support"


def test_planning_issue_requires_direct_effect_on_certificate_or_land_rights():
    generic = _row(
        "Dien tich trong ke hoach su dung dat hang nam duoc phe duyet thu hoi."
    )
    direct = _row(
        "Nguoi su dung dat trong quy hoach duoc tiep tuc thuc hien quyen "
        "khi chua co quyet dinh thu hoi dat."
    )

    ranked, decisions = rank_issue_evidence(
        "Thua dat trong quy hoach co duoc cap Giay chung nhan quyen su dung dat",
        [generic, direct],
        relevance_topics=["planning"],
    )

    assert ranked == [direct]
    assert decisions[0]["reason"] == "planning_not_certificate_effect"


def test_purchase_documents_reject_inheritance_only_checklist():
    inheritance = _row(
        "Giay to ve viec nhan thua ke quyen su dung dat theo phap luat dan su."
    )
    purchase = _row(
        "Ho so nhan chuyen quyen gom giay to mua ban va don dang ky dat dai."
    )

    ranked, decisions = rank_issue_evidence(
        "Chung cu ho so mua dat giay tay va nhan chuyen quyen",
        [inheritance, purchase],
    )

    assert ranked == [purchase]
    assert decisions[0]["reason"] == "transaction_type_mismatch_inheritance"


def test_first_registration_deadline_rejects_registration_change_deadline():
    variation = _row(
        "Thoi han nop ho so cap Giay chung nhan cho ben mua la thoi han "
        "dang ky bien dong theo quy dinh."
    )
    first = _row(
        "Thoi han giai quyet dang ky dat dai lan dau la 20 ngay lam viec."
    )

    ranked, decisions = rank_issue_evidence(
        "Thoi han cap Giay chung nhan quyen su dung dat lan dau",
        [variation, first],
    )

    assert ranked == [first]
    assert decisions[0]["reason"] == "procedure_mismatch_first_vs_replacement"


def test_applicant_documents_reject_internal_cadastral_record_inventory():
    internal = _row(
        "Ho so dia chinh duoc lap dang so gom ban do dia chinh, so muc ke "
        "va so dia chinh."
    )

    ranked, decisions = rank_issue_evidence(
        "Chung cu, ho so nguoi mua can nop de cap Giay chung nhan",
        [internal],
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "document_actor_mismatch_internal_cadastral_record"
    )


def test_applicant_documents_reject_internal_form_printing_and_distribution():
    internal = _row(
        "Sở Tài nguyên và Môi trường chỉ định cơ quan làm nhiệm vụ in và "
        "cung cấp tờ khai cho các cơ quan nhận hồ sơ để cấp phát cho người sử dụng đất."
    )

    ranked, decisions = rank_issue_evidence(
        "Chứng cứ, hồ sơ người mua cần nộp để cấp Giấy chứng nhận lần đầu",
        [internal],
        issue_intent="documents",
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "document_actor_mismatch_internal_form_distribution"
    )


def test_applicant_documents_reject_internal_tax_declaration_distribution():
    source = _row(
        "Cấp phát đầy đủ tờ khai các khoản thu liên quan đến nhà, đất theo yêu cầu "
        "của người sử dụng đất và hướng dẫn người sử dụng đất kê khai theo đúng mẫu."
    )

    kept, decisions = rank_issue_evidence(
        "Chứng cứ, hồ sơ và nơi nộp cấp Giấy chứng nhận lần đầu",
        [source],
        issue_intent="documents",
    )

    assert kept == []
    assert decisions[0]["reason"] == (
        "document_actor_mismatch_internal_form_distribution"
    )


def test_applicant_documents_reject_internal_transfer_to_tax_authority():
    source = _row(
        "Trường hợp các giấy tờ chuyển giao cho cơ quan Thuế yêu cầu bản chính, "
        "nếu sử dụng bản sao thì phải có chứng nhận của công chứng nhà nước."
    )

    kept, decisions = rank_issue_evidence(
        "Chứng cứ, hồ sơ và nơi nộp cấp Giấy chứng nhận lần đầu",
        [source],
        issue_intent="documents",
    )

    assert kept == []
    assert decisions[0]["reason"] == "document_actor_mismatch_internal_tax_transfer"


def test_first_registration_documents_require_direct_applicant_support():
    source = _row(
        "Cơ quan chuyên môn kiểm tra tính đầy đủ của giấy tờ và cập nhật dữ liệu quản lý."
    )

    kept, decisions = rank_issue_evidence(
        "Hồ sơ cấp Giấy chứng nhận quyền sử dụng đất lần đầu",
        [source],
        issue_intent="documents",
    )

    assert kept == []
    assert decisions[0]["reason"] == "missing_first_registration_document_support"


def test_first_registration_documents_keep_direct_applicant_checklist():
    source = _row(
        "Hồ sơ gồm đơn đăng ký đất đai và giấy tờ về quyền sử dụng đất; người sử dụng đất nộp tại cơ quan tiếp nhận."
    )

    kept, decisions = rank_issue_evidence(
        "Hồ sơ cấp Giấy chứng nhận quyền sử dụng đất lần đầu",
        [source],
        issue_intent="documents",
    )

    assert kept == [source]
    assert decisions[0]["reason"] == "accepted"


def test_handwritten_transaction_rejects_certificate_issuer_only_text():
    source = _row(
        "Giấy chứng nhận quyền sử dụng đất do cơ quan quản lý đất đai ở Trung ương phát hành."
    )

    kept, decisions = rank_issue_evidence(
        "Giá trị giao dịch giấy viết tay năm 2009",
        [source],
        issue_intent="rule",
        relevance_topics=["handwritten_paper"],
    )

    assert kept == []
    assert decisions[0]["reason"] == "missing_handwritten_transaction_support"


def test_handwritten_transaction_keeps_direct_incomplete_transfer_rule():
    source = _row(
        "Trường hợp nhận chuyển quyền sử dụng đất trước ngày 01 tháng 7 năm 2014 "
        "mà chưa thực hiện thủ tục chuyển quyền thì thực hiện đăng ký đất đai."
    )

    kept, decisions = rank_issue_evidence(
        "Giá trị giao dịch giấy viết tay năm 2009",
        [source],
        issue_intent="rule",
        relevance_topics=["handwritten_paper"],
    )

    assert kept == [source]
    assert decisions[0]["reason"] == "accepted"


def test_processing_deadline_rejects_land_tenure_duration():
    tenure = _row(
        "Thoi han su dung dat tinh tu ngay duoc cap Giay chung nhan."
    )

    ranked, decisions = rank_issue_evidence(
        "Thoi han giai quyet cap Giay chung nhan lan dau bao lau",
        [tenure],
    )

    assert ranked == []
    assert decisions[0]["reason"] == "deadline_type_mismatch_land_tenure"


def test_first_certificate_finance_rejects_generic_land_price_use_catalog():
    generic = _row(
        "Bang gia dat dung de tinh tien thue dat, tinh thue su dung dat, "
        "tinh thue thu nhap, tinh le phi, tinh tien xu phat va tinh gia khoi diem."
    )

    ranked, decisions = rank_issue_evidence(
        "Nghia vu tai chinh va le phi khi cap Giay chung nhan lan dau",
        [generic],
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "finance_context_mismatch_land_price_uses"
    )


def test_purchase_documents_reject_state_allocation_origin_papers():
    allocated = _row(
        "Giay to doi voi truong hop duoc Nha nuoc giao dat, cho thue dat "
        "hoac trung dau gia quyen su dung dat."
    )

    ranked, decisions = rank_issue_evidence(
        "Ho so cho ca nhan mua dat giay tay va nhan chuyen quyen",
        [allocated],
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "transaction_origin_mismatch_state_allocation"
    )


def test_land_deadline_reject_wildlife_license_procedure():
    wildlife = _row(
        "Trong 30 ngay lam viec, cap giay phep mua ban mau vat cua loai hoang da."
    )

    ranked, decisions = rank_issue_evidence(
        "Thoi han cap Giay chung nhan quyen su dung dat lan dau",
        [wildlife],
    )

    assert ranked == []
    assert decisions[0]["reason"] == "field_mismatch_wildlife"


def test_land_finance_reject_state_capital_contribution_guidance():
    capital = _row(
        "Phan von cua Nha nuoc dong gop vao doanh nghiep theo huong dan "
        "cua Bo Tai chinh."
    )

    ranked, decisions = rank_issue_evidence(
        "Nghia vu tai chinh cap Giay chung nhan quyen su dung dat lan dau",
        [capital],
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "finance_field_mismatch_state_capital"
    )


def test_first_certificate_rejects_area_increase_case_with_existing_original_certificate():
    increase = _row(
        "Thua dat goc da co Giay chung nhan; phan dien tich tang them do "
        "nhan chuyen quyen duoc cap cho toan bo thua dat."
    )

    ranked, decisions = rank_issue_evidence(
        "Ca nhan mua dat chua co so do, cap Giay chung nhan lan dau",
        [increase],
    )

    assert ranked == []
    assert decisions[0]["reason"] == "procedure_case_mismatch_area_increase"


def test_purchase_submission_place_rejects_natural_erosion_workflow():
    erosion = _row(
        "Khi giam dien tich thua dat do sat lo tu nhien, Uy ban nhan dan xa "
        "xac nhan va chuyen Van phong dang ky dat dai."
    )

    ranked, decisions = rank_issue_evidence(
        "Ho so va noi nop cho nguoi mua dat nhan chuyen quyen",
        [erosion],
    )

    assert ranked == []
    assert decisions[0]["reason"] == (
        "procedure_case_mismatch_natural_erosion"
    )


def test_explicit_first_certificate_phrase_rejects_area_increase_case():
    ranked, decisions = rank_issue_evidence(
        "cap Giay chung nhan lan dau do mua dat bang giay viet tay",
        [_row("Thua dat goc da co Giay chung nhan, dien tich tang them do nhan chuyen quyen.")],
    )
    assert ranked == []
    assert decisions[0]["reason"] == "procedure_case_mismatch_area_increase"


def test_recovery_compensation_is_not_planning_certificate_evidence():
    ranked, decisions = rank_issue_evidence(
        "dat trong quy hoach co duoc cap Giay chung nhan lan dau hay khong",
        [_row(
            "Khi Nha nuoc thu hoi dat o, ho gia dinh co Giay chung nhan "
            "hoac du dieu kien duoc cap Giay chung nhan thi duoc boi thuong ve dat."
        )],
    )
    assert ranked == []
    assert decisions[0]["reason"] == "planning_case_mismatch_recovery_compensation"


def test_certificate_display_form_is_not_applicant_document_evidence():
    ranked, decisions = rank_issue_evidence(
        "ho so chung cu va noi nop cap Giay chung nhan lan dau",
        [_row("Noi dung va hinh thuc the hien thong tin tren Giay chung nhan theo Mau so 04/DK-GCN.")],
    )
    assert ranked == []
    assert decisions[0]["reason"] == "document_case_mismatch_certificate_display"


def test_mixed_construction_and_dispute_topics_do_not_cross_reject():
    construction = _row("Nguoi co tham quyen lap bien ban vi pham trat tu xay dung.")
    dispute = _row("Tranh chap ranh gioi dat dai duoc hoa giai tai UBND cap xa.")

    construction_ranked, _ = rank_issue_evidence(
        "Tham quyen lap bien ban xay dung",
        [construction, dispute],
        issue_intent="authority",
        relevance_topics=["construction_authority"],
    )

    dispute_ranked, _ = rank_issue_evidence(
        "Giai quyet tranh chap ranh gioi",
        [construction, dispute],
        issue_intent="dispute",
        relevance_topics=["land_boundary_dispute"],
    )

    assert construction in construction_ranked
    assert dispute in dispute_ranked


def test_first_instance_complaint_rejects_second_instance_only_evidence():
    second_instance = _row(
        "Người có thẩm quyền giải quyết khiếu nại lần hai xem xét nội dung "
        "khiếu nại và ban hành quyết định giải quyết khiếu nại lần hai."
    )
    first_instance = _row(
        "Người đã ra quyết định hành chính có thẩm quyền giải quyết "
        "khiếu nại lần đầu đối với quyết định đó."
    )

    ranked, decisions = rank_issue_evidence(
        "Ai có thẩm quyền giải quyết khiếu nại lần đầu đối với quyết định hành chính?",
        [second_instance, first_instance],
        issue_intent="authority",
    )

    assert ranked == [first_instance]
    assert decisions[0]["reason"] == "complaint_stage_mismatch_second_instance"


def test_general_building_permit_rejects_time_limited_permit_only_evidence():
    time_limited = _row(
        "Điều kiện cấp giấy phép xây dựng có thời hạn gồm công trình thuộc "
        "khu vực có quy hoạch phân khu xây dựng."
    )
    ordinary = _row(
        "Ủy ban nhân dân cấp huyện cấp giấy phép xây dựng đối với nhà ở "
        "riêng lẻ trên địa bàn do mình quản lý."
    )

    ranked, decisions = rank_issue_evidence(
        "Xây nhà ở riêng lẻ cần xin giấy phép xây dựng tại cơ quan nào?",
        [time_limited, ordinary],
        issue_intent="authority",
    )

    assert ranked == [ordinary]
    assert decisions[0]["reason"] == "permit_type_mismatch_time_limited"


def test_initial_temporary_residence_rejects_renewal_deadline_only_evidence():
    renewal = _row(
        "Trong thời hạn 15 ngày trước ngày kết thúc thời hạn tạm trú đã "
        "đăng ký, công dân phải làm thủ tục gia hạn tạm trú."
    )
    initial = _row(
        "Trong thời hạn 03 ngày làm việc kể từ ngày nhận được hồ sơ đầy đủ "
        "và hợp lệ, cơ quan đăng ký cư trú cập nhật thông tin tạm trú."
    )

    ranked, decisions = rank_issue_evidence(
        "Đăng ký tạm trú lần đầu được giải quyết trong thời hạn bao lâu?",
        [renewal, initial],
        issue_intent="deadline",
    )

    assert ranked == [initial]
    assert decisions[0]["reason"] == "residence_case_mismatch_renewal"


def test_general_residence_documents_reject_minor_only_requirement():
    minor_only = _row(
        "Đối với người đăng ký tạm trú là người chưa thành niên thì trong "
        "tờ khai phải ghi rõ ý kiến đồng ý của cha, mẹ hoặc người giám hộ."
    )
    general = _row(
        "Hồ sơ đăng ký tạm trú gồm tờ khai thay đổi thông tin cư trú và "
        "giấy tờ chứng minh chỗ ở hợp pháp."
    )

    ranked, decisions = rank_issue_evidence(
        "Người dân đăng ký tạm trú cần hồ sơ giấy tờ gì?",
        [minor_only, general],
        issue_intent="documents",
    )

    assert ranked == [general]
    assert decisions[0]["reason"] == "subject_mismatch_minor_only"


def test_issuing_building_permit_rejects_revocation_only_provision():
    revocation = _row(
        "Trường hợp cơ quan có thẩm quyền cấp giấy phép xây dựng không thu "
        "hồi giấy phép đã cấp không đúng quy định thì Ủy ban nhân dân cấp "
        "tỉnh quyết định thu hồi giấy phép xây dựng."
    )
    issuance = _row(
        "Ủy ban nhân dân cấp huyện cấp giấy phép xây dựng đối với nhà ở "
        "riêng lẻ thuộc địa bàn do mình quản lý."
    )

    ranked, decisions = rank_issue_evidence(
        "Điều kiện xin cấp giấy phép xây dựng nhà ở riêng lẻ là gì?",
        [revocation, issuance],
        issue_intent="condition",
    )

    assert ranked == [issuance]
    assert decisions[0]["reason"] == "permit_case_mismatch_revocation"


def test_general_marital_status_deadline_rejects_prior_residence_reply_branch():
    special_branch = _row(
        "Ngay trong ngày nhận được văn bản trả lời, nếu thấy đủ cơ sở, "
        "Ủy ban nhân dân cấp xã cấp Giấy xác nhận tình trạng hôn nhân."
    )
    general = _row(
        "Trong thời hạn 03 ngày làm việc kể từ ngày nhận đủ hồ sơ hợp lệ, "
        "công chức tư pháp - hộ tịch kiểm tra, xác minh tình trạng hôn nhân."
    )

    ranked, decisions = rank_issue_evidence(
        "Thời hạn giải quyết cấp Giấy xác nhận tình trạng hôn nhân bao lâu?",
        [special_branch, general],
        issue_intent="deadline",
    )

    assert ranked == [general]
    assert decisions[0]["reason"] == "deadline_case_mismatch_reply_branch"


def test_general_citizen_rejects_foreign_national_only_branch():
    foreign_only = _row(
        "Quy định này cũng được áp dụng để cấp Giấy xác nhận tình trạng "
        "hôn nhân cho công dân nước ngoài và người không quốc tịch cư trú "
        "tại Việt Nam."
    )
    general = _row(
        "Ủy ban nhân dân cấp xã nơi thường trú của công dân Việt Nam thực "
        "hiện việc cấp Giấy xác nhận tình trạng hôn nhân."
    )

    ranked, decisions = rank_issue_evidence(
        "Điều kiện cấp Giấy xác nhận tình trạng hôn nhân cho người dân?",
        [foreign_only, general],
        issue_intent="condition",
    )

    assert ranked == [general]
    assert decisions[0]["reason"] == "subject_mismatch_foreign_only"


def test_building_permit_rejects_internal_consultation_as_user_deadline():
    internal = _row(
        "Trong thời gian 12 ngày kể từ ngày nhận được hồ sơ, các cơ quan "
        "quản lý nhà nước được hỏi ý kiến có trách nhiệm trả lời bằng văn bản."
    )
    overall = _row(
        "Trong thời hạn 20 ngày kể từ ngày nhận đủ hồ sơ hợp lệ, cơ quan có "
        "thẩm quyền xem xét cấp giấy phép xây dựng nhà ở riêng lẻ."
    )

    ranked, decisions = rank_issue_evidence(
        "Thời hạn giải quyết cấp giấy phép xây dựng nhà ở riêng lẻ bao lâu?",
        [internal, overall],
        issue_intent="deadline",
    )

    assert ranked == [overall]
    assert decisions[0]["reason"] == "procedure_actor_mismatch_internal_consultation"


def test_initial_temporary_residence_rejects_temporary_absence_procedure():
    absence = _row(
        "Trước khi đi khỏi nơi cư trú, người đó phải đến khai báo tạm vắng "
        "tại cơ quan đăng ký cư trú nơi người đó cư trú."
    )
    registration = _row(
        "Người đăng ký tạm trú nộp hồ sơ đến cơ quan đăng ký cư trú nơi "
        "mình dự kiến tạm trú."
    )

    ranked, decisions = rank_issue_evidence(
        "Đăng ký tạm trú nộp hồ sơ tại cơ quan nào?",
        [absence, registration],
        issue_intent="authority",
    )

    assert ranked == [registration]
    assert decisions[0]["reason"] == "residence_case_mismatch_temporary_absence"


def test_registration_processing_deadline_rejects_residence_duration():
    duration = _row(
        "Thời hạn tạm trú tối đa là 02 năm và có thể tiếp tục gia hạn nhiều lần."
    )
    processing = _row(
        "Trong thời hạn 03 ngày làm việc kể từ ngày nhận được hồ sơ đầy đủ "
        "và hợp lệ, cơ quan đăng ký cư trú cập nhật thông tin tạm trú."
    )

    ranked, decisions = rank_issue_evidence(
        "Thời hạn giải quyết đăng ký tạm trú là bao lâu?",
        [duration, processing],
        issue_intent="deadline",
    )

    assert ranked == [processing]
    assert decisions[0]["reason"] == "deadline_type_mismatch_residence_duration"


def test_rule_issue_rejects_form_template_body():
    template = _row(
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM Độc lập - Tự do - Hạnh phúc "
        "TỜ KHAI xác nhận tình trạng chỗ ở hợp pháp Kính gửi: ..."
    )
    rule = _row(
        "Công dân đến sinh sống tại chỗ ở hợp pháp từ 30 ngày trở lên thì "
        "phải thực hiện đăng ký tạm trú."
    )

    ranked, decisions = rank_issue_evidence(
        "Quy định đăng ký tạm trú và căn cứ pháp lý",
        [template, rule],
        issue_intent="rule",
    )

    assert ranked == [rule]
    assert decisions[0]["reason"] == "presentation_mismatch_form_template"


def test_private_house_permit_rejects_social_housing_project_deadline():
    social_housing = _row(
        "Cơ quan có thẩm quyền cấp giấy phép xây dựng cấp giấy phép trong thời hạn "
        "30 ngày kể từ ngày nhận đủ hồ sơ hợp lệ."
    )
    social_housing["document_title"] = (
        "Quy định cơ chế, chính sách đặc thù phát triển nhà ở xã hội"
    )
    private_house = _row(
        "Kể từ ngày nhận đủ hồ sơ hợp lệ, cơ quan có thẩm quyền xem xét cấp giấy "
        "phép xây dựng trong thời gian 15 ngày đối với nhà ở riêng lẻ."
    )

    ranked, decisions = rank_issue_evidence(
        "Thời hạn cấp giấy phép xây dựng nhà ở riêng lẻ bao lâu?",
        [social_housing, private_house],
        issue_intent="deadline",
    )

    assert ranked == [private_house]
    assert decisions[0]["reason"] == "permit_project_mismatch_social_housing"


def test_condition_issue_rejects_dossier_composition_without_condition_rule():
    dossier = _row(
        "Hồ sơ đề nghị cấp giấy phép xây dựng mới đối với nhà ở riêng lẻ gồm đơn "
        "đề nghị, giấy tờ về đất đai và bản vẽ thiết kế xây dựng."
    )
    condition = _row(
        "Điều kiện cấp giấy phép xây dựng đối với nhà ở riêng lẻ gồm phù hợp mục "
        "đích sử dụng đất và bảo đảm an toàn cho công trình lân cận."
    )

    ranked, decisions = rank_issue_evidence(
        "Điều kiện cấp giấy phép xây dựng nhà ở riêng lẻ là gì?",
        [dossier, condition],
        issue_intent="condition",
    )

    assert ranked == [condition]
    assert decisions[0]["reason"] == "facet_mismatch_dossier_for_condition"


def test_identity_query_rejects_residence_law_and_residence_only_content():
    residence_law = {
        "chunk_id": "res-law-1",
        "law_number": "68/2020/QH14",
        "document_title": "Luật Cư trú",
        "content": "Điều kiện đăng ký thường trú tại chỗ ở hợp pháp do thuê, mượn, ở nhờ.",
        "score": 1.0,
        "authority_rank": 90,
        "authority_confidence": "verified",
        "effective_status": "active",
    }
    residence_only = {
        "chunk_id": "res-content-1",
        "law_number": "62/2021/TT-BCA",
        "document_title": "Thông tư cư trú",
        "content": "Hồ sơ đăng ký tạm trú, xóa đăng ký thường trú bao gồm tờ khai thay đổi thông tin.",
        "score": 1.0,
        "authority_rank": 70,
        "authority_confidence": "verified",
        "effective_status": "active",
    }
    identity_card = {
        "chunk_id": "id-card-1",
        "law_number": "26/2023/QH15",
        "document_title": "Luật Căn cước",
        "content": "Thủ tục cấp đổi thẻ căn cước công dân và cập nhật thông tin thẻ căn cước.",
        "score": 1.0,
        "authority_rank": 90,
        "authority_confidence": "verified",
        "effective_status": "active",
    }

    ranked, decisions = rank_issue_evidence(
        "Thủ tục cấp đổi thẻ căn cước cần giấy tờ gì",
        [residence_law, residence_only, identity_card],
        issue_intent="documents",
    )

    assert ranked == [identity_card]
    assert {item["reason"] for item in decisions if item["decision"] == "reject"} == {
        "residence_law_mismatch_without_residence_intent",
    }


def test_query_without_residence_intent_rejects_residence_law_even_in_same_domain():
    residence_law = {
        "chunk_id": "res-law-2",
        "law_number": "68/2020/QH14",
        "document_title": "Luật Cư trú",
        "content": "Quyền của công dân về việc tự do cư trú trên lãnh thổ Việt Nam.",
        "score": 1.0,
        "authority_rank": 90,
        "authority_confidence": "verified",
        "effective_status": "active",
    }
    civil_status = {
        "chunk_id": "cs-1",
        "law_number": "60/2014/QH13",
        "document_title": "Luật Hộ tịch",
        "content": "Thủ tục cấp Giấy xác nhận tình trạng hôn nhân tại Ủy ban nhân dân cấp xã.",
        "score": 1.0,
        "authority_rank": 90,
        "authority_confidence": "verified",
        "effective_status": "active",
    }

    ranked, decisions = rank_issue_evidence(
        "Thủ tục cấp giấy xác nhận tình trạng hôn nhân",
        [residence_law, civil_status],
        issue_intent="procedure",
    )

    assert ranked == [civil_status]
    assert decisions[0]["reason"] == "residence_law_mismatch_without_residence_intent"


def test_domain_guard_reads_domain_from_current_candidate_row():
    birth_registration = {
        "chunk_id": "birth-registration-domain-1",
        "document_title": "Quy định đăng ký khai sinh",
        "content": "Đăng ký khai sinh cho trẻ em tại Ủy ban nhân dân cấp xã.",
        "domain": "ho_tich_chung_thuc",
        "score": 1.0,
        "authority_rank": 90,
        "authority_confidence": "verified",
        "effective_status": "active",
    }

    ranked, decisions = rank_issue_evidence(
        "Đăng ký khai sinh cho trẻ em cần làm gì?",
        [birth_registration],
        issue_intent="procedure",
        relevance_topics=["khai_sinh"],
    )

    assert ranked == [birth_registration]
    assert decisions[0]["decision"] == "keep"
