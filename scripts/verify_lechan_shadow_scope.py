"""Independently verify a DB-2 shadow manifest and its rollback invariants."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

try:
    from scripts.build_lechan_shadow_scope import (
        active_pointer,
        sha256_file,
        sha256_jsonl_id,
    )
    from scripts.feature005_db1_snapshot import database_url, table_id_snapshot
except ModuleNotFoundError:  # Direct execution
    from build_lechan_shadow_scope import (  # type: ignore[no-redef]
        active_pointer,
        sha256_file,
        sha256_jsonl_id,
    )
    from feature005_db1_snapshot import (  # type: ignore[no-redef]
        database_url,
        table_id_snapshot,
    )


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=Path(r"J:\legal-chatbot-data\chroma_store"),
    )
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent

    checks: dict[str, bool] = {}
    for section, key in (("documents", "document_id"), ("chunks", "chunk_id")):
        metadata = manifest[section]
        ledger = base / metadata["ledger"]
        count, id_hash = sha256_jsonl_id(ledger, key)
        checks[f"{section}.ledger_exists"] = ledger.is_file()
        checks[f"{section}.count"] = count == int(metadata["total"])
        checks[f"{section}.id_sha256"] = id_hash == metadata["id_sha256"]
        checks[f"{section}.ledger_sha256"] = (
            sha256_file(ledger) == metadata["ledger_sha256"]
        )

    engine = create_engine(database_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        live = {
            "documents": table_id_snapshot(connection, "legal_documents"),
            "articles": table_id_snapshot(connection, "legal_articles"),
            "chunks": table_id_snapshot(connection, "legal_article_chunks"),
        }
        sidecar_tables = connection.execute(
            text(
                """
                SELECT to_regclass('public.legal_serving_scope'),
                       to_regclass('public.legal_serving_chunk_scope'),
                       to_regclass('public.legal_serving_manifest')
                """
            )
        ).one()
        transaction.rollback()
    engine.dispose()
    checks["postgres.source_unchanged"] = (
        live == manifest["source_snapshot"]["after"]
    )
    checks["active_pointer.unchanged"] = (
        active_pointer(args.chroma_path)
        == manifest["active_collection_pointer"]["after"]
    )

    env = dotenv_values(ROOT / ".env")
    checks["feature_flag.false"] = (
        str(env.get("LEGAL_SECTION_GROUNDING_ENABLED") or "").casefold()
        == "false"
    )
    validation = manifest["validation"]
    checks["manifest.status_verified"] = manifest["status"] == "verified"
    checks["classification.no_unknown"] = (
        validation["unknown_document_tiers"] == 0
        and validation["unknown_chunk_tiers"] == 0
    )
    checks["hard_gate.no_included_violations"] = (
        validation["primary_support_hard_gate_violations"] == 0
        and validation["included_chunk_gate_violations"] == 0
    )
    checks["relationships.no_generic_basis_expansion"] = (
        validation["generic_legal_basis_relations_expanded"] == 0
    )
    checks["schema.sidecar_not_created"] = (
        validation["sidecar_schema_created"] is False
        and all(value is None for value in sidecar_tables)
    )
    payload = {
        "schema_version": "feature005-db2-shadow-verification-v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "status": "verified" if all(checks.values()) else "verification_failed",
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
