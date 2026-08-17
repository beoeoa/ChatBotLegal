#!/usr/bin/env python3
"""Build and verify checksum-bound Feature 006 release manifests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "feature006"
DATA_MANIFEST_PATH = REPORT_DIR / "data-readiness-manifest.json"
UNIFIED_MANIFEST_PATH = REPORT_DIR / "unified-release-manifest.json"

ARTIFACTS: dict[str, tuple[str, ...]] = {
    "forms": (
        "reports/feature006/form-requirement-manifest-2026-07-29.json",
        "notebook_data/forms/canonical_forms_catalog_v1.json",
        "notebook_data/forms/procedure_form_bindings_v1.json",
        "notebook_data/forms/canonical_form_checksums_v1.json",
        "notebook_data/forms/haiphong_official_form_index.json",
        "notebook_data/forms/official_forms_candidates_classified.json",
        "release-data/notebook_data/forms/runtime_form_package_manifest.json",
    ),
    "procedures": (
        "notebook_data/forms/canonical_procedures_v1.json",
        "notebook_data/forms/canonical_procedure_sources_v1.json",
        "notebook_data/forms/three_tier_procedure_catalog_v1.json",
        "reports/feature006/procedure-form-coverage-2026-07-29.csv",
    ),
    "legal_sources": (
        "data/source_cache/form_requirements/dvc-form-requirements-2026-07-29.json",
        "data/form_resolution_campaign/runs/20260730083601-9f7dba14/source-attempts.json",
        "data/form_resolution_campaign/runs/20260730083601-9f7dba14/effectivity-graph.json",
        "data/form_resolution_campaign/runs/20260730083601-9f7dba14/gaps.json",
        "reports/feature006/form-gap-research-queue.json",
        "reports/feature006/form-gap-research-queue-probes-reconciled.json",
        "reports/feature006/reconciled-probe-candidate-verification.json",
    ),
    "datasets": (
        "notebook_data/legal-golden-set.json",
        "reports/feature005/step2-data-gap-20260724/expected-sources.jsonl",
        "reports/feature005/goal-20260728/dataset-1000-grounded-v4.json",
        "reports/feature005/goal-20260728/dataset-1000-expected-v4.jsonl",
        "reports/feature006/form-lookup-release-dataset.json",
    ),
    "indexes": (
        "release-data/manifest.sha256.json",
        "data/pilot/ask_quality_manifest.json",
    ),
    "code": (
        "scripts/legal_search_server.py",
        "scripts/run_post_attestation_release_gates.py",
        "scripts/benchmark_feature006_five_domains.py",
        "scripts/backup_form_completion_state.py",
        "scripts/build_feature006_release_manifests.py",
        "scripts/scan_section_grounding_artifacts.py",
        "scripts/run_form_role_api_matrix.py",
        "scripts/verify_reconciled_probe_candidates.py",
        "scripts/reconcile_verified_probe_campaign.py",
        "scripts/build_feature006_form_completion_final.py",
        "api/routers/legal_search.py",
        "api/routers/ward_procedures.py",
        "api/legal_section_grounding.py",
        "frontend/src/app/(dashboard)/search/page.tsx",
        "frontend/src/components/search/StreamingResponse.tsx",
        "frontend/src/app/(dashboard)/legal-import/page.tsx",
        "tests/fixtures/feature006_form_lookup_cases.py",
        "tests/test_feature006_form_lookup_traps.py",
        "tests/test_form_role_api_matrix.py",
        "tests/test_verify_reconciled_probe_candidates.py",
        "tests/test_reconcile_verified_probe_campaign.py",
        "tests/test_feature006_form_completion_final.py",
    ),
    "attestations": (
        "notebook_data/forms/legal_review_attestations_v1.json",
        "data/form_resolution_campaign/status_v1.json",
        "data/form_resolution_campaign/runs/20260730083601-9f7dba14/review-batches.json",
        "data/form_resolution_campaign/runs/20260730083601-9f7dba14/report.json",
        "reports/feature006/probe-campaign-integration.json",
        "reports/feature006/form-completion-final.json",
        "reports/feature006/form-completion-batch-01.json",
    ),
    "release_evidence": (
        "reports/feature006/form-completion-reconciliation-20260730.json",
        "reports/feature006/form-lookup-quality.json",
        "reports/feature006/five-domain-chatbot-quality-final-20260730-rerun7.json",
        "reports/feature006/form-role-api-matrix.json",
        "reports/feature006/reconciled-probe-candidate-verification.json",
        "reports/feature006/probe-campaign-integration.json",
        "reports/feature006/form-completion-final.json",
        "reports/feature005/post-attestation-release-gate-20260730-175711.json",
        "reports/feature005/post-attestation-retrieval-167-20260730-175711.json",
        "reports/feature005/post-attestation-retrieval-1000-20260730-175711.json",
        "backups/form_completion/release-audit-20260730-current/backup-manifest.json",
        "backups/form_completion/release-audit-20260730-current/restore-check/restore-verification.json",
    ),
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def artifact_record(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if not resolved.is_file() or resolved_root not in resolved.parents:
        raise ValueError(f"FEATURE006_ARTIFACT_INVALID:{path}")
    return {
        "artifact": resolved.relative_to(resolved_root).as_posix(),
        "sha256": _sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def verify_artifact_records(
    categories: dict[str, Iterable[dict[str, Any]]],
    *,
    root: Path = ROOT,
) -> list[str]:
    failures: list[str] = []
    resolved_root = root.resolve()
    for records in categories.values():
        for record in records:
            relative = str(record.get("artifact") or "")
            candidate = (resolved_root / relative).resolve()
            if (
                not relative
                or Path(relative).is_absolute()
                or resolved_root not in candidate.parents
                or not candidate.is_file()
            ):
                failures.append(f"MISSING:{relative}")
                continue
            if _sha256(candidate) != str(record.get("sha256") or ""):
                failures.append(f"CHECKSUM_MISMATCH:{relative}")
    return failures


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _official_index_drift() -> dict[str, Any]:
    prior_path = (
        ROOT
        / "backups"
        / "form_completion"
        / "goal-20260730-reconcile-51"
        / "haiphong_official_form_index.json"
    )
    current_path = (
        ROOT
        / "notebook_data"
        / "forms"
        / "haiphong_official_form_index.json"
    )
    prior = _read(prior_path)
    current = _read(current_path)
    prior_ids = {
        str(item.get("id") or "")
        for item in prior.get("forms") or []
        if item.get("id")
    }
    current_ids = {
        str(item.get("id") or "")
        for item in current.get("forms") or []
        if item.get("id")
    }
    return {
        "status": "DRIFT_RECORDED",
        "prior_sha256": _sha256(prior_path),
        "current_sha256": _sha256(current_path),
        "prior_reference_count": len(prior.get("forms") or []),
        "current_reference_count": len(current.get("forms") or []),
        "added_reference_ids": len(current_ids - prior_ids),
        "removed_reference_ids": len(prior_ids - current_ids),
        "runtime_ready_delta": 0,
        "requires_release_rebind": True,
    }


def build_data_manifest() -> dict[str, Any]:
    campaign = _read(
        ROOT
        / "data"
        / "form_resolution_campaign"
        / "runs"
        / "20260730083601-9f7dba14"
        / "report.json"
    )
    integration = _read(
        REPORT_DIR / "probe-campaign-integration.json"
    )
    restore = _read(
        ROOT
        / "backups"
        / "form_completion"
        / "release-audit-20260730-current"
        / "restore-check"
        / "restore-verification.json"
    )
    five_domain = _read(
        REPORT_DIR / "five-domain-chatbot-quality-final-20260730-rerun7.json"
    )
    release_gate = _read(
        ROOT
        / "reports"
        / "feature005"
        / "post-attestation-release-gate-20260730-175711.json"
    )
    categories = {
        category: [
            artifact_record(ROOT / relative)
            for relative in relatives
        ]
        for category, relatives in ARTIFACTS.items()
    }
    checksum_failures = verify_artifact_records(categories)
    if checksum_failures:
        raise ValueError(
            f"FEATURE006_CHECKSUM_BUILD_FAILED:{checksum_failures!r}"
        )
    return {
        "schema_version": "feature006-data-readiness-manifest-v1",
        "generated_at": _utcnow(),
        "legal_as_of": campaign.get("legal_as_of"),
        "status": "BLOCKED_DATA",
        "counts": {
            "target_identities": integration["target_identities"],
            "runtime_approved_identities": (
                integration["runtime_approved_identities"]
            ),
            "non_serving_identities": (
                integration["target_identities"]
                - integration["runtime_approved_identities"]
            ),
            "ready_for_human_attestation_identities": (
                integration["ready_for_human_attestation_identities"]
            ),
            "terminal_gap_identities": integration["terminal_gap_identities"],
            "unaccounted_pending_identities": (
                integration["unaccounted_pending_identities"]
            ),
            "review_batches": integration["review_batch_count"],
            "review_batch_identity_counts": (
                integration["review_batch_identity_counts"]
            ),
        },
        "artifacts": categories,
        "git_head": _git_head(),
        "active_collection": campaign.get("active_collection"),
        "model_fingerprint": (
            "ac8ee06405cf0de65292314f1b173006faaf168d17a447faef0b0fc533ffdca8"
        ),
        "restore_status": restore.get("status"),
        "five_domain_status": (
            (five_domain.get("summary") or {}).get("verdict")
        ),
        "release_gate_status": release_gate.get("status"),
        "official_index_drift": _official_index_drift(),
        "automated_approval_count": 0,
        "feature_flag_enabled": False,
        "checksum_verification": "PASS",
        "release_blockers": [
            f"{integration['target_identities'] - integration['runtime_approved_identities']} form identities remain non-serving.",
            f"{integration['ready_for_human_attestation_identities']} identities require authenticated human legal attestation.",
            f"{integration['terminal_gap_identities']} identities remain explicit official-source/effectivity gaps.",
            "Golden-167 retrieval p95 remains above 3 seconds.",
            "The current campaign has no checksum-bound human attestation for the pending review batches.",
            "Official-form index drift is recorded and bound to this manifest.",
        ],
    }


def build_unified_manifest(
    *,
    data_manifest_sha256: str,
    data_status: str,
    release_gate_status: str,
    five_domain_status: str,
    restore_status: str,
    feature_flag_enabled: bool,
    legal_reviewer_decision: str,
    release_owner_decision: str,
) -> dict[str, Any]:
    go = all(
        (
            data_status == "PASS",
            release_gate_status == "PASS",
            five_domain_status == "PASS",
            restore_status == "PASS",
            legal_reviewer_decision == "GO",
            release_owner_decision == "GO",
        )
    )
    return {
        "schema_version": "feature006-unified-release-manifest-v1",
        "generated_at": _utcnow(),
        "release_verdict": "GO" if go else "NO_GO",
        "rollout_stage": 1 if go else 0,
        "feature_flag_enabled": bool(feature_flag_enabled),
        "data_manifest_sha256": data_manifest_sha256,
        "gates": {
            "data": data_status,
            "technical_release": release_gate_status,
            "five_domain_quality": five_domain_status,
            "restore": restore_status,
            "security": "BLOCKED_UNVERIFIED",
            "soak": "NOT_RUN",
        },
        "human_decisions": {
            "legal_reviewer": legal_reviewer_decision,
            "release_owner": release_owner_decision,
        },
        "rollout_policy": (
            "Admin 24h -> Officer 24h -> Citizen 72h; any red gate returns stage 0."
        ),
    }


def write_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    data = build_data_manifest()
    _write_atomic(DATA_MANIFEST_PATH, data)
    unified = build_unified_manifest(
        data_manifest_sha256=_sha256(DATA_MANIFEST_PATH),
        data_status=str(data["status"]),
        release_gate_status=str(data["release_gate_status"]),
        five_domain_status=str(data["five_domain_status"]),
        restore_status=str(data["restore_status"]),
        feature_flag_enabled=False,
        legal_reviewer_decision="PENDING",
        release_owner_decision="PENDING",
    )
    _write_atomic(UNIFIED_MANIFEST_PATH, unified)
    return data, unified


def verify_manifests() -> dict[str, Any]:
    data = _read(DATA_MANIFEST_PATH)
    unified = _read(UNIFIED_MANIFEST_PATH)
    failures = verify_artifact_records(data.get("artifacts") or {})
    if _sha256(DATA_MANIFEST_PATH) != unified.get("data_manifest_sha256"):
        failures.append("DATA_MANIFEST_CHECKSUM_MISMATCH")
    if (
        unified.get("release_verdict") != "GO"
        and (
            unified.get("rollout_stage") != 0
            or unified.get("feature_flag_enabled") is not False
        )
    ):
        failures.append("NO_GO_MUST_REMAIN_STAGE_ZERO")
    return {
        "status": "PASS" if not failures else "FAIL",
        "artifact_count": sum(
            len(records)
            for records in (data.get("artifacts") or {}).values()
        ),
        "failures": failures,
        "release_verdict": unified.get("release_verdict"),
        "rollout_stage": unified.get("rollout_stage"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = verify_manifests()
    else:
        data, unified = write_manifests()
        result = {
            "status": "PASS",
            "data_status": data["status"],
            "release_verdict": unified["release_verdict"],
            "rollout_stage": unified["rollout_stage"],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
