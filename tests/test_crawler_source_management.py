from unittest.mock import AsyncMock

import pytest

from api.legal_crawl_service import LegalCrawlService


CUSTOM_SOURCE = {
    "name": "Cổng thử nghiệm Hải Phòng",
    "base_url": "https://haiphong.gov.vn/van-ban-moi",
    "sitemap_scope": "haiphong",
    "interval_minutes": 1440,
    "lookback_days": 30,
    "max_documents_per_run": 20,
    "max_listing_pages_per_run": 5,
    "rate_limit_seconds": 1.5,
}


@pytest.mark.asyncio
async def test_custom_source_is_created_disabled_after_official_host_validation(monkeypatch):
    created: list[dict] = []

    async def fake_create(table, payload):
        assert table == "legal_crawl_source"
        created.append(payload)
        return [{"id": "legal_crawl_source:custom", **payload}]

    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.repo_query", AsyncMock(return_value=[]))

    source = await LegalCrawlService.create_source(CUSTOM_SOURCE)

    assert source["source_type"] == "official_listing"
    assert source["enabled"] is False
    assert source["is_default"] is False
    assert created[0]["base_url"] == CUSTOM_SOURCE["base_url"]


@pytest.mark.asyncio
async def test_custom_source_rejects_non_official_or_non_https_host():
    with pytest.raises(ValueError, match="nguồn chính thức"):
        await LegalCrawlService.create_source({**CUSTOM_SOURCE, "base_url": "https://example.org/law"})
    with pytest.raises(ValueError, match="HTTPS"):
        await LegalCrawlService.create_source({**CUSTOM_SOURCE, "base_url": "http://haiphong.gov.vn/law"})


@pytest.mark.asyncio
async def test_custom_source_rejects_duplicate_url(monkeypatch):
    monkeypatch.setattr("api.legal_crawl_service.repo_query", AsyncMock(return_value=[{"id": "legal_crawl_source:existing"}]))
    with pytest.raises(ValueError, match="đã có"):
        await LegalCrawlService.create_source(CUSTOM_SOURCE)


@pytest.mark.asyncio
async def test_default_source_can_be_disabled_but_not_deleted(monkeypatch):
    default_source = {
        "id": "legal_crawl_source:vbpl",
        "name": "VBPL Trung ương",
        "source_type": "vbpl_listing",
        "base_url": "https://vbpl.vn/van-ban/trung-uong",
        "enabled": True,
    }

    async def fake_query(_query, params=None):
        return [default_source]

    async def fake_update(_table, _record_id, payload):
        return [{**default_source, **payload}]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)

    updated = await LegalCrawlService.update_source(str(default_source["id"]), {"enabled": False})
    assert updated["enabled"] is False
    assert updated["is_default"] is True
    with pytest.raises(ValueError, match="không thể xóa"):
        await LegalCrawlService.delete_source(str(default_source["id"]))


@pytest.mark.asyncio
async def test_internal_queue_is_never_run_as_a_web_crawler(monkeypatch):
    source = {
        "id": "legal_crawl_source:officer_proposals",
        "name": "Officer document proposals",
        "source_type": "officer_proposal",
        "base_url": "local://officer-proposals",
        "enabled": True,
    }
    updates: list[dict] = []

    async def fake_query(query, params=None):
        if "FROM legal_crawl_source WHERE id" in query:
            return [source]
        return []

    async def fake_update(_table, _record_id, payload):
        updates.append(payload)
        return [{**source, **payload}]

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", AsyncMock())

    result = await LegalCrawlService.scan_source(str(source["id"]))

    assert result["status"] == "internal"
    assert "không quét web" in result["reason"].lower()
    assert updates[-1]["last_status"] == "internal"
    assert not LegalCrawlService.is_crawlable_source(source)


