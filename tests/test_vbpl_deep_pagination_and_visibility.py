from pathlib import Path

import pytest

from api.legal_crawl_service import LegalCrawlService


FIXTURES = Path(__file__).parent / "fixtures" / "vbpl"
PAGE_3 = (FIXTURES / "central-page-3.html").read_text(encoding="utf-8")
PAGE_4 = (FIXTURES / "central-page-4.html").read_text(encoding="utf-8")

SOURCE = {
    "id": "legal_crawl_source:vbpl_central",
    "name": "VBPL Trung ??ng",
    "source_type": "vbpl_listing",
    "sitemap_scope": "central",
    "base_url": "https://vbpl.vn/van-ban/trung-uong",
    "listing_cursor": "https://vbpl.vn/van-ban/trung-uong?page=3",
    "enabled": True,
    "max_documents_per_run": 2,
    "max_listing_pages_per_run": 10,
    "domains": ["ho_tich_chung_thuc", "dat_dai_xay_dung", "an_sinh_y_te_giao_duc"],
    "rate_limit_seconds": 0.2,
    "content_fetch_allowed": False,
}


@pytest.mark.asyncio
async def test_duplicate_listing_items_do_not_consume_new_candidate_budget(monkeypatch):
    """Verify that when a page contains many duplicates, the candidate budget
    is preserved for truly new items discovered later on the same page."""
    created: list[dict] = []
    updates: list[tuple[str, object, dict]] = []
    fetched: list[str] = []

    async def fake_query(query, params=None):
        if "FROM legal_crawl_source WHERE id" in query:
            return [SOURCE]
        # Simulate that items [0] and [1] from page 3 were already seen.
        if "source_url = $source_url" in query and params:
            url = params.get("source_url", "")
            if "Nghi-dinh-ho-tich" in url or "to-khai-khai-sinh" in url:
                return [{"id": "legal_crawl_candidate:known"}]
        return []

    async def fake_create(table, payload):
        if table == "legal_crawl_run":
            return [{"id": "legal_crawl_run:deep"}]
        if table == "legal_crawl_candidate":
            created.append(payload)
            return [{"id": f"legal_crawl_candidate:{len(created)}"}]
        return [{"id": f"{table}:1"}]

    async def fake_update(table, record_id, payload):
        updates.append((table, record_id, payload))
        return [payload]

    async def robots_allowed(_url):
        return True, None

    async def fetch_listing(url, _rate_limit):
        fetched.append(url)
        return PAGE_4 if "page=4" in url else PAGE_3

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr(LegalCrawlService, "_robots_allowed", staticmethod(robots_allowed))
    monkeypatch.setattr(LegalCrawlService, "_fetch_with_backoff", staticmethod(fetch_listing))
    monkeypatch.setattr("api.legal_crawl_service.asyncio.sleep", no_sleep)

    result = await LegalCrawlService.scan_source(str(SOURCE["id"]))

    assert result["status"] == "completed"
    # Only 2 new candidates should be created (budget limit), even though
    # 4 links were parsed on page 3.  Duplicates do NOT consume the budget.
    assert result["statistics"]["created"] == 2
    # Page 3 and page 4 both contain prior links.  These duplicates are
    # recorded but do not consume the two-new-candidate budget.
    assert result["statistics"]["duplicates"] >= 2
    # The crawler finishes page 3, discovers the next-page URL (page 4),
    # then stops because the new-candidate budget is full.  This prevents
    # a high-volume burst across hundreds of VBPL pages in a single run.
    assert result["next_listing_cursor"].endswith("page=4")
    # Source cursor was persisted.  No document detail/PDF URL is ever fetched;
    # every URL is a same-origin listing page from pagination markup.
    assert all("/van-ban/trung-uong" in url for url in fetched)
    assert any(
        table == "legal_crawl_source" and payload["listing_cursor"].endswith("page=4")
        for table, _record_id, payload in updates
    )


@pytest.mark.asyncio
async def test_candidate_listing_filter_uses_exact_domain_and_source_type(monkeypatch):
    """list_candidates accepts domain/source_type filters and passes them to SQL."""
    calls: list[tuple[str, dict]] = []

    async def fake_query(query, params=None):
        calls.append((query, params or {}))
        return []

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)

    result = await LegalCrawlService.list_candidates(
        status="pending",
        domain="ho_tich_chung_thuc",
        source_type="form",
        limit=999,
    )

    assert result == []
    query, params = calls[-1]
    assert "status = $status" in query
    assert "domain = $domain" in query
    assert "source_type = $source_type" in query
    assert params == {
        "limit": 200,
        "status": "pending",
        "domain": "ho_tich_chung_thuc",
        "source_type": "form",
    }


@pytest.mark.asyncio
async def test_candidate_listing_needs_attention_groups_retry_and_review_states(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_query(query, params=None):
        calls.append((query, params or {}))
        return []

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)

    result = await LegalCrawlService.list_candidates(status="needs_attention", limit=50)

    assert result == []
    query, params = calls[-1]
    assert "status IN ['import_failed', 'changes_requested']" in query
    assert "import_status = 'validation_failed'" in query
    assert "status" not in params
    assert params == {"limit": 50}


