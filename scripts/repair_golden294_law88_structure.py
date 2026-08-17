"""Repair the two swallowed structural units in 88/2025/QH15.

The legacy parser stored the replacement text of Article 99 inside the row for
Article 87 and Article 101 inside Article 100.  This approved Phase-9 command
splits those exact source units, reuses every existing chunk ID, creates only
the three additional chunks required by deterministic structural chunking and
updates the same IDs in both active Chroma collections.  No historical
document is deleted and no unrelated vector is re-indexed.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
import unicodedata

import numpy as np
from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_structural_chunking import split_parent_children
from api.user_service import write_audit_log
from open_notebook.database.repository import repo_query
from scripts.backup_legal_retrieval import _database_url


APPROVAL_PHRASE = "Duyệt cập nhật dữ liệu sống và tái kiểm định 294 ca"
DOCUMENT_ID = 122154
LAW_NUMBER = "88/2025/QH15"
ARTICLE_87_ID = 879432
ARTICLE_100_ID = 879433
SOURCE_COLLECTION = "legal_chunks_vnlegal_lal"
CORE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong"
FULL_BACKUP = ROOT / "backups" / "golden294-live" / "20260811T024600Z"

UNIT_DEFINITIONS = {
    "87": {
        "article_id": ARTICLE_87_ID,
        "start_marker": "17.",
        "end_marker": "18.",
        "embedded_number": "99",
        "embedded_title_prefix": "Điều 99.",
        "embedded_title": (
            "Điều 99. Lập hồ sơ đề nghị áp dụng biện pháp đưa vào trường giáo dưỡng"
        ),
        "amendment_path": "Khoản 17 Điều 1",
    },
    "100": {
        "article_id": ARTICLE_100_ID,
        "start_marker": "19.",
        "end_marker": "20.",
        "embedded_number": "101",
        "embedded_title_prefix": "Điều 101.",
        "embedded_title": (
            "Điều 101. Lập hồ sơ đề nghị áp dụng biện pháp đưa vào cơ sở giáo dục bắt buộc"
        ),
        "amendment_path": "Khoản 19 Điều 1",
    },
}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_embedding(value: Any) -> str:
    return hashlib.sha256(np.asarray(value, dtype=np.float32).tobytes()).hexdigest()


def _normalise_heading(value: str) -> str:
    # VBPL content mixes precomposed Vietnamese characters with decomposed
    # OCR/HTML text (for example ``Điều`` and ``Điều``).  Compare headings
    # through one deterministic accent-insensitive form while retaining the
    # original source text in PostgreSQL and evidence chunks.
    folded = "".join(
        character
        for character in unicodedata.normalize("NFD", value)
        if not unicodedata.combining(character)
    )
    folded = folded.replace("đ", "d").replace("Đ", "D")
    return " ".join(folded.split()).casefold()


def split_swallowed_unit(
    content: str,
    *,
    start_marker: str,
    end_marker: str,
    embedded_title_prefix: str,
) -> tuple[str, str]:
    """Return the original parent and exact embedded replacement body."""

    lines = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()

    def marker_index(marker: str) -> int:
        matches = [
            index for index, line in enumerate(lines) if line.strip().startswith(marker)
        ]
        if len(matches) != 1:
            raise ValueError(f"LAW88_STRUCTURE_MARKER_NOT_EXACT:{marker}:{len(matches)}")
        return matches[0]

    start = marker_index(start_marker)
    end = marker_index(end_marker)
    if not 0 < start < end:
        raise ValueError("LAW88_STRUCTURE_MARKER_ORDER_INVALID")
    heading_matches = [
        index
        for index in range(start + 1, end)
        if _normalise_heading(lines[index]).startswith(
            _normalise_heading(embedded_title_prefix)
        )
    ]
    if len(heading_matches) != 1:
        raise ValueError("LAW88_STRUCTURE_EMBEDDED_HEADING_NOT_EXACT")
    parent = "\n".join(lines[:start]).strip()
    embedded = "\n".join(lines[heading_matches[0] + 1 : end]).strip()
    # Remove only the closing quotation artifact of the amending instrument;
    # retain the legal sentence's own final full stop.
    embedded = re.sub(r"[”\"]\.\s*$", "", embedded).strip()
    if not parent or not embedded:
        raise ValueError("LAW88_STRUCTURE_EMPTY_SPLIT")
    if start_marker in parent or end_marker in embedded:
        raise ValueError("LAW88_STRUCTURE_SPLIT_BOUNDARY_FAILED")
    return parent, embedded


def build_units(rows: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for parent_number, definition in UNIT_DEFINITIONS.items():
        row = rows[parent_number]
        parent, embedded = split_swallowed_unit(
            str(row["content"]),
            start_marker=str(definition["start_marker"]),
            end_marker=str(definition["end_marker"]),
            embedded_title_prefix=str(definition["embedded_title_prefix"]),
        )
        units.extend(
            [
                {
                    "article_number": parent_number,
                    "article_id": int(row["id"]),
                    "title": str(row["title"]),
                    "content": parent,
                    "effective_from": row.get("effective_from"),
                    "effective_to": row.get("effective_to"),
                    "status": str(row.get("status") or "active"),
                    "amendment_path": None,
                },
                {
                    "article_number": str(definition["embedded_number"]),
                    "article_id": None,
                    "title": str(definition["embedded_title"]),
                    "content": embedded,
                    "effective_from": row.get("effective_from"),
                    "effective_to": row.get("effective_to"),
                    "status": str(row.get("status") or "active"),
                    "amendment_path": str(definition["amendment_path"]),
                },
            ]
        )
    return units


def build_chunks(unit: Mapping[str, Any]) -> list[dict[str, Any]]:
    chunks = split_parent_children(
        {
            "article_number": str(unit["article_number"]),
            "title": str(unit["title"]),
            "content": str(unit["content"]),
        }
    )
    amendment_path = str(unit.get("amendment_path") or "")
    if amendment_path:
        for chunk in chunks:
            chunk["heading"] = (
                f"{amendment_path} {LAW_NUMBER} > {chunk.get('heading') or unit['title']}"
            )
    return chunks


def allocate_existing_chunk_ids(
    units: list[dict[str, Any]], existing_by_parent: Mapping[str, list[Mapping[str, Any]]]
) -> tuple[list[dict[str, Any]], int]:
    """Reuse all 14 IDs before requesting exactly three additive IDs."""

    planned: list[dict[str, Any]] = []
    new_count = 0
    for parent_number in ("87", "100"):
        pair = [
            unit
            for unit in units
            if unit["article_number"]
            in {
                parent_number,
                str(UNIT_DEFINITIONS[parent_number]["embedded_number"]),
            }
        ]
        generated = [
            {"unit": unit, "chunk": chunk}
            for unit in pair
            for chunk in build_chunks(unit)
        ]
        existing = list(existing_by_parent[parent_number])
        if len(generated) < len(existing):
            raise ValueError("LAW88_STRUCTURE_WOULD_DELETE_EXISTING_CHUNKS")
        for index, item in enumerate(generated):
            planned.append(
                {
                    **item,
                    "chunk_id": int(existing[index]["id"]) if index < len(existing) else None,
                }
            )
        new_count += len(generated) - len(existing)
    if new_count != 3:
        raise ValueError(f"LAW88_STRUCTURE_ADDITIVE_CHUNK_COUNT:{new_count}")
    return planned, new_count


def _chroma_path() -> Path:
    values = dotenv_values(ROOT / ".env")
    return Path(
        str(
            os.getenv("LEGAL_CHROMA_PATH")
            or values.get("LEGAL_CHROMA_PATH")
            or r"J:\legal-chatbot-data\chroma_store"
        )
    )


def _database_snapshot(connection: Any, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    articles = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, document_id, article_number, title, content,
                       effective_from, effective_to, status
                FROM legal_articles
                WHERE id IN (:a87, :a100)
                ORDER BY id
                """ + suffix
            ),
            {"a87": ARTICLE_87_ID, "a100": ARTICLE_100_ID},
        ).mappings()
    ]
    chunks = [
        dict(row)
        for row in connection.execute(
            text(
                """
                SELECT id, article_id, chunk_index, heading, content, created_at
                FROM legal_article_chunks
                WHERE article_id IN (:a87, :a100)
                ORDER BY article_id, chunk_index, id
                """ + suffix
            ),
            {"a87": ARTICLE_87_ID, "a100": ARTICLE_100_ID},
        ).mappings()
    ]
    if {row["id"] for row in articles} != {ARTICLE_87_ID, ARTICLE_100_ID}:
        raise RuntimeError("LAW88_STRUCTURE_PARENT_IDENTITY_MISMATCH")
    if len(chunks) != 14:
        raise RuntimeError(f"LAW88_STRUCTURE_EXISTING_CHUNK_COUNT:{len(chunks)}")
    document = dict(
        connection.execute(
            text(
                """
                SELECT d.*, f.name AS field_name
                FROM legal_documents d
                JOIN legal_fields f ON f.id=d.field_id
                WHERE d.id=:document_id
                """
            ),
            {"document_id": DOCUMENT_ID},
        ).mappings().one()
    )
    if document["law_number"] != LAW_NUMBER or document["status"] != "active":
        raise RuntimeError("LAW88_STRUCTURE_DOCUMENT_IDENTITY_MISMATCH")
    return {"document": document, "articles": articles, "chunks": chunks}


