"""Safely plan, preflight, apply, or roll back retrieval indexes.

``--plan-only`` validates and prints the migration without a database
connection. The default connected dry run performs read-only preflight checks.
Every migration statement runs on an AUTOCOMMIT connection because PostgreSQL
does not allow ``CREATE/DROP INDEX CONCURRENTLY`` inside a transaction block.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable
from enum import Enum
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = REPO_ROOT / "scripts" / "postgres_retrieval_indexes"
UP_SQL_PATH = MIGRATION_DIR / "001_retrieval_indexes_up.sql"
DOWN_SQL_PATH = MIGRATION_DIR / "001_retrieval_indexes_down.sql"
STATEMENT_DELIMITER = "-- migrate:split"
DEFAULT_BENCHMARK_TERM = "khai sinh"

INDEX_NAMES = (
    "ix_legal_retrieval_chunks_content_trgm",
    "ix_legal_retrieval_chunks_heading_trgm",
    "ix_legal_retrieval_articles_title_trgm",
    "ix_legal_retrieval_documents_title_trgm",
    "ix_legal_retrieval_documents_law_number_trgm",
    "ix_legal_retrieval_chunks_article_id",
    "ix_legal_retrieval_articles_active_document_id",
    "ix_legal_retrieval_relationships_source_document_id",
    "ix_legal_retrieval_relationships_target_document_id",
)

EXPECTED_INDEX_SIGNATURES = {
    "ix_legal_retrieval_chunks_content_trgm": (
        "legal_article_chunks",
        "using gin",
        "content",
        "gin_trgm_ops",
    ),
    "ix_legal_retrieval_chunks_heading_trgm": (
        "legal_article_chunks",
        "using gin",
        "heading",
        "gin_trgm_ops",
    ),
    "ix_legal_retrieval_articles_title_trgm": (
        "legal_articles",
        "using gin",
        "title",
        "gin_trgm_ops",
    ),
    "ix_legal_retrieval_documents_title_trgm": (
        "legal_documents",
        "using gin",
        "title",
        "gin_trgm_ops",
    ),
    "ix_legal_retrieval_documents_law_number_trgm": (
        "legal_documents",
        "using gin",
        "law_number",
        "gin_trgm_ops",
    ),
    "ix_legal_retrieval_chunks_article_id": (
        "legal_article_chunks",
        "using btree",
        "article_id",
    ),
    "ix_legal_retrieval_articles_active_document_id": (
        "legal_articles",
        "using btree",
        "document_id",
        "where",
        "status",
        "active",
    ),
    "ix_legal_retrieval_relationships_source_document_id": (
        "legal_document_relationships",
        "using btree",
        "source_document_id",
    ),
    "ix_legal_retrieval_relationships_target_document_id": (
        "legal_document_relationships",
        "using btree",
        "target_document_id",
    ),
}

REQUIRED_COLUMNS = {
    "legal_article_chunks": {"id", "article_id", "content", "heading"},
    "legal_articles": {"id", "document_id", "title", "status"},
    "legal_documents": {
        "id",
        "title",
        "law_number",
        "status",
        "effective_date",
    },
    "legal_search_scope": {"document_id", "included"},
}

BENCHMARK_SQL = """
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
SELECT DISTINCT
    c.id AS chunk_id,
    d.effective_date
FROM legal_article_chunks c
JOIN legal_articles a ON a.id = c.article_id
JOIN legal_documents d ON d.id = a.document_id
JOIN legal_search_scope search_scope
  ON search_scope.document_id = d.id
 AND search_scope.included = TRUE
WHERE (
       legal_normalize_text(c.content) LIKE :pattern
    OR legal_normalize_text(c.heading) LIKE :pattern
    OR legal_normalize_text(a.title) LIKE :pattern
    OR legal_normalize_text(d.title) LIKE :pattern
    OR legal_normalize_text(d.law_number) LIKE :pattern
)
  AND d.status = 'active'
  AND a.status = 'active'
