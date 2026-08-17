"""Plan and rehearse the Phase 2 lifecycle schema without touching live data."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MIGRATIONS = ROOT / "scripts" / "legal_lifecycle_migrations"
UP = MIGRATIONS / "001_phase2_up.surrealql"
DOWN = MIGRATIONS / "001_phase2_down.surrealql"
TABLES = (
    "legal_document_draft",
    "legal_document_version",
    "legal_document_approval",
    "legal_activation_manifest",
    "legal_lifecycle_idempotency",
)
PROTECTED_DATABASES = {"", "open_notebook", "production", "prod", "legal_chatbot"}


def load_runtime_environment() -> None:
    """Load local defaults without mutating caller-selected process config.

    The module is imported by migration audits, so environment loading must be
    an explicit CLI action. Existing shell/test values remain authoritative.
    """

    load_dotenv(ROOT / ".env", override=False)


def selected_database() -> str:
    return str(os.getenv("SURREAL_DATABASE") or "open_notebook").strip()


def assert_isolated_database(*, confirmed: bool) -> str:
    database = selected_database()
    if not confirmed or database.lower() in PROTECTED_DATABASES:
        raise RuntimeError(
            "isolated_database_required: set SURREAL_DATABASE to a unique test name "
            "and pass --confirm-isolated"
        )
    return database


def migration_sql(direction: str) -> str:
    path = UP if direction == "up" else DOWN
    return path.read_text(encoding="utf-8-sig")


async def _query(sql: str, variables: dict | None = None):
    from open_notebook.database.repository import db_connection

    async with db_connection() as connection:
        return await connection.query(sql, variables)


async def verify_schema() -> dict[str, int]:
    database = assert_isolated_database(confirmed=True)
    counts: dict[str, int] = {}
    for table in TABLES:
        result = await _query(f"SELECT count() AS count FROM {table} GROUP ALL;")
        row = result[0] if isinstance(result, list) and result else {}
        counts[table] = int(row.get("count") or 0) if isinstance(row, dict) else 0
    print({"database": database, "tables": counts})
    return counts


async def apply(direction: str, *, confirmed: bool, allow_drop_records: bool) -> None:
    database = assert_isolated_database(confirmed=confirmed)
    if direction == "down":
        counts = await verify_schema()
        if any(counts.values()) and not allow_drop_records:
            raise RuntimeError("lifecycle_records_present: pass --allow-drop-records for isolated rehearsal")
    await _query(migration_sql(direction))
    print({"database": database, "direction": direction, "status": "applied"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "up", "verify", "down"))
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--allow-drop-records", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_runtime_environment()
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
