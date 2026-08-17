"""Independently verify DB-3 technical results and legal-review gating."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

try:
    from scripts.build_lechan_shadow_scope import (
        active_pointer,
        database_url,
        law_number_key,
        table_id_snapshot,
    )
    from scripts.compare_lechan_shadow_coverage import corpus_law_number_matches
except ModuleNotFoundError:  # Direct execution
    from build_lechan_shadow_scope import (  # type: ignore[no-redef]
        active_pointer,
        database_url,
        law_number_key,
        table_id_snapshot,
    )
    from compare_lechan_shadow_coverage import (  # type: ignore[no-redef]
        corpus_law_number_matches,
    )


ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _legal_review_receipt_state(receipt: dict, packet_path: Path) -> bool:
    """Validate a human approval without rewriting the original packet."""

    if str(receipt.get("status") or "").casefold() != "approved":
        return False
    if not str(receipt.get("reviewer_name") or "").strip():
        return False
    if not str(receipt.get("reviewed_at") or "").strip():
        return False
    expected_hash = str(receipt.get("packet_sha256") or "").casefold()
    return bool(expected_hash) and expected_hash == _sha256_file(packet_path)


def _jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


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
    packet_path = base / str(
        manifest.get("legal_review", {}).get("packet")
        or "legal-review-packet.json"
    )
    receipt_path = base / "legal-review-decision.json"
    receipt: dict = {}
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_approved = (
        receipt_path.is_file()
        and packet_path.is_file()
        and _legal_review_receipt_state(receipt, packet_path)
    )
    checks: dict[str, bool] = {}

    ledger_specs = {
        "sources": manifest["source_coverage"]["ledger"],
        "procedures": manifest["priority_procedure_coverage"]["ledger"],
        "forms": manifest["approved_form_catalog"]["ledger"],
    }
    ledgers: dict[str, list[dict]] = {}
    for name, metadata in ledger_specs.items():
        path = base / metadata["file"]
        rows = _jsonl(path)
        ledgers[name] = rows
        checks[f"{name}.exists"] = path.is_file()
        checks[f"{name}.count"] = len(rows) == int(metadata["count"])
        checks[f"{name}.sha256"] = _sha256_file(path) == metadata["sha256"]

    engine = create_engine(database_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        live_snapshot = {
            "documents": table_id_snapshot(connection, "legal_documents"),
            "articles": table_id_snapshot(connection, "legal_articles"),
            "chunks": table_id_snapshot(connection, "legal_article_chunks"),
        }
        documents = {
            int(row["id"]): {"law_number": row["law_number"]}
            for row in connection.execute(
                text("SELECT id, law_number FROM legal_documents ORDER BY id")
            ).mappings()
        }
        provision_gap_checks: list[bool] = []
        for row in ledgers["sources"]:
            if (
                row.get("outcome") == "VERIFIED_DATA_GAP"
                and row.get("kind") == "provision"
                and row.get("document_id") is not None
            ):
                count = connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM legal_articles
                        WHERE document_id = :document_id
                          AND regexp_replace(
                                lower(coalesce(article_number, '')),
                                '^điều\\s+', ''
                              ) = :article_number
                        """
                    ),
                    {
                        "document_id": int(row["document_id"]),
                        "article_number": str(row["provision"]).casefold(),
                    },
                ).scalar_one()
                provision_gap_checks.append(int(count) == 0)
        transaction.rollback()
    engine.dispose()

    document_gap_checks = [
        corpus_law_number_matches(row.get("expected_law_number"), documents) == 0
        and int(row.get("corpus_match_count", -1)) == 0
        for row in ledgers["sources"]
        if row.get("outcome") == "VERIFIED_DATA_GAP"
        and row.get("kind") == "document"
    ]
    checks["data_gaps.document_absence_reproduced"] = all(document_gap_checks)
    checks["data_gaps.provision_absence_reproduced"] = all(provision_gap_checks)
    checks["postgres.source_unchanged"] = (
        live_snapshot == manifest["source_snapshot"]["after"]
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
    checks["technical.available_sources_correct"] = (
        manifest["source_coverage"]["available_source_rule_or_tier_failures"] == 0
    )
    checks["technical.all_202_procedures_classified"] = (
        manifest["priority_procedure_coverage"]["total"] == 202
    )
    checks["technical.approved_catalog_audited"] = (
        manifest["approved_form_catalog"]["approved_records"]
        == ledger_specs["forms"]["count"]
    )
    checks["technical.no_frequency_exclusion"] = (
        manifest["technical_validation"]["frequency_based_exclusion_used"] is False
    )
    legacy_pending = (
        manifest["status"] == "awaiting_legal_review"
        and manifest["legal_review"]["approved"] is False
        and manifest["legal_review"]["required_before_pass"] is True
    )
    checks["legal_review.receipt_valid"] = (
        receipt_approved if receipt_path.is_file() else True
    )
    checks["legal_review.state_recorded"] = legacy_pending or receipt_approved
    technical_verified = all(checks.values())
    legal_review_approved = bool(
        receipt_approved or manifest.get("legal_review", {}).get("approved") is True
    )
    payload = {
        "schema_version": "feature005-db3-verification-v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": (
            "approved"
            if technical_verified and legal_review_approved
            else "awaiting_legal_review"
            if technical_verified
            else "verification_failed"
        ),
        "technical_verified": technical_verified,
        "legal_review_approved": legal_review_approved,
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if technical_verified else 2


if __name__ == "__main__":
    raise SystemExit(main())
