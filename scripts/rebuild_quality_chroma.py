"""Re-embed eligible core passages into a rollback-safe shadow collection."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

import chromadb
from sqlalchemy import text

try:
    from scripts.legal_search_server import (
        CHROMA_ACTIVE_COLLECTION_FILE,
        CHROMA_COLLECTION,
        CHROMA_PATH,
        retriever,
    )
except ModuleNotFoundError:
    from legal_search_server import (
        CHROMA_ACTIVE_COLLECTION_FILE,
        CHROMA_COLLECTION,
        CHROMA_PATH,
        retriever,
    )

from api.retrieval_release_contracts import require_staging_collection_target


QUALITY_VERSION = "legal-chunk-quality-v1"
DEFAULT_TARGET = "legal_chunks_vnlegal_lal_haiphong_qv1_20260722"
ROW_BATCH = int(os.getenv("LEGAL_CHROMA_REBUILD_ROW_BATCH", "512"))
EMBED_BATCH = int(os.getenv("LEGAL_CHROMA_REBUILD_EMBED_BATCH", "32"))

COUNT_SQL = """
SELECT COUNT(*)
FROM legal_chunk_quality q
JOIN legal_article_chunks c ON c.id = q.chunk_id
JOIN legal_articles a ON a.id = c.article_id
JOIN legal_documents d ON d.id = a.document_id
WHERE q.quality_version = :quality_version
  AND q.eligible = TRUE
  AND d.status = 'active'
  AND a.status = 'active'
  AND d.effective_date <= CURRENT_DATE
  AND (d.expired_date IS NULL OR d.expired_date > CURRENT_DATE)
  AND EXISTS (
      SELECT 1 FROM legal_search_scope s
      WHERE s.document_id = d.id AND s.included = TRUE
  )
"""

ROWS_SQL = """
SELECT
    c.id AS chunk_id, c.chunk_index, c.heading AS chunk_heading, c.content,
    a.id AS article_id, a.article_number, q.cleaned_article_title AS article_title,
    d.id AS document_id, d.title AS document_title, d.law_number,
    d.document_type, d.issuing_agency, d.scope, d.sector, d.field_id,
    f.name AS field_name, d.source_url, d.effective_date,
    scope_row.domain AS domain_slug
FROM legal_chunk_quality q
JOIN legal_article_chunks c ON c.id = q.chunk_id
JOIN legal_articles a ON a.id = c.article_id
JOIN legal_documents d ON d.id = a.document_id
LEFT JOIN legal_fields f ON f.id = d.field_id
LEFT JOIN LATERAL (
    SELECT s.domain
    FROM legal_search_scope s
    WHERE s.document_id = d.id AND s.included = TRUE
    ORDER BY s.evaluated_at DESC NULLS LAST
    LIMIT 1
) scope_row ON TRUE
WHERE q.quality_version = :quality_version
  AND q.eligible = TRUE
  AND d.status = 'active'
  AND a.status = 'active'
  AND d.effective_date <= CURRENT_DATE
  AND (d.expired_date IS NULL OR d.expired_date > CURRENT_DATE)
  AND scope_row.domain IS NOT NULL
