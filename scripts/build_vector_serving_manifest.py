#!/usr/bin/env python3
"""Build the Feature 018 vector serving manifest without mutating stores."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_structural_chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SPLIT_THRESHOLD,
)
from api.legal_validity_models import normalize_law_number
from api.legal_validity_registry import SNAPSHOT_PATH
from api.vector_serving_manifest import (
    build_vector_serving_manifest,
    canonical_sha256,
)
from scripts.backup_legal_retrieval import _database_url
from scripts.feature005_db1_snapshot import (
    DEFAULT_CHROMA_PATH,
    DEFAULT_CORE_COLLECTION,
    safe_database_target,
)


ACTIVE_POINTER_FILE = "active_core_collection.txt"
LEGAL_COLLECTION_NAME = re.compile(r"(?:legal|shadow|staging|historical)", re.I)


def _json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"inventory_unreadable:{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("inventory_object_required")
    return value


def _snapshot(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pointer(path: Path) -> tuple[str, str]:
    configured = str(os.getenv("LEGAL_CHROMA_COLLECTION") or "").strip()
    if configured:
        return configured, "environment"
    pointer = path / ACTIVE_POINTER_FILE
    try:
        value = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value, "pointer_file"
    return DEFAULT_CORE_COLLECTION, "runtime_default"


def _collection_role(name: str, active: str) -> str:
    lowered = name.casefold()
    if name == active:
        return "active"
    if any(token in lowered for token in ("historical", "history", "archive")):
        return "historical"
    if any(token in lowered for token in ("staging", "shadow", "candidate", "preview")):
        return "staging"
    return "other"


def _fingerprints(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    return {
        "embedding": str(
            source.get("embedding_fingerprint")
            or source.get("model_fingerprint")
            or source.get("embedding")
            or ""
        ).strip(),
        "splitter": str(
            source.get("splitter_fingerprint") or source.get("splitter") or ""
        ).strip(),
        "pipeline": str(
            source.get("pipeline_fingerprint")
            or source.get("pipeline_version")
            or source.get("pipeline")
            or ""
        ).strip(),
        "validity_snapshot": str(
            source.get("validity_snapshot_sha256")
            or source.get("validity_snapshot")
            or ""
        ).strip(),
    }


def _expected_fingerprints(snapshot: Mapping[str, Any] | None) -> dict[str, str]:
    splitter_projection = {
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "chunk_overlap": DEFAULT_CHUNK_OVERLAP,
        "split_threshold": DEFAULT_SPLIT_THRESHOLD,
        "algorithm": "legal_structural_chunking",
    }
    return {
        "embedding": str(os.getenv("VNLEGAL_LAL_MODEL_FINGERPRINT") or "").strip(),
        "splitter": str(os.getenv("LEGAL_SPLITTER_FINGERPRINT") or "").strip()
        or canonical_sha256(splitter_projection),
        "pipeline": str(
            os.getenv("LEGAL_ANSWER_PIPELINE_VERSION") or "answer-pipeline-v3"
        ).strip(),
        "validity_snapshot": canonical_sha256(snapshot) if snapshot else "",
    }


def _snapshot_entries(snapshot: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(snapshot, Mapping):
        return {}, {}
    documents = snapshot.get("documents")
    document_ids = snapshot.get("document_ids")
    by_number = dict(documents) if isinstance(documents, Mapping) else {}
    by_id: dict[str, Any] = {}
    if isinstance(document_ids, Mapping):
        for raw_id, raw_number in document_ids.items():
            number = normalize_law_number(raw_number)
            entry = by_number.get(number)
            if isinstance(entry, Mapping):
                by_id[str(raw_id).rsplit(":", 1)[-1]] = entry
    for entry in by_number.values():
        if isinstance(entry, Mapping) and entry.get("document_id") not in (None, ""):
            by_id[str(entry["document_id"]).rsplit(":", 1)[-1]] = entry
    return by_number, by_id


def _serving_action(
    row: Mapping[str, Any],
    *,
    by_number: Mapping[str, Any],
    by_id: Mapping[str, Any],
    legal_as_of: date,
) -> str:
    entry = by_id.get(str(row.get("document_id")))
    if not isinstance(entry, Mapping):
        entry = by_number.get(normalize_law_number(row.get("law_number")))
    if isinstance(entry, Mapping):
        action = str(entry.get("serving_action") or "").strip()
        if action:
            return action
    expired_date = row.get("expired_date")
    article_to = row.get("article_effective_to")
    if isinstance(expired_date, datetime):
        expired_date = expired_date.date()
    if isinstance(article_to, datetime):
        article_to = article_to.date()
    if isinstance(expired_date, date) and expired_date <= legal_as_of:
        return "historical_only"
    if isinstance(article_to, date) and article_to <= legal_as_of:
        return "block_provisions"
    return "allow"


def _postgres_inventory(
    *, snapshot: Mapping[str, Any] | None, legal_as_of: date
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Read canonical expectations in an explicitly read-only transaction."""

    from sqlalchemy import create_engine, text

    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    by_number, by_id = _snapshot_entries(snapshot)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
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
                    warnings.append("legal_chunk_quality_missing")
                if not has_scope:
                    warnings.append("legal_search_scope_missing")
                quality_join = (
                    "LEFT JOIN legal_chunk_quality q ON q.chunk_id = c.id"
                    if has_quality
                    else ""
                )
                eligible = "coalesce(q.eligible, true)" if has_quality else "true"
                canonical = "q.canonical_chunk_id" if has_quality else "NULL::bigint"
                included = (
                    "EXISTS (SELECT 1 FROM legal_search_scope s "
                    "WHERE s.document_id = d.id AND s.included = true)"
                    if has_scope
                    else "false"
                )
                query = text(
                    f"""
                    SELECT c.id AS chunk_id, d.id AS document_id,
                           d.law_number, d.status AS document_status,
                           coalesce(a.status, 'active') AS article_status,
                           d.expired_date, a.effective_to AS article_effective_to,
                           {included} AS included,
                           {eligible} AS eligible,
                           {canonical} AS canonical_chunk_id
                    FROM legal_article_chunks c
                    JOIN legal_articles a ON a.id = c.article_id
                    JOIN legal_documents d ON d.id = a.document_id
                    {quality_join}
                    ORDER BY c.id
                    """
                )
                for mapping in connection.execute(query).mappings():
                    row = dict(mapping)
                    row["serving_action"] = _serving_action(
                        row,
                        by_number=by_number,
                        by_id=by_id,
                        legal_as_of=legal_as_of,
                    )
                    rows.append(row)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return rows, safe_database_target(url), warnings


