#!/usr/bin/env python3
"""Merge source-gap, inventory and official-source observations for legal QA.

This produces a review queue, not an import decision.  It deliberately keeps
the official URL observation separate from legal approval and refuses to mark a
source importable when its official content checksum/effectivity has not been
attested.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256

DEFAULT_GAP = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-legacy.json"
DEFAULT_RECONCILIATION = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-full-inventory-v4.json"
DEFAULT_OBSERVATIONS = ROOT / "reports" / "retrieval-release-v2" / "official-source-observations-v2.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-gap-review-manifest-v4.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("case_id") or ""),
        str(row.get("law_number") or ""),
        str(row.get("article") or ""),
    )


def build(*, gap_path: Path, reconciliation_path: Path, observations_path: Path, output: Path) -> dict[str, Any]:
    gap = _load(gap_path)
    reconciliation = _load(reconciliation_path)
    observations = _load(observations_path)
    gap_rows = list(gap.get("missing_references") or [])
    recon_rows = {_key(row): row for row in reconciliation.get("references") or []}
    observation_rows = {_key(row): row for row in observations.get("records") or []}
    if len(gap_rows) != 46:
        raise RuntimeError(f"source_gap_reference_count:{len(gap_rows)}!=46")
    records: list[dict[str, Any]] = []
    for source in gap_rows:
        key = _key(source)
        recon = recon_rows.get(key)
        observed = observation_rows.get(key)
        if recon is None or observed is None:
            raise RuntimeError(f"source_gap_join_missing:{key}")
        transport = dict(observed.get("transport_observation") or {})
        classification = str(recon.get("classification") or "")
        if classification == "available_in_full_database":
            action = "legal_review_effectivity_and_article_resolution"
        elif classification == "legacy_or_vector_only":
            action = "legal_review_legacy_vector_provenance_then_import_or_rebuild"
        elif classification == "metadata_or_article_gap":
            action = "legal_review_article_content_and_provenance"
        else:
            action = "legal_review_official_source_import_or_correct_case"
        records.append({
            "case_id": source.get("case_id"),
            "dataset": source.get("dataset"),
            "domain": source.get("domain"),
            "legal_as_of": source.get("legal_as_of"),
            "law_number": source.get("law_number"),
            "article": source.get("article"),
            "classification": classification,
            "database_evidence": recon.get("database_evidence") or {},
            "legacy_manifest_evidence": recon.get("legacy_manifest_evidence") or {},
            "chroma_metadata_evidence": recon.get("chroma_metadata_evidence") or {},
            "official_source_url": observed.get("official_source_url"),
            "official_source_checksum": observed.get("official_source_checksum"),
            "transport_observation": {
                "fetched": bool(transport.get("fetched")),
                "status_code": transport.get("status_code"),
                "content_type": transport.get("content_type"),
                "bytes_observed": transport.get("bytes_observed"),
                "sha256": transport.get("sha256"),
                "final_url": transport.get("final_url"),
                "truncated": bool(transport.get("truncated")),
            },
            "source_observation_status": (
                "observed_url_and_transport_checksum"
                if observed.get("official_source_url") and transport.get("sha256") and not transport.get("truncated")
                else "observed_url_only"
                if observed.get("official_source_url") and not observed.get("official_source_checksum")
                else "observed_url_and_checksum"
                if observed.get("official_source_url") and observed.get("official_source_checksum")
                else "missing_official_source_observation"
            ),
            "validity_interval": {
                "from": source.get("effective_from"),
                "to": source.get("effective_to"),
            },
            "jurisdiction": source.get("jurisdiction"),
            "authority": source.get("authority"),
            "relationship": source.get("relationship"),
            "required_action": action,
            "legal_review_required": True,
            "approved_for_import": False,
            "metadata_mutated": False,
            "vectors_mutated": False,
        })
    counts: dict[str, int] = {}
    for row in records:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    payload: dict[str, Any] = {
        "schema_version": "legal-retrieval-source-gap-review-v2",
        "generated_at": str(
            reconciliation.get("generated_at")
            or observations.get("generated_at")
            or datetime.now(timezone.utc).isoformat()
        ),
        "source_inventory_scope": 12_236,
        "reference_count": len(records),
        "law_count": len({str(row.get("law_number") or "") for row in records}),
        "classification_counts": counts,
        "input_checksums": {
            "gap_manifest": file_sha256(gap_path),
            "reconciliation": file_sha256(reconciliation_path),
            "official_observations": file_sha256(observations_path),
        },
        "legal_review_required": True,
        "approved_for_import": False,
        "records": records,
        "mutation": {
            "database_mutated": False,
            "metadata_mutated": False,
            "vectors_mutated": False,
            "active_pointer_changed": False,
        },
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    if output.exists():
        existing = _load(output)
        existing_comparable = dict(existing)
        payload_comparable = dict(payload)
        existing_comparable.pop("generated_at", None)
        existing_comparable.pop("manifest_sha256", None)
        payload_comparable.pop("generated_at", None)
        payload_comparable.pop("manifest_sha256", None)
        if existing_comparable != payload_comparable:
            raise RuntimeError("source_gap_review_manifest_version_already_exists")
        payload = existing
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_sha = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{output_sha}  {output.name}\n", encoding="ascii")
    return {
        "schema_version": "legal-retrieval-source-gap-review-report-v2",
        "status": "LEGAL_REVIEW_REQUIRED",
        "output": str(output.resolve()),
        "output_sha256": output_sha,
        "manifest_sha256": payload["manifest_sha256"],
        "reference_count": len(records),
        "classification_counts": counts,
        "approved_for_import": False,
        "mutation": payload["mutation"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gap", type=Path, default=DEFAULT_GAP)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build(
        gap_path=args.gap.resolve(),
        reconciliation_path=args.reconciliation.resolve(),
        observations_path=args.observations.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
