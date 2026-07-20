# -*- coding: utf-8 -*-
"""Audit live 334 results, expert scores, links, forms and latency gates."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.audit_expert_golden_review import audit as audit_experts
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from audit_expert_golden_review import audit as audit_experts


ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "notebook_data" / "quality_runs" / "live-334-latest.json"
EXPERT = ROOT / "notebook_data" / "legal-golden-expert-review.json"
REPORT = ROOT / "notebook_data" / "quality_runs" / "quality-gate-334.json"
HEALTH = ROOT / "notebook_data" / "quality_runs" / "asset-health-334.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def audit(live: dict[str, Any], expert: dict[str, Any], asset_health: dict[str, Any] | None = None) -> dict[str, Any]:
    expert_gate = audit_experts(expert)
    expert_by_id = {str(item.get("review_id")): item for item in expert.get("records") or []}
    results = list(live.get("results") or [])
    completed = [item for item in results if item.get("status") == "completed"]
    groups: dict[str, list[float]] = defaultdict(list)
    critical: list[dict[str, Any]] = []
    citation_total = citation_good = form_total = form_good = 0
    normal_latency: list[float] = []
    complex_latency: list[float] = []
    for item in completed:
        review = expert_by_id.get(str(item.get("review_id")), {})
        score = review.get("expert_score")
        if isinstance(score, (int, float)):
            groups[f"{item.get('domain')}:{item.get('role')}"] .append(float(score))
        elapsed = float(item.get("elapsed_seconds") or 0)
        if review.get("question_type") in {"land_construction", "complaint_sanction"}:
            complex_latency.append(elapsed)
        else:
            normal_latency.append(elapsed)
        rejected = [claim for claim in item.get("claim_validation") or [] if claim.get("status") == "rejected"]
        if rejected:
            critical.append({"review_id": item.get("review_id"), "reason": "rejected_claims", "count": len(rejected)})
        for citation in item.get("citations") or []:
            citation_total += 1
            if citation.get("doc_id") and citation.get("chunk_id") and citation.get("link_status") not in {"dead", "missing", "broken"}:
                citation_good += 1
        for form in item.get("recommended_forms") or []:
            form_total += 1
            if form.get("review_status") == "approved" and form.get("official_level") == "official" and (form.get("download_url") or form.get("local_file")):
                form_good += 1
    group_scores = {key: round(sum(values) / len(values), 3) for key, values in groups.items() if values}
    citation_rate = citation_good / max(1, citation_total)
    form_rate = form_good / max(1, form_total) if form_total else 1.0
    p95_normal = _percentile(normal_latency, 0.95)
    p95_complex = _percentile(complex_latency, 0.95)
    health_groups = (asset_health or {}).get("by_kind") or {}
    health_ok = bool((asset_health or {}).get("total")) and all(
        float(item.get("rate") or 0) >= 0.99
        for item in health_groups.values()
        if item.get("total")
    )
    passed = (
        expert_gate["pass"]
        and len(completed) == 334
        and len(group_scores) == 10
        and all(score >= 9 for score in group_scores.values())
        and not critical
        and citation_rate >= 0.99
        and form_rate >= 0.99
        and health_ok
        and p95_normal is not None and p95_normal <= 20
        and (p95_complex is None or p95_complex <= 40)
    )
    return {
        "pass": passed,
        "expert_gate": expert_gate,
        "live": {"completed": len(completed), "target": 334, "failed": len(results) - len(completed)},
        "domain_role_scores": group_scores,
        "critical_errors": critical,
        "citation_success_rate": round(citation_rate, 5),
        "form_success_rate": round(form_rate, 5),
        "latency": {"normal_p95_seconds": p95_normal, "complex_p95_seconds": p95_complex},
        "asset_health": {"pass": health_ok, "summary": health_groups},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the complete 334-result release gate")
    parser.add_argument("--live", type=Path, default=LIVE)
    parser.add_argument("--expert", type=Path, default=EXPERT)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--health", type=Path, default=HEALTH)
    args = parser.parse_args()
    result = audit(_read(args.live), _read(args.expert), _read(args.health))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