def _chroma_inventory(
    *, path: Path, active_collection: str, batch_size: int = 10_000
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read Chroma's SQLite metadata with immutable/query-only semantics.

    ``chromadb.PersistentClient`` performs housekeeping writes even for get/list
    calls, so it is deliberately not used by this reconciliation command.
    """

    import sqlite3

    database = path / "chroma.sqlite3"
    if not database.is_file():
        raise RuntimeError(f"chroma_sqlite_missing:{database}")
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    names = [
        str(row[0])
        for row in connection.execute("SELECT name FROM collections ORDER BY name")
    ]
    selected = [
        name
        for name in names
        if name == active_collection or LEGAL_COLLECTION_NAME.search(name)
    ]
    warnings: list[str] = []
    if active_collection not in names:
        warnings.append("active_collection_not_found")
    collections: list[dict[str, Any]] = []
    try:
        for name in selected:
            collection_row = connection.execute(
                "SELECT id FROM collections WHERE name = ?", (name,)
            ).fetchone()
            if collection_row is None:
                continue
            collection_id = str(collection_row[0])
            metadata: dict[str, Any] = {}
            for key, string, integer, floating, boolean in connection.execute(
                "SELECT key, str_value, int_value, float_value, bool_value "
                "FROM collection_metadata WHERE collection_id = ? ORDER BY key",
                (collection_id,),
            ):
                metadata[str(key)] = next(
                    (item for item in (string, integer, floating, boolean) if item is not None),
                    None,
                )
            records: list[dict[str, Any]] = []
            query = """
                SELECT e.embedding_id,
                       coalesce(c.int_value, c.string_value),
                       coalesce(d.int_value, d.string_value),
                       coalesce(ef.string_value, cast(ef.int_value AS TEXT)),
                       coalesce(sf.string_value, cast(sf.int_value AS TEXT)),
                       coalesce(pf.string_value, cast(pf.int_value AS TEXT)),
                       coalesce(vf.string_value, cast(vf.int_value AS TEXT))
                FROM segments s
                JOIN embeddings e ON e.segment_id = s.id
                LEFT JOIN embedding_metadata c ON c.id = e.id AND c.key = 'chunk_id'
                LEFT JOIN embedding_metadata d ON d.id = e.id AND d.key = 'document_id'
                LEFT JOIN embedding_metadata ef ON ef.id = e.id AND ef.key IN ('embedding_fingerprint','model_fingerprint')
                LEFT JOIN embedding_metadata sf ON sf.id = e.id AND sf.key = 'splitter_fingerprint'
                LEFT JOIN embedding_metadata pf ON pf.id = e.id AND pf.key IN ('pipeline_fingerprint','pipeline_version')
                LEFT JOIN embedding_metadata vf ON vf.id = e.id AND vf.key IN ('validity_snapshot_sha256','validity_snapshot')
                WHERE s.collection = ? AND s.scope = 'METADATA'
                ORDER BY e.id
            """
            cursor = connection.execute(query, (collection_id,))
            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break
                for vector_id, chunk_id, document_id, embedding, splitter, pipeline, validity in batch:
                    raw_id = str(vector_id)
                    if chunk_id in (None, "") and raw_id.startswith("chunk-"):
                        chunk_id = raw_id[6:]
                    records.append(
                        {
                            "vector_id": raw_id,
                            "chunk_id": chunk_id,
                            "document_id": document_id,
                            "fingerprints": {
                                "embedding": embedding or "",
                                "splitter": splitter or "",
                                "pipeline": pipeline or "",
                                "validity_snapshot": validity or "",
                            },
                        }
                    )
            collections.append(
                {
                    "name": name,
                    "role": _collection_role(name, active_collection),
                    "fingerprints": _fingerprints(metadata),
                    "records": records,
                }
            )
    finally:
        connection.close()
    return collections, warnings


def _store_signature(path: Path) -> dict[str, Any]:
    files = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        stat = item.stat()
        files.append(
            {
                "path": item.relative_to(path).as_posix(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return {
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "signature": canonical_sha256(files),
    }


def _atomic_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(
    *,
    output: Path,
    inventory: Path | None = None,
    read_only: bool,
    chroma_path: Path = DEFAULT_CHROMA_PATH,
    validity_snapshot: Path = SNAPSHOT_PATH,
    legal_as_of: date | None = None,
) -> dict[str, Any]:
    if not read_only:
        raise RuntimeError("read_only_flag_required")
    observed_as_of = legal_as_of or date.today()
    source_warnings: list[str] = []
    if inventory is not None:
        source = _json_object(inventory)
        before = str(source.get("active_pointer") or source.get("active_collection") or "")
        manifest = build_vector_serving_manifest(
            expected_chunks=source.get("expected_chunks") or [],
            collections=source.get("collections") or [],
            active_collection=source.get("active_collection"),
            expected_fingerprints=source.get("expected_fingerprints") or {},
            active_pointer_before=before,
            active_pointer_after=before,
        )
        source_mode = "fixture_inventory"
        source_details: dict[str, Any] = {
            "inventory_path": str(inventory.resolve()),
            "inventory_input_sha256": canonical_sha256(source),
        }
    else:
        chroma_path = chroma_path.resolve()
        before, pointer_source = _pointer(chroma_path)
        store_before = _store_signature(chroma_path)
        snapshot = _snapshot(validity_snapshot.resolve())
        expected, database_target, postgres_warnings = _postgres_inventory(
            snapshot=snapshot,
            legal_as_of=observed_as_of,
        )
        collections, chroma_warnings = _chroma_inventory(
            path=chroma_path,
            active_collection=before,
        )
        after, after_source = _pointer(chroma_path)
        store_after = _store_signature(chroma_path)
        source_warnings.extend(postgres_warnings)
        source_warnings.extend(chroma_warnings)
        if pointer_source != after_source:
            source_warnings.append("active_pointer_source_changed")
        manifest = build_vector_serving_manifest(
            expected_chunks=expected,
            collections=collections,
            active_collection=before,
            expected_fingerprints=_expected_fingerprints(snapshot),
            active_pointer_before=before,
            active_pointer_after=after,
        )
        source_mode = "live_read_only"
        source_details = {
            "database_target": database_target,
            "chroma_path": str(chroma_path),
            "validity_snapshot_path": str(validity_snapshot.resolve()),
            "legal_as_of": observed_as_of.isoformat(),
            "active_pointer_source": pointer_source,
            "chroma_store_signature_before": store_before,
            "chroma_store_signature_after": store_after,
            "chroma_store_unchanged": store_before == store_after,
        }
        if store_before != store_after:
            manifest["gate_passed"] = False
            manifest["reason_codes"] = sorted(
                set([*manifest["reason_codes"], "chroma_store_changed_during_read"])
            )
    report = {
        **manifest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_mode": source_mode,
        "source_details": source_details,
        "source_warnings": sorted(set(source_warnings)),
        "report_output_only_mutation": True,
    }
    _atomic_report(output.resolve(), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--validity-snapshot", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--legal-as-of", type=date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        report = run(
            output=args.output,
            inventory=args.inventory,
            read_only=args.read_only,
            chroma_path=args.chroma_path,
            validity_snapshot=args.validity_snapshot,
            legal_as_of=args.legal_as_of,
        )
    except RuntimeError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": "passed" if report["gate_passed"] else "needs_review",
                "counts": report["counts"],
                "manifest_fingerprint": report["manifest_fingerprint"],
                "active_pointer_unchanged": report["active_pointer_unchanged"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
