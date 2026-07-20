"""Seed exactly two review-only crawler candidates for the pilot demo.

The records are deliberately metadata-only and remain pending. They are never
imported, embedded, or exposed to public RAG until an admin verifies them.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_crawl_service import LegalCrawlService  # noqa: E402
from open_notebook.database.repository import (  # noqa: E402
    ensure_record_id,
    repo_create,
    repo_query,
)


DEMO_CANDIDATES = (
    {
        "external_id": "demo-weekly-review-ho-tich-001",
        "title": "[DEMO] Văn bản mới về hộ tịch cần admin xác minh",
        "domain": "ho_tich_chung_thuc",
        "scope": "central",
        "source_url": "https://vbpl.vn/van-ban/trung-uong",
        "description": "Bản ghi demo quy trình phát hiện văn bản mới và chuyển admin duyệt.",
    },
    {
        "external_id": "demo-weekly-review-xay-dung-001",
        "title": "[DEMO] Văn bản Hải Phòng về đất đai - xây dựng cần admin xác minh",
        "domain": "dat_dai_xay_dung",
        "scope": "haiphong",
        "source_url": "https://vbpl.vn/van-ban/dia-phuong?province=thanh-pho-hai-phong",
        "description": "Bản ghi demo quy trình quét nguồn Hải Phòng và chuyển admin duyệt.",
    },
)


async def seed() -> dict[str, int]:
    sources = await LegalCrawlService.ensure_vbpl_sources()
    source_by_scope = {str(item.get("sitemap_scope") or ""): item for item in sources}
    created = 0
    existing = 0
    now = datetime.now(timezone.utc)

    for item in DEMO_CANDIDATES:
        rows = await repo_query(
            "SELECT id FROM legal_crawl_candidate WHERE external_id = $external_id LIMIT 1;",
            {"external_id": item["external_id"]},
        )
        if rows:
            existing += 1
            continue

        source = source_by_scope.get(item["scope"])
        if not source:
            raise RuntimeError(f"Missing crawler source for scope {item['scope']}")

        fingerprint = hashlib.sha256(item["external_id"].encode("utf-8")).hexdigest()
        await repo_create(
            "legal_crawl_candidate",
            {
                "source": ensure_record_id(source["id"]),
                "external_id": item["external_id"],
                "detail_url": item["source_url"],
                "source_url": item["source_url"],
                "sitemap_url": item["source_url"],
                "sitemap_lastmod": None,
                "law_number": None,
                "title": item["title"],
                "description": item["description"],
                "document_type": "demo_review_candidate",
                "issuing_agency": None,
                "scope": item["scope"],
                "status": "pending",
                "review_status": "pending",
                "suggested_action": "demo_review_only",
                "comparison_status": "new",
                "detected_changes": [],
                "review_note": None,
                "imported_document": None,
                "raw_metadata": {
                    "candidate_origin": "weekly_crawler_demo",
                    "is_demo": True,
                    "metadata_only": True,
                    "confirmed_official_source": False,
                    "public_rag_allowed": False,
                },
                "content_hash": fingerprint,
                "created": now,
                "updated": now,
                "domain": item["domain"],
                "source_type": "document",
                "proposal_reason": "Candidate demo để kiểm thử hàng chờ admin; không dùng làm căn cứ pháp lý.",
                "submitted_by": None,
            },
        )
        created += 1

    return {"created": created, "existing": existing, "total_demo": len(DEMO_CANDIDATES)}


if __name__ == "__main__":
    print(asyncio.run(seed()))
