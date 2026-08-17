"""Plan or rehearse Feature 017 PostgreSQL schema on an isolated database only."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "scripts" / "feature017_migrations"
UP = MIGRATIONS / "001_procedure_form_governance_up.sql"
DOWN = MIGRATIONS / "001_procedure_form_governance_down.sql"
TABLES = (
    "legal_procedure",
    "legal_form_asset",
    "procedure_form_binding",
    "procedure_question_alias",
    "form_review_case",
    "form_review_revision",
    "form_release",
    "form_source_gap",
    "form_active_release",
    "form_workflow_event",
    "form_notification_outbox",
)
PROTECTED_DATABASES = {
    "",
    "postgres",
    "legal_chatbot",
    "production",
    "prod",
    "release",
    "staging",
}


def database_url() -> str:
    value = str(os.getenv("LEGAL_DATABASE_URL") or "").strip()
    if not value:
        raise RuntimeError("LEGAL_DATABASE_URL_required")
    if not value.casefold().startswith(("postgresql://", "postgresql+")):
        raise RuntimeError("postgresql_database_required")
    return value


def database_name(url: str | None = None) -> str:
    parsed = urlsplit((url or database_url()).replace("postgresql+psycopg2://", "postgresql://", 1))
    return parsed.path.rsplit("/", 1)[-1].strip()


def redacted_url(url: str) -> str:
    parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("postgresql", host, parsed.path, "", ""))


def assert_isolated_database(*, confirmed: bool) -> str:
    url = database_url()
    name = database_name(url)
    if not confirmed or name.casefold() in PROTECTED_DATABASES:
        raise RuntimeError(
            "isolated_database_required: use a unique rehearsal database and --confirm-isolated"
        )
    if not any(marker in name.casefold() for marker in ("isolated", "rehearsal", "test")):
        raise RuntimeError("isolated_database_required: database name must identify rehearsal scope")
    return url


def migration_sql(direction: str) -> str:
    if direction not in {"up", "down"}:
        raise ValueError("direction must be up or down")
    return (UP if direction == "up" else DOWN).read_text(encoding="utf-8")


def plan_payload() -> dict[str, object]:
    url = database_url()
    return {
        "status": "plan_only",
        "database": database_name(url),
        "target": redacted_url(url),
        "up": str(UP),
        "down": str(DOWN),
        "tables": list(TABLES),
        "live_apply": False,
    }


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True, pool_pre_ping=True)


def apply(direction: str, *, confirmed: bool, allow_drop_records: bool = False) -> None:
    url = assert_isolated_database(confirmed=confirmed)
    engine = _engine(url)
    try:
        if direction == "down" and not allow_drop_records:
            counts = verify(confirmed=True)
            if any(counts.values()):
                raise RuntimeError("feature017_records_present: rollback refused")
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute(migration_sql(direction))
            raw.commit()
        finally:
            raw.close()
    finally:
        engine.dispose()


def verify(*, confirmed: bool) -> dict[str, int]:
    from sqlalchemy import inspect, text

    url = assert_isolated_database(confirmed=confirmed)
    engine = _engine(url)
    try:
        existing = set(inspect(engine).get_table_names())
        missing = [table for table in TABLES if table not in existing]
        if missing:
            raise RuntimeError(f"feature017_schema_missing:{','.join(missing)}")
        with engine.connect() as connection:
            return {
                table: int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
                for table in TABLES
            }
    finally:
        engine.dispose()


def verify_down(*, confirmed: bool) -> dict[str, bool]:
    from sqlalchemy import inspect

    url = assert_isolated_database(confirmed=confirmed)
    engine = _engine(url)
    try:
        existing = set(inspect(engine).get_table_names())
        result = {table: table not in existing for table in TABLES}
        if not all(result.values()):
            raise RuntimeError("feature017_tables_still_present")
        return result
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "up", "verify", "down", "verify-down"))
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--allow-drop-records", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        print(plan_payload())
    elif args.command == "verify":
        print(verify(confirmed=args.confirm_isolated))
    elif args.command == "verify-down":
        print(verify_down(confirmed=args.confirm_isolated))
    else:
        apply(
            args.command,
            confirmed=args.confirm_isolated,
            allow_drop_records=args.allow_drop_records,
        )
        print({"status": "applied", "direction": args.command, "database": database_name()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
