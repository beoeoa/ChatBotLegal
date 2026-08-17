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
    stages = artifact.get("stage_latency_ms") or {}
    retrieval_p95 = (stages.get("retrieval") or {}).get("p95")
    generation_p95 = (stages.get("generation") or {}).get("p95")
    quality_gate_failures = int(
        (artifact.get("quality_gate_counts") or {}).get("failed") or 0
    )
    generation_timeouts = int(
        (artifact.get("error_category_counts") or {}).get("timeout") or 0
    )
    warm_c1 = artifact.get("mode") == "warm" and artifact.get("concurrency") == 1
    checks = {
        "privacy_scan": privacy_safe,
        "completed_requests": total > 0 and completed == total,
        "repair_rate_under_10_percent": total > 0 and repairs / total < 0.10,
        "quality_gate_no_failures": quality_gate_failures == 0,
        "generation_timeout_rate_under_50_percent": (
            total > 0 and generation_timeouts / total < 0.50
        ),
        "warm_c1_retrieval_p95_under_3_seconds": (
            not warm_c1
            or isinstance(retrieval_p95, int)
            and retrieval_p95 <= 3_000
        ),
        "warm_c1_generation_p95_under_24_seconds": (
            not warm_c1
            or isinstance(generation_p95, int)
            and generation_p95 <= 24_000
        ),
        "warm_c1_end_to_end_p95_under_30_seconds": (
            not warm_c1
            or isinstance(p95, int)
            and p95 <= 30_000
        ),
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
