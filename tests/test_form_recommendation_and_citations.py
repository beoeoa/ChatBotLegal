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
