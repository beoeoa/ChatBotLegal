#!/usr/bin/env python3
"""Evaluate Golden V3 through the deterministic router and public API contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_router_v3 import resolve_forms  # noqa: E402


DEFAULT_MANIFEST = (
    ROOT
    / "outputs"
    / "feature017-full-release-candidate-20260812-packaged"
    / "form-release-v1.json"
)
DEFAULT_GOLDEN = (
    ROOT
    / "outputs"
    / "feature017-golden-v3-user-journey-1000"
    / "golden-v3-1000.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "feature017" / "golden-v3-1000-evaluation.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_status(case: Mapping[str, Any]) -> str:
    if case.get("expected_clarification"):
        return "clarification_required"
    if case.get("expected_state") == "released":
        return "resolved"
    return "source_gap"


def _answer_mode(result: Mapping[str, Any]) -> str:
    return "grounded_answer" if result.get("status") == "resolved" else "source_view_only"


def _check_case(case: Mapping[str, Any], result: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_ids = sorted(str(value) for value in case.get("expected_form_ids") or [])
    actual_ids = sorted(
        str(item.get("form_id") or "")
        for item in result.get("recommended_forms") or []
    )
    if result.get("status") != _expected_status(case):
        errors.append("STATUS_MISMATCH")
    if case.get("expected_clarification"):
        if actual_ids:
            errors.append("CLARIFICATION_RETURNED_FORMS")
    elif result.get("procedure_id") != case.get("procedure_id"):
        errors.append("PROCEDURE_MISMATCH")
    if actual_ids != expected_ids:
        errors.append("FORM_SET_MISMATCH")
    if set(actual_ids).intersection(case.get("forbidden_form_ids") or []):
        errors.append("FORBIDDEN_FORM_RETURNED")
    if _answer_mode(result) != case.get("expected_answer_mode"):
        errors.append("ANSWER_MODE_MISMATCH")
    if result.get("status") == "resolved":
        packet = result.get("evidence_packet") or {}
        if packet.get("provider_may_modify_form_identity") is not False:
            errors.append("PROVIDER_IDENTITY_GUARD_MISSING")
        if sorted(packet.get("form_ids") or []) != expected_ids:
            errors.append("EVIDENCE_PACKET_FORM_SET_MISMATCH")
    return errors


def evaluate(
    manifest: Mapping[str, Any],
    golden: Mapping[str, Any],
    *,
    include_api: bool = True,
) -> dict[str, Any]:
    cases = list(golden.get("cases") or [])
    if len(cases) != 1000:
        raise ValueError("FEATURE017_GOLDEN_CASE_COUNT_INVALID")

    direct_timings: list[float] = []
    direct_failures: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    direct_results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        result = resolve_forms(
            question=str(case["question"]),
            manifest=manifest,
            audience=str(case.get("audience") or "citizen"),
            legal_as_of=date.fromisoformat(str(case["legal_as_of"])[:10]),
        )
        direct_timings.append((time.perf_counter() - started) * 1000)
        direct_results.append(result)
        status_counts[str(result.get("status") or "unknown")] += 1
        errors = _check_case(case, result)
        if errors:
            direct_failures.append({"case_id": case["case_id"], "errors": errors})

    api_timings: list[float] = []
    api_failures: list[dict[str, Any]] = []
    if include_api:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.routers.procedure_forms_catalog import router

        app = FastAPI()
        app.include_router(router, prefix="/api")

        def configured(**kwargs):
            return {
                **resolve_forms(manifest=manifest, **kwargs),
                "router_mode": "active",
            }

        with patch(
            "api.form_router_v3.resolve_from_configured_release",
            side_effect=configured,
        ):
            client = TestClient(app)
            for case in cases:
                started = time.perf_counter()
                response = client.get(
                    "/api/procedures/forms-catalog/resolve",
                    params={
                        "q": case["question"],
                        "audience": case.get("audience") or "citizen",
                        "legal_as_of": case["legal_as_of"],
                    },
                    headers={"X-User-Role": "citizen", "X-User-Id": "golden-v3-evaluator"},
                )
                api_timings.append((time.perf_counter() - started) * 1000)
                if response.status_code != 200:
                    api_failures.append({
                        "case_id": case["case_id"],
                        "errors": [f"HTTP_{response.status_code}"],
                    })
                    continue
                errors = _check_case(case, response.json())
                if errors:
                    api_failures.append({"case_id": case["case_id"], "errors": errors})

    passed = len(cases) - len(direct_failures)
    api_passed = len(cases) - len(api_failures) if include_api else None
    direct_p95 = statistics.quantiles(direct_timings, n=100)[94]
    api_p95 = statistics.quantiles(api_timings, n=100)[94] if api_timings else None
    report = {
        "schema_version": "feature017-golden-v3-evaluation-v1",
        "case_count": len(cases),
        "direct": {
            "passed": passed,
            "failed": len(direct_failures),
            "pass_rate": passed / len(cases),
            "status_counts": dict(status_counts),
            "p50_ms": statistics.median(direct_timings),
            "p95_ms": direct_p95,
            "max_ms": max(direct_timings),
            "failures": direct_failures[:100],
        },
        "api": {
            "evaluated": include_api,
            "passed": api_passed,
            "failed": len(api_failures),
            "pass_rate": (api_passed / len(cases)) if include_api else None,
            "p50_ms": statistics.median(api_timings) if api_timings else None,
            "p95_ms": api_p95,
            "max_ms": max(api_timings) if api_timings else None,
            "failures": api_failures[:100],
        },
        "gates": {
            "correct_procedure_gte_99_percent": passed / len(cases) >= 0.99,
            "exact_form_set_100_percent": not any(
                "FORM_SET_MISMATCH" in item["errors"] for item in direct_failures
            ),
            "clarification_100_percent": not any(
                case.get("expected_clarification") and _check_case(case, result)
                for case, result in zip(cases, direct_results)
            ),
            "no_forbidden_forms": not any(
                "FORBIDDEN_FORM_RETURNED" in item["errors"] for item in direct_failures
            ),
            "provider_cannot_modify_identity": not any(
                "PROVIDER_IDENTITY_GUARD_MISSING" in item["errors"]
                for item in direct_failures
            ),
            "retrieval_p95_lte_3000_ms": direct_p95 <= 3000,
            "api_contract_100_percent": not api_failures if include_api else None,
        },
    }
    report["passed"] = all(value is True for value in report["gates"].values())
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-api", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    golden = json.loads(args.golden.read_text(encoding="utf-8-sig"))
    report = evaluate(manifest, golden, include_api=not args.skip_api)
    report["manifest_file_sha256"] = _sha256(args.manifest)
    report["golden_file_sha256"] = _sha256(args.golden)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "direct": {key: report["direct"][key] for key in ("passed", "failed", "p95_ms")},
                "api": {key: report["api"][key] for key in ("passed", "failed", "p95_ms")},
                "gates": report["gates"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
