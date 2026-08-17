"""Evaluate Feature 016 Gate C without activating a model or collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


_ALLOWED_DEGRADED_REASONS = {
    "disabled_by_config",
    "model_path_missing",
    "dependency_unavailable",
    "cuda_unavailable",
    "model_load_failed",
}


def evaluate_gate_c(
    *,
    reranker: Mapping[str, Any],
    embedding_shadow: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    reranker_status = str(reranker.get("status") or "")
    reranker_reason = str(reranker.get("reason_code") or "")
    reranker_available = reranker_status == "pass"
    reranker_degraded_ok = (
        reranker_status == "disabled"
        and reranker_reason in _ALLOWED_DEGRADED_REASONS
    )
    if not (reranker_available or reranker_degraded_ok):
        reasons.append("reranker_status_invalid")
    if not bool(reranker.get("fallback_verified")):
        reasons.append("fallback_not_verified")
    if not bool(reranker.get("deterministic")):
        reasons.append("reranker_not_deterministic")
    if int(reranker.get("oom_count") or 0) > 0:
        reasons.append("oom_observed")
    if int(reranker.get("safety_regression_count") or 0) > 0 or int(
        embedding_shadow.get("safety_regression_count") or 0
    ) > 0:
        reasons.append("safety_regression")

    before = str(embedding_shadow.get("active_collection_before") or "")
    after = str(embedding_shadow.get("active_collection_after") or "")
    unchanged = bool(before and before == after)
    if not unchanged:
        reasons.append("active_collection_changed")
    if bool(embedding_shadow.get("activation_requested")):
        reasons.append("shadow_activation_requested")
    shadow_status = str(embedding_shadow.get("status") or "")
    shadow_reason = str(embedding_shadow.get("reason_code") or "")
    if not (
        shadow_status in {"pass", "planned"}
        or (
            shadow_status == "disabled"
            and shadow_reason in _ALLOWED_DEGRADED_REASONS
        )
    ):
        reasons.append("embedding_shadow_status_invalid")

    gate_pass = not reasons
    degraded = reranker_status == "disabled" or shadow_status == "disabled"
    return {
        "schema_version": "feature016-gate-c-v1",
        "status": "pass_degraded" if gate_pass and degraded else "pass" if gate_pass else "fail",
        "gate_c_pass": gate_pass,
        "degraded": degraded,
        "active_collection_unchanged": unchanged,
        "reason_codes": sorted(set(reasons)),
        "reranker": dict(reranker),
        "embedding_shadow": dict(embedding_shadow),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reranker", required=True, type=Path)
    parser.add_argument("--embedding-shadow", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = evaluate_gate_c(
        reranker=json.loads(args.reranker.read_text(encoding="utf-8")),
        embedding_shadow=json.loads(
            args.embedding_shadow.read_text(encoding="utf-8")
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: result[key] for key in ("status", "gate_c_pass", "reason_codes")}, ensure_ascii=False))
    return 0 if result["gate_c_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

