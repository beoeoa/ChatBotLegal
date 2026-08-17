from pathlib import Path
from unittest.mock import AsyncMock

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "legal_crawl"


def test_official_url_validation_rejects_private_and_unapproved_destinations():
    from api.crawlers.legal_document_pipeline import validate_official_public_url

    assert (
        validate_official_public_url("https://vbpl.vn/van-ban/test")
        == "https://vbpl.vn/van-ban/test"
    )
    for url in (
        "http://vbpl.vn/van-ban/test",
        "https://example.com/van-ban/test",
        "https://127.0.0.1/internal",
        "https://localhost/internal",
        "https://10.0.0.2/internal",
    ):
        with pytest.raises(ValueError):
            validate_official_public_url(url)


def test_clean_markdown_removes_page_chrome_and_preserves_legal_structure():
    from api.crawlers.legal_document_pipeline import clean_legal_html

    html = (FIXTURES / "multi_domain_legal_document.html").read_text(encoding="utf-8")
    result = clean_legal_html(html)

    markdown = result["clean_markdown"]
    assert "# Nghị định quy định đất đai" in markdown
    assert "## Chương I. Quy định chung" in markdown
    assert "### Điều 1. Phạm vi điều chỉnh" in markdown
    assert "| Hành vi | Mức phạt |" in markdown
    assert "Trang chủ" not in markdown
    assert "Nội dung quảng cáo" not in markdown
    assert "Bản quyền" not in markdown
    assert "window.track" not in markdown
    assert set(result["removed_noise"]) >= {"header", "navigation", "footer", "script"}


def test_clean_markdown_handles_nested_noise_elements_without_crashing():
    from api.crawlers.legal_document_pipeline import clean_legal_html

    result = clean_legal_html(
        """
        <main>
          <div class="share"><span class="share-item">Chia sẻ</span></div>
          <p>Điều 1. Quy định về giá đất trên địa bàn thành phố Hải Phòng.</p>
        </main>
        """
    )

    assert "Điều 1" in result["clean_markdown"]
    assert "Chia sẻ" not in result["clean_markdown"]


def test_normalize_document_extracts_metadata_and_multiple_domains():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    html = (FIXTURES / "multi_domain_legal_document.html").read_text(encoding="utf-8")
    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/test",
        final_url="https://vbpl.vn/van-ban/test",
        html=html,
        scope="central",
        method="fixture",
    )

    assert result["status"] == "ok"
    assert result["law_number"] == "42/2026/NĐ-CP"
    assert result["issuing_agency"] == "Chính phủ"
    assert result["issued_date"] == "2026-08-09"
    assert result["effective_date"] == "2026-09-01"
    assert result["primary_domain"] == "dat_dai_xay_dung"
    assert set(result["matched_domains"]) == {"dat_dai_xay_dung", "trat_tu_do_thi"}
    assert result["content_hash"]
    assert result["characters"] == len(result["clean_markdown"])


def test_normalize_document_holds_unrelated_content_for_review():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/test",
        final_url="https://vbpl.vn/van-ban/test",
        html="<main><h1>Thông báo lịch nghỉ</h1><p>Thông tin hoạt động nội bộ.</p></main>",
        scope="central",
        method="fixture",
    )

    assert result["status"] == "needs_review"
    assert result["matched_domains"] == []
    assert "domain_unresolved" in result["extraction"]["reason"]


def test_normalize_document_rejects_vbpl_loading_shell():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/chi-tiet/test--1",
        final_url="https://vbpl.vn/van-ban/chi-tiet/test--1",
        html=(
            "<main><h2>Đang tải dữ liệu...</h2>"
            "<p>Vui lòng chờ trong giây lát</p>"
            "<p>Portal VBPL - Đang tải nội dung...</p></main>"
        ),
        scope="central",
        method="fixture",
    )

    assert result["status"] == "rejected"
    assert result["clean_markdown"] == ""
    assert result["characters"] == 0
    assert result["extraction"]["complete"] is False
    assert "loading_placeholder" in result["extraction"]["reason"]


def test_normalize_document_prefers_legislation_json_ld_over_related_page_text():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
        final_url="https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
        html="""
            <head><script type="application/ld+json">{
              "@context":"https://schema.org", "@type":"Legislation",
              "name":"Nghị quyết về thửa đất nhỏ hẹp",
              "legislationIdentifier":"22/2025/NQ-HĐND",
              "legislationType":"Nghị quyết",
              "legislationDate":"2025-10-26T00:00:00",
              "legislationPassedBy":{"@type":"Organization","name":"HĐND Thành phố Hải Phòng"}
            }</script></head>
            <body><main>
              <p>Ngày có hiệu lực: 26/10/2025</p>
              <p>Điều 1. Quy định về đất đai và quyền sử dụng đất.</p>
              <p>Liên quan: Thông tư 39/2026/TT-NHNN.</p>
            </main></body>
        """,
        scope="haiphong",
        method="fixture",
    )

    assert result["law_number"] == "22/2025/NQ-HĐND"
    assert result["document_type"] == "Nghị quyết"
    assert result["issuing_agency"] == "HĐND Thành phố Hải Phòng"
    assert result["issued_date"] == "2025-10-26"
    assert result["effective_date"] == "2025-10-26"


