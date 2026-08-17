"""Plan/rehearse Feature 016 sidecars without touching a live database."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MIGRATIONS = ROOT / "scripts" / "legal_lifecycle_migrations"
UP = MIGRATIONS / "002_feature016_trust_up.surrealql"
DOWN = MIGRATIONS / "002_feature016_trust_down.surrealql"
TABLES = (
    "legal_chunk_provenance",
    "legal_audit_chain",
    "legal_audit_checkpoint",
)
PROTECTED_DATABASES = {"", "open_notebook", "production", "prod", "legal_chatbot"}


def selected_database() -> str:
    return str(os.getenv("SURREAL_DATABASE") or "open_notebook").strip()


def assert_isolated_database(*, confirmed: bool) -> str:
    database = selected_database()
    if not confirmed or database.casefold() in PROTECTED_DATABASES:
        raise RuntimeError(
            "isolated_database_required: set a unique SURREAL_DATABASE and pass --confirm-isolated"
        )
    return database


def migration_sql(direction: str) -> str:
    return (UP if direction == "up" else DOWN).read_text(encoding="utf-8-sig")


async def _query(sql: str):
    from open_notebook.database.repository import db_connection

    async with db_connection() as connection:
        return await connection.query(sql)


async def verify_schema() -> dict[str, int]:
    database = assert_isolated_database(confirmed=True)
    counts: dict[str, int] = {}
    for table in TABLES:
        result = await _query(f"SELECT count() AS count FROM {table} GROUP ALL;")
        row = result[0] if isinstance(result, list) and result else {}
        counts[table] = int(row.get("count") or 0) if isinstance(row, dict) else 0
    print({"database": database, "tables": counts})
    return counts


def _schema_text(value: object) -> str:
    """Canonical enough for a read-only INFO check across Surreal client versions."""

    return str(value).casefold()


async def verify_removed() -> dict[str, bool]:
    database = assert_isolated_database(confirmed=True)
    info = await _query("INFO FOR DB;")
    text = _schema_text(info)
    removed = {table: table.casefold() not in text for table in TABLES}
    if not all(removed.values()):
        raise RuntimeError("trust_sidecar_tables_still_present")
    print({"database": database, "removed": removed})
    return removed


async def apply(direction: str, *, confirmed: bool, allow_drop_records: bool) -> None:
    database = assert_isolated_database(confirmed=confirmed)
    if direction == "down":
        counts = await verify_schema()
        if any(counts.values()) and not allow_drop_records:
            raise RuntimeError("trust_records_present: isolated rollback refused")
    await _query(migration_sql(direction))
    print({"database": database, "direction": direction, "status": "applied"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "up", "verify", "down", "verify-down")
    )
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--allow-drop-records", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        print(
            {
                "database": selected_database(),
                "status": "dry_run",
                "up": str(UP),
                "down": str(DOWN),
                "tables": list(TABLES),
            }
        )
        return 0
    if args.command == "verify":
        asyncio.run(verify_schema())
        return 0
    if args.command == "verify-down":
        asyncio.run(verify_removed())
        return 0
    asyncio.run(
        apply(
            args.command,
            confirmed=args.confirm_isolated,
            allow_drop_records=args.allow_drop_records,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
