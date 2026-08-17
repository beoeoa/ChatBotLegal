import pytest

from api.routers.legal_search import (
    LegalImportRequest,
    _resolved_admin_import_domain,
)


def test_manual_import_accepts_reviewed_domain_slug():
    request = LegalImportRequest(
        title="Thông tư thử nghiệm về cư trú",
        law_number="999/2026/TT-BCA",
        document_type="Thông tư",
        issuing_agency="Bộ Công an",
        scope="Trung ương",
        sector="Đăng ký, quản lý cư trú",
        field_id=9,
        effective_date="2026-07-01",
        source_url="https://vbpl.vn/example",
        content="Điều 1. Phạm vi điều chỉnh",
        confirmed_official_source=True,
        domain_slug="cu_tru_an_ninh",
    )
    assert request.domain_slug == "cu_tru_an_ninh"


def test_reviewed_field_overrides_ambiguous_text_classifier():
    assert _resolved_admin_import_domain(
        field_id=9,
        requested_domain=None,
        classified_domains=["ho_tich_chung_thuc", "hanh_chinh_cong"],
    ) == "cu_tru_an_ninh"


def test_import_rejects_inconsistent_reviewed_field_and_domain():
    with pytest.raises(ValueError, match="FIELD_DOMAIN_MISMATCH"):
        _resolved_admin_import_domain(
            field_id=9,
            requested_domain="ho_tich_chung_thuc",
            classified_domains=["ho_tich_chung_thuc"],
        )
