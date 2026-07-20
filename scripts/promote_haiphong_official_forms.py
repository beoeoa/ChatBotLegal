"""Load all indexed Hai Phong forms into a runtime catalog.

All source references remain visible to administrators. Only a canonical source
without effectivity warning flags and with a usable title is made available to
the chatbot as an official source package pending final effectivity review.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"
CATALOG_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_forms_catalog.json"


def year_score(record: dict[str, Any]) -> int:
    text = f"{record.get('source_package_title', '')} {record.get('source_page_url', '')}"
    years = [int(value) for value in re.findall(r"\b20\d{2}\b", text)]
    return max(years, default=0)


def canonical_rank(record: dict[str, Any]) -> tuple[int, int, int]:
    return (
        year_score(record),
        1 if record.get("source_page") else 0,
        1 if record.get("source_paragraph") else 0,
    )


def build_catalog() -> dict[str, Any]:
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8-sig"))
    rows = [dict(record) for record in payload.get("forms", [])]
    eligible_groups: dict[str, list[dict[str, Any]]] = {}

    for record in rows:
        if record.get("effectivity_flags"):
            record["catalog_status"] = "blocked_effectivity_flag"
        elif record.get("title_quality") != "usable":
            record["catalog_status"] = "review_required_title"
        else:
            record["catalog_status"] = "available_official_source"
            eligible_groups.setdefault(str(record["duplicate_group"]), []).append(record)

    canonical_ids: set[str] = set()
    for records in eligible_groups.values():
        canonical = max(records, key=canonical_rank)
        canonical_ids.add(str(canonical["id"]))

    for record in rows:
        record["is_canonical"] = str(record.get("id")) in canonical_ids
        if (
            record.get("catalog_status") == "available_official_source"
            and not record["is_canonical"]
        ):
            record["catalog_status"] = "duplicate_source"

    summary = {
        "total_source_references": len(rows),
        "available_canonical_forms": len(canonical_ids),
        "duplicate_sources": sum(
            1 for record in rows if record["catalog_status"] == "duplicate_source"
        ),
        "blocked_effectivity_flags": sum(
            1
            for record in rows
            if record["catalog_status"] == "blocked_effectivity_flag"
        ),
        "review_required_title": sum(
            1
            for record in rows
            if record["catalog_status"] == "review_required_title"
        ),
    }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "forms": rows,
    }
    CATALOG_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    result = build_catalog()
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"Runtime catalog: {CATALOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
