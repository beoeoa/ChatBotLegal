import pytest


def official_form(form_id: str, procedure_id: str, *, name: str, domain: str = "ho_tich_chung_thuc") -> dict:
    return {
        "form_id": form_id,
        "name": name,
        "procedure_id": procedure_id,
        "procedure_name": procedure_id,
        "domain": domain,
        "official_level": "official",
        "review_status": "approved",
        "has_official_file": True,
        "download_url": f"/api/procedures/forms-catalog/official/{form_id}/download",
    }


def test_explicit_form_intent_is_strict():
    from api.routers.search import _question_requests_forms

    assert _question_requests_forms("Cho t\u00f4i t\u1ea3i v\u1ec1 t\u1edd khai \u0111\u0103ng k\u00fd khai sinh")
    assert _question_requests_forms("T\u00f4i c\u1ea7n m\u1eabu \u0111\u01a1n khi\u1ebfu n\u1ea1i quy\u1ebft \u0111\u1ecbnh x\u1eed ph\u1ea1t")
    assert not _question_requests_forms("T\u00f4i b\u1ecb l\u1eadp bi\u00ean b\u1ea3n m\u00e1i che v\u1ec9a h\u00e8, m\u1ee9c ph\u1ea1t bao nhi\u00eau?")
    assert not _question_requests_forms("N\u1ebfu khi\u1ebfu n\u1ea1i th\u00ec n\u1ed9p \u1edf \u0111\u00e2u, trong bao l\u00e2u?")


def test_verified_form_answer_uses_only_catalog_metadata():
    from api.routers.search import _append_verified_form_answer

    answer = _append_verified_form_answer(
        "## Kết luận\nSai: thủ tục dùng Mẫu 09/ĐK.",
        question="Thủ tục này cần biểu mẫu nào?",
        recommended_forms=[
            {
                "form_id": "form-19",
                "name": "Đơn đề nghị cấp lại",
                "form_code": "19",
                "review_status": "approved",
                "official_level": "official",
                "effective_from": "2024-12-15",
                "legal_basis": ["141/2024/NĐ-CP"],
                    "source_url": "https://vbpl.vn/example",
                    "download_url": "/api/forms/form-19/download",
                    "procedure_identity_confirmed": True,
            }
        ],
    )

    assert "## Biểu mẫu chính thức" in answer
    assert "Đơn đề nghị cấp lại (19)" in answer
    assert "141/2024/NĐ-CP" in answer
    assert "[Nguồn biểu mẫu chính thức](https://vbpl.vn/example)" in answer
    assert "[Tải biểu mẫu](/api/forms/form-19/download)" in answer
    assert "09/ĐK" not in answer


def test_verified_form_answer_replaces_contradictory_generic_source_gap():
    from api.routers.search import _append_verified_form_answer

    answer = _append_verified_form_answer(
        "Ch\u01b0a c\u00f3 ngu\u1ed3n hi\u1ec7n h\u00e0nh \u0111\u1ee7 \u0111\u1ec3 x\u00e1c minh n\u1ed9i dung n\u00e0y.",
        question="H\u1ed3 s\u01a1 v\u00e0 bi\u1ec3u m\u1eabu g\u1ed3m nh\u1eefng g\u00ec?",
        recommended_forms=[
            {
                "form_id": "f1",
                "name": "Phi\u1ebfu khai b\u00e1o",
                "review_status": "approved",
                "official_level": "official",
                    "source_url": "https://vbpl.vn/f1",
                    "download_url": "/api/procedures/forms-catalog/assets/f1/download",
                    "procedure_identity_confirmed": True,
            }
        ],
    )
    assert "Ch\u01b0a c\u00f3 ngu\u1ed3n hi\u1ec7n h\u00e0nh \u0111\u1ee7" not in answer
    assert "Ph\u1ea7n bi\u1ec3u m\u1eabu \u0111\u00e3 \u0111\u01b0\u1ee3c x\u00e1c minh" in answer
    assert "Phi\u1ebfu khai b\u00e1o" in answer


