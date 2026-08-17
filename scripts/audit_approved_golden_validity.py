"""Audit approved Golden expected sources against the serving validity snapshot.

This command is read-only.  Human approval is preserved, but an expected
source that the chatbot would block at the case's legal date is marked for
revalidation instead of being silently accepted into evaluation.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_validity_registry import project_validity_for_row


def audit_dataset(
    dataset: dict[str, Any], snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checked_sources = 0
    affected_by_law: dict[str, set[str]] = defaultdict(set)

    for case in dataset.get("cases") or []:
        case_id = str(case.get("case_id") or "")
        legal_as_of = date.fromisoformat(
            str(case.get("legal_as_of") or dataset.get("legal_as_of"))[:10]
        )
        for source in case.get("expected_sources") or []:
            law_number = str(source.get("law_number") or "").strip()
            article_number = str(source.get("article") or "").strip() or None
            checked_sources += 1
            projection = project_validity_for_row(
                {
                    "law_number": law_number,
                    "article_number": article_number,
                },
                snapshot=snapshot,
                as_of=legal_as_of,
            )
            item = {
                "case_id": case_id,
                "legal_as_of": legal_as_of.isoformat(),
                "law_number": law_number,
                "article_number": article_number,
                "status": projection.get("status"),
                "serving_action": projection.get("serving_action"),
                "reason_code": projection.get("reason_code"),
                "warning_code": projection.get("warning_code"),
                "display_label": projection.get("display_label"),
            }
            if projection.get("current_answer_eligible") is False:
                blocked.append(item)
                affected_by_law[law_number].add(case_id)
            elif projection.get("warning_code"):
                warnings.append(item)

    blocked_laws = [
        {
            "law_number": law_number,
            "case_count": len(case_ids),
            "case_ids": sorted(case_ids),
        }
        for law_number, case_ids in sorted(affected_by_law.items())
    ]
    return {
        "schema_version": "golden-1000-validity-audit-v1",
        "status": "needs_revalidation" if blocked else "pass",
        "summary": {
            "case_count": len(dataset.get("cases") or []),
            "expected_sources_checked": checked_sources,
            "blocked_expected_source_count": len(blocked),
            "blocked_case_count": len({item["case_id"] for item in blocked}),
            "blocked_law_count": len(blocked_laws),
            "warning_source_count": len(warnings),
        },
        "blocked_laws": blocked_laws,
        "blocked_expected_sources": blocked,
        "warnings": warnings,
        "production_corpus_mutated": False,
        "production_vectors_mutated": False,
        "snapshot_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = audit_dataset(dataset, snapshot)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
