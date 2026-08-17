from datetime import date

from scripts.build_lechan_shadow_scope import (
    ALLOWED_ONE_HOP_RELATIONS,
    DIRECT_PRIMARY_REASONS,
    extract_legal_number,
    normalize,
    resolve_reference,
    scope_decision,
)


def test_direct_seed_contract_is_exactly_the_six_approved_reasons():
    assert DIRECT_PRIMARY_REASONS == {
        "central_commune_specific",
        "hai_phong_commune_specific",
        "hai_phong_relevant_domain",
        "current_core_law",
        "related_current_instrument",
        "manual_verified_import",
    }


def test_relationship_expansion_excludes_generic_legal_basis():
    assert normalize("Văn bản căn cứ") not in ALLOWED_ONE_HOP_RELATIONS
    assert normalize("Văn bản sửa đổi") in ALLOWED_ONE_HOP_RELATIONS
    assert normalize("Văn bản được HD, QĐ chi tiết") in ALLOWED_ONE_HOP_RELATIONS


def test_scope_is_deterministic_from_metadata_or_issuing_agency():
    assert scope_decision({"scope": "Toàn quốc"}) == (
        True,
        "scope_central_metadata",
    )
    assert scope_decision(
        {"scope": None, "issuing_agency": "UBND thành phố Hải Phòng"}
    ) == (True, "scope_derived_local_agency")
    assert scope_decision({"scope": "Hà Nội"}) == (False, None)


def test_expected_source_resolution_never_uses_provision_as_document():
    mapping = {"60/2014/QH13": 10, "123/2015/NĐ-CP": 20}
    assert resolve_reference("Điều 16", mapping) == (
        None,
        "non_document_locator",
    )
    assert resolve_reference("Luật Hộ tịch 2014", mapping) == (10, "alias")
    assert resolve_reference("Nghị định 123/2015", mapping) == (
        20,
        "alias",
    )
    assert resolve_reference("Luật Cư trú 2020", mapping) == (
        None,
        "expected_source_not_in_corpus",
    )


def test_legal_number_extraction_is_exact_and_non_semantic():
    assert extract_legal_number("Nghị định 118/2021/NĐ-CP") == "118/2021/NĐ-CP"
    assert extract_legal_number("không có số hiệu") is None
