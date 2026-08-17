import json
from unittest.mock import AsyncMock

import pytest

from api.legal_crawl_service import LEGAL_DOMAINS, LegalCrawlService, WEEKLY_CRAWL_INTERVAL_MINUTES
from open_notebook.utils.vbpl_crawler import (
    _build_vbpl_listing_html,
    _extract_vbpl_document_page,
)


SOURCE = {
    "id": "legal_crawl_source:vbpl_central",
    "name": "VBPL Trung ương",
    "source_type": "vbpl_listing",
    "sitemap_scope": "central",
    "base_url": "https://vbpl.vn/van-ban/trung-uong",
    "enabled": True,
    "interval_minutes": 1440,
    "max_documents_per_run": 10,
    "domains": ["ho_tich_chung_thuc"],
    "rate_limit_seconds": 0.2,
    "content_fetch_allowed": False,
}


LISTING_HTML = """
<html><body>
  <article><a href="/van-ban/trung-uong/Nghi-dinh-ve-ho-tich-123">Nghị định về hộ tịch 123/2026/NĐ-CP</a></article>
  <article><a href="/files/to-khai-dang-ky-ket-hon.docx">Tờ khai đăng ký kết hôn</a></article>
  <article><a href="/van-ban/trung-uong/Nghi-dinh-ve-chung-khoan-124">Nghị định về chứng khoán 124/2026/NĐ-CP</a></article>
</body></html>
"""


def test_dynamic_vbpl_rsc_payload_becomes_parseable_official_detail_links():
    title = (
        "Nghị quyết số 11/2026/NQ-HĐND Quy định mức tối đa, mức tối thiểu "
        "của hệ số điều chỉnh mức biến động thị trường về giá đất"
    )
    # The current VBPL server-action response can carry UTF-8 text decoded as
    # Latin-1. The adapter must repair it before candidate metadata is stored.
    mojibake_title = title.encode("utf-8").decode("latin-1")
    payload = {
        "total": 3507,
        "pageNumber": 1,
        "pageSize": 10,
        "items": [
            {
                "id": "0e79dc00-8cc1-11f1-b75a-c56b84d4263f",
                "title": mojibake_title,
                "docNum": "11/2026/NQ-HĐND".encode("utf-8").decode("latin-1"),
                "docType": {"name": "Nghị quyết".encode("utf-8").decode("latin-1")},
                "agencyName": "HĐND Thành phố Hải Phòng".encode("utf-8").decode("latin-1"),
                "issueDate": "2026-07-28T00:00:00",
                "effFrom": "2026-08-08T00:00:00",
                "effTo": None,
            }
        ],
    }
    rsc = f'0:["$@1",[]]\n1:{json.dumps(payload, ensure_ascii=False)}\n'

    page = _extract_vbpl_document_page(rsc)
    assert page is not None
    html = _build_vbpl_listing_html(
        page,
        "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
        page_number=1,
    )
    items = LegalCrawlService._parse_listing(
        html,
        "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
    )

    assert len(items) == 1
    assert items[0]["title"] == title
    assert items[0]["law_number"] == "11/2026/NQ-HĐND"
    assert items[0]["issued_date"] == "2026-07-28"
    assert items[0]["effective_date"] == "2026-08-08"
    assert items[0]["document_type"] == "Nghị quyết"
    assert items[0]["issuing_agency"] == "HĐND Thành phố Hải Phòng"
    assert items[0]["url"] == (
        "https://vbpl.vn/van-ban/chi-tiet/"
        "0e79dc00-8cc1-11f1-b75a-c56b84d4263f"
    )
    assert "_crawler_page=2" in html

    terminal_html = _build_vbpl_listing_html(
        {**page, "total": 3507},
        "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
        page_number=7,
        has_next=False,
    )
    assert "_crawler_page=8" not in terminal_html


