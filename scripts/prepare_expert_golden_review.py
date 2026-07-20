# -*- coding: utf-8 -*-
"""Create 334 expert-review records without fabricating expert approval."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "notebook_data" / "legal-golden-set.json"
OUTPUT = ROOT / "notebook_data" / "legal-golden-expert-review.json"

DOMAIN_MAP = {
    "ho_tich": "ho_tich_chung_thuc",
    "ho_tich_chung_thuc": "ho_tich_chung_thuc",
    "dat_dai": "dat_dai_xay_dung",
    "xay_dung": "dat_dai_xay_dung",
    "dat_dai_xay_dung": "dat_dai_xay_dung",
    "cu_tru": "cu_tru_an_ninh",
    "cu_tru_an_ninh": "cu_tru_an_ninh",
    "trat_tu_do_thi": "cu_tru_an_ninh",
    "khieu_nai": "khieu_nai_to_cao_xu_phat",
    "xu_phat": "khieu_nai_to_cao_xu_phat",
    "khieu_nai_to_cao_xu_phat": "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
    "tinh_huong": "khieu_nai_to_cao_xu_phat",
}


def _question(case: dict[str, Any], role: str) -> str:
    return str(case.get(f"question_{role}") or case.get("question") or "").strip()


def build_records(golden: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    today = date.today().isoformat()
    for case in golden.get("questions") or []:
        if not isinstance(case, dict) or not case.get("id"):
            continue
        for role in ("citizen", "officer"):
            records.append({
                "review_id": f"{case['id']}:{role}",
                "case_id": case["id"],
                "domain": DOMAIN_MAP.get(str(case.get("domain") or ""), str(case.get("domain") or "unclassified")),
                "question_type": case.get("question_type"),
                "role": role,
                "question": _question(case, role),
                "citizen_question": _question(case, "citizen"),
                "officer_question": _question(case, "officer"),
                "event_date": case.get("event_date"),
                "legal_as_of": case.get("legal_as_of") or today,
                "required_facts": case.get("required_facts") or case.get("critical_facts") or [],
                "missing_facts_to_ask": case.get("missing_facts_to_ask") or [],
                "expected_documents": None,
                "expected_articles": None,
                "forbidden_documents": case.get("forbidden_documents") or [],
                "expected_authority": None,
                "forbidden_authority": case.get("forbidden_authority") or case.get("forbidden_authority_cues") or [],
                "mandatory_documents": None,
                "conditional_documents": None,
                "processing_time": None,
                "fee": None,
                "penalty_range": None,
                "remedial_measures": None,
                "official_form_ids": None,
                "expected_conclusion": None,
                "allowed_conditional_conclusions": [],
                "critical_errors": case.get("critical_errors") or [],
                "machine_proposal": {
                    "expected_citations": case.get("expected_citations") or [],
                    "expected_authority_cues": case.get("expected_authority_cues") or [],
                    "expected_forms": case.get("expected_forms") or [],
                    "evaluation_hints": case.get("evaluation_hints") or {},
                },
                "expert_review_status": "pending",
                "expert_score": None,
                "expert_name": None,
                "reviewed_at": None,
                "review_version": "1.0",
                "second_expert_name": None,
                "second_reviewed_at": None,
            })
    return records


def main() -> int:
    golden = json.loads(SOURCE.read_text(encoding="utf-8-sig"))
    records = build_records(golden)
    payload = {
        "description": "Citizen/officer legal golden answers awaiting human legal-expert review",
        "source_version": golden.get("version"),
        "review_schema_version": "1.0",
        "expected_review_count": len(golden.get("questions") or []) * 2,
        "records": records,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {len(records)} expert review records at {OUTPUT}")
    return 0 if len(records) == 334 else 1


if __name__ == "__main__":
    raise SystemExit(main())