ORDER BY d.effective_date DESC NULLS LAST, c.id DESC
LIMIT 120
""".strip()

LEGACY_BENCHMARK_SQL = (
    BENCHMARK_SQL
    .replace("legal_normalize_text(c.content)", "LOWER(COALESCE(c.content, ''))")
    .replace("legal_normalize_text(c.heading)", "LOWER(COALESCE(c.heading, ''))")
    .replace("legal_normalize_text(a.title)", "LOWER(COALESCE(a.title, ''))")
    .replace("legal_normalize_text(d.title)", "LOWER(COALESCE(d.title, ''))")
    .replace("legal_normalize_text(d.law_number)", "LOWER(COALESCE(d.law_number, ''))")
)


class Mode(str, Enum):
    PLAN_ONLY = "plan-only"
    DRY_RUN = "dry-run"
    APPLY = "apply"
    ROLLBACK = "rollback"


class PreflightError(RuntimeError):
    """Raised before any migration statement when the target is not safe."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or explicitly execute concurrent PostgreSQL retrieval indexes. "
            "With no action flag, no database object is changed."
        )
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--plan-only",
        dest="mode",
        action="store_const",
        const=Mode.PLAN_ONLY.value,
        help=(
            "Validate and print the up migration without resolving a database "
            "URL or opening a connection."
        ),
    )
    action.add_argument(
        "--apply",
        dest="mode",
        action="store_const",
        const=Mode.APPLY.value,
        help="Explicitly apply the up migration.",
    )
    action.add_argument(
        "--rollback",
        dest="mode",
        action="store_const",
        const=Mode.ROLLBACK.value,
        help="Explicitly execute the down migration; never runs after a failure.",
    )
    parser.set_defaults(mode=Mode.DRY_RUN.value)
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "PostgreSQL SQLAlchemy URL. Prefer LEGAL_DATABASE_URL so a password "
            "is not stored in shell history."
        ),
    )
    parser.add_argument(
        "--benchmark-term",
        default=DEFAULT_BENCHMARK_TERM,
        help="Representative lexical term for EXPLAIN ANALYZE before/after.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report path. Without it, the result is only printed.",
    )
    return parser


def resolve_database_url(explicit_url: str | None = None) -> str:
    database_url = (explicit_url or "").strip() or os.getenv(
        "LEGAL_DATABASE_URL", ""
    ).strip()
    if not database_url:
        raise ValueError(
            "Set LEGAL_DATABASE_URL or pass --database-url; no credential-bearing "
            "database URL is embedded in this runner."
        )
    return database_url


def create_autocommit_engine(
    database_url: str,
    *,
    engine_factory: Callable[..., Any] | None = None,
) -> Any:
    if not database_url.casefold().startswith(("postgresql://", "postgresql+")):
        raise ValueError("Only a PostgreSQL database URL is accepted.")
    if engine_factory is None:
        from sqlalchemy import create_engine

        engine_factory = create_engine
    return engine_factory(
        database_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )


def split_migration_sql(sql: str) -> list[str]:
    statements = [part.strip() for part in sql.split(STATEMENT_DELIMITER)]
    return [statement for statement in statements if _sql_without_comments(statement)]


def _sql_without_comments(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).strip()


def _statement_head(statement: str) -> str:
    return " ".join(_sql_without_comments(statement).split()).casefold()


def _index_names_in(statements: Iterable[str], verb: str) -> set[str]:
    pattern = re.compile(
        rf"\b{verb}\s+INDEX\s+CONCURRENTLY\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
        r"([a-z_][a-z0-9_]*)",
        flags=re.IGNORECASE,
    )
    return {
        match.group(1).casefold()
        for statement in statements
        for match in pattern.finditer(_sql_without_comments(statement))
    }


def _function_names_in(statements: Iterable[str], verb: str) -> set[str]:
    if verb.casefold() == "create":
        pattern = re.compile(
            r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z_][a-z0-9_.]*)",
            flags=re.IGNORECASE,
        )
    else:
        pattern = re.compile(
            r"\bDROP\s+FUNCTION\s+IF\s+EXISTS\s+([a-z_][a-z0-9_.]*)",
            flags=re.IGNORECASE,
        )
    return {
        match.group(1).casefold()
        for statement in statements
        for match in pattern.finditer(_sql_without_comments(statement))
    }


def load_migration(mode: Mode) -> tuple[Path, list[str]]:
    path = DOWN_SQL_PATH if mode == Mode.ROLLBACK else UP_SQL_PATH
    statements = split_migration_sql(path.read_text(encoding="utf-8"))
    expected_indexes = set(INDEX_NAMES)

    if mode == Mode.ROLLBACK:
        allowed = ("drop index concurrently if exists ", "drop function if exists ")
        if any(not _statement_head(statement).startswith(allowed) for statement in statements):
            raise ValueError("Rollback contains an operation outside its owned objects.")
        if _index_names_in(statements, "drop") != expected_indexes:
            raise ValueError("Rollback index ownership does not match the up migration.")
        up_statements = split_migration_sql(UP_SQL_PATH.read_text(encoding="utf-8"))
        if _function_names_in(statements, "drop") != _function_names_in(
            up_statements, "create"
        ):
            raise ValueError("Rollback function ownership does not match the up migration.")
    else:
        allowed = (
            "create extension if not exists pg_trgm",
            "create extension if not exists unaccent",
            "create index concurrently if not exists ",
            "create function ",
            "create or replace function ",
        )
        if any(not _statement_head(statement).startswith(allowed) for statement in statements):
            raise ValueError("Up migration contains an unsupported operation.")
        if _index_names_in(statements, "create") != expected_indexes:
            raise ValueError("Up migration index set does not match the runner contract.")
    return path, statements


