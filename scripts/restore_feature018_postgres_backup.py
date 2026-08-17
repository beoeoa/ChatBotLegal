"""Restore a Feature 018 PostgreSQL dump into a guarded isolated database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

import psycopg2
from dotenv import dotenv_values
from psycopg2 import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
ISOLATED_DATABASE = re.compile(r"^feature018_restore_[a-z0-9_]+$")


def assert_isolated_database_name(name: str) -> str:
    normalized = str(name or "").strip().lower()
    if not ISOLATED_DATABASE.fullmatch(normalized):
        raise ValueError("feature018_restore_database_name_not_isolated")
    return normalized


def compare_counts(
    source: Mapping[str, int], restored: Mapping[str, int]
) -> dict[str, object]:
    mismatches = {
        table: {"source": source.get(table), "restored": restored.get(table)}
        for table in sorted(set(source) | set(restored))
        if source.get(table) != restored.get(table)
    }
    return {"passed": not mismatches, "count_mismatches": mismatches}


def _database_url() -> str:
    values = dotenv_values(ROOT / ".env")
    value = str(
        os.getenv("LEGAL_CORPUS_DATABASE_URL")
        or values.get("LEGAL_RELEASE_DATABASE_URL")
        or ""
    ).strip()
    if not value:
        raise RuntimeError("legal_release_database_url_missing")
    return value.replace("@host.docker.internal:", "@127.0.0.1:")


def _pg_restore_path() -> Path:
    found = shutil.which("pg_restore")
    candidates = [
        Path(found) if found else None,
        Path(r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe"),
        Path(r"C:\Program Files\PostgreSQL\17\bin\pg_restore.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError("pg_restore_executable_not_found")


def _connect(parsed, database: str):
    return psycopg2.connect(
        host=parsed.host or "127.0.0.1",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "",
        dbname=database,
    )


def _table_counts(parsed, database: str) -> dict[str, int]:
    with _connect(parsed, database) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = [str(row[0]) for row in cursor.fetchall()]
            counts: dict[str, int] = {}
            for table in tables:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(
                        sql.Identifier(table)
                    )
                )
                counts[table] = int(cursor.fetchone()[0])
            return counts


def restore(
    *,
    dump_path: Path,
    database_name: str,
    release_manifest: Path,
) -> dict[str, object]:
    started = datetime.now(UTC)
    database_name = assert_isolated_database_name(database_name)
    dump_path = dump_path.resolve()
    if not dump_path.is_file():
        raise FileNotFoundError(f"postgres_dump_missing:{dump_path}")
    parsed = make_url(_database_url())
    source_database = str(parsed.database or "")
    if not source_database:
        raise RuntimeError("source_database_name_missing")
    source_counts = _table_counts(parsed, source_database)

    admin = _connect(parsed, "postgres")
    try:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,),
            )
            if cursor.fetchone():
                raise FileExistsError(
                    f"isolated_restore_database_exists:{database_name}"
                )
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(database_name)
                )
            )
    finally:
        admin.close()

    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    executable = _pg_restore_path()
    list_result = subprocess.run(
        [str(executable), "--list", str(dump_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if list_result.returncode != 0:
        raise RuntimeError("postgres_dump_catalog_invalid")
    restore_result = subprocess.run(
        [
            str(executable),
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "--host",
            str(parsed.host or "127.0.0.1"),
            "--port",
            str(parsed.port or 5432),
            "--username",
            str(parsed.username or "postgres"),
            "--dbname",
            database_name,
            str(dump_path),
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if restore_result.returncode != 0:
        raise RuntimeError("postgres_restore_failed")

    restored_counts = _table_counts(parsed, database_name)
    comparison = compare_counts(source_counts, restored_counts)
    finished = datetime.now(UTC)
    return {
        "schema_version": "feature018-postgres-real-restore-v1",
        "status": "PASS" if comparison["passed"] else "FAIL",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "database": database_name,
        "isolated": True,
        "production_pointer_changed": False,
        "dump_sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
        "release_fingerprint": hashlib.sha256(
            release_manifest.read_bytes()
        ).hexdigest(),
        "dump_catalog_valid": True,
        "source_table_count": len(source_counts),
        "restored_table_count": len(restored_counts),
        "source_total_rows": sum(source_counts.values()),
        "restored_total_rows": sum(restored_counts.values()),
        "comparison": comparison,
        "credentials_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = restore(
        dump_path=args.dump,
        database_name=args.database,
        release_manifest=args.release_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "database": report["database"],
                "table_count": report["restored_table_count"],
                "total_rows": report["restored_total_rows"],
                "mismatch_count": len(report["comparison"]["count_mismatches"]),
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