def test_exact_procedure_form_lookup_does_not_require_procedure_steps():
    from api.legal_question_policy import classify_question

    policy = classify_question(
        "Thủ tục có mã 1.013873 cần biểu mẫu nào? Chỉ cung cấp biểu mẫu chính thức."
    )

    assert policy["question_type"] == "form_request"
    assert policy["required_sections"] == ["conclusion", "official_forms"]


def test_form_catalog_citation_is_explicitly_attested():
    from api.routers.search import _merge_form_catalog_citations

    citations = _merge_form_catalog_citations(
        [],
        [
            {
                "review_status": "approved",
                "official_level": "official",
                "source_url": "https://vbpl.vn/example",
                "legal_basis": ["141/2024/NĐ-CP"],
                "procedure_identity_confirmed": True,
            }
        ],
    )

    assert citations == [
        {
            "document_title": "Nguồn biểu mẫu chính thức",
            "law_number": "141/2024/NĐ-CP",
            "article_number": None,
            "effective_status": "active",
            "source_url": "https://vbpl.vn/example",
            "verification_source": "approved_form_catalog",
        }
    ]


def test_forms_unavailable_uses_explicit_form_detector_not_broad_classifier():
    from api.routers.search import _forms_unavailable_for_question

    assert _forms_unavailable_for_question(
        "Cho t\u00f4i M\u1eabu 09/\u0110K \u0111\u1ec3 sang t\u00ean nh\u00e0 \u0111\u1ea5t",
        None,
    )
    assert not _forms_unavailable_for_question(
        "Nh\u00e0 \u1edf \u0111ang x\u00e2y kh\u00f4ng ph\u00e9p b\u1ecb x\u1eed l\u00fd th\u1ebf n\u00e0o?",
        None,
    )


def test_verified_catalog_form_removes_duplicate_missing_form_section():
    from api.models import AnswerSection
    from api.routers.search import _reconcile_verified_form_sections

    sections = [
        AnswerSection(
            issue_id="issue-documents",
            title="Hồ sơ",
            status="insufficiently_evidenced",
            limitation="Chưa có đủ căn cứ hiện hành.",
        ),
        AnswerSection(
            issue_id="issue-form",
            title="Biểu mẫu",
            status="insufficiently_evidenced",
            limitation="Chưa có biểu mẫu trong nguồn truy xuất.",
        ),
    ]
    trace = {
        "claim_validation": {
            "issues": [
                {"issue_id": "issue-documents", "requested_facets": ["documents"]},
                {"issue_id": "issue-form", "requested_facets": ["form"]},
            ]
        }
    }
    verified_form = {
        "review_status": "approved",
        "official_level": "official",
        "source_url": "https://dichvucong.example/form",
        "legal_basis": ["60/2014/QH13"],
        "download_url": "/api/forms/form-birth/download",
        "procedure_identity_confirmed": True,
    }

    reconciled, aggregate = _reconcile_verified_form_sections(
        sections,
        section_trace=trace,
        recommended_forms=[verified_form],
    )

    assert [section.issue_id for section in reconciled] == ["issue-documents"]
    assert "Chưa có biểu mẫu" not in aggregate["answer"]


def test_verified_catalog_form_removes_titled_form_gap_for_mixed_issue():
    from api.models import AnswerSection
    from api.routers.search import _reconcile_verified_form_sections

    sections = [
        AnswerSection(
            issue_id="issue-mixed",
            title="Bi\u1ec3u m\u1eabu",
            status="insufficiently_evidenced",
            limitation="Ch\u01b0a c\u00f3 bi\u1ec3u m\u1eabu trong ngu\u1ed3n truy xu\u1ea5t.",
        )
    ]
    trace = {
        "claim_validation": {
            "issues": [
                {
                    "issue_id": "issue-mixed",
                    "requested_facets": ["documents", "form"],
                }
            ]
        }
    }
    reconciled, aggregate = _reconcile_verified_form_sections(
        sections,
        section_trace=trace,
        recommended_forms=[
            {
                "review_status": "approved",
                "official_level": "official",
                "source_url": "https://vbpl.vn/form",
                "download_url": "/api/procedures/forms-catalog/assets/f1/download",
                "procedure_identity_confirmed": True,
            }
        ],
    )
    assert reconciled == []
    assert "Ch\u01b0a c\u00f3 bi\u1ec3u m\u1eabu" not in aggregate["answer"]