def test_listing_parser_never_treats_a_date_as_the_law_number():
    html = """
    <article>
      <a href="/van-ban/chi-tiet/example">Quyết định về giáo dục</a>
      <p>Số hiệu: 1654/QĐ-UBND</p>
      <p>Ngày ban hành: 28/04/2026</p>
      <p>Ngày hiệu lực: 10/05/2026</p>
      <p>Cơ quan: UBND Thành phố Hải Phòng</p>
    </article>
    """

    items = LegalCrawlService._parse_listing(html, "https://vbpl.vn/van-ban/dia-phuong")

    assert items[0]["law_number"] == "1654/QĐ-UBND"
    assert items[0]["issued_date"] == "2026-04-28"
    assert items[0]["effective_date"] == "2026-05-10"


@pytest.mark.asyncio
async def test_listing_scan_creates_pending_candidates_without_import(monkeypatch):
    created: list[tuple[str, dict]] = []
    updates: list[tuple[str, object, dict]] = []

    async def fake_query(query, params=None):
        if "FROM legal_crawl_source WHERE id" in query:
            return [SOURCE]
        # Every dedup query returns no existing document.
        return []

    async def fake_create(table, payload):
        created.append((table, payload))
        if table == "legal_crawl_run":
            return [{"id": "legal_crawl_run:scan1"}]
        if table == "legal_crawl_candidate":
            return [{"id": f"legal_crawl_candidate:{len(created)}"}]
        return [{"id": f"{table}:1"}]

    async def fake_update(table, record_id, payload):
        updates.append((table, record_id, payload))
        return [payload]

    async def robots_allowed(_url):
        return True, None

    async def fetch_listing(_url, _rate_limit):
        return LISTING_HTML

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr(LegalCrawlService, "_robots_allowed", staticmethod(robots_allowed))
    monkeypatch.setattr(LegalCrawlService, "_fetch_with_backoff", staticmethod(fetch_listing))

    result = await LegalCrawlService.scan_source(str(SOURCE["id"]))

    candidates = [payload for table, payload in created if table == "legal_crawl_candidate"]
    assert result["status"] == "completed"
    assert result["statistics"] == {
        "discovered": 3,
        "created": 2,
        "duplicates": 0,
        "outside_domain": 1,
        "forms": 1,
    }
    assert len(candidates) == 2
    assert {candidate["source_type"] for candidate in candidates} == {"document", "form"}
    assert all(candidate["status"] == "pending" for candidate in candidates)
    assert all(candidate["review_status"] == "pending" for candidate in candidates)
    assert all(candidate["raw_metadata"]["metadata_only"] is True for candidate in candidates)
    assert all("content" not in candidate for candidate in candidates)
    assert all(candidate["domain"] == "ho_tich_chung_thuc" for candidate in candidates)
    assert not any(table == "source" for table, _ in created)
    assert any(table == "legal_crawl_run" for table, _, _ in updates)


