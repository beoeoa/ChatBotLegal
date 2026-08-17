"""Read-only snapshot and verification helpers for Feature 005 DB-1.

The script never writes to PostgreSQL, Chroma or the active collection pointer.
It writes only the requested JSON artifact and can verify a copied Chroma path.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

import chromadb
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
OLD_ENV_PATH = Path(
    os.getenv("LEGAL_OLD_ENV_PATH", r"J:\ChatBot\legal-chatbot\backend\.env")
)
DEFAULT_DATA_ROOT = Path(os.getenv("LEGAL_DATA_ROOT", r"D:\legal-chatbot-data"))
DEFAULT_CHROMA_PATH = Path(
    os.getenv("LEGAL_CHROMA_PATH", str(DEFAULT_DATA_ROOT / "chroma_store"))
)
DEFAULT_CORE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong"
DEFAULT_SOURCE_COLLECTION = "legal_chunks_vnlegal_lal"


def database_url() -> str:
    direct = os.getenv("LEGAL_DATABASE_URL", "").strip()
    if direct:
        return direct
    old = dotenv_values(OLD_ENV_PATH)
    value = str(old.get("DATABASE_URL") or "").strip()
    if value:
        return value
    repo_settings = dotenv_values(ROOT / ".env")
    release = str(repo_settings.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if release:
        return release.replace("@host.docker.internal:", "@127.0.0.1:")
    return "postgresql+psycopg2://postgres:postgres@localhost:5432/legal_chatbot"


def safe_database_target(url: str) -> dict[str, Any]:
    parsed = make_url(url)
    return {
        "driver": parsed.drivername,
        "host": parsed.host,
        "port": parsed.port,
        "database": parsed.database,
        "username": parsed.username,
    }


def sha256_ids(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def table_id_snapshot(connection, table: str) -> dict[str, Any]:
    rows = connection.execute(
        text(f"SELECT id FROM {table} ORDER BY id")
    ).scalars()
    digest = hashlib.sha256()
    count = 0
    for value in rows:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return {"count": count, "sha256_ids": digest.hexdigest()}


def document_counts(connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'active') AS active,
                count(*) FILTER (
                    WHERE status = 'active'
                      AND (effective_date IS NULL OR effective_date <= CURRENT_DATE)
                      AND (expired_date IS NULL OR expired_date > CURRENT_DATE)
                ) AS active_effective,
                count(*) FILTER (WHERE effective_date IS NULL) AS missing_effective_date,
                count(*) FILTER (
                    WHERE scope IS NULL OR btrim(scope) = ''
                ) AS missing_scope,
                count(*) FILTER (
                    WHERE sector IS NULL OR btrim(sector) = ''
                ) AS missing_sector,
                count(*) FILTER (
                    WHERE source_url IS NULL OR btrim(source_url) = ''
                ) AS missing_source_url
            FROM legal_documents
            """
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def scope_counts(connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT
                count(*) AS scope_rows,
                count(*) FILTER (WHERE included = TRUE) AS included_rows,
                count(DISTINCT document_id) FILTER (WHERE included = TRUE)
                    AS included_documents
            FROM legal_search_scope
            """
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def quality_counts(connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT
                count(*) AS assessed,
                count(*) FILTER (WHERE eligible = TRUE) AS eligible,
                count(*) FILTER (WHERE eligible = FALSE) AS excluded
            FROM legal_chunk_quality
            """
        )
    ).mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def collection_snapshot(client, name: str) -> dict[str, Any]:
    collection = client.get_collection(name)
    payload = collection.get(include=[])
    ids = [str(value) for value in payload.get("ids") or []]
    return {
        "count": int(collection.count()),
        "id_count": len(ids),
        "sha256_ids": sha256_ids(ids),
    }


def active_pointer(chroma_path: Path) -> str | None:
    pointer = chroma_path / "active_core_collection.txt"
    if not pointer.is_file():
        return None
    return pointer.read_text(encoding="utf-8").strip() or None


def git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
        )
    except Exception:
        return None


def retrieval_config(chroma_path: Path, core: str, source: str) -> dict[str, Any]:
    allowed = (
        "LEGAL_EMBED_DEVICE",
        "LEGAL_RETRIEVAL_CACHE_TTL_SECONDS",
        "LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES",
        "LEGAL_LEXICAL_TERM_LIMIT",
    )
    defaults = {
        "LEGAL_EMBED_DEVICE": "auto",
        "LEGAL_RETRIEVAL_CACHE_TTL_SECONDS": "300",
        "LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES": "1024",
        "LEGAL_LEXICAL_TERM_LIMIT": "4",
    }
    values = {
        key: os.getenv(key, defaults[key])
        for key in allowed
    }
    values.update(
        {
            "data_root": str(DEFAULT_DATA_ROOT.resolve()),
            "chroma_path": str(chroma_path.resolve()),
            "core_collection": core,
            "source_collection": source,
        }
    )
    return values


