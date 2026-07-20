from api.legal_crawl_service import LegalCrawlService


def _candidate(**overrides):
    candidate = {
        "title": "Nghị định về hộ tịch",
        "law_number": "12/2026/NĐ-CP",
        "source_type": "document",
        "source_url": "https://vbpl.vn/van-ban/trung-uong/12-2026-nd-cp",
        "scope": "central",
        "content": "Điều 1. Phạm vi điều chỉnh. " * 10,
        "duplicate_candidates": [],
        "status": "approved",
        "review_status": "approved",
        "raw_metadata": {
            "confirmed_official_source": True,
            "effective_date": "2026-01-01",
            "expired_date": None,
        },
    }
    candidate.update(overrides)
    return candidate


def test_import_guard_requires_verified_provenance_and_current_effective_metadata():
    candidate = _candidate(raw_metadata={"confirmed_official_source": False})

    errors = LegalCrawlService.validate_candidate_for_import(candidate)

    assert any("nguồn văn bản chính thức" in error for error in errors)
    assert any("ngày có hiệu lực" in error for error in errors)


def test_import_guard_accepts_verified_current_legal_document():
    assert LegalCrawlService.validate_candidate_for_import(_candidate()) == []


def test_form_candidate_cannot_be_imported_into_legal_rag():
    errors = LegalCrawlService.validate_candidate_for_import(_candidate(source_type="form"))

    assert any("Form Catalog" in error for error in errors)
