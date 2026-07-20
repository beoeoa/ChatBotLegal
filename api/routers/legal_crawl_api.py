"""
API router for multi-source legal document crawling.

Legacy endpoints:
- GET /legacy/legal/crawl/sources - List in-memory registered sources
- GET /legacy/legal/crawl/source/{source_id} - Get source details
- PUT /legacy/legal/crawl/source/{source_id} - Update source config
- POST /legacy/legal/crawl/source/{source_id}/run - Run legacy crawl

The active admin UI and scheduler use `api.legal_crawl_service` through
`/legal/crawl/*`, which persists source IDs, run history, candidate review
state and audit metadata. Keeping this older adapter in a separate namespace
prevents it from shadowing the candidate-first API.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from api.crawlers.orchestrator import MultiSourceCrawlService

router = APIRouter(prefix="/legacy/legal/crawl", tags=["legacy-legal-crawl"])


@router.get("/sources")
async def list_sources() -> dict[str, Any]:
    """List all registered crawl sources."""
    return {
        "sources": MultiSourceCrawlService.get_all_sources(),
        "summary": MultiSourceCrawlService.get_sources_summary(),
    }


@router.get("/source/{source_id}")
async def get_source(source_id: str) -> dict[str, Any]:
    """Get details for a specific source."""
    source = MultiSourceCrawlService.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail=f"Source not found: {source_id}")
    return source


@router.put("/source/{source_id}")
async def update_source(
    source_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Update source configuration."""
    try:
        result = MultiSourceCrawlService.update_source(source_id, payload)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/source/{source_id}/run")
async def run_crawl(source_id: str) -> dict[str, Any]:
    """Run crawl for a single source."""
    try:
        result = await MultiSourceCrawlService.crawl_and_create_candidates(source_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-all")
async def run_all_crawls(only_due: bool = True) -> dict[str, Any]:
    """Run crawl for all enabled/due sources."""
    try:
        result = await MultiSourceCrawlService.crawl_all(only_due=only_due)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/candidates/stats")
async def candidate_stats() -> dict[str, Any]:
    """Get candidate review statistics."""
    stats = await MultiSourceCrawlService.get_candidate_stats()
    return stats


@router.get("/sources/summary")
async def sources_summary() -> dict[str, Any]:
    """Get summary of all sources."""
    return MultiSourceCrawlService.get_sources_summary()
