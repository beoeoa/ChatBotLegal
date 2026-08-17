"""Repair the approved current text of Article 4 of the Teacher Law.

The official 2025 amending law adds ``và trung học nghề`` after
``trung cấp`` in clause 1 Article 4 of 73/2025/QH15.  This bounded Phase-9
operator command changes exactly one article row, one existing chunk row and
the same vector ID in every serving collection where that ID already exists.
It never creates/deletes chunks and never re-indexes another document.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_legal_retrieval import _database_url


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
DOCUMENT_ID = 121955
ARTICLE_ID = 877617
CHUNK_ID = 701396
LAW_NUMBER = "73/2025/QH15"
ARTICLE_NUMBER = "4"
OLD_TEXT = "trình độ trung cấp trong cơ sở giáo dục nghề nghiệp"
NEW_TEXT = "trình độ trung cấp và trung học nghề trong cơ sở giáo dục nghề nghiệp"
OFFICIAL_AMENDMENT_URL = (
    "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187742"
)
SOURCE_COLLECTION = "legal_chunks_vnlegal_lal"
CORE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong"


def corrected_text(value: str) -> str:
    """Apply one exact official replacement and reject ambiguous input."""

    if value.count(NEW_TEXT) == 1 and OLD_TEXT not in value:
        return value
    if value.count(OLD_TEXT) != 1 or NEW_TEXT in value:
        raise ValueError("TEACHER_ARTICLE4_OFFICIAL_REPLACEMENT_NOT_EXACT")
    return value.replace(OLD_TEXT, NEW_TEXT, 1)


def validate_identity(row: Mapping[str, Any]) -> None:
    checks = {
        "document_id": int(row.get("document_id") or 0) == DOCUMENT_ID,
        "article_id": int(row.get("article_id") or 0) == ARTICLE_ID,
        "chunk_id": int(row.get("chunk_id") or 0) == CHUNK_ID,
        "law_number": str(row.get("law_number") or "") == LAW_NUMBER,
        "article_number": str(row.get("article_number") or "") == ARTICLE_NUMBER,
        "document_status": str(row.get("document_status") or "") == "active",
        "article_status": str(row.get("article_status") or "") == "active",
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(
            "TEACHER_ARTICLE4_DATABASE_IDENTITY_MISMATCH:" + ",".join(failed)
        )


def passage_text(row: Mapping[str, Any], chunk_content: str) -> str:
    return "\n".join(
        value
        for value in (
            row.get("document_title"),
            row.get("law_number"),
            row.get("document_type"),
            row.get("issuing_agency"),
            row.get("scope"),
            row.get("sector"),
            row.get("field_name"),
            row.get("article_title"),
            row.get("chunk_heading"),
            chunk_content,
        )
        if value
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_embedding(value: Any) -> str:
    return hashlib.sha256(np.asarray(value, dtype=np.float32).tobytes()).hexdigest()


def _chroma_path() -> Path:
    values = dotenv_values(ROOT / ".env")
    return Path(
        str(
            os.getenv("LEGAL_CHROMA_PATH")
            or values.get("LEGAL_CHROMA_PATH")
            or r"D:\legal-chatbot-data\chroma_store"
        )
    )


def _database_row(connection: Any) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT d.id AS document_id, d.title AS document_title,
                   d.law_number, d.document_type, d.issuing_agency,
                   d.scope, d.sector, d.status AS document_status,
                   f.name AS field_name,
                   a.id AS article_id, a.article_number,
                   a.title AS article_title, a.content AS article_content,
                   a.status AS article_status,
                   c.id AS chunk_id, c.chunk_index,
                   c.heading AS chunk_heading, c.content AS chunk_content
            FROM legal_documents d
            JOIN legal_fields f ON f.id = d.field_id
            JOIN legal_articles a ON a.document_id = d.id
            JOIN legal_article_chunks c ON c.article_id = a.id
            WHERE d.id = :document_id
              AND a.id = :article_id
              AND c.id = :chunk_id
            """
        ),
        {
            "document_id": DOCUMENT_ID,
            "article_id": ARTICLE_ID,
            "chunk_id": CHUNK_ID,
        },
    ).mappings().one()
    result = dict(row)
    validate_identity(result)
    return result