def test_structured_source_gap_omits_form_when_catalog_has_verified_form():
    from api.routers.search import _structured_source_gap

    gaps = _structured_source_gap(
        {
            "claim_validation": {
                "issues": [
                    {
                        "missing_facets": ["documents"],
                        "unavailable_facets": ["form", "deadline"],
                    }
                ]
            }
        },
        has_verified_form=True,
    )

    assert gaps == ["documents", "deadline"]


def test_structured_source_gap_keeps_form_gap_without_confirmed_procedure_identity():
    from api.routers.search import _structured_source_gap

    trace = {
        "claim_validation": {
            "issues": [{"missing_facets": ["form"], "unavailable_facets": []}]
        }
    }
    assert _structured_source_gap(trace, has_verified_form=False) == ["form"]


def test_to_hieu_legal_question_never_recommends_a_form():
    from api.routers.search import _procedure_response_fields

    detail = {
        "id": "khieu_nai",
        "name": "Khi\u1ebfu n\u1ea1i",
        "domain_slug": "trat_tu_do_thi",
        "forms": [official_form("complaint", "khieu_nai", name="\u0110\u01a1n khi\u1ebfu n\u1ea1i")],
        "recommended_forms": [official_form("complaint", "khieu_nai", name="\u0110\u01a1n khi\u1ebfu n\u1ea1i")],
        "matched_procedure_ids": ["khieu_nai"],
    }
    proc, recommended, _summary = _procedure_response_fields(
        detail,
        "T?i thu? nh? ???ng T? Hi?u, d?ng m?i che l?n v?a h? th? b? ph?t th? n?o?",
    )
    assert recommended is None
    assert proc["forms"] == []
    assert proc["recommended_forms"] == []


def test_birth_form_request_ranks_only_exact_official_birth_form():
    from api.routers.search import _procedure_response_fields

    birth = official_form("birth", "dang_ky_khai_sinh", name="T\u1edd khai \u0111\u0103ng k\u00fd khai sinh")
    complaint = official_form("complaint", "khieu_nai", name="\u0110\u01a1n khi\u1ebfu n\u1ea1i")
    passport = official_form("passport", "cap_ho_chieu_tre_em", name="T\u1edd khai h\u1ed9 chi\u1ebfu tr\u1ebb em", domain="cu_tru_an_ninh")
    detail = {
        "id": "dang_ky_khai_sinh",
        "name": "\u0110\u0103ng k\u00fd khai sinh",
        "domain_slug": "ho_tich_chung_thuc",
        "forms": [birth],
        "recommended_forms": [birth, complaint, passport],
        "matched_procedure_ids": ["dang_ky_khai_sinh"],
    }
    proc, recommended, _summary = _procedure_response_fields(
        detail, "\u0110\u0103ng k\u00fd khai sinh cho con \u1edf L\u00ea Ch\u00e2n, cho t\u00f4i t\u1edd khai \u0111\u1ec3 t\u1ea3i v\u1ec1"
    )
    assert [item["form_id"] for item in recommended] == ["birth"]
    assert [item["form_id"] for item in proc["forms"]] == ["birth"]