ORDER BY c.id
"""


def _passage(row: dict[str, Any]) -> str:
    return "\n".join(
        str(row.get(key) or "").strip()
        for key in (
            "document_title", "law_number", "document_type", "issuing_agency",
            "scope", "sector", "field_name", "article_title", "chunk_heading", "content",
        )
        if str(row.get(key) or "").strip()
    )


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "chunk_id": int(row["chunk_id"]),
        "article_id": int(row["article_id"]),
        "chunk_index": int(row.get("chunk_index") or 0),
        "document_id": int(row["document_id"]),
        "document_title": row.get("document_title"),
        "law_number": row.get("law_number"),
        "document_type": row.get("document_type"),
        "issuing_agency": row.get("issuing_agency"),
        "scope": row.get("scope"),
        "sector": row.get("sector"),
        "field_id": int(row["field_id"]) if row.get("field_id") is not None else None,
        "field_name": row.get("field_name"),
        "article_number": row.get("article_number"),
        "article_title": row.get("article_title"),
        "source_url": row.get("source_url"),
        "effective_date": row["effective_date"].isoformat() if row.get("effective_date") else None,
        "domain_slug": row.get("domain_slug"),
        "quality_version": QUALITY_VERSION,
        "status": "active",
    }
    return {key: value for key, value in metadata.items() if value is not None and value != ""}


def _activate(target: str) -> None:
    CHROMA_ACTIVE_COLLECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CHROMA_ACTIVE_COLLECTION_FILE.with_suffix(".tmp")
    temporary.write_text(target + "\n", encoding="utf-8")
    temporary.replace(CHROMA_ACTIVE_COLLECTION_FILE)


def rebuild(*, target_name: str, apply: bool, activate: bool, resume: bool) -> dict[str, Any]:
    try:
        active_pointer = require_staging_collection_target(target_name, CHROMA_PATH) or CHROMA_COLLECTION
    except RuntimeError as exc:
        raise ValueError("Shadow target must differ from the active collection") from exc
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    with retriever._engine.connect() as connection:
        expected = int(connection.execute(text(COUNT_SQL), {"quality_version": QUALITY_VERSION}).scalar_one())
    if not apply:
        return {"mode": "dry-run", "active_collection": active_pointer, "target_collection": target_name, "expected_count": expected}

    names = {item.name for item in client.list_collections()}
    if target_name in names:
        if not resume:
            raise FileExistsError("Shadow collection exists; pass --resume or choose another target")
        target = client.get_collection(target_name)
    else:
        target = client.create_collection(target_name, metadata={"quality_version": QUALITY_VERSION})

    started = perf_counter()
    processed = 0
    embedded = 0
    with retriever._engine.connect().execution_options(stream_results=True) as connection:
        result = connection.execute(text(ROWS_SQL), {"quality_version": QUALITY_VERSION}).mappings()
        while True:
            rows = [dict(row) for row in result.fetchmany(ROW_BATCH)]
            if not rows:
                break
            ids = [f"chunk-{row['chunk_id']}" for row in rows]
            existing = set(target.get(ids=ids, include=[]).get("ids") or []) if resume else set()
            pending = [(identifier, row) for identifier, row in zip(ids, rows) if identifier not in existing]
            if pending:
                passages = [_passage(row) for _, row in pending]
                embeddings = retriever.encode_passages(passages, batch_size=EMBED_BATCH)
                target.upsert(
                    ids=[identifier for identifier, _ in pending],
                    embeddings=embeddings,
                    metadatas=[_metadata(row) for _, row in pending],
                )
                embedded += len(pending)
            processed += len(rows)
            if processed == len(rows) or processed % 5000 < len(rows):
                print(json.dumps({"processed": processed, "expected": expected, "embedded": embedded, "target_count": target.count()}), flush=True)

    actual = int(target.count())
    sample = target.peek(limit=min(100, actual), include=["metadatas"])
    noisy_titles = sum(
        1 for metadata in sample.get("metadatas") or []
        if len(str((metadata or {}).get("article_title") or "")) > 180
    )
    valid = actual == expected and noisy_titles == 0
    if activate and valid:
        _activate(target_name)
    report = {
        "mode": "apply",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "previous_active_collection": active_pointer,
        "active_pointer_unchanged": not bool(activate and valid),
        "target_collection": target_name,
        "expected_count": expected,
        "actual_count": actual,
        "embedded_this_run": embedded,
        "sample_noisy_title_count": noisy_titles,
        "valid": valid,
        "activated": bool(activate and valid),
        "elapsed_seconds": round(perf_counter() - started, 1),
    }
    if activate and not valid:
        raise RuntimeError(json.dumps(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = rebuild(target_name=args.target, apply=args.apply, activate=args.activate, resume=args.resume)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