def build_offline_plan() -> dict[str, Any]:
    """Validate both migrations and describe apply without PostgreSQL."""
    migration_path, statements = load_migration(Mode.APPLY)
    rollback_path, rollback_statements = load_migration(Mode.ROLLBACK)
    return {
        "mode": Mode.PLAN_ONLY.value,
        "migration": migration_path.name,
        "planned_statements": len(statements),
        "statement_plan": [
            _sql_without_comments(statement) for statement in statements
        ],
        "rollback_validation": {
            "migration": rollback_path.name,
            "planned_statements": len(rollback_statements),
            "validated": True,
        },
        "preflight": {
            "performed": False,
            "reason": "Offline plan-only mode does not connect to PostgreSQL.",
        },
        "database_connection_opened": False,
        "executed": False,
    }


def _text(sql: str) -> Any:
    from sqlalchemy import text

    return text(sql)


def _one_mapping(connection: Any, sql: str, params: dict[str, Any] | None = None):
    return connection.execute(_text(sql), params or {}).mappings().one()


def _driver_autocommit(connection: Any) -> bool | None:
    proxy = getattr(connection, "connection", None)
    driver = getattr(proxy, "driver_connection", proxy)
    value = getattr(driver, "autocommit", None)
    return value if isinstance(value, bool) else None


def _inspect_columns(connection: Any, table_name: str) -> set[str]:
    rows = connection.execute(
        _text(
            """
            SELECT attribute.attname AS column_name
            FROM pg_attribute attribute
            WHERE attribute.attrelid = to_regclass(:table_name)
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped
            """
        ),
        {"table_name": table_name},
    ).mappings()
    return {str(row["column_name"]) for row in rows}


def _inspect_indexes(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        _text(
            """
            SELECT
                index_class.relname AS index_name,
                pg_index.indisready AS ready,
                pg_index.indisvalid AS valid,
                pg_get_indexdef(pg_index.indexrelid) AS definition
            FROM pg_index
            JOIN pg_class index_class
              ON index_class.oid = pg_index.indexrelid
            WHERE index_class.relname = ANY(:index_names)
            ORDER BY index_class.relname
            """
        ),
        {"index_names": list(INDEX_NAMES)},
    ).mappings()
    return [dict(row) for row in rows]


def validate_existing_index_definitions(indexes: Iterable[dict[str, Any]]) -> None:
    mismatched: list[str] = []
    for row in indexes:
        index_name = str(row.get("index_name") or "").casefold()
        expected_tokens = EXPECTED_INDEX_SIGNATURES.get(index_name)
        definition = " ".join(
            str(row.get("definition") or "").casefold().split()
        )
        if expected_tokens is None or any(
            token.casefold() not in definition for token in expected_tokens
        ):
            mismatched.append(index_name or "<unnamed>")
    if mismatched:
        raise PreflightError(
            "Target index name has an unexpected definition: "
            + ", ".join(sorted(mismatched))
        )


def run_preflight(connection: Any, mode: Mode) -> dict[str, Any]:
    dialect_name = str(getattr(getattr(connection, "dialect", None), "name", ""))
    if dialect_name != "postgresql":
        raise PreflightError("The connected database is not PostgreSQL.")

    if _driver_autocommit(connection) is not True:
        raise PreflightError(
            "AUTOCOMMIT is required for CREATE/DROP INDEX CONCURRENTLY."
        )

    server = _one_mapping(
        connection,
        """
        SELECT
            current_database() AS database_name,
            current_user AS database_user,
            current_setting('server_version_num')::INTEGER AS server_version_num,
            pg_is_in_recovery() AS in_recovery,
            has_database_privilege(current_database(), 'CREATE') AS can_create
        """,
    )
    if bool(server["in_recovery"]) and mode != Mode.DRY_RUN:
        raise PreflightError("A recovery/read-only PostgreSQL node cannot be changed.")

    extension = _one_mapping(
        connection,
        """
        SELECT
            EXISTS (
                SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'
            ) AS available,
            EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'
            ) AS installed
        """,
    )
    if not bool(extension["available"]):
        raise PreflightError("PostgreSQL extension pg_trgm is not available.")
    if (
        mode == Mode.APPLY
        and not bool(extension["installed"])
        and not bool(server["can_create"])
    ):
        raise PreflightError(
            "pg_trgm is not installed and the current database user cannot create it."
        )

    schema: dict[str, list[str]] = {}
    for table_name, required in REQUIRED_COLUMNS.items():
        columns = _inspect_columns(connection, table_name)
        missing = required - columns
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise PreflightError(
                f"{table_name} is absent or missing required columns: {missing_text}."
            )
        schema[table_name] = sorted(columns)

    indexes = _inspect_indexes(connection)
    # IF NOT EXISTS checks only a relation name, not whether that relation has
    # the intended definition. Refuse both apply and rollback on a name clash.
    validate_existing_index_definitions(indexes)
    invalid = [
        row["index_name"]
        for row in indexes
        if not bool(row["ready"]) or not bool(row["valid"])
    ]
    if invalid and mode != Mode.ROLLBACK:
        raise PreflightError(
            "Target index exists in an invalid state; inspect it manually: "
            + ", ".join(sorted(invalid))
        )

    return {
        "ok": True,
        "database_name": server["database_name"],
        "database_user": server["database_user"],
        "server_version_num": int(server["server_version_num"]),
        "in_recovery": bool(server["in_recovery"]),
        "pg_trgm_available": bool(extension["available"]),
        "pg_trgm_installed": bool(extension["installed"]),
        "required_schema": schema,
        "existing_target_indexes": indexes,
        "invalid_target_indexes": sorted(invalid),
    }