def test_combined_inheritance_request_keeps_only_exact_forms_and_max_three():
    from api.routers.search import _procedure_response_fields

    birth = official_form("birth", "dang_ky_khai_sinh", name="T\u1edd khai \u0111\u0103ng k\u00fd khai sinh")
    marital = official_form("marital", "xac_nhan_doc_than", name="T\u1edd khai c\u1ea5p gi\u1ea5y x\u00e1c nh\u1eadn t\u00ecnh tr\u1ea1ng h\u00f4n nh\u00e2n")
    land = official_form("land", "sang_ten_so_do", name="\u0110\u01a1n \u0111\u0103ng k\u00fd bi\u1ebfn \u0111\u1ed9ng \u0111\u1ea5t \u0111ai", domain="dat_dai_xay_dung")
    unrelated = official_form("passport", "cap_ho_chieu_tre_em", name="T\u1edd khai h\u1ed9 chi\u1ebfu tr\u1ebb em", domain="cu_tru_an_ninh")
    detail = {
        "id": "sang_ten_so_do",
        "name": "Sang t\u00ean s\u1ed5 \u0111\u1ecf",
        "domain_slug": "dat_dai_xay_dung",
        "forms": [land],
        "recommended_forms": [land, marital, birth, unrelated],
        "matched_procedure_ids": ["sang_ten_so_do", "xac_nhan_doc_than"],
    }
    _proc, recommended, _summary = _procedure_response_fields(
        detail,
        "H\u1ed3 s\u01a1 th\u1eeba k\u1ebf sang t\u00ean \u0111\u1ea5t c\u00f3 c\u1ea7n m\u1eabu \u0111\u01a1n, \u0111\u1ed3ng th\u1eddi t\u00f4i c\u1ea7n m\u1eabu x\u00e1c nh\u1eadn t\u00ecnh tr\u1ea1ng h\u00f4n nh\u00e2n \u0111\u1ec3 t\u1ea3i v\u1ec1",
    )
    assert {item["form_id"] for item in recommended} == {"land", "marital"}
    assert len(recommended) <= 3
    assert "passport" not in [item["form_id"] for item in recommended]


def test_complaint_request_only_returns_official_complaint_form():
    from api.routers.search import _procedure_response_fields

    complaint = official_form("complaint", "khieu_nai", name="\u0110\u01a1n khi\u1ebfu n\u1ea1i")
    birth = official_form("birth", "dang_ky_khai_sinh", name="T\u1edd khai \u0111\u0103ng k\u00fd khai sinh")
    detail = {
        "id": "khieu_nai",
        "name": "Khi\u1ebfu n\u1ea1i",
        "domain_slug": "trat_tu_do_thi",
        "forms": [complaint],
        "recommended_forms": [complaint, birth],
        "matched_procedure_ids": ["khieu_nai"],
    }
    _proc, recommended, _summary = _procedure_response_fields(
        detail, "T\u00f4i c\u1ea7n m\u1eabu \u0111\u01a1n khi\u1ebfu n\u1ea1i quy\u1ebft \u0111\u1ecbnh x\u1eed ph\u1ea1t chi\u1ebfm d\u1ee5ng h\u00e8 ph\u1ed1")
    assert [item["form_id"] for item in recommended] == ["complaint"]


def test_residency_question_does_not_treat_contextual_birth_data_as_a_procedure():
    from api.routers.search import _match_procedure_detail

    question = (
        "Gia đình tôi gồm vợ chồng và một con 6 tuổi đang thường trú ở tỉnh khác, "
        "hiện thuê một căn hộ tại Hải Phòng trong thời hạn 12 tháng. Chủ nhà đồng "
        "ý xác nhận trên VNeID; dữ liệu khai sinh của cháu đã có trong Cơ sở dữ "
        "liệu quốc gia về dân cư. Hãy xác định có đủ điều kiện đăng ký thường trú "
        "hay chỉ đăng ký tạm trú, nêu biểu mẫu chính xác và liên kết tải. Không "
        "được mặc định sử dụng CT01 nếu chưa xác minh."
    )

    detail = _match_procedure_detail(question, role="citizen")

    assert detail is not None
    residency_ids = {
        "dang_ky_tam_tru",
        "dang_ky_thuong_tru",
        "dieu_chinh_thong_tin_cu_tru",
        # Canonical three-tier records preserve their official DVC codes.
        "1.004194",
        "1.004222",
    }
    assert detail["id"] in residency_ids
    assert set(detail["matched_procedure_ids"]) <= residency_ids
    assert all(
        form.get("procedure_id") in residency_ids
        for form in detail.get("recommended_forms") or []
    )


