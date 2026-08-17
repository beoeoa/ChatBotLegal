"""Plan and rehearse Feature 018 support/FAQ migration on an isolated DB only.

This command never changes a runtime pointer. JSON inputs are read-only and
remain the compatibility source throughout rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "scripts" / "feature018_migrations"
UPS = (
    MIGRATIONS / "001_support_faq_up.sql",
    MIGRATIONS / "002_lifecycle_impact_index_up.sql",
)
DOWNS = (
    MIGRATIONS / "002_lifecycle_impact_index_down.sql",
    MIGRATIONS / "001_support_faq_down.sql",
)
DEFAULT_SUPPORT_DIR = ROOT / "data" / "support_tickets"
DEFAULT_FAQ_FILE = ROOT / "data" / "notebook_data" / "faq_store.json"
TABLES = (
    "feature018_import_batch",
    "feature018_source_record",
    "support_ticket",
    "support_state_event",
    "support_message",
    "support_attachment",
    "officer_presence",
    "support_assignment",
    "support_content_access",
    "faq_identity",
    "faq_revision",
    "faq_release",
    "faq_release_item",
    "faq_active_release",
    "legal_change_event",
    "legal_document_relation_candidate",
    "legal_provision_effectivity",
    "legal_impact_case",
    "legal_index_manifest",
    "legal_index_job",
)
PROTECTED_DATABASES = {
    "",
    "postgres",
    "production",
    "prod",
    "release",
    "staging",
}


def database_url(*, required: bool = True) -> str | None:
    value = str(os.getenv("FEATURE018_DATABASE_URL") or "").strip()
    if not value:
        if required:
            raise RuntimeError("FEATURE018_DATABASE_URL_required")
        return None
    if not value.casefold().startswith(("postgresql://", "postgresql+")):
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
    name = database_name(url)
    if (
        not confirmed
        or name.casefold() in PROTECTED_DATABASES
        or not name.casefold().startswith("feature018_isolated")
    ):
        raise RuntimeError(
            "isolated_database_required: use a unique rehearsal database and --confirm-isolated"
        )
    return url


def migration_sql(direction: str) -> str:
    if direction not in {"up", "down"}:
        raise ValueError("direction must be up or down")
    paths = UPS if direction == "up" else DOWNS
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths)


def plan_payload() -> dict[str, Any]:
    url = database_url(required=False)
    return {
        "status": "plan_only",
        "database": database_name(url) if url else "not_configured",
        "target": redacted_url(url) if url else "not_configured",
        "up": [str(path) for path in UPS],
        "down": [str(path) for path in DOWNS],
        "tables": list(TABLES),
        "json_mode": "read_only",
        "postgres_mode": "shadow_only",
        "active_pointer_change": False,
        "live_apply": False,
    }


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True, pool_pre_ping=True)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def read_source_snapshot(
    support_dir: Path = DEFAULT_SUPPORT_DIR,
    faq_file: Path = DEFAULT_FAQ_FILE,
) -> dict[str, Any]:
    support: list[dict[str, Any]] = []
    if support_dir.is_dir():
        for path in sorted(support_dir.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                support.append(value)
    faqs: list[dict[str, Any]] = []
    if faq_file.is_file():
        try:
            payload = json.loads(faq_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        raw_faqs = payload.get("faqs") if isinstance(payload, dict) else []
        faqs = [item for item in (raw_faqs or []) if isinstance(item, dict)]
    snapshot = {"support": support, "faqs": faqs}
    return {
        **snapshot,
        "support_count": len(support),
        "faq_count": len(faqs),
        "snapshot_sha256": _sha256(snapshot),
    }


def normalize_support_ticket(value: dict[str, Any]) -> dict[str, Any]:
    ticket_id = str(value.get("id") or "").strip()
    owner = str(value.get("citizen_id") or value.get("owner_user_id") or "").strip()
    domain = str(value.get("domain") or value.get("assigned_department") or "").strip()
    if not ticket_id:
        raise ValueError("support_ticket_id_missing")
    if not owner:
        raise ValueError(f"support_owner_missing:{ticket_id}")
    if not domain:
        raise ValueError(f"support_domain_missing:{ticket_id}")
    status_map = {
        "open": "queued",
        "waiting": "queued",
        "answered": "active",
        "in_progress": "active",
    }
    allowed = {
        "queued", "assigned", "active", "waiting_citizen", "waiting_officer",
        "resolved", "closed", "cancelled", "expired",
    }
    status = status_map.get(str(value.get("status") or "waiting"), str(value.get("status") or "queued"))
    if status not in allowed:
        status = "queued"
    created = str(value.get("created_at") or datetime.now(timezone.utc).isoformat())
    updated = str(value.get("updated_at") or created)
    return {
        "id": ticket_id,
        "owner_user_id": owner,
        "canonical_domain": domain,
        "question_summary": str(value.get("ai_summary") or value.get("question") or "Yêu cầu hỗ trợ").strip(),
        "status": status,
        "priority": str(value.get("priority") or "normal"),
        "retention_expires_at": (datetime.now(timezone.utc) + timedelta(days=180)).isoformat(),
        "source_created_at": created,
        "source_updated_at": updated,
        "source_sha256": _sha256(value),
    }


def normalize_faq(value: dict[str, Any]) -> dict[str, Any]:
    faq_id = str(value.get("id") or "").strip()
    if not faq_id:
        raise ValueError("faq_id_missing")
    procedure_id = str(
        value.get("confirmed_procedure_id") or value.get("procedure_id") or "unresolved"
    ).strip()
    review_status = str(value.get("review_status") or "draft")
    return {
        "id": faq_id,
        "question": str(value.get("question") or "").strip(),
        "answer": str(value.get("answer") or "").strip(),
        "canonical_domain": str(value.get("domain") or "unknown").strip(),
        "confirmed_procedure_id": procedure_id,
        # Imported JSON is never public truth. An admin must confirm it into a release.
        "public_state": "needs_review" if review_status == "approved" else "pending",
        "evidence": value.get("verified_source_refs") or [],
        "source_sha256": _sha256(value),
    }


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


def import_shadow(
    *,
    confirmed: bool,
    support_dir: Path = DEFAULT_SUPPORT_DIR,
    faq_file: Path = DEFAULT_FAQ_FILE,
) -> dict[str, Any]:
    from sqlalchemy import text

    url = assert_isolated_database(confirmed=confirmed)
    snapshot = read_source_snapshot(support_dir, faq_file)
    support = [normalize_support_ticket(item) for item in snapshot["support"]]
    faqs = [normalize_faq(item) for item in snapshot["faqs"]]
    batch_id = f"feature018-{snapshot['snapshot_sha256'][:24]}"
    engine = _engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO feature018_import_batch
                    (id, source_kind, source_snapshot_sha256, source_record_count, status)
                VALUES (:id, 'support_json', :checksum, :count, 'running')
                ON CONFLICT (source_kind, source_snapshot_sha256) DO NOTHING
            """), {"id": batch_id, "checksum": snapshot["snapshot_sha256"], "count": len(support) + len(faqs)})
            for item in support:
                connection.execute(text("""
                    INSERT INTO support_ticket
                        (id, owner_user_id, canonical_domain, question_summary, status,
                         priority, retention_expires_at, source_created_at, source_updated_at)
                    VALUES (:id, :owner_user_id, :canonical_domain, :question_summary, :status,
                            :priority, :retention_expires_at, :source_created_at, :source_updated_at)
                    ON CONFLICT (id) DO NOTHING
                """), item)
                connection.execute(text("""
                    INSERT INTO feature018_source_record
                        (id, import_batch_ref, source_kind, source_id, source_sha256, target_table, target_id)
                    VALUES (:ledger_id, :batch, 'support_ticket', :id, :source_sha256, 'support_ticket', :id)
                    ON CONFLICT (source_kind, source_id, source_sha256) DO NOTHING
                """), {**item, "ledger_id": f"support-{item['source_sha256'][:24]}", "batch": batch_id})
            for item in faqs:
                identity_id = f"faq-{item['id']}"
                revision_id = f"faq-revision-{item['source_sha256'][:24]}"
                connection.execute(text("""
                    INSERT INTO faq_identity (id, faq_key) VALUES (:identity_id, :id)
                    ON CONFLICT (faq_key) DO NOTHING
                """), {**item, "identity_id": identity_id})
                connection.execute(text("""
                    INSERT INTO faq_revision
                        (id, faq_ref, revision_number, question, answer, canonical_domain,
                         confirmed_procedure_id, evidence, public_state, source_sha256)
                    VALUES (:revision_id, :identity_id, 1, :question, :answer, :canonical_domain,
                            :confirmed_procedure_id, CAST(:evidence_json AS JSONB), :public_state, :source_sha256)
                    ON CONFLICT (faq_ref, source_sha256) DO NOTHING
                """), {**item, "identity_id": identity_id, "revision_id": revision_id, "evidence_json": _canonical_json(item["evidence"])})
                connection.execute(text("""
                    INSERT INTO feature018_source_record
                        (id, import_batch_ref, source_kind, source_id, source_sha256, target_table, target_id)
                    VALUES (:ledger_id, :batch, 'faq', :id, :source_sha256, 'faq_revision', :revision_id)
                    ON CONFLICT (source_kind, source_id, source_sha256) DO NOTHING
                """), {**item, "revision_id": revision_id, "ledger_id": f"faq-{item['source_sha256'][:24]}", "batch": batch_id})
            connection.execute(text("""
                UPDATE feature018_import_batch
                SET imported_record_count = :count,
                    reconciliation = CAST(:reconciliation AS JSONB),
                    status = 'reconciled', completed_at = CURRENT_TIMESTAMP
                WHERE id = :id
            """), {
                "id": batch_id,
                "count": len(support) + len(faqs),
                "reconciliation": _canonical_json({"support": len(support), "faq": len(faqs), "active_pointer_changes": 0}),
            })
    finally:
        engine.dispose()
    return {
        "status": "reconciled",
        "batch_id": batch_id,
        "support": len(support),
        "faq": len(faqs),
        "active_pointer_changes": 0,
        "json_mode": "read_only",
    }


def verify(*, confirmed: bool) -> dict[str, Any]:
    from sqlalchemy import inspect, text

    url = assert_isolated_database(confirmed=confirmed)
    engine = _engine(url)
    try:
        existing = set(inspect(engine).get_table_names())
        missing = [table for table in TABLES if table not in existing]
        if missing:
            raise RuntimeError(f"feature018_schema_missing:{','.join(missing)}")
        with engine.connect() as connection:
            counts = {
                table: int(connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
                for table in TABLES
            }
        return {
            "status": "verified",
            "counts": counts,
            "active_pointer_changes": 0,
            "json_mode": "read_only",
        }
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "up", "import-shadow", "verify", "down"))
    parser.add_argument("--confirm-isolated", action="store_true")
    parser.add_argument("--allow-drop-fixtures", action="store_true")
    parser.add_argument("--support-dir", type=Path, default=DEFAULT_SUPPORT_DIR)
    parser.add_argument("--faq-file", type=Path, default=DEFAULT_FAQ_FILE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        result = plan_payload()
    elif args.command == "import-shadow":
        result = import_shadow(
            confirmed=args.confirm_isolated,
            support_dir=args.support_dir,
            faq_file=args.faq_file,
        )
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
            "active_pointer_changes": 0,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