def _passage(document: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    unit = item["unit"]
    chunk = item["chunk"]
    return "\n".join(
        str(value)
        for value in (
            document.get("title"),
            document.get("law_number"),
            document.get("document_type"),
            document.get("issuing_agency"),
            document.get("scope"),
            document.get("sector"),
            document.get("field_name"),
            unit.get("title"),
            chunk.get("heading"),
            chunk.get("content"),
        )
        if value
    )


async def _active_admin_id() -> str:
    rows = await repo_query(
        "SELECT id FROM user_account WHERE username='admin' AND is_active=true LIMIT 1;"
    )
    if len(rows) != 1:
        raise RuntimeError("LAW88_STRUCTURE_ACTIVE_ADMIN_MISSING")
    return str(rows[0]["id"])


def repair(output_path: Path) -> dict[str, Any]:
    import chromadb
    from scripts.legal_search_server import LegalRetriever

    # The repository keeps one asynchronous SurrealDB connection.  Reusing a
    # single loop for both the preflight identity lookup and the final audit
    # prevents that connection from being attached to an already-closed loop.
    audit_loop = asyncio.new_event_loop()
    try:
        actor_user_id = audit_loop.run_until_complete(_active_admin_id())
    except Exception:
        audit_loop.close()
        raise
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        before = _database_snapshot(connection)
    rows = {str(row["article_number"]): row for row in before["articles"]}
    units = build_units(rows)
    existing_by_parent = {
        number: [
            row
            for row in before["chunks"]
            if int(row["article_id"]) == int(UNIT_DEFINITIONS[number]["article_id"])
        ]
        for number in UNIT_DEFINITIONS
    }
    planned, additive_count = allocate_existing_chunk_ids(units, existing_by_parent)

    client = chromadb.PersistentClient(path=str(_chroma_path()))
    collections = {
        name: client.get_collection(name) for name in (SOURCE_COLLECTION, CORE_COLLECTION)
    }
    existing_vector_ids = [f"chunk-{row['id']}" for row in before["chunks"]]
    before_vectors: dict[str, dict[str, Any]] = {}
    for name, collection in collections.items():
        snapshot = collection.get(
            ids=existing_vector_ids, include=["embeddings", "metadatas"]
        )
        if set(snapshot.get("ids") or []) != set(existing_vector_ids):
            raise RuntimeError(f"LAW88_STRUCTURE_VECTOR_SET_MISMATCH:{name}")
        before_vectors[name] = snapshot

    prepared = {
        "schema_version": "golden294-law88-structure-v1",
        "phase": "prepared",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "law_number": LAW_NUMBER,
        "document_id": DOCUMENT_ID,
        "existing_article_ids": [ARTICLE_87_ID, ARTICLE_100_ID],
        "existing_chunk_ids": [int(row["id"]) for row in before["chunks"]],
        "planned_new_articles": ["99", "101"],
        "planned_additive_chunk_count": additive_count,
        "before_sha256": {
            "articles": {
                str(row["id"]): _sha256_text(str(row["content"]))
                for row in before["articles"]
            },
            "chunks": {
                str(row["id"]): _sha256_text(str(row["content"]))
                for row in before["chunks"]
            },
            "vectors": {
                name: {
                    vector_id: _sha256_embedding(embedding)
                    for vector_id, embedding in zip(
                        snapshot["ids"], snapshot["embeddings"], strict=True
                    )
                }
                for name, snapshot in before_vectors.items()
            },
        },
        "full_backup": str(FULL_BACKUP),
        "mutation_performed": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    retriever = LegalRetriever()
    embeddings = retriever.encode_passages(
        [_passage(before["document"], item) for item in planned], batch_size=16
    )
    new_article_ids: dict[str, int] = {}
    new_chunk_ids: list[int] = []
    db_committed = False
    vector_touched: dict[str, list[str]] = {name: [] for name in collections}
    try:
        with engine.begin() as connection:
            locked = _database_snapshot(connection, lock=True)
            if [row["content"] for row in locked["articles"]] != [
                row["content"] for row in before["articles"]
            ]:
                raise RuntimeError("LAW88_STRUCTURE_PARENT_CHANGED_CONCURRENTLY")
            if [row["content"] for row in locked["chunks"]] != [
                row["content"] for row in before["chunks"]
            ]:
                raise RuntimeError("LAW88_STRUCTURE_CHUNK_CHANGED_CONCURRENTLY")

            for unit in units:
                if unit["article_id"] is not None:
                    connection.execute(
                        text("UPDATE legal_articles SET content=:content WHERE id=:id"),
                        {"content": unit["content"], "id": unit["article_id"]},
                    )
                    continue
                article_id = int(
                    connection.execute(
                        text(
                            """
                            INSERT INTO legal_articles (
                                document_id, article_number, title, content,
                                effective_from, effective_to, status
                            ) VALUES (
                                :document_id, :article_number, :title, :content,
                                :effective_from, :effective_to, :status
                            ) RETURNING id
                            """
                        ),
                        {"document_id": DOCUMENT_ID, **unit},
                    ).scalar_one()
                )
                unit["article_id"] = article_id
                new_article_ids[str(unit["article_number"])] = article_id

            for item in planned:
                unit = item["unit"]
                chunk = item["chunk"]
                values = {
                    "article_id": int(unit["article_id"]),
                    "chunk_index": int(chunk["chunk_index"]),
                    "heading": str(chunk.get("heading") or unit["title"]),
                    "content": str(chunk["content"]),
                }
                if item["chunk_id"] is not None:
                    connection.execute(
                        text(
                            """
                            UPDATE legal_article_chunks
                            SET article_id=:article_id, chunk_index=:chunk_index,
                                heading=:heading, content=:content
                            WHERE id=:chunk_id
                            """
                        ),
                        {**values, "chunk_id": item["chunk_id"]},
                    )
                else:
                    item["chunk_id"] = int(
                        connection.execute(
                            text(
                                """
                                INSERT INTO legal_article_chunks (
                                    article_id, chunk_index, heading, content
                                ) VALUES (
                                    :article_id, :chunk_index, :heading, :content
                                ) RETURNING id
                                """
                            ),
                            values,
                        ).scalar_one()
                    )
                    new_chunk_ids.append(int(item["chunk_id"]))
        db_committed = True
        if len(new_chunk_ids) != additive_count:
            raise RuntimeError("LAW88_STRUCTURE_NEW_CHUNK_ID_COUNT")

        base_metadata = dict(before_vectors[SOURCE_COLLECTION]["metadatas"][0])
        vector_ids = [f"chunk-{item['chunk_id']}" for item in planned]
        metadatas = []
        for item in planned:
            unit = item["unit"]
            chunk = item["chunk"]
            metadata = {
                **base_metadata,
                "chunk_id": int(item["chunk_id"]),
                "article_id": int(unit["article_id"]),
                "chunk_index": int(chunk["chunk_index"]),
                "article_number": str(unit["article_number"]),
                "article_title": str(unit["title"]),
                "structural_heading": str(chunk.get("heading") or ""),
                "parent_kind": str(chunk.get("parent_kind") or "article"),
                "child_kind": str(chunk.get("child_kind") or "fallback"),
                "clause_number": str(chunk.get("clause_number") or ""),
                "point_number": str(chunk.get("point_number") or ""),
                "doc_status": "active",
                "status": "active",
            }
            metadatas.append(metadata)
        for name, collection in collections.items():
            collection.upsert(
                ids=vector_ids,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            vector_touched[name] = list(vector_ids)

        audit_loop.run_until_complete(
            write_audit_log(
                action="legal.corpus.golden294_law88_structure_repair",
                entity_type="legal_document",
                entity_id=str(DOCUMENT_ID),
                actor_user_id=actor_user_id,
                actor_role="admin",
                details={
                    "law_number": LAW_NUMBER,
                    "new_article_ids": new_article_ids,
                    "new_chunk_ids": new_chunk_ids,
                    "updated_existing_chunk_ids": [
                        int(row["id"]) for row in before["chunks"]
                    ],
                    "vector_collections": sorted(collections),
                    "hard_delete": False,
                },
            )
        )
    except Exception:
        for name, collection in collections.items():
            snapshot = before_vectors[name]
            if snapshot.get("ids"):
                collection.upsert(
                    ids=snapshot["ids"],
                    embeddings=snapshot["embeddings"],
                    metadatas=snapshot["metadatas"],
                )
            if new_chunk_ids:
                collection.delete(ids=[f"chunk-{value}" for value in new_chunk_ids])
        if db_committed:
            with engine.begin() as connection:
                for row in before["articles"]:
                    connection.execute(
                        text("UPDATE legal_articles SET content=:content WHERE id=:id"),
                        {"content": row["content"], "id": row["id"]},
                    )
                for row in before["chunks"]:
                    connection.execute(
                        text(
                            """
                            UPDATE legal_article_chunks
                            SET article_id=:article_id, chunk_index=:chunk_index,
                                heading=:heading, content=:content
                            WHERE id=:id
                            """
                        ),
                        row,
                    )
                if new_chunk_ids:
                    connection.execute(
                        text("DELETE FROM legal_article_chunks WHERE id=ANY(:ids)"),
                        {"ids": new_chunk_ids},
                    )
                if new_article_ids:
                    connection.execute(
                        text("DELETE FROM legal_articles WHERE id=ANY(:ids)"),
                        {"ids": list(new_article_ids.values())},
                    )
        raise
    finally:
        audit_loop.close()

    with engine.connect() as connection:
        after_rows = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT a.id, a.article_number, a.title, a.content,
                           count(c.id) AS chunk_count
                    FROM legal_articles a
                    JOIN legal_article_chunks c ON c.article_id=a.id
                    WHERE a.document_id=:document_id
                      AND a.article_number IN ('87','99','100','101')
                    GROUP BY a.id ORDER BY a.article_number
                    """
                ),
                {"document_id": DOCUMENT_ID},
            ).mappings()
        ]
    engine.dispose()
    if {row["article_number"] for row in after_rows} != {"87", "99", "100", "101"}:
        raise RuntimeError("LAW88_STRUCTURE_POSTCONDITION_ARTICLES")
    if sum(int(row["chunk_count"]) for row in after_rows) != len(planned):
        raise RuntimeError("LAW88_STRUCTURE_POSTCONDITION_CHUNKS")
    for name, collection in collections.items():
        got = collection.get(
            ids=[f"chunk-{item['chunk_id']}" for item in planned],
            include=["embeddings", "metadatas"],
        )
        if len(got.get("ids") or []) != len(planned):
            raise RuntimeError(f"LAW88_STRUCTURE_POSTCONDITION_VECTORS:{name}")

    report = {
        **prepared,
        "phase": "applied",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "new_article_ids": new_article_ids,
        "new_chunk_ids": new_chunk_ids,
        "exact_id_manifest": [
            {
                "chunk_id": int(item["chunk_id"]),
                "article_id": int(item["unit"]["article_id"]),
                "article_number": str(item["unit"]["article_number"]),
                "chunk_index": int(item["chunk"]["chunk_index"]),
                "content_sha256": _sha256_text(str(item["chunk"]["content"])),
            }
            for item in planned
        ],
        "after_articles": [
            {
                **{key: row[key] for key in ("id", "article_number", "title", "chunk_count")},
                "content_sha256": _sha256_text(str(row["content"])),
            }
            for row in after_rows
        ],
        "vector_collections": sorted(collections),
        "preservation": {
            "existing_chunk_ids_reused": len(before["chunks"]),
            "additive_article_count": 2,
            "additive_chunk_count": len(new_chunk_ids),
            "unrelated_vectors_reindexed": 0,
            "historical_documents_deleted": 0,
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.approval != APPROVAL_PHRASE:
        raise SystemExit("LAW88_STRUCTURE_APPROVAL_REQUIRED")
    report = repair(args.output.resolve())
    print(
        json.dumps(
            {
                "status": report["phase"],
                "new_article_ids": report["new_article_ids"],
                "new_chunk_ids": report["new_chunk_ids"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
