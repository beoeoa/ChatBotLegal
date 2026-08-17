from scripts.discover_official_procedure_sources import (
    classify_form_source,
    collect_profile_components,
    procedure_queries,
    score_formality_candidate,
    select_legacy_form_candidate,
    select_formality_candidate,
)


def test_exact_current_ward_formality_wins_over_unrelated_result():
    procedure = {
        "procedure_id": "dang_ky_khai_sinh",
        "name": "Đăng ký khai sinh",
        "aliases": ["khai sinh"],
        "domain": "ho_tich_chung_thuc",
    }
    candidates = [
        {
            "id": "wrong",
            "name": "Đăng ký khai tử",
            "code": "1.000001",
            "categories": ["Hộ tịch"],
            "departments": ["Ủy ban nhân dân cấp xã"],
            "state": "ACTIVE",
        },
        {
            "id": "right",
            "name": "Thủ tục đăng ký khai sinh",
            "code": "1.001193",
            "categories": ["Hộ tịch"],
            "departments": ["Ủy ban nhân dân cấp xã"],
            "state": "UPDATED",
        },
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "right"
    assert selected["match_score"] >= 0.8
    assert score_formality_candidate(procedure, candidates[1]) > (
        score_formality_candidate(procedure, candidates[0])
    )


def test_profile_component_collection_and_package_page_classification():
    detail = {
        "executionCases": [
            {
                "profileComponents": [
                    {
                        "name": "Tờ khai đăng ký khai sinh theo mẫu",
                        "code": "KQ-01",
                        "hasElectronicForm": False,
                        "attachments": [],
                    }
                ]
            }
        ],
        "profileComponents": [
            {
                "name": "Giấy chứng sinh",
                "code": "KQ-02",
                "hasElectronicForm": False,
                "attachments": [],
            }
        ],
    }
    form = {
        "canonical_name": "Tờ khai đăng ký khai sinh",
        "form_code": None,
    }

    components = collect_profile_components(detail)
    finding = classify_form_source(
        form=form,
        components=components,
        source_page="https://dichvucong.gov.vn/thu-tuc-hanh-chinh/right",
    )

    assert len(components) == 2
    assert finding["status"] == "OFFICIAL_PACKAGE_PAGE"
    assert finding["review_status"] == "candidate_pending_review"
    assert finding["approved"] is False
    assert finding["matched_component_code"] == "KQ-01"


def test_missing_component_is_verified_no_public_download_not_approval():
    finding = classify_form_source(
        form={"canonical_name": "Mẫu không có trong thủ tục"},
        components=[
            {
                "name": "Giấy chứng sinh",
                "code": "KQ-02",
                "hasElectronicForm": False,
                "attachments": [],
            }
        ],
        source_page="https://dichvucong.gov.vn/thu-tuc-hanh-chinh/right",
    )

    assert finding["status"] == "NO_PUBLIC_DOWNLOAD_VERIFIED"
    assert finding["approved"] is False
    assert finding["review_status"] == "candidate_pending_review"


def test_procedure_queries_include_short_alias_for_portal_search():
    queries = procedure_queries(
        {
            "procedure_id": "dang_ky_ket_hon",
            "name": "Đăng ký kết hôn trong nước",
            "aliases": ["kết hôn", "làm giấy kết hôn"],
        }
    )

    assert "Đăng ký kết hôn trong nước" in queries
    assert "kết hôn" in queries
    assert len(queries) <= 5


def test_procedure_queries_include_deterministic_official_title_alias():
    queries = procedure_queries(
        {
            "procedure_id": "cap_doi_giay_chung_nhan_dat",
            "name": "Cap doi GCNQSDD",
            "aliases": ["cap doi giay chung nhan dat"],
        }
    )

    assert (
        "cap doi giay chung nhan quyen su dung dat"
        in [query.casefold() for query in queries]
    )
    assert len(queries) <= 5


def test_procedure_queries_cover_reviewed_current_official_titles():
    expected = {
        "dang_ky_nuoi_con_nuoi": (
            "dang ky viec nuoi con nuoi trong nuoc"
        ),
        "ho_tro_nguoi_khuyet_tat": (
            "xac dinh xac dinh lai muc do khuyet tat va cap "
            "giay xac nhan khuyet tat"
        ),
        "chung_thuc_hop_dong_giao_dich": (
            "chung thuc giao dich lien quan den tai san la dong san "
            "quyen su dung dat nha o"
        ),
    }

    for procedure_id, official_title in expected.items():
        queries = procedure_queries(
            {
                "procedure_id": procedure_id,
                "name": procedure_id.replace("_", " "),
                "aliases": [],
            }
        )
        assert official_title in [query.casefold() for query in queries]


def test_candidate_selection_uses_reviewed_alias_for_coverage_gate():
    procedure = {
        "procedure_id": "cap_phieu_ly_lich_tu_phap",
        "name": "Huong dan cap Phieu ly lich tu phap",
        "aliases": ["Cap Phieu ly lich tu phap"],
        "domain": "ho_tich_chung_thuc",
    }
    candidates = [
        {
            "id": "official-procedure",
            "name": "Thu tuc cap Phieu ly lich tu phap",
            "code": "2.000488",
            "categories": ["Ly lich tu phap"],
            "departments": ["So Tu phap"],
            "state": "UPDATED",
        }
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "official-procedure"


def test_exact_action_beats_delete_or_renew_variant():
    procedure = {
        "procedure_id": "dang_ky_tam_tru",
        "name": "Dang ky tam tru",
        "aliases": ["tam tru"],
        "domain": "cu_tru_an_ninh",
        "authority_level": "commune",
        "jurisdiction": "Hai Phong",
    }
    candidates = [
        {
            "id": "delete",
            "name": "Xoa dang ky tam tru",
            "code": "1.010028",
            "categories": ["Dang ky, quan ly cu tru"],
            "departments": ["Cong an cap Xa"],
            "departmentPromulgate": "Bo Cong an",
            "state": "UPDATED",
        },
        {
            "id": "exact",
            "name": "Dang ky tam tru",
            "code": "1.004194",
            "categories": ["Dang ky, quan ly cu tru"],
            "departments": ["Cong an cap Xa"],
            "departmentPromulgate": "Bo Cong an",
            "state": "UPDATED",
        },
        {
            "id": "renew",
            "name": "Gia han tam tru",
            "code": "1.002755",
            "categories": ["Dang ky, quan ly cu tru"],
            "departments": ["Cong an cap Xa"],
            "departmentPromulgate": "Bo Cong an",
            "state": "UPDATED",
        },
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "exact"


def test_exact_foreign_birth_beats_reregistration_variant():
    procedure = {
        "procedure_id": "dang_ky_khai_sinh_nuoc_ngoai",
        "name": "Dang ky khai sinh co yeu to nuoc ngoai",
        "aliases": ["khai sinh co yeu to nuoc ngoai"],
        "domain": "ho_tich_chung_thuc",
    }
    candidates = [
        {
            "id": "reregister",
            "name": "Thu tuc dang ky lai khai sinh co yeu to nuoc ngoai",
            "code": "2.000522",
            "categories": ["Ho tich"],
            "departments": ["Uy ban nhan dan cap xa"],
            "state": "UPDATED",
        },
        {
            "id": "exact",
            "name": "Thu tuc dang ky khai sinh co yeu to nuoc ngoai",
            "code": "2.000528",
            "categories": ["Ho tich"],
            "departments": ["Uy ban nhan dan cap xa"],
            "state": "UPDATED",
        },
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "exact"


def test_hai_phong_candidate_breaks_same_name_province_tie():
    procedure = {
        "procedure_id": "tach_thua_dat",
        "name": "Tach thua dat",
        "aliases": [],
        "domain": "dat_dai_xay_dung",
        "jurisdiction": "Hai Phong",
    }
    candidates = [
        {
            "id": "other-province",
            "name": "Tach thua dat, hop thua dat",
            "code": "1.115270",
            "categories": ["Dat dai"],
            "departmentPromulgate": "UBND tinh Dak Lak",
            "state": "ACTIVE",
        },
        {
            "id": "hai-phong",
            "name": "Tach thua dat, hop thua dat",
            "code": "1.115614",
            "categories": ["Dat dai"],
            "departmentPromulgate": "UBND Thanh pho Hai Phong",
            "state": "ACTIVE",
        },
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "hai-phong"


def test_first_explicit_slash_branch_can_match_official_procedure():
    procedure = {
        "procedure_id": "tach_ho_khau_cu_tru",
        "name": "Tach ho / dieu chinh thong tin cu tru",
        "aliases": [],
        "domain": "cu_tru_an_ninh",
    }
    candidates = [
        {
            "id": "split-household",
            "name": "Tach ho",
            "code": "1.010038",
            "categories": ["Dang ky, quan ly cu tru"],
            "departments": ["Cong an cap Xa"],
            "state": "UPDATED",
        }
    ]

    selected = select_formality_candidate(procedure, candidates)

    assert selected is not None
    assert selected["id"] == "split-household"


def test_legacy_candidate_requires_same_domain_and_official_source():
    form = {
        "canonical_name": "Tờ khai đăng ký khai sinh",
        "domain": "ho_tich_chung_thuc",
    }
    selected = select_legacy_form_candidate(
        form,
        [
            {
                "id": "wrong-domain",
                "form_title": "Tờ khai đăng ký khai sinh",
                "domain": "dat_dai_xay_dung",
                "source_page_url": "https://haiphong.gov.vn/wrong",
                "has_official_file": True,
            },
            {
                "id": "unofficial",
                "form_title": "Tờ khai đăng ký khai sinh",
                "domain": "ho_tich",
                "source_page_url": "https://local.invalid/source",
                "has_official_file": True,
            },
            {
                "id": "right",
                "form_title": "Mẫu tờ khai đăng ký khai sinh",
                "domain": "ho_tich",
                "source_page_url": "https://haiphong.gov.vn/right",
                "has_official_file": True,
            },
        ],
    )

    assert selected is not None
    assert selected["id"] == "right"


def test_candidate_selection_rejects_wrong_subtype_and_ministry_scope():
    adoption = {
        "procedure_id": "dang_ky_nuoi_con_nuoi",
        "name": "Đăng ký nuôi con nuôi",
        "aliases": [],
        "domain": "ho_tich_chung_thuc",
    }
    complaint = {
        "procedure_id": "khieu_nai_hanh_chinh",
        "name": "Khiếu nại hành chính",
        "aliases": ["khiếu nại"],
        "domain": "khieu_nai_to_cao_xu_phat",
    }

    assert (
        select_formality_candidate(
            adoption,
            [
                {
                    "id": "foreign",
                    "name": (
                        "Ghi vào Sổ đăng ký nuôi con nuôi việc nuôi con nuôi "
                        "đã được giải quyết tại cơ quan nước ngoài"
                    ),
                    "categories": ["Nuôi con nuôi"],
                    "state": "UPDATED",
                }
            ],
        )
        is None
    )
    assert (
        select_formality_candidate(
            complaint,
            [
                {
                    "id": "military",
                    "name": "Giải quyết đơn khiếu nại lần đầu cấp Bộ Quốc phòng",
                    "categories": ["Khiếu nại BQP"],
                    "departmentPromulgate": "Bộ Quốc phòng",
                    "state": "ACTIVE",
                }
            ],
        )
        is None
    )
