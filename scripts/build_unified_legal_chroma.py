"""Build a single, non-active Chroma collection for the approved keep set.

The command never deletes source collections and never changes the active
pointer. It is a staging step for the retention/unification gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
import psycopg2

from api.retrieval_release_contracts import require_staging_collection_target

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
FIVE_GROUPS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
)


def _dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _database_url() -> str:
    values = _dotenv()
    url = (
        os.getenv("LEGAL_CORPUS_DATABASE_URL")
        or os.getenv("LEGAL_RELEASE_DATABASE_URL")
        or values.get("LEGAL_RELEASE_DATABASE_URL")
    )
    if not url:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is required")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
        "host.docker.internal", "127.0.0.1"
    )


def _chroma_path() -> Path:
    return Path(os.getenv("LEGAL_CHROMA_PATH", r"D:\legal-chatbot-data\chroma_store"))


def _approved_ids(manifest_path: Path) -> set[int]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    ids = {int(value) for value in payload.get("new_document_ids", [])}
    connection = psycopg2.connect(_database_url(), connect_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT d.id
                FROM legal_documents d
                JOIN legal_commune_field_groups g ON g.field_id = d.field_id
                WHERE d.status = 'active'
                  AND g.included = TRUE
                  AND g.group_slug = ANY(%s)
                """,
                (list(FIVE_GROUPS),),
            )
            ids.update(int(row[0]) for row in cursor.fetchall())
    finally:
        connection.close()
    if len(ids) != int(payload.get("requested_keep_count", 0)):
        raise RuntimeError(
            f"keep_set_changed: manifest={payload.get('requested_keep_count')} "
            f"computed={len(ids)}"
        )
    return ids


def _chunk_ids(document_ids: set[int]) -> list[int]:
    connection = psycopg2.connect(_database_url(), connect_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id
                FROM legal_documents d
                JOIN legal_articles a ON a.document_id = d.id
                JOIN legal_article_chunks c ON c.article_id = a.id
                WHERE d.status = 'active'
                  AND a.status = 'active'
                  AND d.id = ANY(%s)
                ORDER BY c.id
                """,
                (list(document_ids),),
            )
            return [int(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def build(
    *,
    manifest_path: Path,
    target_name: str,
    source_name: str,
    batch_size: int,
    replace: bool,
) -> dict[str, Any]:
    keep_ids = _approved_ids(manifest_path)
    chunk_ids = _chunk_ids(keep_ids)
    chroma_path = _chroma_path()
    active_pointer = require_staging_collection_target(target_name, chroma_path) or ""
    client = chromadb.PersistentClient(path=str(chroma_path))
    source = client.get_collection(source_name)
    if target_name == source_name:
        raise RuntimeError("source_and_target_must_differ")
    if target_name in {item.name for item in client.list_collections()}:
        if not replace:
            raise RuntimeError("target_exists_use_replace_for_staging_rebuild")
        client.delete_collection(target_name)
    target = client.create_collection(
        target_name,
        metadata={
            "pipeline": "legal-retention-unification-v1",
            "source_collection": source_name,
            "keep_manifest": manifest_path.name,
            "active_pointer_changed": "false",
        },
    )
    requested_ids = [f"chunk-{value}" for value in chunk_ids]
    missing: list[str] = []
    copied = 0
    for start in range(0, len(requested_ids), batch_size):
        ids = requested_ids[start : start + batch_size]
        batch = source.get(
            ids=ids,
            include=["embeddings", "metadatas", "documents"],
        )
        found = list(batch.get("ids") or [])
        if found:
            kwargs: dict[str, Any] = {
                "ids": found,
                "embeddings": batch.get("embeddings"),
                "metadatas": batch.get("metadatas"),
            }
            if batch.get("documents") is not None:
                kwargs["documents"] = batch["documents"]
            target.add(**kwargs)
            copied += len(found)
        missing.extend(sorted(set(ids) - set(found)))
        print(
            f"processed={min(start + batch_size, len(requested_ids))}/"
            f"{len(requested_ids)} copied={copied} missing={len(missing)}",
            flush=True,
        )
    report = {
        "schema_version": "legal-unified-chroma-build-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path.resolve()),
        "source_collection": source_name,
        "target_collection": target_name,
        "keep_documents": len(keep_ids),
        "requested_chunks": len(requested_ids),
        "copied_chunks": copied,
        "missing_chunks": missing,
        "target_count": target.count(),
        "active_pointer_changed": False,
        "active_pointer_before": active_pointer,
        "active_pointer_after": active_pointer,
        "passed": not missing and target.count() == len(requested_ids),
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    output = ROOT / "output" / "legal-unified-chroma-build-v1.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "output" / "legal-retention-manifest-v1.json",
    )
    parser.add_argument("--source", default="legal_chunks_vnlegal_lal")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    report = build(
        manifest_path=args.manifest,
        target_name=args.target,
        source_name=args.source,
        batch_size=args.batch_size,
        replace=args.replace,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
