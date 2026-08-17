"""Delete only approved active documents outside the retention keep set.

The command is guarded by the retention manifest, recomputes the keep set from
PostgreSQL, and commits one transaction. Expired documents are never deleted.
"""

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


def _dotenv() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _database_url() -> str:
    values = _dotenv()
    value = (
        os.getenv("LEGAL_CORPUS_DATABASE_URL")
        or os.getenv("LEGAL_RELEASE_DATABASE_URL")
        or values.get("LEGAL_RELEASE_DATABASE_URL")
    )
    if not value:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL_REQUIRED")
    return value.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
        "host.docker.internal", "127.0.0.1"
    )


def _manifest_ids(path: Path) -> tuple[dict[str, Any], set[int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "proposed":
        raise RuntimeError("retention_manifest_not_proposed")
    if int(payload.get("requested_keep_count", 0)) != 7245:
        raise RuntimeError("retention_manifest_keep_count_mismatch")
    return payload, {int(value) for value in payload.get("new_document_ids", [])}


def apply(manifest_path: Path, *, confirm: str, dry_run: bool) -> dict[str, Any]:
    if confirm != "DELETE_ACTIVE_OUTSIDE_7245":
        raise RuntimeError("explicit_confirmation_required")
    manifest, new_ids = _manifest_ids(manifest_path)
    connection = psycopg2.connect(_database_url(), connect_timeout=10)
    connection.autocommit = False
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
            keep = {int(row[0]) for row in cursor.fetchall()} | new_ids
            if len(keep) != 7245:
                raise RuntimeError(f"keep_set_changed:{len(keep)}")
            cursor.execute(
                """
                SELECT id
                FROM legal_documents
                WHERE status = 'active' AND NOT (id = ANY(%s))
                ORDER BY id
                """,
                (list(keep),),
            )
            delete_ids = [int(row[0]) for row in cursor.fetchall()]
            raw_ids = ",".join(str(value) for value in delete_ids).encode("ascii")
            counts: dict[str, int] = {"documents": len(delete_ids)}
            if delete_ids:
                for name, query in (
                    (
                        "articles",
                        "SELECT count(*) FROM legal_articles WHERE document_id = ANY(%s)",
                    ),
                    (
                        "chunks",
                        """SELECT count(*) FROM legal_article_chunks c
                           JOIN legal_articles a ON a.id=c.article_id
                           WHERE a.document_id = ANY(%s)""",
                    ),
                    (
                        "scope",
                        "SELECT count(*) FROM legal_search_scope WHERE document_id = ANY(%s)",
                    ),
                    (
                        "relationships_source",
                        "SELECT count(*) FROM legal_document_relationships WHERE source_document_id = ANY(%s)",
                    ),
                    (
                        "relationships_target",
                        "SELECT count(*) FROM legal_document_relationships WHERE target_document_id = ANY(%s)",
                    ),
                ):
                    cursor.execute(query, (delete_ids,))
                    counts[name] = int(cursor.fetchone()[0])
            if not dry_run and delete_ids:
                cursor.execute(
                    """
                    DELETE FROM legal_chunk_quality q
                    USING legal_article_chunks c, legal_articles a
                    WHERE q.chunk_id = c.id
                      AND c.article_id = a.id
                      AND a.document_id = ANY(%s)
                    """,
                    (delete_ids,),
                )
                quality_by_chunk = cursor.rowcount
                cursor.execute(
                    """
                    DELETE FROM legal_chunk_quality q
                    USING legal_article_chunks c, legal_articles a
                    WHERE q.canonical_chunk_id = c.id
                      AND c.article_id = a.id
                      AND a.document_id = ANY(%s)
                    """,
                    (delete_ids,),
                )
                counts["chunk_quality_deleted"] = quality_by_chunk + cursor.rowcount
                cursor.execute(
                    "DELETE FROM legal_documents WHERE status='active' AND NOT (id = ANY(%s))",
                    (list(keep),),
                )
                counts["deleted_rowcount"] = cursor.rowcount
                connection.commit()
            else:
                connection.rollback()
            cursor.execute("SELECT count(*) FROM legal_documents WHERE status='active'")
            counts["active_after"] = int(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM legal_documents WHERE status<>'active'")
            counts["non_active_after"] = int(cursor.fetchone()[0])
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    result = {
        "schema_version": "legal-retention-apply-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "dry_run": dry_run,
        "confirmation": confirm,
        "keep_count": len(keep),
        "delete_id_count": len(delete_ids),
        "delete_ids_sha256": hashlib.sha256(raw_ids).hexdigest(),
        "counts": counts,
        "expired_retained": True,
    }
    output = ROOT / "output" / "legal-retention-apply-v1.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "output" / "legal-retention-manifest-v1.json")
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = apply(args.manifest, confirm=args.confirm, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
