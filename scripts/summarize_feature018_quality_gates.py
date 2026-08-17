"""Build a content-free Feature 018 quality-gate summary from authoritative runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_feature017_deepseek_golden1000 import (  # noqa: E402
    EVALUATOR_VERSION,
    REPORT_SCHEMA_VERSION,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_report_required:{path}")
    return value


def summarize(
    *,
    golden100: Path,
    golden294: Path,
    golden_v3: Path,
    deepseek_live: Path,
    release_manifest: Path,
) -> dict[str, Any]:
    reports = {name: load(path) for name, path in {
        "golden100": golden100,
        "golden294": golden294,
        "golden_v3": golden_v3,
        "deepseek_live": deepseek_live,
    }.items()}
    live = reports["deepseek_live"].get("summary") or {}
    live_count = int(live.get("case_count") or 0)
    live_passed = int(live.get("passed") or 0)
    live_http_200 = int(live.get("http_200") or 0)
    exact_forms = int(live.get("exact_form_set") or 0)
    p95 = float(live.get("p95_seconds") or 0)
    fallback = int(live.get("unexpected_provider_fallback") or 0)
    run_identity = reports["deepseek_live"].get("run_identity") or {}
    concurrency = int(run_identity.get("concurrency") or 0)
    evaluator_identity_bound = bool(
        reports["deepseek_live"].get("schema_version") == REPORT_SCHEMA_VERSION
        and reports["deepseek_live"].get("evaluator_version") == EVALUATOR_VERSION
        and run_identity.get("evaluator_version") == EVALUATOR_VERSION
        and int(run_identity.get("case_count") or 0) == live_count
        and run_identity.get("golden_sha256")
        and run_identity.get("target_sha256")
        and run_identity.get("case_selection_sha256")
    )
    live_checks = {
        "evaluator_identity_bound": evaluator_identity_bound,
        "case_count_1000": live_count == 1_000,
        "full_answer_p95_at_most_25_seconds": 0 < p95 <= 25,
        "case_pass_rate_at_least_99_percent": live_count > 0 and live_passed / live_count >= 0.99,
        "http_error_rate_below_0_5_percent": live_count > 0 and (live_count - live_http_200) / live_count < 0.005,
        "exact_form_set_100_percent": live_count > 0 and exact_forms == live_count,
        "unexpected_fallback_at_most_3_percent": live_count > 0 and fallback / live_count <= 0.03,
    }
    gates = {
        "golden100_regression": {
            "passed": reports["golden100"].get("status") == "PASS"
            and int(reports["golden100"].get("request_count") or 0) == 100,
            "case_count": int(reports["golden100"].get("request_count") or 0),
        },
        "golden294": {
            "passed": reports["golden294"].get("status") == "PASS"
            and int(reports["golden294"].get("case_count") or 0) == 294,
            "case_count": int(reports["golden294"].get("case_count") or 0),
            "expired_selection_count": int(reports["golden294"].get("expired_selection_count") or 0),
        },
        "golden_v3_1000_deterministic": {
            "passed": reports["golden_v3"].get("passed") is True
            and int(reports["golden_v3"].get("case_count") or 0) == 1_000,
            "case_count": int(reports["golden_v3"].get("case_count") or 0),
        },
        "golden_v3_1000_live_full_answer": {
            "passed": all(live_checks.values()),
            "case_count": live_count,
            "evaluator_passed": live_passed,
            "http_200": live_http_200,
            "exact_form_set": exact_forms,
            "p95_seconds": p95,
            "concurrency": concurrency,
            "unexpected_provider_fallback": fallback,
            "checks": live_checks,
            "error_counts": dict(live.get("error_counts") or {}),
        },
    }
    paths = {
        "golden100": golden100,
        "golden294": golden294,
        "golden_v3": golden_v3,
        "deepseek_live": deepseek_live,
    }
    return {
        "schema_version": "feature018-quality-gates-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_fingerprint": digest(release_manifest),
        "passed": all(bool(item["passed"]) for item in gates.values()),
        "gates": gates,
        "artifacts": {
            name: {"path": path.as_posix(), "sha256": digest(path)}
            for name, path in paths.items()
        },
        "privacy": "Aggregate metrics and artifact hashes only; no question or answer content.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(
        golden100=ROOT / "reports/feature016/answer-pipeline-v2/golden100-corrected-retrieval-v2.json",
        golden294=ROOT / "reports/feature016/answer-pipeline-v2/golden294-validity-subset-final.json",
        golden_v3=ROOT / "reports/feature017/golden-v3-1000-evaluation.json",
        deepseek_live=ROOT / "reports/feature018/deepseek-live-v2-1000-c100.json",
        release_manifest=ROOT / "release-data/manifest.sha256.json",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "gates": result["gates"]}, ensure_ascii=False))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