@pytest.mark.asyncio
async def test_dynamic_vbpl_listing_always_uses_exact_rendered_records(monkeypatch):
    """Navigation links in the loading shell must not bypass the RSC adapter."""

    shell_html = """
        <html><body>
          <a href="/van-ban/trung-uong">Văn bản Trung ương</a>
          <a href="/van-ban/dia-phuong">Văn bản địa phương</a>
        </body></html>
    """

    class FakeResponse:
        status_code = 200
        text = shell_html

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, _url):
            return FakeResponse()

    rendered = AsyncMock(
        side_effect=[ValueError("transient pagination state"), {"html": LISTING_HTML}]
    )
    monkeypatch.setattr("api.legal_crawl_service.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("open_notebook.utils.vbpl_crawler.crawl_vbpl_listing", rendered)

    html = await LegalCrawlService._fetch_with_backoff(
        "https://vbpl.vn/van-ban/trung-uong",
        0.2,
    )

    assert html == LISTING_HTML
    assert rendered.await_count == 2
    rendered.assert_awaited_with("https://vbpl.vn/van-ban/trung-uong")


@pytest.mark.asyncio
async def test_robots_denied_marks_source_failed_without_candidate(monkeypatch):
    created: list[tuple[str, dict]] = []

    async def fake_query(query, params=None):
        if "FROM legal_crawl_source WHERE id" in query:
            return [SOURCE]
        return []

    async def fake_create(table, payload):
        created.append((table, payload))
        if table == "legal_crawl_run":
            return [{"id": "legal_crawl_run:scan2"}]
        return [{"id": f"{table}:1"}]

    async def robots_denied(_url):
        return False, "robots.txt không cho phép crawler truy cập URL này"

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", AsyncMock(return_value=[]))
    monkeypatch.setattr(LegalCrawlService, "_robots_allowed", staticmethod(robots_denied))

    result = await LegalCrawlService.scan_source(str(SOURCE["id"]))

    assert result["status"] == "failed"
    assert "robots.txt" in result["failure_reason"]
    assert not any(table == "legal_crawl_candidate" for table, _ in created)


@pytest.mark.asyncio
async def test_default_sources_use_weekly_schedule(monkeypatch):
    created = []

    async def fake_query(query, params=None):
        return []

    async def fake_create(table, payload):
        created.append((table, payload))
        return [{"id": f"{table}:{len(created)}", **payload}]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr(LegalCrawlService, "list_sources", AsyncMock(return_value=[]))

    await LegalCrawlService.ensure_vbpl_sources()

    source_payloads = [payload for table, payload in created if table == "legal_crawl_source"]
    assert len(source_payloads) == 6
    assert all(item["interval_minutes"] == WEEKLY_CRAWL_INTERVAL_MINUTES for item in source_payloads)
    assert all(item["base_url"].startswith("https://") for item in source_payloads)


@pytest.mark.asyncio
async def test_old_daily_default_is_migrated_but_custom_schedule_is_preserved(monkeypatch):
    updates = []

    async def fake_query(query, params=None):
        interval = 1440 if "trung-uong" in params["base_url"] else 2880
        domains = (
            ["ho_tich_chung_thuc", "hanh_chinh_cong", "trat_tu_do_thi"]
            if "sotp.haiphong.gov.vn" in params["base_url"]
            else list(LEGAL_DOMAINS)
        )
        return [{"id": f"legal_crawl_source:{interval}", "interval_minutes": interval, "domains": domains}]

    async def fake_update(table, record_id, payload):
        updates.append((table, record_id, payload))
        return [payload]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr(LegalCrawlService, "list_sources", AsyncMock(return_value=[]))

    await LegalCrawlService.ensure_vbpl_sources()

    assert len(updates) == 1
    assert updates[0][2]["interval_minutes"] == WEEKLY_CRAWL_INTERVAL_MINUTES


@pytest.mark.asyncio
async def test_legacy_builtin_sitemap_sources_are_migrated_to_weekly(monkeypatch):
    updates = []

    async def fake_query(query, params=None):
        interval = 60 if params["base_url"] == "https://vbpl.vn" else 1440
        return [{"id": f"legal_crawl_source:{interval}", "interval_minutes": interval}]

    async def fake_update(table, record_id, payload):
        updates.append((table, record_id, payload))
        return [payload]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)

    await LegalCrawlService._migrate_legacy_default_source_intervals()

    assert len(updates) == 2
    assert all(item[2]["interval_minutes"] == WEEKLY_CRAWL_INTERVAL_MINUTES for item in updates)
    assert all(item[2]["enabled"] is False for item in updates)


@pytest.mark.asyncio
async def test_listing_deduplication_skips_law_number_already_in_runtime(monkeypatch):
    query = AsyncMock(return_value=[])
    runtime_lookup = AsyncMock(return_value={"id": 127439, "law_number": "24/2026/QĐ-UBND"})
    monkeypatch.setattr("api.legal_crawl_service.repo_query", query)
    monkeypatch.setattr(LegalCrawlService, "find_runtime_document_conflict", runtime_lookup)

    known = await LegalCrawlService._already_known(
        "https://vbpl.vn/van-ban/chi-tiet/187919",
        "24/2026/QĐ-UBND",
        "2026-04-13",
        "fingerprint",
    )

    assert known is True
    runtime_lookup.assert_awaited_once_with({"law_number": "24/2026/QĐ-UBND"})
