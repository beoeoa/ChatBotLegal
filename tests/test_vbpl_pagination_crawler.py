from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from api.legal_crawl_service import LegalCrawlService


FIXTURES = Path(__file__).parent / "fixtures" / "vbpl"
PAGE_1 = (FIXTURES / "central-page-1.html").read_text(encoding="utf-8")
PAGE_2 = (FIXTURES / "central-page-2.html").read_text(encoding="utf-8")

SOURCE = {
    "id": "legal_crawl_source:vbpl_central",
    "name": "VBPL Trung ương",
    "source_type": "vbpl_listing",
    "sitemap_scope": "central",
    "base_url": "https://vbpl.vn/van-ban/trung-uong",
    "listing_cursor": "https://vbpl.vn/van-ban/trung-uong",
    "enabled": True,
    "max_documents_per_run": 20,
    "max_listing_pages_per_run": 3,
    "domains": ["ho_tich_chung_thuc", "dat_dai_xay_dung"],
    "rate_limit_seconds": 0.2,
    "content_fetch_allowed": False,
}


def test_listing_fixture_extracts_next_page_metadata_and_form_link():
    items = LegalCrawlService._parse_listing(PAGE_1, SOURCE["base_url"])

    assert len(items) == 2
    document = next(item for item in items if item["source_type"] == "document")
    form = next(item for item in items if item["source_type"] == "form")
    assert document["url"] == "https://vbpl.vn/van-ban/trung-uong/Nghi-dinh-ho-tich-12-2026"
    assert document["law_number"] == "12/2026/NĐ-CP"
    assert document["document_type"] == "Nghị định"
    assert document["issuing_agency"] == "Bộ Tư pháp"
    assert form["url"] == "https://vbpl.vn/files/to-khai-khai-sinh.docx"
    assert LegalCrawlService._next_listing_url(PAGE_1, SOURCE["base_url"], SOURCE["base_url"]) == (
        "https://vbpl.vn/van-ban/trung-uong?page=2"
    )
    assert LegalCrawlService._next_listing_url(PAGE_2, "https://vbpl.vn/van-ban/trung-uong?page=2", SOURCE["base_url"]) is None
    assert (FIXTURES / "form-link.pdf").read_bytes().startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_paginated_listing_scan_persists_cursor_and_never_fetches_detail(monkeypatch):
    created: list[tuple[str, dict]] = []
    updates: list[tuple[str, object, dict]] = []
    fetched_urls: list[str] = []

    async def fake_query(query, params=None):
        if "FROM legal_crawl_source WHERE id" in query:
            return [SOURCE]
        return []  # candidate dedup: no existing listing or imported record

    async def fake_create(table, payload):
        created.append((table, payload))
        if table == "legal_crawl_run":
            return [{"id": "legal_crawl_run:pages"}]
        return [{"id": f"{table}:{len(created)}"}]

    async def fake_update(table, record_id, payload):
        updates.append((table, record_id, payload))
        return [payload]

    async def robots_allowed(_url):
        return True, None

    async def fetch_listing(url, _rate_limit):
        fetched_urls.append(url)
        if "page=2" in url:
            return PAGE_2
        return PAGE_1

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr(LegalCrawlService, "_robots_allowed", staticmethod(robots_allowed))
    monkeypatch.setattr(LegalCrawlService, "_fetch_with_backoff", staticmethod(fetch_listing))
    monkeypatch.setattr("api.legal_crawl_service.asyncio.sleep", no_sleep)

    result = await LegalCrawlService.scan_source(str(SOURCE["id"]))

    candidates = [payload for table, payload in created if table == "legal_crawl_candidate"]
    assert result["status"] == "completed"
    assert result["statistics"] == {
        "discovered": 3,
        "created": 3,
        "duplicates": 0,
        "outside_domain": 0,
        "forms": 1,
    }
    assert result["pagination"] == {"listing_pages": 2, "cursor_reset": 1}
    assert result["catalog_completed"] is True
    assert result["next_listing_cursor"] == SOURCE["base_url"]
    assert fetched_urls == [SOURCE["base_url"], "https://vbpl.vn/van-ban/trung-uong?page=2"]
    assert {candidate["source_type"] for candidate in candidates} == {"document", "form"}
    assert all(candidate["status"] == "pending" for candidate in candidates)
    assert all(candidate["review_status"] == "pending" for candidate in candidates)
    assert all(candidate["raw_metadata"]["metadata_only"] is True for candidate in candidates)
    assert all("content" not in candidate for candidate in candidates)
    assert not any("Nghi-dinh-ho-tich" in url or "Nghi-dinh-xay-dung" in url for url in fetched_urls)
    assert any(
        table == "legal_crawl_source" and payload["listing_cursor"] == SOURCE["base_url"]
        for table, _record_id, payload in updates
    )


@pytest.mark.asyncio
async def test_existing_law_number_skips_second_listing_page_candidate(monkeypatch):
    calls: list[tuple[str, dict | None]] = []

    async def fake_query(query, params=None):
        calls.append((query, params))
        if "raw_metadata.issued_date" in query or "law_number = $law_number" in query:
            return [{"id": "legal_crawl_candidate:already-known"}]
        return []

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    created, reason = await LegalCrawlService._create_listing_candidate(
        SOURCE,
        {
            "url": "https://vbpl.vn/van-ban/trung-uong/Nghi-dinh-ho-tich-12-2026",
            "title": "Nghị định về đăng ký hộ tịch 12/2026/NĐ-CP",
            "context": "Nghị định về đăng ký hộ tịch 12/2026/NĐ-CP",
            "source_type": "document",
            "law_number": "12/2026/NĐ-CP",
            "document_type": "Nghị định",
            "issuing_agency": "Bộ Tư pháp",
        },
        "legal_crawl_run:test",
    )

    assert created is False
    assert reason == "duplicate"
    assert any("law_number = $law_number" in query for query, _params in calls)
