"""Plan and rehearse the additive Vector Integrity V3 schema.

Only a uniquely named isolated PostgreSQL database can be mutated.  The
command has no Chroma client and cannot change an active serving pointer.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "scripts" / "feature018_migrations"
UP = MIGRATIONS / "003_vector_integrity_v3_up.sql"
DOWN = MIGRATIONS / "003_vector_integrity_v3_down.sql"
TABLES = (
    "legal_chunk_release",
    "legal_chunk_revision",
    "legal_metadata_review_case",
)


def database_url(*, required: bool = True) -> str | None:
    value = str(os.getenv("FEATURE018_VECTOR_DATABASE_URL") or "").strip()
    if not value:
        if required:
            raise RuntimeError("FEATURE018_VECTOR_DATABASE_URL_required")
        return None
    if not value.casefold().startswith(("postgresql://", "postgresql+psycopg2://")):
        raise RuntimeError("postgresql_database_required")
    return value


def database_name(url: str | None = None) -> str:
    value = url or database_url()
    assert value
    parsed = urlsplit(value.replace("postgresql+psycopg2://", "postgresql://", 1))
    return parsed.path.rsplit("/", 1)[-1].strip()


def redacted_url(url: str) -> str:
    parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("postgresql", host, parsed.path, "", ""))


def assert_isolated_database(*, confirmed: bool) -> str:
    url = database_url()
    assert url
    name = database_name(url).casefold()
    if not confirmed or not (
        name.startswith("feature018_vector_")
        or name.startswith("feature018_isolated_")
    ):
        raise RuntimeError(
            "isolated_database_required: use feature018_vector_* and --confirm-isolated"
        )
    return url


def migration_sql(direction: str) -> str:
    if direction == "up":
        path = UP
    elif direction == "down":
        path = DOWN
    else:
        raise ValueError("direction must be up or down")
    return path.read_text(encoding="utf-8")


def plan_payload() -> dict[str, object]:
    url = database_url(required=False)
    return {
        "status": "plan_only",
        "database": database_name(url) if url else "not_configured",
        "target": redacted_url(url) if url else "not_configured",
        "up": str(UP),
        "down": str(DOWN),
        "tables": list(TABLES),
        "active_pointer_change": False,
        "vector_mutation": False,
        "live_apply": False,
    }


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True, pool_pre_ping=True)


def apply_schema(
    direction: str,
    *,
    confirmed: bool,
    allow_drop_fixtures: bool = False,
) -> None:
    url = assert_isolated_database(confirmed=confirmed)
    if direction == "down" and not allow_drop_fixtures:
        raise RuntimeError("rehearsal_down_refused: --allow-drop-fixtures is required")
    engine = _engine(url)
    try:
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute(migration_sql(direction))
            raw.commit()
        finally:
            raw.close()
    finally:
        engine.dispose()


def verify(*, confirmed: bool) -> dict[str, object]:
    from sqlalchemy import inspect

    url = assert_isolated_database(confirmed=confirmed)
    engine = _engine(url)
    try:
        existing = set(inspect(engine).get_table_names())
        missing = [table for table in TABLES if table not in existing]
        if missing:
            raise RuntimeError("vector_integrity_schema_missing:" + ",".join(missing))
        return {
            "status": "verified",
            "tables": list(TABLES),
            "active_pointer_change": False,
            "vector_mutation": False,
        }
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "up", "verify", "down"))
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--allow-drop-fixtures", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        result = plan_payload()
    elif args.command == "verify":
        result = verify(confirmed=args.confirm_isolated)
    else:
        apply_schema(
            args.command,
            confirmed=args.confirm_isolated,
            allow_drop_fixtures=args.allow_drop_fixtures,
        )
        result = {
            "status": "applied",
            "direction": args.command,
            "database": database_name(),
            "active_pointer_change": False,
            "vector_mutation": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
