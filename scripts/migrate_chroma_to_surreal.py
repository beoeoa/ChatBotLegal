
"""Migrate VNLegal-LAL Chroma vectors into SurrealDB source embeddings.

The script reads a Chroma collection, creates one SurrealDB `source` record per
document_id, and inserts the chunk embeddings into `source_embedding` using the
same schema as the existing embed_source job.

It is intentionally conservative:
- preserves the original embedding vectors from Chroma
- keeps imports idempotent by deleting existing source_embedding rows for a source
- supports an optional scope filter for incremental imports
- can run in dry-run mode for smoke checks
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import chromadb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from open_notebook.database.repository import ensure_record_id, repo_insert, repo_query, repo_upsert


DEFAULT_CHROMA_PATH = Path(os.getenv("LEGAL_CHROMA_PATH", r"J:\legal-chatbot-data\chroma_store"))
DEFAULT_COLLECTION = os.getenv("LEGAL_CHROMA_SOURCE_COLLECTION", "legal_chunks_vnlegal_lal")
DEFAULT_BATCH_SIZE = int(os.getenv("LEGAL_MIGRATION_BATCH_SIZE", "500"))

LOCAL_TERMS = {
    "ubnd cap xa",
    "uy ban nhan dan cap xa",
    "ubnd xa",
    "ubnd phuong",
    "hdnd cap xa",
    "hoi dong nhan dan cap xa",
    "cong an cap xa",
    "cong an xa",
    "cong an phuong",
    "cap xa",
    "xa phuong",
    "phuong xa",
    "bo phan mot cua",
    "trung tam phuc vu hanh chinh cong",
    "chinh quyen dia phuong",
}


def normalize(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    ascii_text = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    ).replace("?", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def source_record_id(document_id: Any) -> str:
    return f"source:vnlegal_{document_id}"


def infer_scope(metadata: dict[str, Any], text: str) -> str:
    raw = normalize(
        " ".join(
            str(metadata.get(key) or "")
            for key in ("scope", "issuing_agency", "document_title", "title", "applicability_info")
        )
        + " "
        + text[:1500]
    )
    if "hai phong" in raw:
        return "haiphong"
    if any(term in raw for term in LOCAL_TERMS):
        return "local"
    explicit = normalize(metadata.get("scope"))
    if explicit in {"central", "haiphong", "local"}:
        return explicit
    return "central"


def build_topics(metadata: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for key in ("field_name", "sector", "document_type", "law_number", "issuing_agency"):
        value = str(metadata.get(key) or "").strip()
        if value and value not in topics:
            topics.append(value)
    return topics


@dataclass
class DocumentBuffer:
    source_id: str
    document_id: int
    title: str
    scope: str
    topics: list[str] = field(default_factory=list)
    full_text: str | None = None
    chunk_count: int = 0
    chunk_order: int = 0

    def merge_chunk(self, content: str) -> None:
        if not self.full_text:
            self.full_text = content
        self.chunk_count += 1


def scope_matches(scope: str, requested: str | None) -> bool:
    if not requested:
        return True
    return scope == requested


async def upsert_source(buffer: DocumentBuffer, metadata: dict[str, Any]) -> None:
    await repo_upsert(
        "source",
        buffer.source_id,
        {
            "title": buffer.title,
            "full_text": buffer.full_text,
            "scope": buffer.scope,
            "topics": buffer.topics,
            "asset": {
                "url": str(metadata.get("source_url") or "").strip() or None,
                "file_path": None,
            },
        },
        add_timestamp=True,
    )


async def migrate_collection(
    *,
    chroma_path: Path,
    collection_name: str,
    batch_size: int,
    scope_filter: str | None,
    dry_run: bool,
    limit: int | None,
) -> dict[str, int]:
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    total = collection.count()
    if limit is not None:
        total = min(total, limit)

    print(
        f"collection={collection_name} total={total} batch_size={batch_size} scope_filter={scope_filter or 'all'} dry_run={dry_run}",
        flush=True,
    )

    buffers: dict[str, DocumentBuffer] = {}
    created_sources = 0
    updated_sources = 0
    inserted_embeddings = 0
    skipped_scope = 0
    skipped_missing_embeddings = 0
    skipped_missing_docs = 0
    deleted_existing = 0

    processed = 0
    started = perf_counter()

    for offset in range(0, total, batch_size):
        batch_limit = min(batch_size, total - offset)
        batch = collection.get(
            limit=batch_limit,
            offset=offset,
            include=["metadatas", "documents", "embeddings"],
        )
        ids = batch.get("ids")
        metadatas = batch.get("metadatas")
        documents = batch.get("documents")
        embeddings = batch.get("embeddings")
        ids = list(ids) if ids is not None else []
        metadatas = list(metadatas) if metadatas is not None else []
        documents = list(documents) if documents is not None else []
        embeddings = list(embeddings) if embeddings is not None else []

        if len(ids) != len(metadatas):
            raise RuntimeError(
                f"Chroma batch mismatch: ids={len(ids)} metadatas={len(metadatas)}"
            )

        chunk_records_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for item_id, metadata, document, embedding in zip(ids, metadatas, documents, embeddings):
            processed += 1
            if metadata is None:
                skipped_missing_docs += 1
                continue
            if embedding is None:
                skipped_missing_embeddings += 1
                continue

            document_id = metadata.get("document_id")
            if document_id is None:
                skipped_missing_docs += 1
                continue

            text = document or ""
            scope = infer_scope(metadata, text)
            if not scope_matches(scope, scope_filter):
                skipped_scope += 1
                continue

            src_id = source_record_id(document_id)
            title = str(metadata.get("document_title") or metadata.get("title") or metadata.get("law_number") or src_id)
            buffer = buffers.get(src_id)
            if buffer is None:
                buffer = DocumentBuffer(
                    source_id=src_id,
                    document_id=int(document_id),
                    title=title,
                    scope=scope,
                    topics=build_topics(metadata),
                    full_text=text.strip() or None,
                )
                buffers[src_id] = buffer
                created_sources += 1
            else:
                buffer.chunk_count += 0
                if scope == "haiphong" and buffer.scope != "haiphong":
                    buffer.scope = "haiphong"
                for topic in build_topics(metadata):
                    if topic not in buffer.topics:
                        buffer.topics.append(topic)
                if not buffer.full_text and text.strip():
                    buffer.full_text = text.strip()
                updated_sources += 1

            buffer.merge_chunk(text)
            buffer.chunk_order += 1
            chunk_records_by_source[src_id].append(
                {
                    "source": ensure_record_id(src_id),
                    "order": buffer.chunk_order,
                    "content": text,
                    "embedding": (embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)),
                }
            )

        if dry_run:
            if offset == 0 or (offset + batch_size) >= total:
                print(
                    f"processed={processed} sources_seen={len(buffers)} dry_run=true",
                    flush=True,
                )
            continue

        for src_id, records in chunk_records_by_source.items():
            buffer = buffers[src_id]
            if not records:
                continue
            await repo_upsert(
                "source",
                src_id,
                {
                    "title": buffer.title,
                    "full_text": buffer.full_text,
                    "scope": buffer.scope,
                    "topics": buffer.topics,
                },
                add_timestamp=True,
            )
            deleted = await repo_query(
                "DELETE source_embedding WHERE source = $source_id",
                {"source_id": src_id},
            )
            if deleted:
                deleted_existing += len(deleted)
            inserted = await repo_insert("source_embedding", records)
            inserted_embeddings += len(inserted)

        if offset == 0 or (offset + batch_size) % (batch_size * 20) == 0:
            elapsed = perf_counter() - started
            print(
                f"processed={processed}/{total} sources={len(buffers)} inserted_embeddings={inserted_embeddings} elapsed={elapsed:.1f}s",
                flush=True,
            )

    elapsed = perf_counter() - started
    if dry_run:
        print(
            f"dry_run complete processed={processed} sources_seen={len(buffers)} skipped_scope={skipped_scope}",
            flush=True,
        )
    return {
        "processed": processed,
        "sources_seen": len(buffers),
        "inserted_embeddings": inserted_embeddings,
        "created_sources": created_sources,
        "updated_sources": updated_sources,
        "skipped_scope": skipped_scope,
        "skipped_missing_embeddings": skipped_missing_embeddings,
        "skipped_missing_docs": skipped_missing_docs,
        "deleted_existing": deleted_existing,
        "elapsed_seconds": int(elapsed),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--scope-filter", choices=["central", "haiphong", "local"], default=None)
    parser.add_argument("--limit", type=int, default=None, help="Limit number of Chroma chunks processed")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not args.chroma_path.exists():
        raise SystemExit(f"Chroma path does not exist: {args.chroma_path}")

    summary = await migrate_collection(
        chroma_path=args.chroma_path,
        collection_name=args.collection,
        batch_size=args.batch_size,
        scope_filter=args.scope_filter,
        dry_run=args.dry_run,
        limit=args.limit,
    )
    print(summary, flush=True)
    if args.report:
        args.report.write_text(__import__("json").dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
