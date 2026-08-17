"""Guarded Feature 017 deployment into the approved local live PostgreSQL database.

The command is intentionally pinned to one release, one approved Golden set,
one database name and a verified pre-mutation backup. It never modifies the
legal corpus or vector indexes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import WorkflowEvent, canonical_sha256, utc_now
from api.form_governance_repository import PostgresFormGovernanceRepository
from scripts.approve_feature017_golden_v3 import approved_checksum
from scripts.evaluate_feature017_golden_v3 import evaluate
from scripts.manage_feature017_form_schema import TABLES, migration_sql


EXPECTED_DATABASE = "legal_chatbot"
EXPECTED_RELEASE_ID = "forms-2026-08-11-feature017-attested"
EXPECTED_RELEASE_HASH = "a54a014dcc6eaec4834fa8fc0234919f5a8e3fcbdf98fccc66d8b30888473f96"
EXPECTED_GOLDEN_CHECKSUM = "609a1fff8839379080307778e65ffe7990d0f5ce543e5f2a3c9e867473a98263"
DEFAULT_CANDIDATE = ROOT / "outputs/feature017-full-release-candidate-20260812-packaged/release-candidate.json"
DEFAULT_APPROVAL = ROOT / "outputs/feature017-golden-v3-user-journey-1000/approved-manifest.json"
DEFAULT_GOLDEN = ROOT / "outputs/feature017-golden-v3-user-journey-1000/golden-v3-1000-approved.json"
DEFAULT_REPORT = ROOT / "reports/feature017/live-activation-20260812.json"
OFFICERS = (
    "officer_hotich",
    "officer_daidai",
    "officer_ansinh",
    "officer_cutru",
    "officer_khieunai",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dotenv_value(key: str) -> str:
    path = ROOT / ".env"
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        text = raw.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def release_database_url() -> str:
    value = str(os.getenv("LEGAL_RELEASE_DATABASE_URL") or _dotenv_value("LEGAL_RELEASE_DATABASE_URL")).strip()
    if not value:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL_REQUIRED")
    return value.replace("@host.docker.internal:", "@127.0.0.1:")


def _database_name(url: str) -> str:
    parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    return parsed.path.rsplit("/", 1)[-1]


def _redacted_url(url: str) -> str:
    parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(("postgresql", host, parsed.path, "", ""))


def assert_live_target(url: str, *, confirmed_database: str) -> str:
    name = _database_name(url)
    if name != EXPECTED_DATABASE:
        raise RuntimeError("LIVE_DATABASE_TARGET_INVALID")
    if confirmed_database != name:
        raise RuntimeError("LIVE_DATABASE_CONFIRMATION_MISMATCH")
    return url


def verify_backup(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise RuntimeError("LIVE_BACKUP_MANIFEST_MISSING")
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != "feature017-live-backup-v1":
        raise RuntimeError("LIVE_BACKUP_MANIFEST_INVALID")
    dump = manifest_path.parent / str(manifest.get("postgres_dump") or "")
    if not dump.is_file():
        raise RuntimeError("LIVE_BACKUP_DUMP_MISSING")
    if _sha256(dump) != manifest.get("postgres_sha256"):
        raise RuntimeError("LIVE_BACKUP_CHECKSUM_MISMATCH")
    if int(manifest.get("feature017_tables_before", -1)) != 0:
        raise RuntimeError("LIVE_BACKUP_BASELINE_INVALID")
    return manifest


def validate_approved_artifacts(
    candidate: Mapping[str, Any], approval: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = dict(candidate.get("manifest") or {})
    actual_release_hash = canonical_sha256(manifest)
    if actual_release_hash != EXPECTED_RELEASE_HASH or candidate.get("manifest_sha256") != actual_release_hash:
        raise RuntimeError("LIVE_RELEASE_HASH_MISMATCH")
    if manifest.get("release_id") != EXPECTED_RELEASE_ID:
        raise RuntimeError("LIVE_RELEASE_ID_MISMATCH")
    if not (candidate.get("gate_report") or {}).get("passed"):
        raise RuntimeError("LIVE_RELEASE_GATE_NOT_PASSED")
    if candidate.get("activation_allowed") is not False:
        raise RuntimeError("LIVE_CANDIDATE_REHEARSAL_FLAG_INVALID")
    if (
        approval.get("status") != "approved"
        or int(approval.get("case_count") or 0) != 1000
        or approval.get("approved_checksum") != EXPECTED_GOLDEN_CHECKSUM
        or approval.get("release_id") != EXPECTED_RELEASE_ID
        or approval.get("release_manifest_sha256") != EXPECTED_RELEASE_HASH
    ):
        raise RuntimeError("LIVE_GOLDEN_APPROVAL_MISMATCH")
    return {"release_id": EXPECTED_RELEASE_ID, "manifest_sha256": actual_release_hash}


def schema_state(existing: set[str]) -> str:
    feature_tables = set(TABLES)
    present = feature_tables.intersection(existing)
    if not present:
        return "absent"
    if present != feature_tables:
        missing = ",".join(sorted(feature_tables - present))
        raise RuntimeError(f"FEATURE017_PARTIAL_SCHEMA:{missing}")
    return "complete"


def _engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, future=True, pool_pre_ping=True)


def inspect_schema(url: str) -> str:
    from sqlalchemy import inspect

    engine = _engine(url)
    try:
        return schema_state(set(inspect(engine).get_table_names()))
    finally:
        engine.dispose()


def apply_live_migration(url: str) -> str:
    state = inspect_schema(url)
    if state == "complete":
        return "already_applied"
    engine = _engine(url)
    try:
        raw = engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute(migration_sql("up"))
            raw.commit()
        finally:
            raw.close()
    finally:
        engine.dispose()
    if inspect_schema(url) != "complete":
        raise RuntimeError("FEATURE017_SCHEMA_VERIFY_FAILED")
    return "applied"


def import_validated_release(
    repo: PostgresFormGovernanceRepository, candidate: Mapping[str, Any], *, actor: str
) -> dict[str, Any]:
    manifest = dict(candidate["manifest"])
    current = repo.get_release(EXPECTED_RELEASE_ID)
    if current:
        if current.get("manifest_sha256") != EXPECTED_RELEASE_HASH:
            raise RuntimeError("LIVE_RELEASE_CONFLICT")
        return current
    release = {
        "release_id": EXPECTED_RELEASE_ID,
        "version": int(manifest["version"]),
        "legal_as_of": manifest["legal_as_of"],
        "status": "candidate",
        "source_snapshot_sha256": manifest["source_snapshot_sha256"],
        "manifest": manifest,
        "manifest_sha256": EXPECTED_RELEASE_HASH,
        "previous_release_id": manifest.get("previous_release_id"),
        "created_by": actor,
        "gate_report": dict(candidate.get("gate_report") or {}),
    }
    repo.save_release(release)
    release["status"] = "validated"
    repo.update_release(release)
    saved = repo.get_release(EXPECTED_RELEASE_ID)
    if not saved or saved.get("status") != "validated":
        raise RuntimeError("LIVE_RELEASE_IMPORT_VERIFY_FAILED")
    return saved


def verify_shadow(repo: PostgresFormGovernanceRepository, golden: Mapping[str, Any]) -> dict[str, Any]:
    release = repo.get_release(EXPECTED_RELEASE_ID)
    if not release or release.get("status") not in {"validated", "active"}:
        raise RuntimeError("LIVE_SHADOW_RELEASE_UNAVAILABLE")
    if approved_checksum(golden) != EXPECTED_GOLDEN_CHECKSUM:
        raise RuntimeError("LIVE_GOLDEN_DATASET_TAMPERED")
    report = evaluate(dict(release["manifest"]), golden, include_api=True)
    if not report.get("passed"):
        raise RuntimeError("LIVE_SHADOW_GOLDEN_FAILED")
    return report


def _ensure_release_notifications(repo: PostgresFormGovernanceRepository, *, actor: str) -> int:
    from sqlalchemy import text

    event_id = "event-feature017-live-activation-20260812"
    if not any(item.event_id == event_id for item in repo.events(EXPECTED_RELEASE_ID)):
        repo.append_event(
            WorkflowEvent(
                event_id=event_id,
                object_type="form_release",
                object_id=EXPECTED_RELEASE_ID,
                actor_id=actor,
                actor_role="admin",
                action="activate",
                from_status=None,
                to_status=None,
                reason_code="USER_APPROVED_LIVE_ACTIVATION",
                detail_hash=EXPECTED_RELEASE_HASH,
                occurred_at=utc_now(),
            )
        )
    with repo.engine.connect() as connection:
        existing = set(
            connection.execute(
                text("SELECT recipient_id FROM form_notification_outbox WHERE workflow_event_ref=:event"),
                {"event": event_id},
            ).scalars()
        )
    for officer in OFFICERS:
        if officer in existing:
            continue
        repo.enqueue_notification(
            {
                "workflow_event_id": event_id,
                "recipient_id": officer,
                "notification_type": "form_release_activated",
                "release_id": EXPECTED_RELEASE_ID,
                "status": "released",
                "message": "Danh mục thủ tục và biểu mẫu mới đã được phát hành.",
            }
        )
    return len(OFFICERS)


def activate_release(repo: PostgresFormGovernanceRepository, *, actor: str) -> dict[str, Any]:
    active = repo.active_release()
    if active and active.get("release_id") != EXPECTED_RELEASE_ID:
        raise RuntimeError("LIVE_UNRELATED_ACTIVE_RELEASE")
    if active:
        result = {"release_id": EXPECTED_RELEASE_ID, "already_active": True}
    else:
        result = repo.set_active_release(EXPECTED_RELEASE_ID, actor_id=actor)
    _ensure_release_notifications(repo, actor=actor)
    return result


def verify_active(repo: PostgresFormGovernanceRepository) -> dict[str, Any]:
    from sqlalchemy import text

    active = repo.active_release()
    if not active or active.get("release_id") != EXPECTED_RELEASE_ID:
        raise RuntimeError("LIVE_ACTIVE_POINTER_INVALID")
    expected = {"legal_procedure": 191, "legal_form_asset": 91, "procedure_form_binding": 141, "procedure_question_alias": 382}
    with repo.engine.connect() as connection:
        counts = {
            table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table} WHERE release_ref=:release"), {"release": EXPECTED_RELEASE_ID}).scalar_one())
            for table in expected
        }
        outbox = int(connection.execute(text("SELECT COUNT(*) FROM form_notification_outbox WHERE workflow_event_ref=:event"), {"event": "event-feature017-live-activation-20260812"}).scalar_one())
        audit_rows = connection.execute(text("SELECT previous_hash,entry_hash FROM form_workflow_event ORDER BY event_sequence")).mappings().all()
    if counts != expected:
        raise RuntimeError(f"LIVE_CATALOG_COUNT_MISMATCH:{counts}")
    if outbox != len(OFFICERS):
        raise RuntimeError("LIVE_NOTIFICATION_OUTBOX_MISMATCH")
    if not audit_rows or any(not row["entry_hash"] for row in audit_rows):
        raise RuntimeError("LIVE_AUDIT_CHAIN_INVALID")
    return {"active_release_id": EXPECTED_RELEASE_ID, "counts": counts, "outbox": outbox, "audit_events": len(audit_rows)}


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "migrate", "import-shadow", "verify-shadow", "activate", "verify-active", "all"))
    parser.add_argument("--confirm-live-database", required=True)
    parser.add_argument("--confirm-user-approved-live-activation", action="store_true")
    parser.add_argument("--actor", required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--approval", type=Path, default=DEFAULT_APPROVAL)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_user_approved_live_activation:
        raise SystemExit("live deployment refused: explicit approval flag required")
    if args.actor != "admin":
        raise SystemExit("live deployment refused: actor must be admin")
    url = assert_live_target(release_database_url(), confirmed_database=args.confirm_live_database)
    backup = verify_backup(args.backup_manifest)
    candidate = _read_json(args.candidate)
    approval = _read_json(args.approval)
    artifacts = validate_approved_artifacts(candidate, approval)
    report: dict[str, Any] = {
        "schema_version": "feature017-live-activation-report-v1",
        "target": _redacted_url(url),
        "actor": args.actor,
        "backup": {"manifest": str(args.backup_manifest), "postgres_sha256": backup["postgres_sha256"]},
        "artifacts": artifacts,
        "command": args.command,
        "vector_index_mutated": False,
    }
    if args.command == "preflight":
        report["schema_state"] = inspect_schema(url)
    else:
        repo = PostgresFormGovernanceRepository(url)
        if args.command in {"migrate", "all"}:
            report["migration"] = apply_live_migration(url)
        if args.command in {"import-shadow", "all"}:
            report["release_status"] = import_validated_release(repo, candidate, actor=args.actor).get("status")
        if args.command in {"verify-shadow", "all"}:
            shadow = verify_shadow(repo, _read_json(args.golden))
            report["shadow_golden"] = {"passed": shadow["passed"], "direct": shadow["direct"], "api": shadow["api"]}
        if args.command in {"activate", "all"}:
            report["activation"] = activate_release(repo, actor=args.actor)
        if args.command in {"verify-active", "all"}:
            report["active_verification"] = verify_active(repo)
    report["completed_at"] = utc_now().isoformat()
    _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