def _collect_index_names(plan_node: Any) -> list[str]:
    names: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            index_name = value.get("Index Name")
            if index_name:
                names.add(str(index_name))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(plan_node)
    return sorted(names)


def run_explain_analyze(connection: Any, benchmark_term: str) -> dict[str, Any]:
    cleaned_term = " ".join(benchmark_term.casefold().split())
    if len(cleaned_term) < 3 or len(cleaned_term) > 120:
        raise ValueError("Benchmark term must contain between 3 and 120 characters.")

    normalized_available = bool(
        connection.execute(
            _text("SELECT to_regprocedure('legal_normalize_text(text)') IS NOT NULL")
        ).scalar_one()
    )
    if normalized_available:
        decomposed = unicodedata.normalize("NFD", cleaned_term).replace("đ", "d")
        cleaned_term = "".join(
            char for char in decomposed if unicodedata.category(char) != "Mn"
        )
    payload = connection.execute(
        _text(BENCHMARK_SQL if normalized_available else LEGACY_BENCHMARK_SQL),
        {"pattern": f"%{cleaned_term}%"},
    ).scalar_one()
    if isinstance(payload, str):
        payload = json.loads(payload)
    root = payload[0] if isinstance(payload, list) else payload
    if not isinstance(root, dict):
        raise RuntimeError("PostgreSQL returned an unexpected EXPLAIN JSON payload.")

    return {
        "planning_time_ms": root.get("Planning Time"),
        "execution_time_ms": root.get("Execution Time"),
        "indexes_used": _collect_index_names(root.get("Plan")),
        "plan": root.get("Plan"),
    }


def execute_migration(connection: Any, statements: Iterable[str]) -> None:
    for statement in statements:
        connection.execute(_text(statement))


def run_with_engine(
    engine: Any,
    *,
    mode: Mode,
    benchmark_term: str = DEFAULT_BENCHMARK_TERM,
) -> dict[str, Any]:
    if mode == Mode.PLAN_ONLY:
        raise ValueError("Use build_offline_plan() for plan-only mode.")
    migration_mode = Mode.APPLY if mode == Mode.DRY_RUN else mode
    migration_path, statements = load_migration(migration_mode)

    with engine.connect() as connection:
        preflight = run_preflight(connection, mode)
        result: dict[str, Any] = {
            "mode": mode.value,
            "migration": migration_path.name,
            "planned_statements": len(statements),
            "statement_plan": [
                _sql_without_comments(statement) for statement in statements
            ],
            "preflight": preflight,
            "executed": False,
        }
        if mode == Mode.DRY_RUN:
            return result

        result["explain_before"] = run_explain_analyze(
            connection, benchmark_term
        )
        # Failures deliberately propagate. This runner never invokes rollback
        # implicitly after a partial or failed concurrent index build.
        execute_migration(connection, statements)
        result["executed"] = True
        result["explain_after"] = run_explain_analyze(connection, benchmark_term)
        before_ms = result["explain_before"].get("execution_time_ms")
        after_ms = result["explain_after"].get("execution_time_ms")
        if isinstance(before_ms, (int, float)) and isinstance(
            after_ms, (int, float)
        ):
            result["execution_comparison"] = {
                "before_ms": before_ms,
                "after_ms": after_ms,
                "delta_ms": round(after_ms - before_ms, 3),
                "before_over_after_ratio": (
                    round(before_ms / after_ms, 3) if after_ms > 0 else None
                ),
            }
        return result


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    mode = Mode(args.mode)
    engine = None
    try:
        if mode == Mode.PLAN_ONLY:
            result = build_offline_plan()
        else:
            engine = create_autocommit_engine(
                resolve_database_url(args.database_url)
            )
            result = run_with_engine(
                engine,
                mode=mode,
                benchmark_term=args.benchmark_term,
            )
        rendered = json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
        if args.report:
            report_path = args.report.expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