@pytest.mark.asyncio
async def test_internal_queue_can_be_renamed_but_identity_stays_locked(monkeypatch):
    source = {
        "id": "legal_crawl_source:officer_proposals",
        "name": "Officer document proposals",
        "source_type": "officer_proposal",
        "base_url": "local://officer-proposals",
        "enabled": False,
    }

    monkeypatch.setattr(
        "api.legal_crawl_service.repo_query",
        AsyncMock(return_value=[source]),
    )
    update = AsyncMock(return_value=[{**source, "name": "Đề xuất văn bản của cán bộ"}])
    monkeypatch.setattr("api.legal_crawl_service.repo_update", update)

    renamed = await LegalCrawlService.update_source(
        str(source["id"]),
        {"name": "Đề xuất văn bản của cán bộ"},
    )

    assert renamed["name"] == "Đề xuất văn bản của cán bộ"
    assert renamed["base_url"] == "local://officer-proposals"
    assert renamed["source_type"] == "officer_proposal"
    with pytest.raises(ValueError, match="chỉ được đổi tên"):
        await LegalCrawlService.update_source(
            str(source["id"]),
            {"base_url": "https://haiphong.gov.vn/van-ban"},
        )


@pytest.mark.asyncio
async def test_internal_queue_delete_archives_configuration_and_preserves_record(monkeypatch):
    source = {
        "id": "legal_crawl_source:officer_proposals",
        "name": "Đề xuất văn bản của cán bộ",
        "source_type": "officer_proposal",
        "base_url": "local://officer-proposals",
        "enabled": False,
        "last_status": "internal",
    }
    monkeypatch.setattr(
        "api.legal_crawl_service.repo_query",
        AsyncMock(return_value=[source]),
    )
    update = AsyncMock(return_value=[{**source, "last_status": "deleted"}])
    delete = AsyncMock()
    monkeypatch.setattr("api.legal_crawl_service.repo_update", update)
    monkeypatch.setattr("api.legal_crawl_service.repo_delete", delete)

    result = await LegalCrawlService.delete_source(str(source["id"]))

    assert result["deleted"] is True
    assert result["archived"] is True
    assert result["history_preserved"] is True
    assert update.await_args.args[2]["last_status"] == "deleted"
    assert update.await_args.args[2]["enabled"] is False
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_archived_internal_queue_is_hidden_from_source_list(monkeypatch):
    archived = {
        "id": "legal_crawl_source:officer_proposals",
        "name": "Đề xuất văn bản của cán bộ",
        "source_type": "officer_proposal",
        "base_url": "local://officer-proposals",
        "enabled": False,
        "last_status": "deleted",
    }
    active = {
        "id": "legal_crawl_source:source_gap",
        "name": "Khoảng trống nguồn",
        "source_type": "source_gap_candidate",
        "base_url": "local://source-gap-candidates",
        "enabled": False,
        "last_status": "internal",
    }
    monkeypatch.setattr(
        "api.legal_crawl_service.repo_query",
        AsyncMock(return_value=[archived, active]),
    )
    monkeypatch.setattr("api.legal_crawl_service.repo_update", AsyncMock())

    sources = await LegalCrawlService.list_sources()

    assert [str(source["id"]) for source in sources] == [str(active["id"])]


def test_strict_assessment_holds_missing_legal_evidence_in_vietnamese():
    recommendation = LegalCrawlService.build_review_recommendation(
        {
            "title": "Văn bản chưa đủ bằng chứng",
            "source_type": "document",
            "scope": "central",
            "raw_metadata": {"confirmed_official_source": False},
            "extraction_result": {"characters": 40, "ocr_status": "empty"},
            "duplicate_candidates": [{"id": "legal_crawl_candidate:duplicate"}],
        }
    )

    assert recommendation["action"] == "hold_for_evidence"
    assert recommendation["passed_hard_gates"] is False
    assert recommendation["scores"]["nguon_chinh_thuc"] == 0
    assert any("Thiếu" in item or "trùng" in item.lower() for item in recommendation["evidence"])


@pytest.mark.asyncio
async def test_summary_counts_pending_documents_without_form_candidates(monkeypatch):
    async def fake_query(query, params=None):
        if "status = 'pending' AND" in query and "source_type != 'form'" in query:
            return [{"count": 2}]
        if "status = 'pending' GROUP ALL" in query:
            return [{"count": 3}]
        return []

    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)

    summary = await LegalCrawlService.summary()

    assert summary["pending_candidates"] == 3
    assert summary["pending_document_candidates"] == 2
