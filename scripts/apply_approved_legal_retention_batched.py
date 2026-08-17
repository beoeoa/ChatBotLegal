"""Apply the approved active-document retention set in bounded batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
FIVE_GROUPS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
)


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _url() -> str:
    values = _env()
    value = os.getenv("LEGAL_CORPUS_DATABASE_URL") or os.getenv(
        "LEGAL_RELEASE_DATABASE_URL"
    ) or values.get("LEGAL_RELEASE_DATABASE_URL")
    if not value:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL_REQUIRED")
    return value.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
        "host.docker.internal", "127.0.0.1"
    )


def _keep_ids(connection: Any, manifest: dict[str, Any]) -> set[int]:
    if manifest.get("status") != "proposed" or int(manifest.get("requested_keep_count", 0)) != 7245:
        raise RuntimeError("invalid_retention_manifest")
    new_ids = {int(value) for value in manifest.get("new_document_ids", [])}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT d.id
            FROM legal_documents d
            JOIN legal_commune_field_groups g ON g.field_id=d.field_id
            WHERE d.status='active' AND g.included=TRUE
              AND g.group_slug=ANY(%s)
            """,
            (list(FIVE_GROUPS),),
        )
        old_ids = {int(row[0]) for row in cursor.fetchall()}
    keep = old_ids | new_ids
    if len(keep) != 7245:
        raise RuntimeError(f"keep_set_changed:{len(keep)}")
    return keep


def _delete_batch(connection: Any, document_ids: list[int]) -> dict[str, int]:
    counts = {"documents": 0, "articles": 0, "chunks": 0, "quality": 0}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM legal_articles WHERE document_id=ANY(%s)",
            (document_ids,),
        )
        article_ids = [int(row[0]) for row in cursor.fetchall()]
        if article_ids:
            cursor.execute(
                "SELECT id FROM legal_article_chunks WHERE article_id=ANY(%s)",
                (article_ids,),
            )
            chunk_ids = [int(row[0]) for row in cursor.fetchall()]
            if chunk_ids:
                cursor.execute(
                    "DELETE FROM legal_chunk_quality WHERE chunk_id=ANY(%s)",
                    (chunk_ids,),
                )
                quality_deleted = cursor.rowcount
                cursor.execute(
                    "DELETE FROM legal_chunk_quality WHERE canonical_chunk_id=ANY(%s)",
                    (chunk_ids,),
                )
                counts["quality"] = quality_deleted + cursor.rowcount
                cursor.execute(
                    "DELETE FROM legal_article_chunks WHERE article_id=ANY(%s)",
                    (article_ids,),
                )
                counts["chunks"] = cursor.rowcount
            cursor.execute(
                "DELETE FROM legal_articles WHERE document_id=ANY(%s)",
                (document_ids,),
            )
            counts["articles"] = cursor.rowcount
        cursor.execute(
            "DELETE FROM legal_documents WHERE status='active' AND id=ANY(%s)",
            (document_ids,),
        )
        counts["documents"] = cursor.rowcount
    connection.commit()
    return counts


def apply(manifest_path: Path, batch_size: int, confirm: str, dry_run: bool) -> dict[str, Any]:
    if confirm != "DELETE_ACTIVE_OUTSIDE_7245_BATCHED":
        raise RuntimeError("explicit_confirmation_required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    connection = psycopg2.connect(_url(), connect_timeout=10)
    try:
        keep = _keep_ids(connection, manifest)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM legal_documents WHERE status='active' AND NOT (id=ANY(%s)) ORDER BY id",
                (list(keep),),
            )
            delete_ids = [int(row[0]) for row in cursor.fetchall()]
        if dry_run:
            result = {"dry_run": True, "keep_count": len(keep), "delete_count": len(delete_ids)}
        else:
            totals = {"documents": 0, "articles": 0, "chunks": 0, "quality": 0}
            for start in range(0, len(delete_ids), batch_size):
                batch = delete_ids[start : start + batch_size]
                counts = _delete_batch(connection, batch)
                for key, value in counts.items():
                    totals[key] += value
                print(f"deleted_documents={min(start+batch_size,len(delete_ids))}/{len(delete_ids)}", flush=True)
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM legal_documents WHERE status='active'")
                active_after = int(cursor.fetchone()[0])
                cursor.execute("SELECT count(*) FROM legal_documents WHERE status<>'active'")
                non_active_after = int(cursor.fetchone()[0])
            result = {
                "dry_run": False,
                "keep_count": len(keep),
                "delete_count": len(delete_ids),
                "totals": totals,
                "active_after": active_after,
                "non_active_after": non_active_after,
            }
            if active_after != 7245 or non_active_after != 4990:
                raise RuntimeError(f"post_delete_invariant_failed:{result}")
    finally:
        connection.close()
    result.update(
        {
            "schema_version": "legal-retention-apply-batched-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "delete_ids_sha256": hashlib.sha256(",".join(map(str, delete_ids)).encode("ascii")).hexdigest(),
            "confirmation": confirm,
            "expired_retained": True,
        }
    )
    (ROOT / "output" / "legal-retention-apply-batched-v1.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "output" / "legal-retention-manifest-v1.json")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(args.manifest, args.batch_size, args.confirm, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
