#!/usr/bin/env python3
"""Build a read-only PostgreSQL/Chroma/validity reconciliation manifest."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chromadb
from sqlalchemy import create_engine, text

from api.legal_data_manifest import build_read_only_manifest
from api.legal_validity_registry import SNAPSHOT_PATH
from scripts.backup_legal_retrieval import _database_url
from scripts.feature005_db1_snapshot import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_CORE_COLLECTION,
    safe_database_target,
)

CHUNK_SQL_TEMPLATE = """
SELECT c.id AS chunk_id,
       d.status AS document_status,
       coalesce(a.status, 'active') AS article_status,
       {scope_expression} AS included,
       {eligible_expression} AS eligible,
       {canonical_expression} AS canonical_chunk_id,
       btrim(coalesce(c.content, '')) <> '' AS has_content
FROM legal_article_chunks c
JOIN legal_articles a ON a.id = c.article_id
JOIN legal_documents d ON d.id = a.document_id
{quality_join}
ORDER BY c.id
"""


SCOPE_EXPRESSION = """EXISTS (
           SELECT 1 FROM legal_search_scope s
           WHERE s.document_id = d.id AND s.included = true
       )"""


def _chunk_sql(*, has_quality: bool, has_scope: bool) -> str:
    return CHUNK_SQL_TEMPLATE.format(
        scope_expression=SCOPE_EXPRESSION if has_scope else "false",
        eligible_expression="coalesce(q.eligible, true)" if has_quality else "true",
        canonical_expression="q.canonical_chunk_id" if has_quality else "NULL::bigint",
        quality_join=(
            "LEFT JOIN legal_chunk_quality q ON q.chunk_id = c.id"
            if has_quality
            else ""
        ),
    )


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _active_collection(path: Path, fallback: str) -> str:
    configured = os.getenv("LEGAL_CHROMA_COLLECTION", "").strip()
    if configured:
        return configured
    pointer = path / "active_core_collection.txt"
    try:
        value = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or fallback


def _vector_chunk_ids(
    path: Path, collection_name: str, *, batch_size: int = 10_000
) -> set[int]:
    collection = chromadb.PersistentClient(path=str(path)).get_collection(
        collection_name
    )
    output: set[int] = set()
    offset = 0
    total = collection.count()
    while offset < total:
        payload = collection.get(
            include=["metadatas"], limit=batch_size, offset=offset
        )
        ids = payload.get("ids") or []
        metadatas = payload.get("metadatas") or []
        if not ids:
            break
        for vector_id, metadata in zip(ids, metadatas):
            raw = (metadata or {}).get("chunk_id")
            if raw in (None, "") and str(vector_id).startswith("chunk-"):
                raw = str(vector_id)[6:]
            try:
                output.add(int(raw))
            except (TypeError, ValueError):
                continue
        offset += len(ids)
    return output


def execute(
    *,
    output: Path,
    chroma_path: Path,
    collection_name: str,
    validity_path: Path,
) -> dict[str, Any]:
    # Reconciliation must target the reviewed serving corpus, never the generic
    # LEGAL_DATABASE_URL commonly exported for isolated/staging tests.
    url = _database_url()
    engine = create_engine(url, future=True)
    schema_warnings: list[str] = []
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION READ ONLY"))
            has_quality = bool(
                connection.execute(
                    text("SELECT to_regclass('public.legal_chunk_quality')")
                ).scalar()
            )
            has_scope = bool(
                connection.execute(
                    text("SELECT to_regclass('public.legal_search_scope')")
                ).scalar()
            )
            if not has_quality:
                schema_warnings.append(
                    "quality_sidecar_missing: duplicate classification is unavailable"
                )
            if not has_scope:
                schema_warnings.append(
                    "search_scope_missing: active serving membership is unavailable"
                )
            rows = [
                dict(row)
                for row in connection.execute(
                    text(_chunk_sql(has_quality=has_quality, has_scope=has_scope))
                ).mappings()
            ]
            transaction.rollback()
    finally:
        engine.dispose()
    vector_ids = _vector_chunk_ids(chroma_path, collection_name)
    manifest = build_read_only_manifest(
        postgres_rows=rows,
        vector_chunk_ids=vector_ids,
        collection_name=collection_name,
        validity_snapshot=_read_snapshot(validity_path),
        embedding_fingerprint=(
            os.getenv("VNLEGAL_LAL_MODEL_FINGERPRINT", "").strip() or None
        ),
        pipeline_version=(
            os.getenv("LEGAL_ANSWER_PIPELINE_VERSION", "answer-pipeline-v3").strip()
            or "answer-pipeline-v3"
        ),
    )
    manifest.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "database_target": safe_database_target(url),
            "chroma_path": str(chroma_path.resolve()),
            "validity_snapshot_path": str(validity_path.resolve()),
            "schema_warnings": schema_warnings,
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--collection")
    parser.add_argument("--validity-snapshot", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args()
    chroma_path = args.chroma_path.resolve()
    collection = args.collection or _active_collection(
        chroma_path, DEFAULT_CORE_COLLECTION
    )
    manifest = execute(
        output=args.output.resolve(),
        chroma_path=chroma_path,
        collection_name=collection,
        validity_path=args.validity_snapshot.resolve(),
    )
    print(json.dumps({"status": "ok", "counts": manifest["counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