@pytest.mark.asyncio
async def test_candidate_listing_omits_full_content_and_bounds_ocr_preview(monkeypatch):
    full_text = "Nội dung kiểm chứng. " * 300
    preview = "Bản xem trước OCR. " * 300

    async def fake_query(_query, _params=None):
        return [{
            "id": "legal_crawl_candidate:large",
            "content": full_text,
            "extraction_result": {"preview": preview, "characters": 0},
        }]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)

    result = await LegalCrawlService.list_candidates(status="import_failed")

    assert len(result) == 1
    assert "content" not in result[0]
    assert result[0]["content_characters"] == len(full_text)
    assert len(result[0]["extraction_result"]["preview"]) <= 2001
    assert result[0]["extraction_result"]["preview"].endswith("…")


@pytest.mark.asyncio
async def test_summary_exposes_candidate_counts_by_domain_and_source(monkeypatch):
    """summary() returns by_domain and by_source breakdowns."""
    async def fake_query(query, _params=None):
        if "GROUP BY domain" in query:
            return [{"domain": "ho_tich_chung_thuc", "count": 4}]
        if "GROUP BY source" in query:
            return [{"source": "legal_crawl_source:vbpl_central", "count": 4}]
        if "status = 'pending'" in query:
            return [{"count": 2}]
        if "status = 'approved'" in query:
            return [{"count": 1}]
        if "SELECT status, import_status, imported_document" in query:
            return [{
                "status": "imported",
                "import_status": "completed",
                "imported_document": {
                    "document_id": "document:1",
                    "activation_status": "active",
                    "chunk_count": 4,
                },
            }]
        if "status = 'imported'" in query:
            return [{"count": 1}]
        if "status = 'rejected'" in query:
            return [{"count": 0}]
        if "legal_import_job" in query:
            return [
                {"status": "queued", "count": 2},
                {"status": "running", "count": 1},
                {"status": "failed", "count": 3},
            ]
        return [{"count": 4}]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)

    summary = await LegalCrawlService.summary()

    assert summary["by_domain"] == {"ho_tich_chung_thuc": 4}
    assert summary["by_source"] == {"legal_crawl_source:vbpl_central": 4}
    assert summary["pending_candidates"] == 2
    assert summary["activated_candidates"] == 1
    assert summary["unverified_imported_candidates"] == 0
    assert summary["import_metrics"]["activated_candidates"] == 1
    assert summary["import_queue"] == {
        "queued": 2,
        "running": 1,
        "failed": 3,
        "completed": 0,
    }


def test_active_import_confirmation_rejects_legacy_status_without_vector_evidence():
    assert LegalCrawlService._has_active_import_confirmation({
        "status": "imported",
        "import_status": "completed",
        "imported_document": {
            "document_id": "legal_document:1",
            "activation_status": "active",
            "chunk_count": 1,
        },
    })
    assert not LegalCrawlService._has_active_import_confirmation({
        "status": "imported",
        "import_status": "completed",
        "imported_document": {
            "document_id": "legal_document:legacy",
            "chunk_count": 0,
        },
    })


def test_import_queue_metrics_are_timestamp_based_and_do_not_guess_missing_values():
    metrics = LegalCrawlService._build_import_queue_metrics(
        [
            {
                "status": "completed",
                "created_at": "2026-08-07T00:00:00+00:00",
                "started_at": "2026-08-07T00:01:00+00:00",
                "completed_at": "2026-08-07T00:03:00+00:00",
            },
            {
                "status": "failed",
                "created_at": "2026-08-07T00:10:00+00:00",
                "started_at": "2026-08-07T00:10:00+00:00",
                "completed_at": "2026-08-07T00:11:00+00:00",
            },
            {"status": "completed", "created_at": None, "started_at": None, "completed_at": None},
        ],
        import_queue={"queued": 0, "running": 0, "failed": 1, "completed": 1},
        activated_candidates=1,
        validation_failed_candidates=2,
        duplicate_conflict_candidates=3,
    )

    assert metrics == {
        "observed_job_count": 3,
        "success_rate_percent": 50.0,
        "average_queue_seconds": 30.0,
        "average_processing_seconds": 90.0,
        "validation_failed_candidates": 2,
        "duplicate_conflict_candidates": 3,
        "activated_candidates": 1,
    }


@pytest.mark.asyncio
async def test_source_status_returns_freshness_and_config(monkeypatch):
    """source_status() returns safe dashboard data per source."""
    async def fake_list():
        return [
            {
                "id": "legal_crawl_source:vbpl_central",
                "name": "VBPL Trung ??ng",
                "sitemap_scope": "central",
                "enabled": True,
                "listing_cursor": "https://vbpl.vn/van-ban/trung-uong?page=5",
                "last_checked_at": "2026-07-12T10:00:00Z",
                "last_success_at": "2026-07-12T10:05:00Z",
                "source_freshness": "2026-07-12T10:05:00Z",
                "last_status": "completed",
                "last_error": None,
                "last_run_stats": {"listing_pages": 5, "created": 12},
            },
        ]

    monkeypatch.setattr(LegalCrawlService, "list_sources", staticmethod(fake_list))

    status = await LegalCrawlService.source_status()

    assert len(status) == 1
    src = status[0]
    assert src["name"] == "VBPL Trung ??ng"
    assert src["scope"] == "central"
    assert src["enabled"] is True
    assert src["last_status"] == "completed"
    assert src["last_run_stats"]["created"] == 12
