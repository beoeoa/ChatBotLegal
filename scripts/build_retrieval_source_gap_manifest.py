"""Build an immutable, read-only source-gap manifest for retrieval datasets."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import build_source_gap_manifest
from scripts.backup_legal_retrieval import _database_url


DEFAULT_CANDIDATE = ROOT / "reports" / "m1-freeze" / "candidate_manifest.json"
DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-quality-v2" / "source_gap_manifest.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_root_must_be_object:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(
    *,
    candidate_path: Path,
    golden_path: Path,
    hard_path: Path,
) -> dict[str, Any]:
    candidate = _load(candidate_path)
    datasets = {
        "golden-1000": _load(golden_path),
        "hard-negative-100": _load(hard_path),
    }
    manifest = build_source_gap_manifest(candidate, datasets)
    manifest["inputs"] = {
        "candidate_manifest": {
            "path": str(candidate_path.resolve()),
            "sha256": _sha256(candidate_path),
        },
        "golden-1000": {
            "path": str(golden_path.resolve()),
            "sha256": _sha256(golden_path),
        },
        "hard-negative-100": {
            "path": str(hard_path.resolve()),
            "sha256": _sha256(hard_path),
        },
    }
    # Rebind the checksum after adding input provenance.
    unhashed = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def enrich_from_read_only_inventory(manifest: dict[str, Any]) -> dict[str, Any]:
    """Attach existing PostgreSQL metadata without making legal decisions."""

    from sqlalchemy import bindparam, create_engine, text

    law_numbers = sorted(
        {str(row.get("law_number") or "") for row in manifest["missing_references"]}
    )
    if not law_numbers:
        manifest["inventory_enrichment"] = {
            "status": "not_needed",
            "read_only": True,
            "matched_document_count": 0,
        }
        return manifest
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            document_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT id, title, law_number, effective_date, expired_date, "
                        "status, source_url, issuing_agency, scope, document_type "
                        "FROM legal_documents WHERE law_number IN :law_numbers ORDER BY id"
                    ).bindparams(bindparam("law_numbers", expanding=True)),
                    {"law_numbers": law_numbers},
                ).mappings()
            ]
            document_ids = [int(row["id"]) for row in document_rows]
            relationships: list[dict[str, Any]] = []
            if document_ids:
                relationships = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            "SELECT r.source_document_id, r.target_document_id, "
                            "r.relationship_type, s.law_number source_law_number, "
                            "t.law_number target_law_number "
                            "FROM legal_document_relationships r "
                            "JOIN legal_documents s ON s.id=r.source_document_id "
                            "JOIN legal_documents t ON t.id=r.target_document_id "
                            "WHERE r.source_document_id IN :document_ids "
                            "OR r.target_document_id IN :document_ids"
                        )
                        .bindparams(bindparam("document_ids", expanding=True)),
                        {"document_ids": document_ids},
                    ).mappings()
                ]
    finally:
        engine.dispose()

    by_law: dict[str, list[dict[str, Any]]] = {}
    for row in document_rows:
        by_law.setdefault(str(row["law_number"]), []).append(row)
    for gap in manifest["missing_references"]:
        matches = by_law.get(str(gap["law_number"]), [])
        gap["inventory_document_ids"] = [int(row["id"]) for row in matches]
        gap["inventory_match_count"] = len(matches)
        gap["inventory_matches"] = [
            {
                key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in row.items()
            }
            for row in matches
        ]
        related = [
            row
            for row in relationships
            if str(row.get("source_law_number")) == str(gap["law_number"])
            or str(row.get("target_law_number")) == str(gap["law_number"])
        ]
        gap["relationship"] = related or None
        if not matches:
            gap["required_action"] = "official_source_discovery"
            continue
        preferred = matches[-1]
        gap["official_source_url"] = preferred.get("source_url")
        gap["status"] = preferred.get("status")
        gap["effective_from"] = (
            preferred["effective_date"].isoformat()
            if preferred.get("effective_date")
            else None
        )
        gap["effective_to"] = (
            preferred["expired_date"].isoformat()
            if preferred.get("expired_date")
            else None
        )
        gap["authority"] = preferred.get("issuing_agency")
        gap["jurisdiction"] = preferred.get("scope")
        legal_as_of = date.fromisoformat(str(gap["legal_as_of"]))
        effective_from = preferred.get("effective_date")
        effective_to = preferred.get("expired_date")
        valid_at_case_date = bool(
            (effective_from is None or effective_from <= legal_as_of)
            and (effective_to is None or effective_to > legal_as_of)
            and str(preferred.get("status") or "").casefold()
            not in {"expired", "repealed", "replaced", "quarantined"}
        )
        gap["inventory_valid_at_legal_as_of"] = valid_at_case_date
        gap["required_action"] = (
            "candidate_vnext_legal_review"
            if valid_at_case_date
            else "golden_source_validity_review"
        )
    manifest["inventory_enrichment"] = {
        "status": "completed",
        "read_only": True,
        "matched_document_count": len(document_rows),
        "matched_law_count": len(by_law),
        "relationship_count": len(relationships),
        "legal_decision_performed": False,
    }
    unhashed = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    canonical = json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-database-enrichment",
        action="store_true",
        help="Skip the read-only PostgreSQL inventory lookup.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    payload = build(
        candidate_path=args.candidate_manifest.resolve(),
        golden_path=args.golden.resolve(),
        hard_path=args.hard_negative.resolve(),
    )
    if not args.no_database_enrichment:
        payload = enrich_from_read_only_inventory(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(output)}  {output.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "output": str(output),
                "missing_reference_count": payload["missing_reference_count"],
                "missing_law_count": payload["missing_law_count"],
                "manifest_sha256": payload["manifest_sha256"],
                "candidate_collection_mutated": False,
                "active_pointer_changed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