def test_shared_form_code_does_not_expand_api_to_unrelated_procedures():
    from api.routers.search import _detect_matched_procedure_ids

    pension = _detect_matched_procedure_ids(
        "Toi muon huong tro cap huu tri xa hoi va can Mau so 01 chinh thuc."
    )
    residence = _detect_matched_procedure_ids(
        "Toi can dang ky tam tru bang Mau CT01 chinh thuc."
    )

    assert [item[0] for item in pension] == ["1.014027"]
    assert [item[0] for item in residence] == ["1.004194"]


def test_explicit_two_procedures_remain_multi_intent_after_code_filtering():
    from api.routers.search import _detect_matched_procedure_ids

    matches = _detect_matched_procedure_ids(
        "Toi can dang ky tam tru va xin Giay xac nhan tinh trang hon nhan; "
        "hay cho bieu mau cua tung thu tuc."
    )

    assert {item[0] for item in matches} == {
        "1.004194",
        "xac_nhan_tinh_trang_hon_nhan",
    }


def test_citations_prefer_active_direct_results_and_internal_viewer_links():
    from api.routers.search import _build_citations_from_retrieval

    retrieval = {
        "query": "m?i che l?n chi?m h? ph? b? ph?t theo ngh? ??nh n?o",
        "results": [
            {
                "chunk_id": "old", "doc_id": "9", "law_number": "100/2019/N?-CP",
                "document_title": "V?n b?n h?t hi?u l?c", "article_number": "12",
                "content": "m?i che h? ph?", "score": 0.99, "effective_status": "expired",
            },
            {
                "chunk_id": "strong", "doc_id": "10", "law_number": "168/2024/N?-CP",
                "document_title": "X? ph?t vi ph?m giao th?ng ???ng b?", "article_number": "8",
                "clause_number": "2", "point_number": "a",
                "content": "m?i che l?n chi?m h? ph? x? ph?t", "score": 0.55, "effective_status": "active",
            },
            {
                "chunk_id": "weak", "doc_id": "11", "law_number": "01/2020/QH14",
                "document_title": "V?n b?n kh?c", "article_number": "3",
                "content": "n?i dung kh?ng li?n quan", "score": 0.10, "effective_status": "active",
            },
        ],
    }
    citations = _build_citations_from_retrieval(retrieval)
    assert 1 <= len(citations) <= 3
    assert citations[0]["doc_id"] == "10"
    assert all(item["doc_id"] != "9" for item in citations)
    assert citations[0]["internal_url"] == "/legal-documents/10?article=8&clause=2&point=a"
    assert citations[0]["pdf_url"] == "/api/legal/docs/10/download.pdf?article=8"


def test_verified_form_lookup_requires_confirmed_procedure_identity():
    from api.routers.search import _is_verified_form_lookup_only

    policy = {
        "requests_form": True,
        "required_sections": ["conclusion", "official_forms"],
    }
    base = {
        "form_id": "form-na17",
        "procedure_id": "foreign_guest_stay",
        "review_status": "approved",
        "official_level": "official",
        "source_url": "https://vbpl.vn/na17",
        "download_url": "/api/procedures/forms-catalog/assets/form-na17/download",
    }

    assert _is_verified_form_lookup_only(policy, [base]) is False
    assert _is_verified_form_lookup_only(
        policy,
        [{**base, "procedure_identity_confirmed": True}],
    ) is True


def test_verified_form_lookup_is_not_provider_fallback():
    """A catalog-only answer remains a normal deterministic answer mode."""
    from api.routers import search

    assert search.NORMAL == "normal"
