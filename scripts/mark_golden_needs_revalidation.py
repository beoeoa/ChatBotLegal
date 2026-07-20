# -*- coding: utf-8 -*-
"""Mark approved expert cases stale when a referenced law changes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebook_data" / "legal-golden-expert-review.json"


def mark(payload: dict[str, Any], changed_laws: set[str]) -> int:
    normalized = {value.strip().casefold() for value in changed_laws if value.strip()}
    changed = 0
    for item in payload.get("records") or []:
        if item.get("expert_review_status") != "approved":
            continue
        documents = item.get("expected_documents") or []
        blob = " ".join(str(value) for value in documents).casefold()
        if any(law in blob for law in normalized):
            item["expert_review_status"] = "needs_revalidation"
            item["revalidation_reason"] = "referenced_document_changed"
            item["changed_law_numbers"] = sorted(changed_laws)
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Invalidate approved golden cases after a legal document changes")
    parser.add_argument("law_number", nargs="+")
    parser.add_argument("--input", type=Path, default=SOURCE)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    count = mark(payload, set(args.law_number))
    temporary = args.input.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.input)
    print(f"Marked {count} records needs_revalidation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