def snapshot(chroma_path: Path, core: str, source: str) -> dict[str, Any]:
    url = database_url()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            db = {
                "target": safe_database_target(url),
                "documents": document_counts(connection),
                "document_ids": table_id_snapshot(connection, "legal_documents"),
                "articles": table_id_snapshot(connection, "legal_articles"),
                "chunks": table_id_snapshot(connection, "legal_article_chunks"),
                "scope": scope_counts(connection),
                "quality": quality_counts(connection),
            }
    finally:
        engine.dispose()

    client = chromadb.PersistentClient(path=str(chroma_path))
    chroma = {
        "path": str(chroma_path.resolve()),
        "active_pointer": active_pointer(chroma_path),
        "collections": {
            core: collection_snapshot(client, core),
            source: collection_snapshot(client, source),
        },
    }
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": date.today().isoformat(),
        "git_commit": git_commit(),
        "retrieval_config": retrieval_config(chroma_path, core, source),
        "postgres": db,
        "chroma": chroma,
    }


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for name in ("documents", "document_ids", "articles", "chunks", "scope", "quality"):
        checks[f"postgres.{name}"] = before["postgres"][name] == after["postgres"][name]
    checks["postgres.target"] = before["postgres"]["target"] == after["postgres"]["target"]
    checks["chroma.active_pointer"] = (
        before["chroma"]["active_pointer"] == after["chroma"]["active_pointer"]
    )
    for name, before_collection in before["chroma"]["collections"].items():
        checks[f"chroma.{name}"] = (
            before_collection == after["chroma"]["collections"].get(name)
        )
    return {
        "all_match": all(checks.values()),
        "checks": checks,
    }


def verify_backup_files(backup_manifest: dict[str, Any], backup_dir: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    postgres = backup_manifest["postgres"]
    postgres_path = backup_dir / str(postgres["file"])
    checks["postgres.exists"] = postgres_path.is_file()
    if checks["postgres.exists"]:
        checks["postgres.bytes"] = postgres_path.stat().st_size == int(postgres["bytes"])
        checks["postgres.sha256"] = _sha256_file(postgres_path) == postgres["sha256"]
    for item in backup_manifest["chroma"]["files"]:
        path = backup_dir / "chroma_store" / str(item["path"])
        key = f"chroma:{item['path']}"
        checks[f"{key}:exists"] = path.is_file()
        if checks[f"{key}:exists"]:
            checks[f"{key}:bytes"] = path.stat().st_size == int(item["bytes"])
            checks[f"{key}:sha256"] = _sha256_file(path) == item["sha256"]
    return {"all_match": all(checks.values()), "checks": checks}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--core-collection", default=DEFAULT_CORE_COLLECTION)
    parser.add_argument("--source-collection", default=DEFAULT_SOURCE_COLLECTION)
    parser.add_argument("--compare", nargs=2, type=Path)
    parser.add_argument("--combine", action="store_true")
    parser.add_argument("--before", type=Path)
    parser.add_argument("--copy-verification", type=Path)
    parser.add_argument("--after", type=Path)
    parser.add_argument("--post-verification", type=Path)
    parser.add_argument("--backup-manifest", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--pg-restore-list-exit", type=int, default=0)
    args = parser.parse_args()

    if args.combine:
        if not all(
            path is not None
            for path in (
                args.before,
                args.copy_verification,
                args.after,
                args.post_verification,
                args.backup_manifest,
                args.backup_dir,
            )
        ):
            parser.error("--combine requires --before, --copy-verification, --backup-manifest and --backup-dir")
        before = json.loads(args.before.read_text(encoding="utf-8"))
        copy_verification = json.loads(
            args.copy_verification.read_text(encoding="utf-8")
        )
        after = json.loads(args.after.read_text(encoding="utf-8"))
        post_verification = json.loads(
            args.post_verification.read_text(encoding="utf-8")
        )
        backup_manifest = json.loads(
            args.backup_manifest.read_text(encoding="utf-8")
        )
        file_verification = verify_backup_files(backup_manifest, args.backup_dir)
        payload = {
            "schema_version": "feature005-db1-v1",
            "status": (
                "verified"
                if copy_verification.get("all_match")
                and file_verification.get("all_match")
                and post_verification.get("all_match")
                and args.pg_restore_list_exit == 0
                else "verification_failed"
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "legal_as_of": before["legal_as_of"],
            "git_commit": before["git_commit"],
            "retrieval_config": before["retrieval_config"],
            "runtime_target": before["postgres"]["target"],
            "active_collection_pointer": before["chroma"]["active_pointer"],
            "source_snapshot": before,
            "post_snapshot": after,
            "backup_artifact": backup_manifest,
            "verification": {
                "copied_collection_snapshot": copy_verification,
                "backup_file_checksums": file_verification,
                "post_restore_source_comparison": post_verification,
                "pg_restore_list_exit": args.pg_restore_list_exit,
            },
        }
    elif args.compare:
        before = json.loads(args.compare[0].read_text(encoding="utf-8"))
        after = json.loads(args.compare[1].read_text(encoding="utf-8"))
        payload = compare_snapshots(before, after)
    else:
        payload = snapshot(
            args.chroma_path.resolve(),
            args.core_collection,
            args.source_collection,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
