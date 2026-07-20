"""
Multi-source crawl orchestration service.

Coordinates crawling across all registered sources, manages candidate review
workflow, deduplication, and feeds into the legal import/embedding pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from api.crawlers.source_registry import (
    SourceRegistry, LegalSource, SourceType
)
from api.crawlers.base_crawler import content_fingerprint
from api.crawlers.vbpl_crawler import VBPLCrawler
from api.crawlers.dvc_crawler import DVCCrawler
from api.crawlers.ubnd_crawler import UBNDCrawler
from api.crawlers.local_gov_crawler import LocalGovCrawler

LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8765"
).rstrip("/")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Article-structure markers used by legal import (/import expects "Dieu N.")
_ARTICLE_PATTERN = re.compile(
    r"(?mi)^\s*(?:Đ|D)i[eê]u\s+\d+(?:\s*[.:\-–]|\s+|$)"
)


def detect_document_structure(text: str) -> str:
    """Return structured if text has article blocks like 'Dieu N.', else unstructured."""
    if not text or not str(text).strip():
        return "unstructured"
    return "structured" if _ARTICLE_PATTERN.search(str(text)) else "unstructured"


def normalize_document_structure(doc: dict[str, Any]) -> dict[str, Any]:
    """Attach structure flag; keep original content untouched."""
    content = doc.get("content", "") or ""
    structure = detect_document_structure(content)
    doc["structure"] = structure
    # Keep fingerprint stable for content, but expose structure for downstream import routing
    return doc



def _get_crawler_for_source(source: LegalSource):
    """Return appropriate crawler instance for a source."""
    if source.source_type == SourceType.VBPL:
        return VBPLCrawler(source)
    elif source.source_type in (SourceType.DVC_NATIONAL, SourceType.DVC_HAIPHONG):
        return DVCCrawler(source)
    elif source.source_type in (SourceType.UBND_HAIPHONG, SourceType.SO_NGANH):
        return UBNDCrawler(source)
    elif source.source_type in (SourceType.QUAN_HUYEN, SourceType.PHUONG_XA):
        return LocalGovCrawler(source)
    else:
        return VBPLCrawler(source)  # fallback


class MultiSourceCrawlService:
    """Orchestrates crawling from multiple legal sources."""

    @classmethod
    def get_all_sources(cls) -> list[dict[str, Any]]:
        """List all registered sources with status."""
        sources = SourceRegistry.get_all()
        return [
            {
                "source_id": s.source_id,
                "name": s.name,
                "source_type": s.source_type.value,
                "base_url": s.base_url,
                "official_level": s.official_level.value,
                "scope": s.scope,
                "domain": s.domain,
                "enabled": s.enabled,
                "crawl_strategy": s.crawl_strategy,
                "interval_minutes": s.interval_minutes,
                "last_crawl_at": s.last_crawl_at,
                "last_crawl_status": s.last_crawl_status,
                "last_crawl_count": s.last_crawl_count,
                "total_crawled": s.total_crawled,
            }
            for s in sources
        ]

    @classmethod
    def get_source(cls, source_id: str) -> dict[str, Any] | None:
        source = SourceRegistry.get(source_id)
        if not source:
            return None
        return {
            "source_id": source.source_id,
            "name": source.name,
            "source_type": source.source_type.value,
            "base_url": source.base_url,
            "official_level": source.official_level.value,
            "scope": source.scope,
            "domain": source.domain,
            "enabled": source.enabled,
            "crawl_strategy": source.crawl_strategy,
            "interval_minutes": source.interval_minutes,
            "last_crawl_at": source.last_crawl_at,
            "last_crawl_status": source.last_crawl_status,
            "last_crawl_count": source.last_crawl_count,
            "total_crawled": source.total_crawled,
        }

    @classmethod
    def update_source(cls, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = SourceRegistry.get(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        
        for key in ["enabled", "interval_minutes", "lookback_days", "max_per_run"]:
            if key in payload and payload[key] is not None:
                setattr(source, key, payload[key])
        
        source.updated_at = _utcnow()
        return cls.get_source(source_id)

    @classmethod
    async def crawl_source(cls, source_id: str) -> dict[str, Any]:
        """Crawl a single source and return results."""
        source = SourceRegistry.get(source_id)
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        
        if not source.enabled:
            return {"source_id": source_id, "status": "disabled", "count": 0}
        
        try:
            crawler = _get_crawler_for_source(source)
            # Run in thread since HTTP calls are sync
            loop = asyncio.get_running_loop()
            documents = await loop.run_in_executor(None, crawler.crawl)
            documents = [normalize_document_structure(doc) for doc in documents]

            SourceRegistry.update_status(source_id, "success", len(documents))
            
            return {
                "source_id": source_id,
                "status": "success",
                "count": len(documents),
                "documents": documents,
            }
        except Exception as e:
            logger.error(f"Crawl failed for {source_id}: {e}")
            SourceRegistry.update_status(source_id, f"error: {str(e)[:200]}", 0)
            return {
                "source_id": source_id,
                "status": "error",
                "error": str(e)[:500],
                "count": 0,
            }

    @classmethod
    async def crawl_all(cls, only_due: bool = True) -> dict[str, Any]:
        """Crawl all enabled sources."""
        sources = SourceRegistry.get_due() if only_due else SourceRegistry.get_enabled()
        
        results = {}
        total = 0
        
        for source in sources:
            result = await cls.crawl_source(source.source_id)
            results[source.source_id] = result
            total += result.get("count", 0)
        
        return {
            "status": "completed",
            "sources_crawled": len(results),
            "total_documents": total,
            "results": results,
            "timestamp": _utcnow(),
        }

    @classmethod
    async def crawl_and_create_candidates(cls, source_id: str) -> dict[str, Any]:
        """Crawl source and create candidate records for review."""
        result = await cls.crawl_source(source_id)
        documents = result.get("documents", [])
        
        if not documents:
            return result
        
        candidates_created = 0
        for doc in documents:
            try:
                await cls._create_candidate(doc)
                candidates_created += 1
            except Exception as e:
                logger.warning(f"Failed to create candidate for {doc.get('title', '?')[:50]}: {e}")
        
        result["candidates_created"] = candidates_created
        return result

    @classmethod
    async def _create_candidate(cls, doc: dict[str, Any]) -> str:
        """Create a candidate record in the database for admin review."""
        from open_notebook.database.repository import repo_create
        
        candidate_id = f"legal_crawl_candidate:{doc['content_fingerprint']}"
        
        candidate = {
            "id": candidate_id,
            "title": doc.get("title", ""),
            "law_number": doc.get("law_number", ""),
            "document_type": doc.get("document_type", "VanBanPhapLuat"),
            "issuing_agency": doc.get("issuing_agency", ""),
            "scope": doc.get("scope", "central"),
            "sector": doc.get("sector", ""),
            "source_url": doc.get("source_url", ""),
            "source_id": doc.get("source_id", ""),
            "source_type": doc.get("source_type", ""),
            "official_level": doc.get("official_level", "unverified"),
            "source_name": doc.get("source_name", ""),
            "content": doc.get("content", ""),
            "content_fingerprint": doc.get("content_fingerprint", ""),
            "structure": doc.get("structure") or detect_document_structure(doc.get("content", "")),
            "status": "pending",
            "crawled_at": doc.get("crawled_at", _utcnow()),
            "created_at": _utcnow(),
        }
        
        try:
            result = await repo_create("legal_crawl_candidate", candidate)
            return candidate_id
        except Exception:
            # If already exists, skip
            return candidate_id

    @classmethod
    async def get_candidate_stats(cls) -> dict[str, Any]:
        """Get candidate statistics."""
        from open_notebook.database.repository import repo_query
        
        try:
            total = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate GROUP ALL;"
            )
            pending = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'pending' GROUP ALL;"
            )
            approved = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'approved' GROUP ALL;"
            )
            imported = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'imported' GROUP ALL;"
            )
            rejected = await repo_query(
                "SELECT count() AS count FROM legal_crawl_candidate WHERE status = 'rejected' GROUP ALL;"
            )
        except Exception as e:
            logger.error(f"Error getting candidate stats: {e}")
            total = [{"count": 0}]
            pending = [{"count": 0}]
            approved = [{"count": 0}]
            imported = [{"count": 0}]
            rejected = [{"count": 0}]
        
        return {
            "total": total[0]["count"] if total else 0,
            "pending": pending[0]["count"] if pending else 0,
            "approved": approved[0]["count"] if approved else 0,
            "imported": imported[0]["count"] if imported else 0,
            "rejected": rejected[0]["count"] if rejected else 0,
        }

    @classmethod
    def get_sources_summary(cls) -> dict[str, Any]:
        """Get a summary of all sources."""
        sources = SourceRegistry.get_all()
        enabled = [s for s in sources if s.enabled]
        due = SourceRegistry.get_due()
        
        return {
            "total_sources": len(sources),
            "enabled_sources": len(enabled),
            "due_sources": len(due),
            "sources": [
                {
                    "source_id": s.source_id,
                    "name": s.name,
                    "source_type": s.source_type.value,
                    "official_level": s.official_level.value,
                    "scope": s.scope,
                    "enabled": s.enabled,
                    "last_crawl_at": s.last_crawl_at,
                    "last_crawl_status": s.last_crawl_status,
                    "last_crawl_count": s.last_crawl_count,
                    "total_crawled": s.total_crawled,
                }
                for s in sources
            ],
        }
