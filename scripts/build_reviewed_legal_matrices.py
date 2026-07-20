# -*- coding: utf-8 -*-
"""Build authority and procedure matrices from approved expert records only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebook_data" / "legal-golden-expert-review.json"
OUTPUT_DIR = ROOT / "notebook_data" / "legal_quality"


def build(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    records = [item for item in payload.get("records") or [] if item.get("expert_review_status") == "approved"]
    generated_at = datetime.now(timezone.utc).isoformat()
    authority_rows: list[dict[str, Any]] = []
    procedure_rows: list[dict[str, Any]] = []
    for item in records:
        common = {
            "case_id": item.get("case_id"),
            "domain": item.get("domain"),
            "legal_as_of": item.get("legal_as_of"),
            "expert_name": item.get("expert_name"),
            "review_version": item.get("review_version"),
        }
        authority_rows.append({
            **common,
            "authority": item.get("expected_authority"),
            "forbidden_authority": item.get("forbidden_authority") or [],
            "expected_documents": item.get("expected_documents") or [],
            "expected_articles": item.get("expected_articles") or [],
        })
        procedure_rows.append({
            **common,
            "question_type": item.get("question_type"),
            "mandatory_documents": item.get("mandatory_documents") or [],
            "conditional_documents": item.get("conditional_documents") or [],
            "processing_time": item.get("processing_time"),
            "fee": item.get("fee"),
            "penalty_range": item.get("penalty_range"),
            "remedial_measures": item.get("remedial_measures"),
            "official_form_ids": item.get("official_form_ids") or [],
        })
    meta = {"generated_at": generated_at, "approved_source_records": len(records), "source": str(SOURCE)}
    return ({**meta, "rows": authority_rows}, {**meta, "rows": procedure_rows})


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
    authority, procedures = build(payload)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "authority_matrix.json").write_text(json.dumps(authority, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "procedure_matrix.json").write_text(json.dumps(procedures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Built {len(authority['rows'])} approved authority rows and {len(procedures['rows'])} procedure rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
