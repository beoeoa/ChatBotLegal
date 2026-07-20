# -*- coding: utf-8 -*-
"""Release gate for the 334 citizen/officer expert-reviewed golden records."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "notebook_data" / "legal-golden-expert-review.json"
ALLOWED_STATUSES = {"pending", "approved", "expert_disputed", "needs_revalidation"}
REQUIRED_APPROVED_FIELDS = (
    "expected_documents",
    "expected_articles",
    "expected_authority",
    "mandatory_documents",
    "conditional_documents",
    "processing_time",
    "fee",
    "official_form_ids",
    "expected_conclusion",
    "expert_score",
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def audit(payload: dict[str, Any]) -> dict[str, Any]:
    records = [item for item in payload.get("records") or [] if isinstance(item, dict)]
    statuses = Counter(str(item.get("expert_review_status") or "missing") for item in records)
    duplicate_ids = [key for key, count in Counter(item.get("review_id") for item in records).items() if count > 1]
    invalid_status = [item.get("review_id") for item in records if item.get("expert_review_status") not in ALLOWED_STATUSES]
    invalid_approved: list[dict[str, Any]] = []
    domain_role = defaultdict(lambda: {"total": 0, "approved": 0})
    for item in records:
        bucket = domain_role[f"{item.get('domain')}:{item.get('role')}"]
        bucket["total"] += 1
        if item.get("expert_review_status") == "approved":
            bucket["approved"] += 1
            missing = [field for field in REQUIRED_APPROVED_FIELDS if field not in item]
            score = item.get("expert_score")
            if (
                not item.get("expert_name")
                or not item.get("reviewed_at")
                or missing
                or not isinstance(score, (int, float))
                or not 0 <= float(score) <= 10
            ):
                invalid_approved.append({"review_id": item.get("review_id"), "missing": missing})
    passed = (
        len(records) == 334
        and statuses.get("approved", 0) == 334
        and not duplicate_ids
        and not invalid_status
        and not invalid_approved
    )
    return {
        "pass": passed,
        "record_count": len(records),
        "status_counts": dict(statuses),
        "duplicate_ids": duplicate_ids,
        "invalid_status": invalid_status,
        "invalid_approved": invalid_approved,
        "domain_role": dict(domain_role),
        "message": "Đủ 334 lượt chuyên gia duyệt." if passed else "Chưa đủ điều kiện chuyên gia để phát hành pilot.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit expert-reviewed legal golden records")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()
    report = audit(json.loads(args.input.read_text(encoding="utf-8-sig")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