def repair(output_path: Path) -> dict[str, Any]:
    import chromadb
    from scripts.legal_search_server import LegalRetriever

    engine = create_engine(_database_url())
    with engine.connect() as connection:
        before_db = _database_row(connection)
    corrected_article = corrected_text(str(before_db["article_content"]))
    corrected_chunk = corrected_text(str(before_db["chunk_content"]))

    chroma = chromadb.PersistentClient(path=str(_chroma_path()))
    collections: dict[str, Any] = {}
    before_vectors: dict[str, dict[str, Any]] = {}
    for name in (CORE_COLLECTION, SOURCE_COLLECTION):
        collection = chroma.get_collection(name)
        snapshot = collection.get(
            ids=[f"chunk-{CHUNK_ID}"],
            include=["embeddings", "metadatas"],
        )
        if snapshot.get("ids"):
            collections[name] = collection
            before_vectors[name] = snapshot
    if SOURCE_COLLECTION not in before_vectors:
        raise RuntimeError("TEACHER_ARTICLE4_SOURCE_VECTOR_MISSING")
    for name, snapshot in before_vectors.items():
        metadata = snapshot["metadatas"][0]
        if int(metadata.get("chunk_id") or 0) != CHUNK_ID:
            raise RuntimeError(f"TEACHER_ARTICLE4_VECTOR_IDENTITY_MISMATCH:{name}")
        if str(metadata.get("law_number") or "") != LAW_NUMBER:
            raise RuntimeError(f"TEACHER_ARTICLE4_VECTOR_LAW_MISMATCH:{name}")

    prepared = {
        "schema_version": "golden-294-teacher-article4-repair-v1",
        "phase": "prepared",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "law_number": LAW_NUMBER,
        "document_id": DOCUMENT_ID,
        "article_id": ARTICLE_ID,
        "chunk_id": CHUNK_ID,
        "official_amendment_url": OFFICIAL_AMENDMENT_URL,
        "official_change": {"old": OLD_TEXT, "new": NEW_TEXT},
        "before": {
            "article_sha256": _sha256_text(str(before_db["article_content"])),
            "chunk_sha256": _sha256_text(str(before_db["chunk_content"])),
            "vectors": {
                name: _sha256_embedding(snapshot["embeddings"][0])
                for name, snapshot in before_vectors.items()
            },
        },
        "after_expected": {
            "article_sha256": _sha256_text(corrected_article),
            "chunk_sha256": _sha256_text(corrected_chunk),
        },
        "vector_collections": sorted(before_vectors),
        "full_backup": str(
            ROOT / "backups" / "golden294-live" / "20260811T024600Z"
        ),
        "mutation_performed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    retriever = LegalRetriever()
    embedding = retriever.encode_passages(
        [passage_text(before_db, corrected_chunk)], batch_size=1
    )[0]
    updated_collections: list[str] = []
    try:
        with engine.begin() as connection:
            locked = _database_row(connection)
            if locked["article_content"] != before_db["article_content"]:
                raise RuntimeError("TEACHER_ARTICLE4_ARTICLE_CHANGED_CONCURRENTLY")
            if locked["chunk_content"] != before_db["chunk_content"]:
                raise RuntimeError("TEACHER_ARTICLE4_CHUNK_CHANGED_CONCURRENTLY")
            connection.execute(
                text(
                    "UPDATE legal_articles SET content = :content "
                    "WHERE id = :article_id AND content = :before"
                ),
                {
                    "content": corrected_article,
                    "article_id": ARTICLE_ID,
                    "before": before_db["article_content"],
                },
            )
            connection.execute(
                text(
                    "UPDATE legal_article_chunks SET content = :content "
                    "WHERE id = :chunk_id AND content = :before"
                ),
                {
                    "content": corrected_chunk,
                    "chunk_id": CHUNK_ID,
                    "before": before_db["chunk_content"],
                },
            )
            for name, collection in collections.items():
                snapshot = before_vectors[name]
                collection.update(
                    ids=[f"chunk-{CHUNK_ID}"],
                    embeddings=[embedding],
                    metadatas=snapshot["metadatas"],
                )
                updated_collections.append(name)
    except Exception:
        for name in updated_collections:
            snapshot = before_vectors[name]
            collections[name].update(
                ids=[f"chunk-{CHUNK_ID}"],
                embeddings=[snapshot["embeddings"][0]],
                metadatas=snapshot["metadatas"],
            )
        raise

    with engine.connect() as connection:
        after_db = _database_row(connection)
    after_vectors = {
        name: collection.get(
            ids=[f"chunk-{CHUNK_ID}"], include=["embeddings", "metadatas"]
        )
        for name, collection in collections.items()
    }
    engine.dispose()
    if after_db["article_content"] != corrected_article:
        raise RuntimeError("TEACHER_ARTICLE4_ARTICLE_POSTCONDITION_FAILED")
    if after_db["chunk_content"] != corrected_chunk:
        raise RuntimeError("TEACHER_ARTICLE4_CHUNK_POSTCONDITION_FAILED")
    expected_vector_hash = _sha256_embedding(embedding)
    if any(
        _sha256_embedding(snapshot["embeddings"][0]) != expected_vector_hash
        for snapshot in after_vectors.values()
    ):
        raise RuntimeError("TEACHER_ARTICLE4_VECTOR_POSTCONDITION_FAILED")

    report = {
        **prepared,
        "phase": "applied",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "after": {
            "article_sha256": _sha256_text(after_db["article_content"]),
            "chunk_sha256": _sha256_text(after_db["chunk_content"]),
            "vector_sha256": expected_vector_hash,
        },
        "preservation": {
            "document_count_changed": False,
            "article_count_changed": False,
            "chunk_count_changed": False,
            "vector_ids_changed": False,
            "hard_delete": False,
        },
        "mutation_performed": True,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.approval != APPROVAL_PHRASE:
        raise ValueError("GOLDEN294_LIVE_APPROVAL_PHRASE_REQUIRED")
    report = repair(args.output.resolve())
    print(
        json.dumps(
            {
                "law_number": LAW_NUMBER,
                "article_id": ARTICLE_ID,
                "chunk_id": CHUNK_ID,
                "status": report["phase"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
