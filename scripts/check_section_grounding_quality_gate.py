"""Evaluate the non-legal release gates for Feature 005.

This tool can make a rollout *blocked* decision from privacy-safe evidence. It
cannot approve legal correctness: a substantive legal reviewer remains required.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:  # supports both `python scripts/...py` and test-module imports
    from scripts.scan_section_grounding_artifacts import validate_artifact
except ModuleNotFoundError:  # pragma: no cover - direct-script compatibility
    from scan_section_grounding_artifacts import validate_artifact


def evaluate_gate(artifact: dict[str, Any]) -> dict[str, Any]:
    privacy_safe, privacy_reason = validate_artifact(artifact)
    p95 = (artifact.get("latency_ms") or {}).get("p95")
    total = int(artifact.get("request_count") or 0)
    completed = int(artifact.get("completed_count") or 0)
    repairs = int(artifact.get("repair_count") or 0)
    checks = {
        "privacy_scan": privacy_safe,
        "completed_requests": total > 0 and completed == total,
        "repair_rate_under_10_percent": total > 0 and repairs / total < 0.10,
        "warm_p95_under_15_seconds": artifact.get("mode") != "warm" or isinstance(p95, int) and p95 <= 15_000,
        "legal_reviewer_approval": False,
    }
    return {
        "decision": "ready_for_legal_review" if all(value for key, value in checks.items() if key != "legal_reviewer_approval") else "blocked",
        "checks": checks,
        "privacy_reason": privacy_reason,
        "legal_reviewer_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Feature 005 technical quality gates.")
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"decision": "blocked", "reason": "unreadable_artifact"}))
        return 1
    result = evaluate_gate(artifact)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["decision"] == "ready_for_legal_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