def test_normalize_document_extracts_worded_effective_date_from_legal_text():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--test",
        final_url="https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--test",
        html="""
            <head><script type="application/ld+json">{
              "@context":"https://schema.org", "@type":"Legislation",
              "name":"Nghị quyết quy định hệ số điều chỉnh giá đất",
              "legislationIdentifier":"11/2026/NQ-HĐND",
              "legislationType":"Nghị quyết",
              "legislationDate":"2026-07-28",
              "legislationPassedBy":{"@type":"Organization","name":"HĐND thành phố Hải Phòng"}
            }</script></head>
            <body><main>
              <p>Điều 1. Phạm vi điều chỉnh về giá đất trên địa bàn thành phố Hải Phòng.</p>
              <p>Nghị quyết này có hiệu lực thi hành kể từ ngày 08 tháng 8 năm 2026.</p>
            </main></body>
        """,
        scope="haiphong",
        method="fixture",
    )

    assert result["issued_date"] == "2026-07-28"
    assert result["effective_date"] == "2026-08-08"
    assert "effective_date_missing" not in result["extraction"]["reason"]


def test_normalize_document_extends_truncated_official_identifier_from_heading():
    from api.crawlers.legal_document_pipeline import normalize_legal_document

    result = normalize_legal_document(
        source_url="https://vbpl.vn/van-ban/chi-tiet/quyet-dinh--test",
        final_url="https://vbpl.vn/van-ban/chi-tiet/quyet-dinh--test",
        html="""
            <head>
              <meta name="effective-date" content="2026-01-12">
              <script type="application/ld+json">{
                "@context":"https://schema.org", "@type":"Legislation",
                "name":"Quyết định số 253/2025/QĐ-UBND về quản lý du lịch",
                "legislationIdentifier":"253/2025",
                "legislationType":"Quyết định",
                "legislationDate":"2025-12-31",
                "legislationPassedBy":{"@type":"Organization","name":"UBND thành phố Hải Phòng"}
              }</script>
            </head>
            <body><main>
              <p>Số: 253/2025/QĐ-UBND</p>
              <p>Điều 1. Quy định quản lý hoạt động du lịch và đất đai tại Hải Phòng.</p>
            </main></body>
        """,
        scope="haiphong",
        method="fixture",
    )

    assert result["law_number"] == "253/2025/QĐ-UBND"


@pytest.mark.asyncio
async def test_enabled_detail_fetch_persists_normalized_content_and_metadata(monkeypatch):
    from api.legal_crawl_service import LegalCrawlService

    candidate_payloads = []

    async def fake_query(_query, _params=None):
        return []

    async def fake_create(table, payload):
        if table == "legal_crawl_candidate":
            candidate_payloads.append(payload)
            return [{"id": "legal_crawl_candidate:normalized"}]
        return [{"id": f"{table}:test"}]

    async def fake_fetch(_url, *, scope, timeout_seconds):
        assert scope == "haiphong"
        assert timeout_seconds == 45
        return {
            "status": "ok",
            "source_url": "https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
            "final_url": "https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
            "title": "Nghị quyết về đất đai Hải Phòng",
            "law_number": "22/2025/NQ-HĐND",
            "document_type": "Nghị quyết",
            "issuing_agency": "HĐND Thành phố Hải Phòng",
            "issued_date": "2025-10-26",
            "effective_date": "2025-10-26",
            "expired_date": None,
            "clean_markdown": "Điều 1. Quy định về đất đai. " * 20,
            "content_hash": "a" * 64,
            "primary_domain": "dat_dai_xay_dung",
            "matched_domains": ["dat_dai_xay_dung", "trat_tu_do_thi"],
            "domain_evidence": {
                "dat_dai_xay_dung": ["term:đất đai"],
                "trat_tu_do_thi": ["term:đô thị"],
            },
            "extraction": {"method": "fixture", "complete": True, "reason": ""},
        }

    monkeypatch.setenv("LEGAL_CRAWL_DETAIL_FETCH_ENABLED", "true")
    monkeypatch.setattr("api.legal_crawl_service.repo_query", fake_query)
    monkeypatch.setattr("api.legal_crawl_service.repo_create", fake_create)
    monkeypatch.setattr("api.legal_crawl_service.fetch_normalized_legal_document", fake_fetch)
    monkeypatch.setattr(LegalCrawlService, "persist_automatic_assessment", AsyncMock())
    monkeypatch.setattr(
        LegalCrawlService,
        "find_runtime_document_conflict",
        AsyncMock(return_value=None),
    )

    created, reason = await LegalCrawlService._create_listing_candidate(
        {
            "id": "legal_crawl_source:vbpl_hp",
            "base_url": "https://vbpl.vn/van-ban/dia-phuong",
            "sitemap_scope": "haiphong",
            "domains": ["dat_dai_xay_dung", "trat_tu_do_thi"],
            "content_fetch_allowed": True,
        },
        {
            "url": "https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
            "title": "Tiêu đề listing không được dùng thay metadata chi tiết",
            "context": "Công báo có nhiều số hiệu liên quan",
            "source_type": "document",
            "law_number": "",
            "issued_date": "",
            "document_type": "",
            "issuing_agency": "",
        },
        "legal_crawl_run:test",
    )

    assert created is True, reason
    assert reason is None
    payload = candidate_payloads[0]
    assert payload["law_number"] == "22/2025/NQ-HĐND"
    assert payload["content_hash"] == "a" * 64
    assert payload["content"].startswith("Điều 1")
    assert payload["raw_metadata"]["metadata_only"] is False
    assert payload["raw_metadata"]["matched_domains"] == [
        "dat_dai_xay_dung", "trat_tu_do_thi"
    ]
