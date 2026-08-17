"""Capture a read-only, credential-safe baseline for the Golden-294 live slice."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_validity_registry import SNAPSHOT_PATH
from open_notebook.database.repository import repo_query
from scripts.backup_legal_retrieval import _database_url


TARGET_LAWS = (
    "31/2024/QH15",
    "73/2025/QH15",
    "55/2021/TT-BCA",
    "66/2023/TT-BCA",
    "88/2025/QH15",
    "116/2026/TT-BCA",
    "62/2020/QH14",
    "43/2025/NQ-HDND",
)
SURREAL_TABLES = (
    "legal_crawl_candidate",
    "legal_import_job",
    "legal_validity_observation",
    "legal_validity_event",
    "legal_validity_sync_run",
)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _sql_snapshot() -> dict[str, Any]:
    engine = create_engine(_database_url())
    with engine.connect() as connection:
        counts = {
            table: int(
                connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            )
            for table in (
                "legal_documents",
                "legal_articles",
                "legal_article_chunks",
                "legal_search_scope",
            )
        }
        documents = connection.execute(
            text(
                """
                SELECT d.id, d.law_number, d.title, d.status, d.issued_date,
                       d.effective_date, d.expired_date, d.source_url, d.field_id,
                       s.included, s.reason AS scope_reason, s.domain,
                       count(DISTINCT a.id) AS article_count,
                       count(ch.id) AS chunk_count
                FROM legal_documents d
                LEFT JOIN legal_search_scope s ON s.document_id = d.id
                LEFT JOIN legal_articles a ON a.document_id = d.id
                LEFT JOIN legal_article_chunks ch ON ch.article_id = a.id
                WHERE REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D')
                      = ANY(:numbers)
                GROUP BY d.id, s.included, s.reason, s.domain
                ORDER BY d.id
                """
            ),
            {"numbers": list(TARGET_LAWS)},
        ).mappings().all()
        target_articles = connection.execute(
            text(
                """
                SELECT d.law_number, a.id AS article_id, a.article_number,
                       a.status, length(a.content) AS article_chars,
                       count(ch.id) AS chunk_count,
                       min(ch.chunk_index) AS first_chunk_index,
                       max(ch.chunk_index) AS last_chunk_index
                FROM legal_documents d
                JOIN legal_articles a ON a.document_id = d.id
                LEFT JOIN legal_article_chunks ch ON ch.article_id = a.id
                WHERE (
                    REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D')
                        = '88/2025/QH15'
                    AND a.article_number IN ('87', '99', '100', '101')
                ) OR (
                    REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D')
                        = '62/2020/QH14' AND a.article_number = '1'
                )
                GROUP BY d.law_number, a.id
                ORDER BY d.law_number, a.id
                """
            )
        ).mappings().all()
        metadata_rows = connection.execute(
            text(
                """
                SELECT id, law_number, status, source_url, issued_date,
                       effective_date, expired_date
                FROM legal_documents
                ORDER BY id
                """
            )
        ).mappings()
        digest = hashlib.sha256()
        for row in metadata_rows:
            digest.update(
                (
                    json.dumps(dict(row), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=_json_default)
                    + "\n"
                ).encode("utf-8")
            )
    engine.dispose()
    return {
        "counts": counts,
        "metadata_sha256": digest.hexdigest(),
        "target_documents": [dict(row) for row in documents],
        "target_articles": [dict(row) for row in target_articles],
    }


async def _surreal_counts() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for table in SURREAL_TABLES:
        try:
            rows = await repo_query(
                f"SELECT count() AS count FROM {table} GROUP ALL;"
            )
            results[table] = int(rows[0].get("count") or 0) if rows else 0
        except Exception as exc:
            results[table] = {
                "available": False,
                "reason": type(exc).__name__,
            }
    return results


async def _retrieval_snapshot(base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        health_response = await client.get(f"{base_url.rstrip('/')}/health")
        health_response.raise_for_status()
        health = health_response.json()
        details: dict[str, Any] = {}
        for document in _sql_snapshot()["target_documents"]:
            document_id = str(document["id"])
            response = await client.get(
                f"{base_url.rstrip('/')}/management/documents/{document_id}"
            )
            details[document_id] = (
                response.json()
                if response.status_code == 200
                else {"status_code": response.status_code}
            )
    return {"health": health, "target_details": details}


async def build_baseline(retrieval_url: str) -> dict[str, Any]:
    sql = _sql_snapshot()
    retrieval: dict[str, Any]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            health_response = await client.get(f"{retrieval_url.rstrip('/')}/health")
            health_response.raise_for_status()
            retrieval = {"health": health_response.json()}
            details: dict[str, Any] = {}
            for document in sql["target_documents"]:
                document_id = str(document["id"])
                response = await client.get(
                    f"{retrieval_url.rstrip('/')}/management/documents/{document_id}"
                )
                details[document_id] = (
                    response.json()
                    if response.status_code == 200
                    else {"status_code": response.status_code}
                )
            retrieval["target_details"] = details
    except Exception as exc:
        retrieval = {"available": False, "reason": type(exc).__name__}
    return {
        "schema_version": "golden-294-live-baseline-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "targets": list(TARGET_LAWS),
        "postgres": sql,
        "surreal": await _surreal_counts(),
        "validity_snapshot": {
            "path": str(SNAPSHOT_PATH),
            "sha256": _sha256(Path(SNAPSHOT_PATH)),
            "bytes": Path(SNAPSHOT_PATH).stat().st_size
            if Path(SNAPSHOT_PATH).is_file()
            else None,
        },
        "retrieval": retrieval,
        "mutation_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    payload = asyncio.run(build_baseline(args.retrieval_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "postgres_counts": payload["postgres"]["counts"],
                "target_documents": len(payload["postgres"]["target_documents"]),
                "retrieval_ready": payload["retrieval"].get("health", {}).get("ready"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
