"""Build the current privacy-safe Feature 006 completion assessment."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "feature006"
DEFAULT_OUTPUT = REPORT_DIR / "form-completion-final.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _blocked_identity_records(campaign_gaps: dict[str, Any]) -> list[dict[str, Any]]:
    """Collapse occurrence-level gaps into privacy-safe identity-level gaps."""
    blocked: dict[str, dict[str, Any]] = {}
    for item in campaign_gaps.get("records") or []:
        identity_id = str(item.get("requirement_identity_id") or "").strip()
        if not identity_id:
            continue
        record = blocked.setdefault(
            identity_id,
            {
                "requirement_identity_id": identity_id,
                "reason_code": str(
                    item.get("reason_code") or "UNCLASSIFIED_GAP"
                ),
                "procedure_ids": [],
            },
        )
        procedure_id = str(item.get("procedure_id") or "").strip()
        if procedure_id and procedure_id not in record["procedure_ids"]:
            record["procedure_ids"].append(procedure_id)
    return [
        {
            **item,
            "procedure_ids": sorted(item["procedure_ids"]),
        }
        for item in sorted(blocked.values(), key=lambda value: value["requirement_identity_id"])
    ]


def build_current_campaign_report(
    *,
    campaign: dict[str, Any],
    campaign_gaps: dict[str, Any],
    review_batches: dict[str, Any],
    five_domain: dict[str, Any],
    form_lookup: dict[str, Any],
    form_role: dict[str, Any],
    release_gate: dict[str, Any],
    artifact_checksums: dict[str, str],
) -> dict[str, Any]:
    """Build the final report from the checksum-bound current campaign run.

    The legacy probe integration remains supported by :func:`build_report`,
    while this path prevents a later campaign from being represented by stale
    counts from an earlier probe run.
    """
    counts = campaign.get("completion_counts") or campaign.get("counts") or {}
    target = int(counts.get("target_identities") or 0)
    runtime = int(counts.get("approved_runtime_identities") or 0)
    ready = int(
        counts.get("ready_for_human_attestation_identities")
        or counts.get("ready_for_attestation_identities")
        or 0
    )
    terminal = int(counts.get("terminal_gap_identities") or 0)
    if target != runtime + ready + terminal:
        raise ValueError("FEATURE006_CURRENT_COUNT_MISMATCH")

    blocked = _blocked_identity_records(campaign_gaps)
    if len(blocked) != terminal:
        raise ValueError("FEATURE006_CURRENT_TERMINAL_COUNT_MISMATCH")

    batch_sizes = [
        int(batch.get("identity_count") or 0)
        for batch in review_batches.get("batches") or []
    ]
    if sum(batch_sizes) != ready or any(size < 1 or size > 25 for size in batch_sizes):
        raise ValueError("FEATURE006_CURRENT_BATCH_COUNT_MISMATCH")

    ready_ids = sorted(
        {
            str(record.get("requirement_identity_id") or "")
            for batch in review_batches.get("batches") or []
            for record in batch.get("records") or []
            if record.get("requirement_identity_id")
        }
    )
    if len(ready_ids) != ready:
        raise ValueError("FEATURE006_CURRENT_READY_IDENTITY_MISMATCH")

    reason_counts = dict(
        sorted(
            Counter(item["reason_code"] for item in blocked).items()
        )
    )
    five_summary = five_domain.get("summary") or {}
    return {
        "schema_version": "feature006-form-completion-final-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": campaign.get("legal_as_of"),
        "status": "BLOCKED_HUMAN_ATTESTATION_AND_RESEARCH",
        "release_verdict": "NO_GO",
        "counts": {
            "target_identities": target,
            "runtime_approved_identities": runtime,
            "ready_for_human_attestation_identities": ready,
            "terminal_gap_identities": terminal,
            "non_serving_identities": target - runtime,
            "unaccounted_pending_identities": int(
                counts.get("unaccounted_pending_identities") or 0
            ),
            "review_batches": len(batch_sizes),
            "review_batch_identity_counts": batch_sizes,
        },
        "newly_verified": {
            "identity_count": ready,
            "candidate_binding_count": int(
                (campaign.get("counts") or {}).get("ready_for_attestation") or 0
            ),
            "requirement_identity_ids": ready_ids,
            "file_format_counts": {},
            "file_openability_passed": int(
                (campaign.get("counts") or {}).get("source_resolved_occurrences") or 0
            ),
            "file_openability_failed": 0,
        },
        "blocked_reason_counts": reason_counts,
        "blocked_identities": blocked,
        "quality": {
            "five_domain_verdict": five_summary.get("verdict"),
            "five_domain_minimum_score": five_summary.get("minimum_score"),
            "five_domain_average_score": five_summary.get("average_score"),
            "five_domain_median_seconds": five_summary.get("median_latency_seconds"),
            "five_domain_p95_seconds": five_summary.get("p95_latency_seconds"),
            "five_domain_critical_errors": five_summary.get("critical_error_count"),
            "form_lookup_cases": form_lookup.get("case_count"),
            "form_lookup_exact_recall": form_lookup.get("exact_form_recall"),
            "wrong_form_count": form_lookup.get("wrong_form_count"),
            "pending_form_exposure_count": form_lookup.get("pending_form_exposure_count"),
            "expired_form_exposure_count": form_lookup.get("expired_form_exposure_count"),
            "role_leakage_count": form_lookup.get("role_leakage_count"),
            "form_role_matrix_pass_count": form_role.get("pass_count"),
            "form_role_matrix_case_count": form_role.get("case_count"),
            "form_role_matrix_p95_ms": form_role.get("lookup_p95_ms"),
        },
        "release_gate": {
            "status": release_gate.get("status"),
            "passed": release_gate.get("passed"),
            "failed": release_gate.get("failed"),
            "blocked": release_gate.get("blocked"),
        },
        "human_decisions": {"legal_reviewer": "PENDING", "release_owner": "PENDING"},
        "artifact_checksums": dict(sorted(artifact_checksums.items())),
        "campaign_run_id": campaign.get("run_id"),
        "campaign_status": campaign.get("status"),
        "candidate_only": True,
        "automated_approval": False,
        "runtime_catalog_mutated": False,
        "feature_flag_enabled": False,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }


def build_report(
    *,
    integration: dict[str, Any],
    research_queue: dict[str, Any],
    campaign_gaps: dict[str, Any],
    verification: dict[str, Any],
    five_domain: dict[str, Any],
    form_lookup: dict[str, Any],
    form_role: dict[str, Any],
    release_gate: dict[str, Any],
    artifact_checksums: dict[str, str],
) -> dict[str, Any]:
    verified_records = [
        item
        for item in research_queue.get("records") or []
        if item.get("research_action")
        == "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION"
    ]
    blocked_by_identity: dict[str, dict[str, Any]] = {}
    for item in campaign_gaps.get("records") or []:
        identity_id = str(item.get("requirement_identity_id") or "").strip()
        if not identity_id:
            continue
        record = blocked_by_identity.setdefault(
            identity_id,
            {
                "requirement_identity_id": identity_id,
                "reason_code": str(item.get("reason_code") or "UNCLASSIFIED_GAP"),
                "procedure_ids": [],
            },
        )
        procedure_id = str(item.get("procedure_id") or "").strip()
        if procedure_id and procedure_id not in record["procedure_ids"]:
            record["procedure_ids"].append(procedure_id)
    unresolved_records = [
        {
            **item,
            "procedure_ids": sorted(item["procedure_ids"]),
        }
        for item in sorted(
            blocked_by_identity.values(),
            key=lambda value: value["requirement_identity_id"],
        )
    ]
    reason_counts = dict(
        sorted(
            Counter(
                str(item.get("reason_code") or "UNCLASSIFIED_GAP")
                for item in unresolved_records
            ).items()
        )
    )
    target = int(integration["target_identities"])
    runtime = int(integration["runtime_approved_identities"])
    ready = int(integration["ready_for_human_attestation_identities"])
    terminal = int(integration["terminal_gap_identities"])
    if target != runtime + ready + terminal:
        raise ValueError("FEATURE006_FINAL_COUNT_MISMATCH")
    if len(verified_records) != int(integration["supplemental_identity_count"]):
        raise ValueError("FEATURE006_FINAL_SUPPLEMENTAL_COUNT_MISMATCH")
    if len(unresolved_records) != terminal:
        raise ValueError("FEATURE006_FINAL_TERMINAL_COUNT_MISMATCH")
    if (
        verification.get("technical_pass") is not True
        or verification.get("passed_count") != len(verified_records)
    ):
        raise ValueError("FEATURE006_FINAL_PROBE_VERIFICATION_MISMATCH")

    five_summary = five_domain.get("summary") or {}
    return {
        "schema_version": "feature006-form-completion-final-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": five_domain.get("legal_as_of"),
        "status": "BLOCKED_HUMAN_ATTESTATION_AND_RESEARCH",
        "release_verdict": "NO_GO",
        "counts": {
            "target_identities": target,
            "runtime_approved_identities": runtime,
            "ready_for_human_attestation_identities": ready,
            "terminal_gap_identities": terminal,
            "non_serving_identities": target - runtime,
            "unaccounted_pending_identities": int(
                integration["unaccounted_pending_identities"]
            ),
            "review_batches": int(integration["review_batch_count"]),
            "review_batch_identity_counts": list(
                integration["review_batch_identity_counts"]
            ),
        },
        "newly_verified": {
            "identity_count": len(verified_records),
            "candidate_binding_count": int(
                integration["supplemental_candidate_binding_count"]
            ),
            "requirement_identity_ids": sorted(
                str(item.get("requirement_identity_id") or "")
                for item in verified_records
            ),
            "file_format_counts": dict(
                verification.get("file_format_counts") or {}
            ),
            "file_openability_passed": int(
                verification.get("passed_count") or 0
            ),
            "file_openability_failed": int(
                verification.get("failed_count") or 0
            ),
        },
        "blocked_reason_counts": reason_counts,
        "blocked_identities": unresolved_records,
        "quality": {
            "five_domain_verdict": five_summary.get("verdict"),
            "five_domain_minimum_score": five_summary.get("minimum_score"),
            "five_domain_average_score": five_summary.get("average_score"),
            "five_domain_median_seconds": five_summary.get(
                "median_latency_seconds"
            ),
            "five_domain_p95_seconds": five_summary.get(
                "p95_latency_seconds"
            ),
            "five_domain_critical_errors": five_summary.get(
                "critical_error_count"
            ),
            "form_lookup_cases": form_lookup.get("case_count"),
            "form_lookup_exact_recall": form_lookup.get("exact_form_recall"),
            "wrong_form_count": form_lookup.get("wrong_form_count"),
            "pending_form_exposure_count": form_lookup.get(
                "pending_form_exposure_count"
            ),
            "expired_form_exposure_count": form_lookup.get(
                "expired_form_exposure_count"
            ),
            "role_leakage_count": form_lookup.get("role_leakage_count"),
            "form_role_matrix_pass_count": form_role.get("pass_count"),
            "form_role_matrix_case_count": form_role.get("case_count"),
            "form_role_matrix_p95_ms": form_role.get("lookup_p95_ms"),
        },
        "release_gate": {
            "status": release_gate.get("status"),
            "passed": release_gate.get("passed"),
            "failed": release_gate.get("failed"),
            "blocked": release_gate.get("blocked"),
        },
        "human_decisions": {
            "legal_reviewer": "PENDING",
            "release_owner": "PENDING",
        },
        "artifact_checksums": dict(sorted(artifact_checksums.items())),
        "candidate_only": True,
        "automated_approval": False,
        "runtime_catalog_mutated": False,
        "feature_flag_enabled": False,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    paths = {
        "probe_campaign_integration": (
            REPORT_DIR / "probe-campaign-integration.json"
        ),
        "reconciled_research_queue": (
            REPORT_DIR / "form-gap-research-queue-probes-reconciled.json"
        ),
        "campaign_gaps": (
            ROOT
            / "data/form_resolution_campaign/runs/20260730083601-9f7dba14/gaps.json"
        ),
        "probe_candidate_verification": (
            REPORT_DIR / "reconciled-probe-candidate-verification.json"
        ),
        "five_domain_quality": (
            REPORT_DIR / "five-domain-chatbot-quality-final-20260730-rerun7.json"
        ),
        "form_lookup_quality": REPORT_DIR / "form-lookup-quality.json",
        "form_role_matrix": REPORT_DIR / "form-role-api-matrix.json",
        "release_gate": (
            ROOT
            / "reports/feature005"
            / "post-attestation-release-gate-20260730-121132.json"
        ),
    }
    current_campaign_path = REPORT_DIR / "form-completion-campaign.json"
    latest_pointer_path = ROOT / "data/form_resolution_campaign/latest.json"
    latest_pointer = _read(latest_pointer_path) if latest_pointer_path.is_file() else {}
    current_run_id = str(latest_pointer.get("run_id") or "").strip()
    current_run_dir = (
        ROOT / "data/form_resolution_campaign/runs" / current_run_id
        if current_run_id
        else Path()
    )
    current_paths = {
        "campaign": current_campaign_path,
        "campaign_gaps": current_run_dir / "gaps.json",
        "review_batches": current_run_dir / "review-batches.json",
        "requirement_manifest": (
            REPORT_DIR / "form-requirement-manifest-2026-07-30.json"
        ),
        "source_snapshot": (
            ROOT / "data/source_cache/form_requirements/dvc-form-requirements-2026-07-30.json"
        ),
    }
    current_campaign = (
        _read(current_campaign_path) if current_campaign_path.is_file() else {}
    )
    current_ready = current_campaign.get("status") == "READY_FOR_HUMAN_ATTESTATION"
    current_complete = all(path.is_file() for path in current_paths.values())
    current_checksum_bound = current_complete and (
        current_campaign.get("manifest_sha256")
        == _sha256(current_paths["requirement_manifest"])
        and current_campaign.get("source_snapshot_sha256")
        == _sha256(current_paths["source_snapshot"])
    )
    if current_ready and current_checksum_bound:
        report = build_current_campaign_report(
            campaign=current_campaign,
            campaign_gaps=_read(current_paths["campaign_gaps"]),
            review_batches=_read(current_paths["review_batches"]),
            five_domain=_read(paths["five_domain_quality"]),
            form_lookup=_read(paths["form_lookup_quality"]),
            form_role=_read(paths["form_role_matrix"]),
            release_gate=_read(paths["release_gate"]),
            artifact_checksums={
                **{key: _sha256(path) for key, path in paths.items()},
                **{key: _sha256(path) for key, path in current_paths.items()},
            },
        )
    else:
        report = build_report(
            integration=_read(paths["probe_campaign_integration"]),
            research_queue=_read(paths["reconciled_research_queue"]),
            campaign_gaps=_read(paths["campaign_gaps"]),
            verification=_read(paths["probe_candidate_verification"]),
            five_domain=_read(paths["five_domain_quality"]),
            form_lookup=_read(paths["form_lookup_quality"]),
            form_role=_read(paths["form_role_matrix"]),
            release_gate=_read(paths["release_gate"]),
            artifact_checksums={
                key: _sha256(path) for key, path in paths.items()
            },
        )
    _write_atomic(args.output.resolve(), report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "release_verdict": report["release_verdict"],
                "counts": report["counts"],
                "newly_verified_identity_count": (
                    report["newly_verified"]["identity_count"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
