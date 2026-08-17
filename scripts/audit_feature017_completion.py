#!/usr/bin/env python3
"""Audit Feature 017 completion from authoritative local artifacts.

The audit is read-only. It intentionally reports incomplete until an exact
attestation, a full validated release and the proposed Golden V3 dataset all
exist and agree by checksum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "data" / "source_cache" / "feature017_approved_sources_20260811"
DEFAULT_OUTPUT = ROOT / "reports" / "feature017" / "completion-audit-20260812.json"
DEFAULT_FULL_RELEASE_REHEARSAL = ROOT / "reports" / "feature017" / "postgres-full-release-rehearsal.json"
DEFAULT_EVALUATION = ROOT / "reports" / "feature017" / "golden-v3-1000-evaluation.json"
DEFAULT_REMOTE_SOURCE_GATE = ROOT / "reports" / "feature017" / "remote-source-gate.json"
DEFAULT_WORKBOOK = ROOT / "outputs" / "feature017-golden-v3-user-journey-1000" / "golden-v3-1000-approved.xlsx"
DEFAULT_FULL_RELEASE = ROOT / "outputs" / "feature017-full-release-candidate-20260812-packaged" / "release-candidate.json"
DEFAULT_GOLDEN = ROOT / "outputs" / "feature017-golden-v3-user-journey-1000" / "golden-v3-1000-approved.json"


def _read_optional(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_completion_audit(
    *,
    candidate: Mapping[str, Any] | None,
    preview: Mapping[str, Any] | None,
    attestation: Mapping[str, Any] | None,
    release_delta: Mapping[str, Any] | None,
    full_release: Mapping[str, Any] | None,
    golden: Mapping[str, Any] | None,
    full_release_rehearsal: Mapping[str, Any] | None = None,
    evaluation: Mapping[str, Any] | None = None,
    remote_source_gate: Mapping[str, Any] | None = None,
    workbook_exists: bool = False,
) -> dict[str, Any]:
    candidate_ok = bool(
        candidate
        and candidate.get("schema_version")
        == "feature017-legal-enrichment-candidate-v1"
        and candidate.get("scope_complete") is True
        and int(candidate.get("source_approved_identity_count") or 0) == 60
        and int(candidate.get("deferred_identity_count") or 0) == 40
        and int(candidate.get("attestation_preview_ready_count") or 0) == 60
        and candidate.get("runtime_catalog_mutated") is False
    )
    preview_ok = bool(
        preview
        and preview.get("schema_version")
        == "feature017-batch-attestation-preview-v1"
        and preview.get("attestation_status")
        == "awaiting_explicit_fingerprint_confirmation"
        and preview.get("candidate_manifest_sha256")
        == (candidate or {}).get("manifest_sha256")
        and int(preview.get("asset_count") or 0) == 60
        and preview.get("runtime_catalog_mutated") is False
    )
    attestation_ok = bool(
        attestation
        and attestation.get("schema_version")
        == "feature017-batch-attestation-v1"
        and attestation.get("attestation_status") == "attested"
        and attestation.get("candidate_manifest_sha256")
        == (candidate or {}).get("manifest_sha256")
        and attestation.get("attestation_fingerprint")
        == (preview or {}).get("attestation_fingerprint")
    )
    delta_ok = bool(
        release_delta
        and release_delta.get("schema_version") == "feature017-release-delta-v1"
        and release_delta.get("attestation_id")
        == (attestation or {}).get("attestation_id")
        and release_delta.get("activation_allowed") is False
    )
    release_manifest = (full_release or {}).get("manifest") or full_release or {}
    release_gate_report = (
        (full_release or {}).get("gate_report")
        or release_manifest.get("gate_report")
        or {}
    )
    coverage = release_manifest.get("coverage") or {}
    full_release_ok = bool(
        full_release
        and release_manifest.get("schema_version") == "form-release-v1"
        and coverage.get("complete") is True
        and all(
            int(coverage.get(done) or 0) == int(coverage.get(total) or -1)
            for done, total in (
                ("procedure_decided", "procedure_total"),
                ("identity_decided", "identity_total"),
                ("binding_decided", "binding_total"),
            )
        )
        and release_gate_report.get("passed") is True
    )
    cases = list((golden or {}).get("cases") or [])
    golden_review_status = (golden or {}).get("review_status")
    golden_ok = bool(
        golden
        and golden.get("schema_version") == "feature017-golden-v3-dataset-v1"
        and golden_review_status in {"proposed", "approved"}
        and len(cases) == 1000
        and all(
            item.get("release_manifest_sha256") == _canonical_hash(release_manifest)
            for item in cases
        )
    )
    golden_approved_ok = bool(
        golden_ok
        and golden_review_status == "approved"
        and all(item.get("review_status") == "approved" for item in cases)
        and golden.get("approved_checksum")
        == _canonical_hash(
            {
                "schema_version": "feature017-golden-v3-dataset-v1",
                "review_status": "approved",
                "cases": cases,
            }
        )
    )
    rehearsal_coverage = (full_release_rehearsal or {}).get("coverage") or {}
    rehearsal_counts = (full_release_rehearsal or {}).get("counts") or {}
    rehearsal_ok = bool(
        full_release_rehearsal
        and full_release_rehearsal.get("status") == "passed"
        and full_release_rehearsal.get("database") == "isolated_rehearsal"
        and full_release_rehearsal.get("live_apply") is False
        and full_release_rehearsal.get("release_status") == "validated"
        and full_release_rehearsal.get("gate_passed") is True
        and rehearsal_coverage.get("complete") is True
        and int(rehearsal_coverage.get("procedure_decided") or 0) == 191
        and int(rehearsal_coverage.get("identity_decided") or 0) == 131
        and int(rehearsal_coverage.get("binding_decided") or 0) == 229
        and int(rehearsal_counts.get("form_release") or 0) == 1
        and int(rehearsal_counts.get("form_active_release") or 0) == 0
        and full_release_rehearsal.get("active_pointer_changed") is False
    )
    direct_evaluation = (evaluation or {}).get("direct") or {}
    api_evaluation = (evaluation or {}).get("api") or {}
    evaluation_ok = bool(
        evaluation
        and evaluation.get("passed") is True
        and int(evaluation.get("case_count") or 0) == 1000
        and int(direct_evaluation.get("passed") or 0) == 1000
        and direct_evaluation.get("failed") == 0
        and float(direct_evaluation.get("p95_ms") or 999999) <= 3000
        and api_evaluation.get("evaluated") is True
        and int(api_evaluation.get("passed") or 0) == 1000
        and api_evaluation.get("failed") == 0
        and workbook_exists
    )
    remote_checks = (remote_source_gate or {}).get("source_checks") or {}
    remote_source_ok = bool(
        remote_source_gate
        and remote_source_gate.get("passed") is True
        and int(remote_source_gate.get("asset_count") or 0) == 91
        and remote_checks.get("mode") == "remote"
        and int(remote_checks.get("required") or 0) == 91
        and int(remote_checks.get("passed") or 0) == 91
        and remote_source_gate.get("failure_count") == 0
        and remote_source_gate.get("activation_allowed") is False
        and remote_source_gate.get("active_pointer_changed") is False
    )
    checks = [
        {
            "requirement": "owner_scoped_source_candidate",
            "passed": candidate_ok,
            "evidence": "scoped-legal-enrichment-candidate.json",
        },
        {
            "requirement": "tamper_evident_attestation_preview",
            "passed": preview_ok,
            "evidence": "scoped-attestation-preview.json",
        },
        {
            "requirement": "explicit_admin_attestation",
            "passed": attestation_ok,
            "evidence": "scoped-attestation.json",
        },
        {
            "requirement": "non_activatable_release_delta",
            "passed": delta_ok,
            "evidence": "scoped-release-delta.json",
        },
        {
            "requirement": "complete_validated_191_131_229_release",
            "passed": full_release_ok,
            "evidence": "form-release-v1 manifest + gate_report",
        },
        {
            "requirement": "isolated_postgres_validated_release_without_pointer_activation",
            "passed": rehearsal_ok,
            "evidence": "postgres-full-release-rehearsal.json",
        },
        {
            "requirement": "remote_source_and_runtime_asset_checksum_gate_91_of_91",
            "passed": remote_source_ok,
            "evidence": "remote-source-gate.json + runtime-asset-package.json",
        },
        {
            "requirement": "golden_v3_1000_from_validated_release",
            "passed": golden_ok,
            "evidence": "golden-v3-1000-approved.json",
        },
        {
            "requirement": "golden_v3_user_approved_checksum_frozen",
            "passed": golden_approved_ok,
            "evidence": "approved-manifest.json + approval-receipt.json",
        },
        {
            "requirement": "golden_v3_router_api_performance_and_review_workbook",
            "passed": evaluation_ok,
            "evidence": "golden-v3-1000-evaluation.json + golden-v3-1000-approved.xlsx",
        },
    ]
    incomplete = [item["requirement"] for item in checks if not item["passed"]]
    return {
        "schema_version": "feature017-completion-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "complete": not incomplete,
        "checks": checks,
        "incomplete_requirements": incomplete,
        "safe_to_activate_public": False,
        "browser_uat_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, default=DEFAULT_DIR / "scoped-legal-enrichment-candidate.json")
    parser.add_argument("--preview", type=Path, default=DEFAULT_DIR / "scoped-attestation-preview.json")
    parser.add_argument("--attestation", type=Path, default=DEFAULT_DIR / "scoped-attestation.json")
    parser.add_argument("--release-delta", type=Path, default=DEFAULT_DIR / "scoped-release-delta.json")
    parser.add_argument("--full-release", type=Path, default=DEFAULT_FULL_RELEASE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--full-release-rehearsal", type=Path, default=DEFAULT_FULL_RELEASE_REHEARSAL)
    parser.add_argument("--evaluation", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--remote-source-gate", type=Path, default=DEFAULT_REMOTE_SOURCE_GATE)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_completion_audit(
        candidate=_read_optional(args.candidate),
        preview=_read_optional(args.preview),
        attestation=_read_optional(args.attestation),
        release_delta=_read_optional(args.release_delta),
        full_release=_read_optional(args.full_release) if args.full_release else None,
        golden=_read_optional(args.golden) if args.golden else None,
        full_release_rehearsal=_read_optional(args.full_release_rehearsal),
        evaluation=_read_optional(args.evaluation),
        remote_source_gate=_read_optional(args.remote_source_gate),
        workbook_exists=args.workbook.is_file() and args.workbook.stat().st_size > 0,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "incomplete_requirements": result["incomplete_requirements"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
