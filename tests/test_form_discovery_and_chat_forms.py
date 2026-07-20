import asyncio
import json
from pathlib import Path


def test_discovery_writes_candidate_only(monkeypatch, tmp_path):
    from scripts import discover_missing_faq_forms as discovery

    faq_path = tmp_path / "faq.json"
    candidate_path = tmp_path / "candidates.json"
    report_path = tmp_path / "report.json"
    discovery.FAQ_PATH = faq_path
    discovery.CANDIDATE_PATH = candidate_path
    discovery.DOWNLOAD_DIR = tmp_path / "downloads"
    faq_path.write_text(json.dumps({"faqs": [{
        "id": "faq-x", "question": "Tôi cần mẫu tờ khai đăng ký khai sinh", "requires_forms": True,
        "domain": "ho_tich_chung_thuc"
    }]}), encoding="utf-8")
    candidate_path.write_text(json.dumps({"records": [], "summary": {}}), encoding="utf-8")
    monkeypatch.setattr(discovery, "_search", lambda client, query: [])
    monkeypatch.setattr(discovery, "_fallback_pages", lambda question: ["https://dichvucong.gov.vn/form"])
    monkeypatch.setattr(discovery, "_file_links", lambda client, url: [])
    result = discovery.discover_missing_forms(limit=1)
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert result["candidates"] == 1
    assert payload["records"][0]["review_status"] == "candidate_pending_review"
    assert payload["records"][0]["downloadable"] is False
    assert payload["records"][0]["official_level"] == "candidate"


def test_chat_form_queue_is_non_blocking(monkeypatch):
    from api.routers import search

    queued = []
    monkeypatch.setattr(search, "_question_requests_forms", lambda question: True)
    monkeypatch.setattr(
        "api.form_discovery_service.queue_missing_form_discovery",
        lambda limit=50: queued.append(limit),
    )
    search._queue_form_discovery_if_needed("xin mẫu khai sinh", None)
    search._queue_form_discovery_if_needed("xin mẫu khai sinh", [{"id": "approved"}])
    assert queued == [50]

