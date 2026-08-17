"""Repair the reviewed domain metadata of imported 116/2026/TT-BCA.

The command changes exactly one PostgreSQL document/scope row and metadata for
the same 246 vector IDs in the two serving collections.  Embeddings, legal
text, articles and chunks are not regenerated or deleted.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

import chromadb
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backup_legal_retrieval import _database_url


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
DOCUMENT_ID = 127597
LAW_NUMBER = "116/2026/TT-BCA"
EXPECTED_CHUNKS = 246
FIELD_ID = 9
FIELD_NAME = "An ninh trật tự"
DOMAIN = "cu_tru_an_ninh"
CORE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong"
SOURCE_COLLECTION = "legal_chunks_vnlegal_lal"


def corrected_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if int(metadata.get("document_id") or 0) != DOCUMENT_ID:
        raise ValueError("REPAIR_116_VECTOR_DOCUMENT_MISMATCH")
    if str(metadata.get("law_number") or "") != LAW_NUMBER:
        raise ValueError("REPAIR_116_VECTOR_LAW_MISMATCH")
    return {
        **dict(metadata),
        "field_id": FIELD_ID,
        "field_name": FIELD_NAME,
        "domain_slug": DOMAIN,
        "doc_status": "active",
        "status": "active",
    }


def _chroma_path() -> Path:
    values = dotenv_values(ROOT / ".env")
    value = str(
        os.getenv("LEGAL_CHROMA_PATH")
        or values.get("LEGAL_CHROMA_PATH")
        or r"D:\legal-chatbot-data\chroma_store"
    )
    return Path(value)


def _document_vectors(collection: Any) -> dict[str, Any]:
    return collection.get(
        where={"document_id": DOCUMENT_ID},
        include=["metadatas"],
    )


def _validate_vector_snapshot(snapshot: Mapping[str, Any]) -> None:
    ids = list(snapshot.get("ids") or [])
    metadatas = list(snapshot.get("metadatas") or [])
    if len(ids) != EXPECTED_CHUNKS or len(metadatas) != EXPECTED_CHUNKS:
        raise RuntimeError("REPAIR_116_VECTOR_COUNT_MISMATCH")
    if len(set(ids)) != EXPECTED_CHUNKS:
        raise RuntimeError("REPAIR_116_VECTOR_IDS_NOT_UNIQUE")
    for metadata in metadatas:
        corrected_metadata(metadata)


def repair(output_path: Path) -> dict[str, Any]:
    engine = create_engine(_database_url())
    client = chromadb.PersistentClient(path=str(_chroma_path()))
    collections = {
        CORE_COLLECTION: client.get_collection(CORE_COLLECTION),
        SOURCE_COLLECTION: client.get_collection(SOURCE_COLLECTION),
    }
    before_vectors = {
        name: _document_vectors(collection)
        for name, collection in collections.items()
    }
    for snapshot in before_vectors.values():
        _validate_vector_snapshot(snapshot)
    id_sets = {name: set(value["ids"]) for name, value in before_vectors.items()}
    if id_sets[CORE_COLLECTION] != id_sets[SOURCE_COLLECTION]:
        raise RuntimeError("REPAIR_116_VECTOR_COLLECTION_MEMBERSHIP_MISMATCH")

    with engine.begin() as connection:
        before_db = dict(
            connection.execute(
                text(
                    """
                    SELECT d.id, d.law_number, d.field_id, d.status,
                           s.included, s.reason, s.domain,
                           count(DISTINCT a.id) AS article_count,
                           count(ch.id) AS chunk_count
                    FROM legal_documents d
                    JOIN legal_search_scope s ON s.document_id = d.id
                    LEFT JOIN legal_articles a ON a.document_id = d.id
                    LEFT JOIN legal_article_chunks ch ON ch.article_id = a.id
                    WHERE d.id = :document_id
                    GROUP BY d.id, s.included, s.reason, s.domain
                    """
                ),
                {"document_id": DOCUMENT_ID},
            ).mappings().one()
        )
    if before_db["law_number"] != LAW_NUMBER or before_db["status"] != "active":
        raise RuntimeError("REPAIR_116_DATABASE_IDENTITY_MISMATCH")
    if int(before_db["article_count"] or 0) != 30 or int(before_db["chunk_count"] or 0) != EXPECTED_CHUNKS:
        raise RuntimeError("REPAIR_116_DATABASE_STRUCTURE_MISMATCH")

    prepared = {
        "schema_version": "golden-294-116-domain-repair-v1",
        "phase": "prepared",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "document_id": DOCUMENT_ID,
        "law_number": LAW_NUMBER,
        "before_db": before_db,
        "before_vectors": before_vectors,
        "full_backup": str(ROOT / "backups" / "golden294-live" / "20260811T024600Z"),
        "mutation_performed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    updated: list[str] = []
    try:
        for name, collection in collections.items():
            snapshot = before_vectors[name]
            collection.update(
                ids=snapshot["ids"],
                metadatas=[corrected_metadata(value) for value in snapshot["metadatas"]],
            )
            updated.append(name)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE legal_documents SET field_id = :field_id "
                    "WHERE id = :document_id AND law_number = :law_number"
                ),
                {
                    "field_id": FIELD_ID,
                    "document_id": DOCUMENT_ID,
                    "law_number": LAW_NUMBER,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE legal_search_scope
                    SET included = TRUE,
                        reason = 'metadata_domain_corrected_2026-08-11',
                        domain = :domain,
                        evaluated_as_of = :as_of,
                        evaluated_at = :evaluated_at
                    WHERE document_id = :document_id
                    """
                ),
                {
                    "domain": DOMAIN,
                    "as_of": date(2026, 8, 11),
                    "evaluated_at": datetime.now(),
                    "document_id": DOCUMENT_ID,
                },
            )
    except Exception:
        for name in updated:
            snapshot = before_vectors[name]
            collections[name].update(
                ids=snapshot["ids"], metadatas=snapshot["metadatas"]
            )
        raise

    after_vectors = {
        name: _document_vectors(collection)
        for name, collection in collections.items()
    }
    for snapshot in after_vectors.values():
        _validate_vector_snapshot(snapshot)
        if any(corrected_metadata(value) != value for value in snapshot["metadatas"]):
            raise RuntimeError("REPAIR_116_VECTOR_POSTCONDITION_FAILED")
    with engine.connect() as connection:
        after_db = dict(
            connection.execute(
                text(
                    """
                    SELECT d.id, d.law_number, d.field_id, d.status,
                           s.included, s.reason, s.domain,
                           (SELECT count(*) FROM legal_articles a WHERE a.document_id=d.id) AS article_count,
                           (SELECT count(*) FROM legal_article_chunks ch JOIN legal_articles a ON a.id=ch.article_id WHERE a.document_id=d.id) AS chunk_count
                    FROM legal_documents d
                    JOIN legal_search_scope s ON s.document_id=d.id
                    WHERE d.id=:document_id
                    """
                ),
                {"document_id": DOCUMENT_ID},
            ).mappings().one()
        )
    engine.dispose()
    if (
        after_db["field_id"] != FIELD_ID
        or after_db["domain"] != DOMAIN
        or after_db["included"] is not True
        or after_db["article_count"] != before_db["article_count"]
        or after_db["chunk_count"] != before_db["chunk_count"]
    ):
        raise RuntimeError("REPAIR_116_DATABASE_POSTCONDITION_FAILED")

    report = {
        **prepared,
        "phase": "applied",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "after_db": after_db,
        "after_vector_counts": {
            name: len(value["ids"]) for name, value in after_vectors.items()
        },
        "preservation": {
            "embedding_values_recomputed": False,
            "article_text_changed": False,
            "chunk_text_changed": False,
            "vector_ids_changed": False,
            "hard_delete": False,
        },
        "mutation_performed": True,
    }
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
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
    print(json.dumps({"document_id": report["document_id"], "status": "applied"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
