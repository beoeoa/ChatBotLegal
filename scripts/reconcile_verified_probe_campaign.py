"""Create a new candidate-only campaign from verified supplemental probes.

The source campaign remains immutable.  The command copies its evidence into a
new run, adds only checksum-verified non-serving candidates, rebuilds immutable
review batches, and backs up the global candidate queue before an atomic merge.
It never writes the runtime form catalog, bindings, attestations or feature
flag.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_resolution_campaign import build_review_batches
from api.form_resolution_registry import validate_review_ready_candidate
from api.form_source_resolution import merge_pending_records
from scripts.run_form_resolution_campaign import _candidate_with_identity


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


def _new_run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )


def _queue_identity_evidence(
    queue: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("requirement_identity_id") or ""): dict(
            item.get("technical_evidence") or {}
        )
        for item in queue.get("records") or []
        if (
            item.get("research_action")
            == "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION"
            and str(item.get("requirement_identity_id") or "")
        )
    }


def load_verified_probe_candidates(
    *,
    queue_path: Path,
    verification_path: Path,
    probe_dirs: Iterable[Path],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    queue = _read(queue_path)
    verification = _read(verification_path)
    if (
        verification.get("technical_pass") is not True
        or verification.get("failed_count") != 0
        or verification.get("queue_sha256") != _sha256(queue_path)
    ):
        raise ValueError("PROBE_VERIFICATION_NOT_CURRENT_OR_PASSING")
    identity_evidence = _queue_identity_evidence(queue)
    passing_ids = {
        str(item.get("requirement_identity_id") or "")
        for item in verification.get("checks") or []
        if item.get("status") == "PASS"
    }
    if passing_ids != set(identity_evidence):
        raise ValueError("PROBE_VERIFICATION_IDENTITY_SET_MISMATCH")

    evidence_by_sha = {
        str(evidence.get("sha256") or "").casefold(): (identity_id, evidence)
        for identity_id, evidence in identity_evidence.items()
    }
    records: list[dict[str, Any]] = []
    groups_by_id: dict[str, dict[str, Any]] = {}
    seen_candidate_keys: set[tuple[str, str]] = set()
    for probe_dir in probe_dirs:
        groups_payload = _read(probe_dir / "code-groups.json")
        resolution = _read(probe_dir / "code-resolution.json")
        resolved_by_id = {
            str(item.get("group_id") or ""): dict(item)
            for item in resolution.get("resolved_groups") or []
            if isinstance(item, dict) and item.get("group_id")
        }
        for item in groups_payload.get("groups") or []:
            if not isinstance(item, dict) or not item.get("group_id"):
                continue
            group_id = str(item["group_id"])
            group = {
                **dict(item),
                **resolved_by_id.get(group_id, {}),
                "procedure_metadata": item.get("procedure_metadata") or {},
                "occurrence_ids": item.get("occurrence_ids") or [],
            }
            group["canonical_identity_key"] = (
                group.get("canonical_identity_key") or group_id
            )
            groups_by_id[group_id] = group
        for raw in resolution.get("pending_records") or []:
            if not isinstance(raw, dict):
                continue
            sha = str(raw.get("sha256") or "").casefold()
            matched = evidence_by_sha.get(sha)
            if matched is None:
                continue
            identity_id, evidence = matched
            candidate_id = str(raw.get("id") or "")
            procedure_id = str(raw.get("procedure_id") or "")
            key = (candidate_id, procedure_id)
            if not candidate_id or key in seen_candidate_keys:
                continue
            group = groups_by_id.get(
                str(raw.get("three_tier_group_id") or ""),
                {},
            )
            prepared = _candidate_with_identity(raw, group)
            prepared["canonical_identity_key"] = (
                prepared.get("canonical_identity_key")
                or raw.get("three_tier_group_id")
            )
            prepared["requirement_identity_id"] = identity_id
            prepared["requirement_identity_ids"] = [identity_id]
            prepared["candidate_id"] = candidate_id
            prepared["canonical_form_id"] = (
                raw.get("proposed_canonical_form_id")
            )
            prepared["approved"] = False
            prepared["is_approved"] = False
            prepared["runtime_eligible"] = False
            prepared["automated_approval"] = False
            prepared["human_attestation_required"] = True
            prepared["effectivity_source_url"] = (
                raw.get("effectivity_source_url")
                or evidence.get("effectivity_source_url")
                or (
                    evidence.get("source_page_url")
                    if evidence.get("effectivity_reason_code")
                    == "CURRENT_AS_OF_DATE"
                    else None
                )
            )
            validation = validate_review_ready_candidate(prepared)
            if validation["eligible"] is not True:
                raise ValueError(
                    "PROBE_CANDIDATE_REVIEW_GATE_FAILED:"
                    + ",".join(validation["reason_codes"])
                )
            prepared["hard_gate_reason_codes"] = [
                "HUMAN_LEGAL_REVIEW_REQUIRED"
            ]
            records.append(prepared)
            seen_candidate_keys.add(key)

    covered_ids = {
        str(item.get("requirement_identity_id") or "") for item in records
    }
    if covered_ids != set(identity_evidence):
        raise ValueError("PROBE_CANDIDATE_COVERAGE_INCOMPLETE")
    return records, identity_evidence


def reconcile_campaign(
    *,
    source_run_dir: Path,
    destination_run_dir: Path,
    queue_path: Path,
    verification_path: Path,
    probe_dirs: Iterable[Path],
    candidate_store_path: Path,
    candidate_backup_dir: Path,
    campaign_status_path: Path,
    latest_path: Path,
    report_output_path: Path,
) -> dict[str, Any]:
    if destination_run_dir.exists():
        raise ValueError("DESTINATION_CAMPAIGN_RUN_ALREADY_EXISTS")
    candidates, identity_evidence = load_verified_probe_candidates(
        queue_path=queue_path,
        verification_path=verification_path,
        probe_dirs=probe_dirs,
    )
    supplemental_ids = set(identity_evidence)
    source_report = _read(source_run_dir / "report.json")
    manifest_sha = str(source_report.get("manifest_sha256") or "")
    source_snapshot_sha = str(
        source_report.get("source_snapshot_sha256") or ""
    )
    if len(manifest_sha) != 64 or len(source_snapshot_sha) != 64:
        raise ValueError("SOURCE_CAMPAIGN_PROVENANCE_MISSING")

    source_batches = _read(source_run_dir / "review-batches.json")
    existing_records = [
        dict(record)
        for batch in source_batches.get("batches") or []
        if isinstance(batch, dict)
        for record in batch.get("records") or []
        if isinstance(record, dict)
    ]
    existing_ids = {
        str(value)
        for item in existing_records
        for value in (
            item.get("requirement_identity_ids")
            or [item.get("requirement_identity_id")]
        )
        if str(value or "")
    }
    if supplemental_ids & existing_ids:
        raise ValueError("SUPPLEMENTAL_IDENTITY_ALREADY_IN_REVIEW_BATCH")
    combined = [*existing_records, *candidates]
    batches = build_review_batches(
        combined,
        manifest_sha256=manifest_sha,
        source_snapshot_sha256=source_snapshot_sha,
        max_batch_size=25,
    )
    combined_ids = {
        str(value)
        for item in combined
        for value in (
            item.get("requirement_identity_ids")
            or [item.get("requirement_identity_id")]
        )
        if str(value or "")
    }

    candidate_store = _read(candidate_store_path)
    store_records = [
        dict(item)
        for item in candidate_store.get("records") or []
        if isinstance(item, dict)
    ]
    incoming_by_id = {
        str(item.get("id") or ""): item
        for item in candidates
        if str(item.get("id") or "")
    }
    existing_by_id = {
        str(item.get("id") or ""): item
        for item in store_records
        if str(item.get("id") or "")
    }
    to_create: list[dict[str, Any]] = []
    preserved = 0
    for candidate_id, incoming in incoming_by_id.items():
        existing = existing_by_id.get(candidate_id)
        if existing is None:
            to_create.append(incoming)
            continue
        if (
            str(existing.get("sha256") or "").casefold()
            != str(incoming.get("sha256") or "").casefold()
            or existing.get("approved") is True
            or existing.get("runtime_eligible") is True
        ):
            raise ValueError("EXISTING_PROBE_CANDIDATE_STATE_CONFLICT")
        preserved += 1
    merged = merge_pending_records(store_records, to_create)

    candidate_backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = candidate_backup_dir / candidate_store_path.name
    shutil.copy2(candidate_store_path, backup_path)
    _write_atomic(
        candidate_backup_dir / "backup-manifest.json",
        {
            "schema_version": "feature006-probe-candidate-backup-v1",
            "created_at": _utcnow(),
            "source_artifact": candidate_store_path.name,
            "sha256": _sha256(backup_path),
            "size_bytes": backup_path.stat().st_size,
            "runtime_catalog_included": False,
            "legal_corpus_included": False,
            "vector_collection_included": False,
        },
    )
    summary = dict(candidate_store.get("summary") or {})
    summary.update(
        {
            "feature006_reconciled_probe_identity_count": len(
                supplemental_ids
            ),
            "feature006_reconciled_probe_candidate_count": len(candidates),
            "feature006_reconciled_probe_created_count": len(to_create),
            "feature006_reconciled_probe_preserved_count": preserved,
            "feature006_reconciled_probe_auto_approved": 0,
        }
    )
    _write_atomic(
        candidate_store_path,
        {
            **candidate_store,
            "summary": summary,
            "records": merged["records"],
        },
    )

    shutil.copytree(source_run_dir, destination_run_dir)
    gaps_payload = _read(destination_run_dir / "gaps.json")
    filtered_gaps = [
        item
        for item in gaps_payload.get("records") or []
        if str(item.get("requirement_identity_id") or "")
        not in supplemental_ids
    ]
    _write_atomic(destination_run_dir / "gaps.json", {"records": filtered_gaps})

    attempts = _read(destination_run_dir / "source-attempts.json")
    identity_records = [
        dict(item) for item in attempts.get("identity_records") or []
    ]
    by_identity = {
        str(item.get("requirement_identity_id") or ""): item
        for item in identity_records
    }
    for identity_id in supplemental_ids:
        by_identity[identity_id] = {
            "requirement_identity_id": identity_id,
            "status": "ready_for_human_attestation",
            "reason_code": "TECHNICAL_HARD_GATES_PASS_AFTER_RESEARCH",
            "source_attempt_count": 1,
        }
    attempts["identity_records"] = [
        by_identity[key] for key in sorted(by_identity)
    ]
    attempts["identity_count"] = len(attempts["identity_records"])
    _write_atomic(destination_run_dir / "source-attempts.json", attempts)

    effectivity_graph = _read(destination_run_dir / "effectivity-graph.json")
    nodes = {
        str(item.get("requirement_identity_id") or ""): dict(item)
        for item in effectivity_graph.get("nodes") or []
    }
    for identity_id, evidence in identity_evidence.items():
        nodes[identity_id] = {
            "requirement_identity_id": identity_id,
            "effectivity_status": (
                "technically_verified_pending_legal_review"
            ),
            "reason_code": str(
                evidence.get("effectivity_reason_code")
                or "TECHNICAL_HARD_GATES_PASS_AFTER_RESEARCH"
            ),
            "effectivity_source_url": (
                evidence.get("effectivity_source_url")
                or evidence.get("source_page_url")
            ),
        }
    effectivity_graph["nodes"] = [nodes[key] for key in sorted(nodes)]
    effectivity_graph["automated_legal_decision"] = False
    _write_atomic(
        destination_run_dir / "effectivity-graph.json",
        effectivity_graph,
    )

    report = dict(source_report)
    target = int(
        (source_report.get("completion_counts") or {}).get(
            "target_identities", 0
        )
    )
    approved = int(
        (source_report.get("completion_counts") or {}).get(
            "approved_runtime_identities", 0
        )
    )
    ready = len(combined_ids)
    terminal = target - approved - ready
    completion_counts = {
        "target_identities": target,
        "approved_runtime_identities": approved,
        "pending_identities": target - approved,
        "ready_for_human_attestation_identities": ready,
        "terminal_gap_identities": terminal,
        "unaccounted_pending_identities": 0,
    }
    report.update(
        {
            "run_id": destination_run_dir.name,
            "generated_at": _utcnow(),
            "status": "READY_FOR_HUMAN_ATTESTATION",
            "completion_counts": completion_counts,
            "supplemental_probe_reconciliation": {
                "source_run_id": source_run_dir.name,
                "queue_sha256": _sha256(queue_path),
                "verification_sha256": _sha256(verification_path),
                "identity_count": len(supplemental_ids),
                "candidate_binding_count": len(candidates),
                "candidate_store_created_count": len(to_create),
                "candidate_store_preserved_count": preserved,
                "automated_approval": False,
                "runtime_catalog_mutated": False,
            },
            "gap_reason_counts": dict(
                sorted(
                    Counter(
                        str(item.get("reason_code") or "UNCLASSIFIED_GAP")
                        for item in filtered_gaps
                    ).items()
                )
            ),
            "identity_terminal_reason_counts": dict(
                sorted(
                    Counter(
                        str(item.get("reason_code") or "UNCLASSIFIED_GAP")
                        for item in by_identity.values()
                        if item.get("status") == "terminal_gap"
                    ).items()
                )
            ),
        }
    )
    counts = dict(report.get("counts") or {})
    counts.update(completion_counts)
    counts["review_batches"] = len(batches)
    counts["ready_for_attestation_identities"] = ready
    report["counts"] = counts

    _write_atomic(
        destination_run_dir / "review-batches.json",
        {
            "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": source_snapshot_sha,
            "batches": batches,
        },
    )
    first = batches[0] if batches else {"records": []}
    _write_atomic(
        destination_run_dir / "review-shortlist.json",
        {
            **first,
            "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": source_snapshot_sha,
            "legal_as_of": report.get("legal_as_of"),
            "batch_count": len(batches),
        },
    )
    _write_atomic(destination_run_dir / "report.json", report)
    status = {**report, "stage": "complete"}
    _write_atomic(campaign_status_path, status)
    _write_atomic(
        latest_path,
        {
            "run_id": destination_run_dir.name,
            "manifest": str(destination_run_dir),
        },
    )
    integration_report = {
        "schema_version": "feature006-probe-campaign-integration-v1",
        "generated_at": _utcnow(),
        "source_run_id": source_run_dir.name,
        "destination_run_id": destination_run_dir.name,
        "manifest_sha256": manifest_sha,
        "source_snapshot_sha256": source_snapshot_sha,
        "target_identities": target,
        "runtime_approved_identities": approved,
        "ready_for_human_attestation_identities": ready,
        "terminal_gap_identities": terminal,
        "unaccounted_pending_identities": 0,
        "supplemental_identity_count": len(supplemental_ids),
        "supplemental_candidate_binding_count": len(candidates),
        "review_batch_count": len(batches),
        "review_batch_identity_counts": [
            int(item.get("identity_count") or 0) for item in batches
        ],
        "candidate_store_created_count": len(to_create),
        "candidate_store_preserved_count": preserved,
        "candidate_store_sha256": _sha256(candidate_store_path),
        "candidate_backup_sha256": _sha256(backup_path),
        "automated_approval": False,
        "runtime_catalog_mutated": False,
        "feature_flag_enabled": False,
        "human_attestation_required": True,
        "status": "READY_FOR_HUMAN_ATTESTATION",
    }
    _write_atomic(report_output_path, integration_report)
    return integration_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-dir", type=Path, required=True)
    parser.add_argument("--destination-run-dir", type=Path)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--probe-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--candidate-store",
        type=Path,
        default=(
            ROOT
            / "notebook_data"
            / "forms"
            / "official_forms_candidates_classified.json"
        ),
    )
    parser.add_argument("--candidate-backup-dir", type=Path)
    parser.add_argument(
        "--campaign-status",
        type=Path,
        default=ROOT / "data/form_resolution_campaign/status_v1.json",
    )
    parser.add_argument(
        "--latest",
        type=Path,
        default=ROOT / "data/form_resolution_campaign/latest.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=(
            ROOT / "reports/feature006/probe-campaign-integration.json"
        ),
    )
    args = parser.parse_args()
    run_id = (
        args.destination_run_dir.name
        if args.destination_run_dir
        else _new_run_id()
    )
    destination = (
        args.destination_run_dir
        or ROOT / "data/form_resolution_campaign/runs" / run_id
    )
    backup = (
        args.candidate_backup_dir
        or ROOT / "backups/form_completion"
        / f"{run_id}-pre-probe-candidate-merge"
    )
    result = reconcile_campaign(
        source_run_dir=args.source_run_dir.resolve(),
        destination_run_dir=destination.resolve(),
        queue_path=args.queue.resolve(),
        verification_path=args.verification.resolve(),
        probe_dirs=[path.resolve() for path in args.probe_dir],
        candidate_store_path=args.candidate_store.resolve(),
        candidate_backup_dir=backup.resolve(),
        campaign_status_path=args.campaign_status.resolve(),
        latest_path=args.latest.resolve(),
        report_output_path=args.report.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
