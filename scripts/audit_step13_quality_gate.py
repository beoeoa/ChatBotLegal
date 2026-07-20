"""Deterministic readiness gate for the Step 13 legal QA benchmark."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "notebook_data" / "legal-golden-set.json"
DOMAINS = (
    "ho_tich_chung_thuc", "dat_dai_xay_dung", "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat", "an_sinh_y_te_giao_duc",
)
MARKERS = ("Ã", "Â", "Ä", "áº", "á»", "Æ")


def main() -> int:
    data = json.loads(GOLDEN.read_text(encoding="utf-8-sig"))
    questions = [item for item in data.get("questions", []) if isinstance(item, dict)]
    ids = [item.get("id") for item in questions]
    counts = Counter(item.get("domain") for item in questions)
    errors: list[str] = []
    if len(ids) != len(set(ids)):
        errors.append("duplicate question id")
    if len(questions) < 150:
        errors.append(f"golden set: need at least 150 cases, got {len(questions)}")
    for domain in DOMAINS:
        if counts[domain] < 30:
            errors.append(f"{domain}: need at least 30 cases, got {counts[domain]}")
    for item in questions:
        if not item.get("question_citizen") or not item.get("question_officer"):
            errors.append(f"{item.get('id')}: missing citizen/officer question")
        if any(marker in str(item.get("question_citizen", "")) for marker in MARKERS):
            errors.append(f"{item.get('id')}: mojibake in citizen question")
        if any(marker in str(item.get("question_officer", "")) for marker in MARKERS):
            errors.append(f"{item.get('id')}: mojibake in officer question")
        for field in ("expected_citations", "critical_facts", "no_fake_deadline", "no_fake_fee"):
            if field not in item:
                errors.append(f"{item.get('id')}: missing {field}")
    report = {
        "status": "pass" if not errors else "fail",
        "total": len(questions),
        "target_total": 150,
        "minimum_cases_per_domain": 30,
        "by_domain": dict(counts),
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
