from unittest.mock import AsyncMock

import pytest

from api.legal_crawl_service import LEGAL_DOMAINS, LegalCrawlService, WEEKLY_CRAWL_INTERVAL_MINUTES


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
