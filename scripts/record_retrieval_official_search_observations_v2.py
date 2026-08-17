#!/usr/bin/env python3
"""Record primary-source discovery evidence for unresolved V2 references.

The input is a human-curated evidence packet produced from official-source
review.  This command only reconciles URLs and observations with the local
gap inventory.  It never changes legal metadata, Golden cases, collections or
release pointers, and it never treats a discovered URL as an approval.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.reconcile_retrieval_source_gaps_v2 import normalize_law

DEFAULT_GAPS = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-full-inventory-v2-fullchroma-r1.json"
DEFAULT_INPUT = ROOT / "reports" / "retrieval-release-v2" / "official-web-observation-input-v2.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "official-web-observations-v2.json"
ALLOWED_HOSTS = ("vbpl.vn", "baohiemxahoi.gov.vn")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _official_url(value: Any) -> bool:
    parsed = urlparse(str(value or ""))
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS
    )


def build(*, gaps_path: Path, input_path: Path, output: Path) -> dict[str, Any]:
    gaps = _load(gaps_path)
    packet = _load(input_path)
    gap_rows = list(gaps.get("references") or [])
    expected_laws = {
        normalize_law(row.get("law_number")) for row in gap_rows
    }
    observations = list(packet.get("observations") or [])
    by_law: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for row in observations:
        law_key = normalize_law(row.get("law_number"))
        if not law_key:
            errors.append("observation_law_number_required")
            continue
        if law_key in by_law:
            errors.append(f"duplicate_observation:{row.get('law_number')}")
        by_law[law_key] = row
        if not _official_url(row.get("official_source_url")):
            errors.append(f"official_url_not_allowlisted:{row.get('law_number')}")
        if row.get("evidence_kind") not in {
            "primary_document_page",
            "primary_pdf",
            "official_reference_only",
        }:
            errors.append(f"invalid_evidence_kind:{row.get('law_number')}")
        if not str(row.get("observation_note") or "").strip():
            errors.append(f"observation_note_required:{row.get('law_number')}")
    missing = sorted(expected_laws - set(by_law))
    extra = sorted(set(by_law) - expected_laws)
    if missing:
        errors.append(f"missing_law_observations:{len(missing)}")
    if extra:
        errors.append(f"unexpected_law_observations:{len(extra)}")
    local_by_law: dict[str, dict[str, Any]] = {}
    for row in gap_rows:
        key = normalize_law(row.get("law_number"))
        current = local_by_law.setdefault(
            key,
            {
                "law_number": row.get("law_number"),
                "classification": row.get("classification"),
                "inventory_match_count": int(row.get("inventory_match_count") or 0),
                "reference_count": 0,
            },
        )
        current["reference_count"] += 1
    records = []
    for key in sorted(expected_laws):
        observation = dict(by_law.get(key) or {})
        local = dict(local_by_law.get(key) or {})
        records.append({
            **local,
            "law_number": observation.get("law_number") or local.get("law_number"),
            "official_source_url": observation.get("official_source_url"),
            "evidence_kind": observation.get("evidence_kind"),
            "observed_title": observation.get("observed_title"),
            "effectivity_observation": observation.get("effectivity_observation"),
            "observation_note": observation.get("observation_note"),
            "legal_review_status": "required",
            "approved_for_import": False,
        })
    report = {
        "schema_version": "legal-retrieval-official-web-observations-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gap_manifest_file_sha256": file_sha256(gaps_path),
        "observation_input_file_sha256": file_sha256(input_path),
        "law_count": len(expected_laws),
        "reference_count": len(gap_rows),
        "observed_law_count": len(by_law),
        "missing_law_count": len(missing),
        "errors": sorted(set(errors)),
        "legal_review_required": True,
        "approved_for_import": False,
        "records": records,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["status"] = "EVIDENCE_ONLY" if not errors else "BLOCKED"
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build(
        gaps_path=args.gaps.resolve(),
        input_path=args.input.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps({
        "status": report["status"],
        "law_count": report["law_count"],
        "reference_count": report["reference_count"],
        "missing_law_count": report["missing_law_count"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if report["status"] == "EVIDENCE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
