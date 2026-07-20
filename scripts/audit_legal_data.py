"""Create a deterministic audit report for the legal and form corpora."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def build_report() -> dict:
    forms = ROOT / "notebook_data" / "forms"
    inventory = load(forms / "forms_inventory_report.json", {})
    index = load(forms / "haiphong_official_form_index.json", {})
    candidates = load(forms / "official_forms_candidates_classified.json", {})
    records = list(index.get("forms") or [])
    missing_file = []
    missing_url = []
    local_only = []
    for record in records:
        relative = str(record.get("source_package_path") or record.get("local_path") or record.get("priority_path") or "").replace("\\", "/")
        if not relative or not (ROOT / relative).is_file():
            missing_file.append(record)
        source_url = (
            record.get("source_url")
            or record.get("full_url")
            or record.get("source_page_url")
            or record.get("source_download_url")
        )
        if not str(source_url or "").startswith(("http://", "https://")):
            missing_url.append(record)
            if relative and (ROOT / relative).is_file():
                local_only.append(record)
    status_counts = {}
    for record in candidates.get("records") or []:
        status = str(record.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forms": {
            "inventory": inventory,
            "official_index_total": len(records),
            "official_index_missing_file": len(missing_file),
            "official_index_missing_url": len(missing_url),
            "official_index_local_file_without_external_url": len(local_only),
            "candidate_total": len(candidates.get("records") or []),
            "candidate_status_counts": status_counts,
            "missing_file_examples": [
                {"id": item.get("id"), "title": item.get("form_title") or item.get("name")}
                for item in missing_file[:50]
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="notebook_data/forms/legal_data_audit_report.json")
    args = parser.parse_args()
    report = build_report()
    destination = ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
