from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.expert_review_service import ExpertReviewError, update_review
from scripts.check_334_asset_health import _collect_urls


def _review_file(path: Path, *, status: str = "pending") -> None:
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "review_id": "case-1:citizen",
                        "case_id": "case-1",
                        "question": "Cần làm gì?",
                        "expert_review_status": status,
                        "expected_documents": None,
                        "expected_articles": None,
                        "expected_authority": None,
                        "mandatory_documents": None,
                        "conditional_documents": None,
                        "processing_time": None,
                        "fee": None,
                        "official_form_ids": None,
                        "expected_conclusion": None,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _approval() -> dict:
    return {
        "expert_review_status": "approved",
        "expected_documents": ["Luật A"],
        "expected_articles": ["Điều 1"],
        "expected_authority": "UBND cấp xã",
        "mandatory_documents": [],
        "conditional_documents": [],
        "processing_time": "Không áp dụng",
        "fee": "Không áp dụng",
        "official_form_ids": [],
        "expected_conclusion": "Kết luận đã duyệt",
        "expert_score": 9.5,
        "expert_name": "Chuyên gia A",
    }


def test_approval_requires_complete_expert_fields(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    _review_file(path)
    with pytest.raises(ExpertReviewError, match="Chưa đủ trường"):
        update_review("case-1:citizen", {"expert_review_status": "approved"}, path=path)


def test_approved_review_is_written_atomically(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    _review_file(path)
    result = update_review("case-1:citizen", _approval(), path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))["records"][0]
    assert result["expert_review_status"] == "approved"
    assert saved["reviewed_at"]
    assert saved["question"] == "Cần làm gì?"
    assert not path.with_suffix(".json.tmp").exists()


def test_disputed_review_needs_independent_second_expert(tmp_path: Path) -> None:
    path = tmp_path / "reviews.json"
    _review_file(path, status="expert_disputed")
    with pytest.raises(ExpertReviewError, match="chuyên gia thứ hai"):
        update_review("case-1:citizen", _approval(), path=path)
    approved = {**_approval(), "second_expert_name": "Chuyên gia B"}
    assert update_review("case-1:citizen", approved, path=path)["expert_review_status"] == "approved"


def test_asset_urls_are_deduplicated_and_typed() -> None:
    payload = {
        "results": [
            {
                "citations": [
                    {
                        "internal_url": "/api/legal/docs/1",
                        "pdf_url": "/api/legal/docs/1/download.pdf",
                        "source_url": "https://example.gov.vn/law/1",
                    }
                ],
                "recommended_forms": [{"download_url": "/api/procedures/forms/1"}],
            },
            {"citations": [{"internal_url": "/api/legal/docs/1"}]},
        ]
    }
    urls = _collect_urls(payload, "http://127.0.0.1:5055")
    assert len(urls) == 4
    assert {item["kind"] for item in urls} == {"viewer", "pdf", "source", "form"}
