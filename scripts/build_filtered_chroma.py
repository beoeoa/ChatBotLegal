"""Copy approved VNLegal-LAL vectors into a scoped Chroma collection."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter

import chromadb
from sqlalchemy import text

from legal_search_server import CHROMA_PATH, retriever
from api.retrieval_release_contracts import require_staging_collection_target


SOURCE_COLLECTION = os.getenv(
    "LEGAL_CHROMA_SOURCE_COLLECTION", "legal_chunks_vnlegal_lal"
)
TARGET_COLLECTION = os.getenv(
    "LEGAL_CHROMA_FILTERED_COLLECTION",
    "legal_chunks_vnlegal_lal_haiphong",
)
# Larger batches keep the one-off scope rebuild practical on local disks while
# preserving a bounded memory footprint for the 1,024-dim VNLegal-LAL vectors.
BATCH_SIZE = int(os.getenv("LEGAL_CHROMA_BUILD_BATCH_SIZE", "2000"))


def main() -> None:
    if SOURCE_COLLECTION == TARGET_COLLECTION:
        raise RuntimeError("Source and target collections must be different")
    if not Path(CHROMA_PATH).exists():
        raise RuntimeError(f"Chroma path does not exist: {CHROMA_PATH}")
    # Retain this legacy scope builder for diagnostics, but never let it
    # delete/recreate the live collection by accident.
    require_staging_collection_target(TARGET_COLLECTION, CHROMA_PATH)

    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    source = client.get_collection(SOURCE_COLLECTION)
    try:
        client.delete_collection(TARGET_COLLECTION)
    except Exception:
        pass
    target = client.create_collection(
        TARGET_COLLECTION,
        metadata=source.metadata,
    )

    with retriever._engine.connect() as connection:
        chunk_ids = [
            int(row[0])
            for row in connection.execute(
                text(
                    """
                    SELECT c.id
                    FROM legal_search_scope s
                    JOIN legal_documents d ON d.id = s.document_id
                    JOIN legal_articles a ON a.document_id = d.id
                    JOIN legal_article_chunks c ON c.article_id = a.id
                    WHERE s.included = TRUE
                      AND d.status = 'active'
                      AND a.status = 'active'
                      AND (d.effective_date IS NULL
                           OR d.effective_date <= CURRENT_DATE)
                      AND (d.expired_date IS NULL
                           OR d.expired_date > CURRENT_DATE)
                      AND (a.effective_from IS NULL
                           OR a.effective_from <= CURRENT_DATE)
                      AND (a.effective_to IS NULL
                           OR a.effective_to > CURRENT_DATE)
                    ORDER BY c.id
                    """
                )
            )
        ]

    started = perf_counter()
    copied = 0
    missing = 0
    total = len(chunk_ids)
    for start in range(0, total, BATCH_SIZE):
        requested_ids = [
            f"chunk-{chunk_id}"
            for chunk_id in chunk_ids[start : start + BATCH_SIZE]
        ]
        batch = source.get(
            ids=requested_ids,
            include=["embeddings", "metadatas"],
        )
        found_ids = batch.get("ids") or []
        if found_ids:
            target.add(
                ids=found_ids,
                embeddings=batch["embeddings"],
                metadatas=batch["metadatas"],
            )
            copied += len(found_ids)
        missing += len(requested_ids) - len(found_ids)
        if start == 0 or (start + BATCH_SIZE) % 10000 == 0:
            print(
                f"processed={min(start + BATCH_SIZE, total)}/{total} "
                f"copied={copied} missing={missing}",
                flush=True,
            )

    elapsed = perf_counter() - started
    print(
        f"source={source.count()} target={target.count()} "
        f"requested={total} missing={missing} elapsed_seconds={elapsed:.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
