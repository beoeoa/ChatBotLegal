"""
End-to-end crawl and embedding pipeline for the multi-source legal crawler.

Usage:
    python scripts/crawl_and_embed.py                     # Crawl all sources, embed new candidates
    python scripts/crawl_and_embed.py --source vbpl_main   # Crawl single source
    python scripts/crawl_and_embed.py --dry-run             # List what would be crawled
    python scripts/crawl_and_embed.py --embed-only          # Only embed pending approved candidates
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from loguru import logger

from api.crawlers.source_registry import SourceRegistry, SourceType
from api.crawlers.orchestrator import MultiSourceCrawlService


LEGAL_SEARCH_URL = os.getenv("LEGAL_SEARCH_URL", "http://127.0.0.1:8765").rstrip("/")
API_URL = os.getenv("API_URL", "http://127.0.0.1:5055").rstrip("/")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def crawl_sources(source_id: str | None = None, dry_run: bool = False):
    """Crawl sources and print results."""
    SourceRegistry.initialize()
    
    if source_id:
        sources = [SourceRegistry.get(source_id)]
        if not sources[0]:
            logger.error(f"Source not found: {source_id}")
            return
    else:
        sources = SourceRegistry.get_enabled()
    
    all_docs = []
    
    for source in sources:
        if dry_run:
            print(f"\n[DRY RUN] Would crawl: {source.name} ({source.source_type.value})")
            print(f"  Base URL: {source.base_url}")
            print(f"  Strategy: {source.crawl_strategy}")
            print(f"  Max/run: {source.max_per_run}")
            print(f"  Interval: {source.interval_minutes}min")
            continue
        
        print(f"\n--- Crawling: {source.name} ---")
        result = await MultiSourceCrawlService.crawl_source(source.source_id)
        docs = result.get("documents", [])
        all_docs.extend(docs)
        
        print(f"  Status: {result['status']}")
        print(f"  Documents found: {len(docs)}")
        
        for i, doc in enumerate(docs[:3]):
            print(f"  [{i+1}] {doc.get('title', 'N/A')[:80]}")
            print(f"      Law: {doc.get('law_number', 'N/A')}")
            print(f"      Scope: {doc.get('scope', 'N/A')}")
            print(f"      Content: {len(doc.get('content', ''))} chars")
    
    return all_docs


async def create_candidates(docs: list[dict]):
    """Create candidate records from crawled documents."""
    candidates_created = 0
    for doc in docs:
        try:
            await MultiSourceCrawlService._create_candidate(doc)
            candidates_created += 1
        except Exception as e:
            logger.warning(f"Failed to create candidate: {e}")
    
    print(f"\nCandidates created: {candidates_created}/{len(docs)}")
    return candidates_created


async def embed_approved_candidates():
    """Fetch approved candidates and send them to legal search server for embedding."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Get approved candidates
            resp = await client.get(
                f"{LEGAL_SEARCH_URL}/candidates",
                params={"status": "approved", "limit": 10},
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            
            if not candidates:
                print("No approved candidates to embed.")
                return
            
            print(f"Found {len(candidates)} approved candidates for embedding.")
            
            for candidate in candidates:
                try:
                    # Import into legal search
                    import_resp = await client.post(
                        f"{LEGAL_SEARCH_URL}/import",
                        json={
                            "title": candidate.get("title", ""),
                            "law_number": candidate.get("law_number", ""),
                            "document_type": candidate.get("document_type", "VanBanPhapLuat"),
                            "issuing_agency": candidate.get("issuing_agency", ""),
                            "scope": candidate.get("scope", "central"),
                            "sector": candidate.get("sector", ""),
                            "field_id": 0,
                            "issued_date": candidate.get("issued_date", ""),
                            "effective_date": candidate.get("effective_date", ""),
                            "expired_date": candidate.get("expired_date", ""),
                            "source_url": candidate.get("source_url", ""),
                            "content": candidate.get("content", ""),
                            "confirmed_official_source": True,
                        },
                    )
                    import_resp.raise_for_status()
                    result = import_resp.json()
                    print(f"  Embedded: {candidate.get('title', 'N/A')[:60]} - chunks: {result.get('chunks_count', '?')}")
                    
                    # Mark as imported
                    candidate_id = candidate.get("id", "").split(":")[-1]
                    await client.post(
                        f"{API_URL}/legal/candidates/{candidate_id}/decide",
                        json={"decision": "imported", "review_note": "Auto-imported after embedding"},
                    )
                    
                except Exception as e:
                    logger.warning(f"Failed to embed candidate: {e}")
    
    except httpx.HTTPError as e:
        logger.error(f"Failed to connect to legal search server: {e}")
        print("ERROR: Legal search server (8765) is not running. Start it first.")
        print("  python scripts/legal_search_server.py")


async def show_stats():
    """Show current statistics."""
    SourceRegistry.initialize()
    
    print("\n=== SOURCE REGISTRY ===")
    sources = SourceRegistry.get_all()
    for s in sources:
        status_icon = "ON" if s.enabled else "OFF"
        last = s.last_crawl_at or "never"
        print(f"  [{status_icon}] {s.source_id}: {s.name}")
        print(f"       Type: {s.source_type.value} | Level: {s.official_level.value} | Scope: {s.scope}")
        print(f"       Last: {last} | Total: {s.total_crawled}")
    
    print("\n=== LEGAL SEARCH SERVER ===")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{LEGAL_SEARCH_URL}/health")
            health = resp.json()
            print(f"  Status: OK")
            print(f"  Indexed records: {health.get('indexed_records', '?')}")
            print(f"  Database chunks: {health.get('database_chunks', '?')}")
            print(f"  Collection: {health.get('collection', '?')}")
    except Exception:
        print("  Status: NOT RUNNING (start with: python scripts/legal_search_server.py)")
    
    try:
        stats = await MultiSourceCrawlService.get_candidate_stats()
        print(f"\n=== CANDIDATES ===")
        print(f"  Total: {stats['total']}")
        print(f"  Pending: {stats['pending']}")
        print(f"  Approved: {stats['approved']}")
        print(f"  Imported: {stats['imported']}")
        print(f"  Rejected: {stats['rejected']}")
    except Exception:
        pass




async def get_chroma_chunk_count() -> int | None:
    """Best-effort Chroma chunk count from legal search health endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{LEGAL_SEARCH_URL}/health")
            resp.raise_for_status()
            health = resp.json()
            for key in ("database_chunks", "indexed_records", "chunk_count"):
                if key in health and isinstance(health[key], int):
                    return int(health[key])
    except Exception as e:
        logger.warning(f"Could not read Chroma chunk count: {e}")
    return None


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    # Accept YYYY-MM-DD or DD/MM/YYYY
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    if "/" in value:
        parts = value.replace("-", "/").split("/")
        if len(parts) == 3:
            d, m, y = parts
            if len(y) == 2:
                y = "20" + y
            return f"{y.zfill(4)}-{m.zfill(2)}-{d.zfill(2)}"
    return None


async def run_full_pipeline(docs: list[dict]) -> dict[str, int]:
    """Import each document via correct endpoint (structured/unstructured)."""
    imported = 0
    skipped = 0
    errors = 0
    structured_count = 0
    unstructured_count = 0
    skip_reasons: dict[str, int] = {}

    before_chunks = await get_chroma_chunk_count()
    if before_chunks is not None:
        print(f"\nChroma chunks BEFORE: {before_chunks}")

    async with httpx.AsyncClient(timeout=180) as client:
        # Resolve a valid field_id once (import requires field_id >= 1)
        field_id = 1
        try:
            fields_resp = await client.get(f"{LEGAL_SEARCH_URL}/import/fields")
            if fields_resp.status_code == 200:
                fields = fields_resp.json().get("fields") or fields_resp.json().get("items") or []
                if fields and isinstance(fields, list):
                    first = fields[0]
                    field_id = int(first.get("id") or first.get("field_id") or 1)
        except Exception:
            field_id = 1

        for doc in docs:
            structure = (doc.get("structure") or "auto").strip().lower()
            title = (doc.get("title") or "N/A")[:80]
            content = (doc.get("content") or "").strip()
            law_number = (doc.get("law_number") or "").strip()
            scope = (doc.get("scope") or "central").strip()

            if not content or len(content) < 50:
                reason = "content_too_short"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                print(f"  [SKIP] {title} | reason={reason}")
                skipped += 1
                continue
            if not law_number:
                reason = "missing_law_number"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                print(f"  [SKIP] {title} | reason={reason}")
                skipped += 1
                continue

            issued = _normalize_date(doc.get("issued_date"))
            effective = _normalize_date(doc.get("effective_date")) or issued or datetime.now(timezone.utc).date().isoformat()
            expired = _normalize_date(doc.get("expired_date"))

            try:
                payload = {
                    "title": doc.get("title") or "Untitled",
                    "law_number": law_number,
                    "document_type": doc.get("document_type") or "VanBanPhapLuat",
                    "issuing_agency": doc.get("issuing_agency") or "Unknown",
                    "scope": scope,
                    "sector": doc.get("sector") or "",
                    "field_id": field_id,
                    "issued_date": issued,
                    "effective_date": effective,
                    "expired_date": expired,
                    "source_url": doc.get("source_url") or "",
                    "content": content,
                    "confirmed_official_source": True,
                    "structure": structure if structure in {"structured", "unstructured"} else "auto",
                }

                endpoint = "/import/unstructured" if payload["structure"] == "unstructured" else "/import"
                resp = await client.post(f"{LEGAL_SEARCH_URL}{endpoint}", json=payload)

                if resp.status_code >= 400:
                    detail = resp.text[:250]
                    # Treat duplicates as skip, not hard error
                    if "đã tồn tại" in detail.lower() or "ton tai" in detail.lower() or "duplicate" in detail.lower():
                        reason = "duplicate_law_number"
                        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                        print(f"  [SKIP] {title} | reason={reason}")
                        skipped += 1
                        continue
                    print(f"  [ERROR] {title} | {detail}")
                    errors += 1
                    continue

                result = resp.json()
                if payload["structure"] == "structured" or result.get("structure") == "structured":
                    structured_count += 1
                    used_structure = "structured"
                else:
                    unstructured_count += 1
                    used_structure = "unstructured"

                chunks = result.get("chunks_count") or result.get("chunk_count") or "?"
                print(
                    f"  [IMPORTED] {title} | structure={used_structure} | "
                    f"doc_id={result.get('document_id', '?')} | chunks={chunks}"
                )
                imported += 1

            except Exception as e:
                print(f"  [ERROR] {title} | {e}")
                errors += 1

    after_chunks = await get_chroma_chunk_count()
    if after_chunks is not None:
        print(f"Chroma chunks AFTER: {after_chunks}")
        if before_chunks is not None:
            print(f"Chroma chunks DELTA: {after_chunks - before_chunks}")

    if skip_reasons:
        print("Skip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  - {reason}: {count}")

    return {
        "crawled": len(docs),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "structured": structured_count,
        "unstructured": unstructured_count,
        "chroma_before": before_chunks if before_chunks is not None else -1,
        "chroma_after": after_chunks if after_chunks is not None else -1,
    }


async def main():
    parser = argparse.ArgumentParser(description="Multi-source legal document crawler and embedding pipeline")
    parser.add_argument("--source", type=str, help="Crawl specific source by ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be crawled without executing")
    parser.add_argument("--embed-only", action="store_true", help="Only embed approved candidates")
    parser.add_argument("--stats", action="store_true", help="Show statistics only")
    parser.add_argument("--all", action="store_true", help="Crawl all enabled sources and embed")
    parser.add_argument("--embed-all", action="store_true", help="Crawl all sources, create candidates, and import/embed")
    
    args = parser.parse_args()
    
    if args.stats:
        await show_stats()
        return
    
    if args.embed_only:
        await show_stats()
        await embed_approved_candidates()
        return
    
    if args.embed_all:
        await show_stats()
        docs = await crawl_sources(None, args.dry_run)
        
        if not args.dry_run and docs:
            stats = await run_full_pipeline(docs)
            # Save results
            output_file = Path("notebook_data") / "crawl_results" / f"crawl_{now_iso()[:19].replace(':', '-')}.json"
            output_file.parent.mkdir(exist_ok=True)
            output_file.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n=== PIPELINE SUMMARY ===")
            for k, v in stats.items():
                print(f"  {k}: {v}")
            print(f"Saved results to: {output_file}")
        return
    
    if args.all or args.source:
        await show_stats()
        source_id = args.source or None
        docs = await crawl_sources(source_id, args.dry_run)

        if not args.dry_run and docs:
            print(f"\nTotal crawled: {len(docs)} documents")
            await create_candidates(docs)
            stats = await run_full_pipeline(docs)

            output_file = Path("notebook_data") / "crawl_results" / f"crawl_{now_iso()[:19].replace(':', '-')}.json"
            output_file.parent.mkdir(exist_ok=True)
            output_file.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Saved results to: {output_file}")
            print("\n=== PIPELINE SUMMARY ===")
            for k, v in stats.items():
                print(f"  {k}: {v}")
        return
    
    # Default: show stats and usage
    await show_stats()
    print("\nUsage:")
    print("  python scripts/crawl_and_embed.py --stats          Show statistics")
    print("  python scripts/crawl_and_embed.py --dry-run        Show what would be crawled")
    print("  python scripts/crawl_and_embed.py --all            Crawl all sources + embed")
    print("  python scripts/crawl_and_embed.py --embed-all      Crawl + import + embed all sources")
    print("  python scripts/crawl_and_embed.py --source vbpl_main  Crawl specific source")
    print("  python scripts/crawl_and_embed.py --embed-only     Only embed pending candidates")


if __name__ == "__main__":
    asyncio.run(main())
