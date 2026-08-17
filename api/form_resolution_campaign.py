"""Campaign orchestration and privacy-safe status for form resolution."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from api.form_resolution_registry import (
    build_occurrence_registry,
    build_privacy_safe_campaign_summary,
    has_invalid_unicode_metadata,
)

ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
CAMPAIGN_DIR = ROOT / "data" / "form_resolution_campaign"
STATUS_PATH = CAMPAIGN_DIR / "status_v1.json"
LATEST_PATH = CAMPAIGN_DIR / "latest.json"
INVENTORY_PATH = FORMS_DIR / "three_tier_form_inventory_v1.json"
CATALOG_PATH = FORMS_DIR / "canonical_forms_catalog_v1.json"
ATTESTATIONS_PATH = FORMS_DIR / "legal_review_attestations_v1.json"
BRIDGE_REPORT_PATH = (
    ROOT / "reports" / "feature006" / "form-completion-bridge-verification.json"
)

# Source failures are deliberately granular in the campaign artifacts.  Keep
# the aggregate release status fail-closed for every external dependency
# failure, while preserving the more useful reason code per occurrence.
EXTERNAL_SOURCE_REASON_CODES = {
    "BLOCKED_EXTERNAL",
    "OFFICIAL_SOURCE_DNS_FAILURE",
    "OFFICIAL_SOURCE_TLS_FAILURE",
    "OFFICIAL_SOURCE_CONNECT_TIMEOUT",
    "OFFICIAL_SOURCE_READ_TIMEOUT",
    "OFFICIAL_SOURCE_FORBIDDEN",
    "OFFICIAL_SOURCE_RATE_LIMITED",
    "OFFICIAL_SOURCE_SERVER_ERROR",
    "OFFICIAL_SOURCE_ENDPOINT_CHANGED",
    "OFFICIAL_SOURCE_CAPTCHA",
    "OFFICIAL_SOURCE_CONTENT_TYPE_DRIFT",
    "OFFICIAL_SOURCE_RESPONSE_INVALID",
    "EXACT_CODE_RESOLVER_PARTIAL_TIMEOUT",
}

NON_GAP_SOURCE_REASONS = {
    "EXACT_OFFICIAL_DOCUMENT_FOUND",
    "OFFICIAL_SOURCE_FOUND_ARTIFACT_PENDING",
    "OFFICIAL_SOURCE_RESOLUTION_PENDING",
}

TERMINAL_REASON_CODES = {
    "READY_FOR_HUMAN_ATTESTATION",
    "ALREADY_APPROVED_RUNTIME",
    "FORM_APPENDIX_SUPERSEDED",
    "ISSUING_INSTRUMENT_EXPIRED",
    "FORM_IDENTITY_UNRESOLVED",
    "FORM_CODE_UNRESOLVED",
    "ISSUING_INSTRUMENT_UNRESOLVED",
    "OFFICIAL_FORM_FILE_NOT_FOUND",
    "EFORM_SESSION_LINK_REJECTED",
    "EFORM_LOGIN_REQUIRED",
    "EFORM_CAPTCHA_BLOCKED",
    "EFORM_WRONG_PROCEDURE",
    "EFORM_ROLE_NOT_VISIBLE",
    *EXTERNAL_SOURCE_REASON_CODES,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def completion_input_paths(
    *,
    project_root: Path,
    legal_as_of: str,
) -> tuple[Path, Path]:
    """Return the checksum-bound manifest and source snapshot for one legal date."""

    legal_date = date.fromisoformat(_text(legal_as_of)).isoformat()
    return (
        project_root
        / "reports"
        / "feature006"
        / f"form-requirement-manifest-{legal_date}.json",
        project_root
        / "data"
        / "source_cache"
        / "form_requirements"
        / f"dvc-form-requirements-{legal_date}.json",
    )


def _bridge_report_path(project_root: Path) -> Path:
    return (
        project_root
        / "reports"
        / "feature006"
        / "form-completion-bridge-verification.json"
    )


def _load(path: Path, default: Mapping[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"INVALID_JSON_OBJECT:{path.name}")
    return value


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def validate_completion_run(
    *,
    expected_manifest_sha256: str,
    actual_manifest_sha256: str,
    expected_source_snapshot_sha256: str,
    actual_source_snapshot_sha256: str,
    expected_preview_fingerprint: str | None = None,
    actual_preview_fingerprint: str | None = None,
) -> None:
    """Refuse stale legal inputs or a changed human-review preview."""

    if _text(expected_manifest_sha256) != _text(actual_manifest_sha256):
        raise ValueError("MANIFEST_CHECKSUM_DRIFT")
    if _text(expected_source_snapshot_sha256) != _text(
        actual_source_snapshot_sha256
    ):
        raise ValueError("SOURCE_SNAPSHOT_CHECKSUM_DRIFT")
    if expected_preview_fingerprint is not None and _text(
        expected_preview_fingerprint
    ) != _text(actual_preview_fingerprint):
        raise ValueError("PREVIEW_STALE_OR_TAMPERED")


def is_terminal_reason(reason_code: Any) -> bool:
    return _text(reason_code) in TERMINAL_REASON_CODES


def review_batch_fingerprint(
    records: Sequence[Mapping[str, Any]],
    *,
    manifest_sha256: str,
    source_snapshot_sha256: str = "",
) -> str:
    payload = {
        "manifest_sha256": _text(manifest_sha256),
        "source_snapshot_sha256": _text(source_snapshot_sha256),
        "records": [
            {
                "candidate_id": _text(item.get("candidate_id")),
                "requirement_identity_id": _text(
                    item.get("requirement_identity_id")
                ),
                "requirement_identity_ids": sorted(
                    _text(value)
                    for value in item.get("requirement_identity_ids") or []
                    if _text(value)
                ),
                "canonical_form_id": _text(item.get("canonical_form_id")),
                "procedure_id": _text(item.get("procedure_id")),
                "sha256": _text(item.get("sha256")),
                "source_page_url": _text(item.get("source_page_url")),
                "source_download_url": _text(
                    item.get("source_download_url")
                    or item.get("official_download_url")
                ),
                "effective_from": _text(item.get("effective_from")) or None,
                "effective_to": _text(item.get("effective_to")) or None,
                "jurisdiction": _text(item.get("jurisdiction")) or None,
                "administrative_level": _text(
                    item.get("administrative_level") or item.get("scope")
                )
                or None,
                "delivery_type": _text(item.get("delivery_type")) or None,
                "effectivity_reason_code": _text(
                    item.get("effectivity_reason_code")
                )
                or None,
            }
            for item in records
        ],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def build_review_batches(
    candidates: Sequence[Mapping[str, Any]],
    *,
    manifest_sha256: str,
    source_snapshot_sha256: str = "",
    max_batch_size: int = 25,
) -> list[dict[str, Any]]:
    """Build stable candidate-only batches grouped by instrument and domain."""

    if not 1 <= max_batch_size <= 25:
        raise ValueError("FORM_REVIEW_BATCH_SIZE_INVALID")
    normalized: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        if row.get("approved") is True or row.get("runtime_eligible") is True:
            raise ValueError("AUTOMATED_FORM_APPROVAL_FORBIDDEN")
        row["approved"] = False
        row["runtime_eligible"] = False
        normalized.append(row)
    normalized.sort(
        key=lambda item: (
            _text(item.get("issuing_instrument")),
            _text(item.get("source_domain")),
            _text(item.get("requirement_identity_id")),
            _text(item.get("procedure_id")),
            _text(item.get("candidate_id")),
        )
    )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in normalized:
        identity_key = _text(row.get("requirement_identity_id")) or _text(
            row.get("candidate_id")
        )
        grouped.setdefault(identity_key, []).append(row)
    identity_keys = sorted(
        grouped,
        key=lambda key: (
            _text(grouped[key][0].get("issuing_instrument")),
            _text(grouped[key][0].get("source_domain")),
            key,
        ),
    )

    batches: list[dict[str, Any]] = []
    pending_keys = list(identity_keys)
    while pending_keys:
        batch_keys: list[str] = []
        batch_identity_ids: set[str] = set()
        while pending_keys:
            key = pending_keys[0]
            row_identity_ids = {
                _text(value)
                for row in grouped[key]
                for value in (
                    row.get("requirement_identity_ids")
                    or [row.get("requirement_identity_id") or key]
                )
                if _text(value)
            }
            if batch_keys and len(batch_identity_ids | row_identity_ids) > max_batch_size:
                break
            pending_keys.pop(0)
            batch_keys.append(key)
            batch_identity_ids.update(row_identity_ids)
        records = [row for key in batch_keys for row in grouped[key]]
        fingerprint = review_batch_fingerprint(
            records,
            manifest_sha256=manifest_sha256,
            source_snapshot_sha256=source_snapshot_sha256,
        )
        batches.append(
            {
                "batch_id": f"form-review-{len(batches) + 1:03d}-{fingerprint[:16]}",
                "manifest_sha256": _text(manifest_sha256),
                "source_snapshot_sha256": _text(source_snapshot_sha256),
                "preview_fingerprint": fingerprint,
                "fingerprint_schema_version": "form-review-batch-v2",
                "records": records,
                "identity_count": len(batch_identity_ids),
                "candidate_only": True,
                "automated_approval": False,
                "human_attestation_required": True,
            }
        )
    return batches


def mark_campaign_batch_attested(
    *,
    run_id: str,
    batch_id: str,
    attestation_id: str,
    release_gate_status: str,
    total_batch_count: int,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record one human-decided batch without closing later batches."""

    path = path or STATUS_PATH
    current = load_campaign_status(path)
    if _text(current.get("run_id")) != _text(run_id):
        raise ValueError("FORM_RESOLUTION_RUN_NOT_CURRENT")
    batch_id = _text(batch_id)
    attestation_id = _text(attestation_id)
    if not batch_id or not attestation_id:
        raise ValueError("FORM_RESOLUTION_BATCH_ATTESTATION_REQUIRED")
    gate_status = _text(release_gate_status)
    if gate_status not in {"queued", "running", "PASS", "BLOCKED_RELEASE"}:
        raise ValueError("FORM_RESOLUTION_RELEASE_GATE_STATUS_INVALID")
    decided = [
        dict(item)
        for item in current.get("attested_batches") or []
        if isinstance(item, Mapping)
    ]
    existing = next(
        (item for item in decided if _text(item.get("batch_id")) == batch_id),
        None,
    )
    if existing:
        if _text(existing.get("attestation_id")) != attestation_id:
            raise ValueError("FORM_RESOLUTION_BATCH_ATTESTATION_CONFLICT")
    else:
        decided.append(
            {
                "batch_id": batch_id,
                "attestation_id": attestation_id,
                "release_gate_status": gate_status,
            }
        )
    decided.sort(key=lambda item: _text(item.get("batch_id")))
    remaining = max(0, int(total_batch_count) - len(decided))
    if remaining:
        status = "READY_FOR_HUMAN_ATTESTATION"
        stage = "human_attestation"
    elif gate_status == "PASS":
        status = "ATTESTED_RELEASE_GATES_PASS"
        stage = "post_attestation_release_gates"
    elif gate_status == "BLOCKED_RELEASE":
        status = "ATTESTED_RELEASE_GATE_BLOCKED"
        stage = "post_attestation_release_gates"
    else:
        status = "ATTESTED_PENDING_RELEASE_GATES"
        stage = "post_attestation_release_gates"
    updated = {
        **current,
        "status": status,
        "stage": stage,
        "attested_batches": decided,
        "decided_batch_count": len(decided),
        "remaining_batch_count": remaining,
        "human_attestation_required": remaining > 0,
        "release_gate_status": gate_status,
        "automated_approval": False,
        "feature_flag_enabled": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_atomic(path, updated)
    return updated


def build_occurrences_from_inventory(
    inventory: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = [
        *inventory.get("verified_data_gaps", []),
        *inventory.get("canonical_forms", []),
    ]
    result = [
        dict(item)
        for item in rows
        if isinstance(item, Mapping)
        and item.get("component_kind") == "applicant_form"
        and item.get("prior_approval_status") is not True
    ]
    result.sort(key=lambda item: _text(item.get("candidate_id")))
    return result


def build_campaign_baseline(
    *,
    run_id: str,
    legal_as_of: str,
    occurrences: Sequence[Mapping[str, Any]],
    catalog_path: Path,
    attestations_path: Path,
    feature_flag: bool,
    active_collection: str,
    manifest_sha256: str | None = None,
    source_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Create an aggregate baseline without copying form names or content."""

    catalog = _load(catalog_path, {"forms": []})
    attestations = _load(attestations_path, {"attestations": []})
    approved_forms = sum(
        item.get("approved") is True
        for item in catalog.get("forms") or []
        if isinstance(item, Mapping)
    )
    runtime_forms = sum(
        item.get("runtime_eligible") is True
        for item in catalog.get("forms") or []
        if isinstance(item, Mapping)
    )
    try:
        from api.crawlers.ocr_extractor import _ocr_runtime_readiness
        from api.crawlers.office_converter import detect_office_converter

        office_ready = detect_office_converter() is not None
        ocr_ready, ocr_reason = _ocr_runtime_readiness()
    except Exception:
        office_ready = False
        ocr_ready = False
        ocr_reason = "LOCAL_DOCUMENT_TOOL_PROBE_FAILED"

    return {
        "schema_version": "form-resolution-baseline-v1",
        "run_id": _text(run_id),
        "legal_as_of": _text(legal_as_of),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "occurrences": len(occurrences),
            "catalog_forms": len(catalog.get("forms") or []),
            "approved_catalog_forms": approved_forms,
            "runtime_approved_forms": runtime_forms,
            "attestations": len(attestations.get("attestations") or []),
        },
        "checksums": {
            "catalog": _sha256_path(catalog_path),
            "attestations": _sha256_path(attestations_path),
            "requirement_manifest": _text(manifest_sha256) or None,
            "source_snapshot": _text(source_snapshot_sha256) or None,
        },
        "feature_flag": bool(feature_flag),
        "active_collection": _text(active_collection),
        "local_document_tools": {
            "libreoffice": "ready" if office_ready else "unavailable",
            "ocr_vie_eng": "ready" if ocr_ready else "unavailable",
            "ocr_reason": "" if ocr_ready else ocr_reason,
        },
        "candidate_only": True,
        "automated_approval": False,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }


def list_campaign_gaps(
    *,
    registry: Mapping[str, Any],
    gap_records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return gap progress with opaque IDs and source-attempt counts only."""

    indexed = {
        _text(item.get("occurrence_id")): item
        for item in registry.get("occurrences") or []
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    for record in gap_records:
        if not isinstance(record, Mapping):
            continue
        occurrence_id = _text(record.get("occurrence_id"))
        if occurrence_id not in indexed:
            continue
        result.append(
            {
                "occurrence_id": occurrence_id,
                "procedure_id": _text(record.get("procedure_id")) or None,
                "reason_code": _text(record.get("reason_code"))
                or _text(indexed[occurrence_id].get("reason_code"))
                or "UNCLASSIFIED_GAP",
                "source_attempt_count": len(
                    [
                        item
                        for item in record.get("source_attempts") or []
                        if isinstance(item, Mapping)
                    ]
                ),
            }
        )
    return sorted(result, key=lambda item: item["occurrence_id"])


def build_campaign_report(
    *,
    registry: Mapping[str, Any],
    baseline: Mapping[str, Any],
    gap_records: Sequence[Mapping[str, Any]],
    shortlist: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    registry_summary = dict(registry.get("summary") or {})
    reason_counts = Counter(
        _text(item.get("reason_code")) or "UNCLASSIFIED_GAP"
        for item in gap_records
    )
    invalid_shortlist_records = sum(
        1
        for item in shortlist
        if any(
            has_invalid_unicode_metadata(item.get(field))
            for field in (
                "canonical_form_name",
                "canonical_name",
                "detected_form_name",
                "form_title",
                "publisher",
            )
        )
    )
    valid_shortlist_count = len(shortlist) - invalid_shortlist_records
    if invalid_shortlist_records:
        reason_counts["INVALID_UNICODE_METADATA"] += invalid_shortlist_records
    blocked_external = sum(
        count
        for reason, count in reason_counts.items()
        if reason in EXTERNAL_SOURCE_REASON_CODES
    )
    unresolved_identity = sum(
        count
        for reason, count in reason_counts.items()
        if reason in {
            "FORM_IDENTITY_UNRESOLVED",
            "ISSUING_INSTRUMENT_UNRESOLVED",
            "INVALID_UNICODE_METADATA",
            "MISSING_PROCEDURE_ID",
        }
    )
    return {
        "schema_version": "form-resolution-campaign-v1",
        "run_id": _text(registry.get("run_id")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": _text(registry.get("legal_as_of")),
        "status": (
            "BLOCKED_RELEASE"
            if invalid_shortlist_records
            else (
                "READY_FOR_HUMAN_ATTESTATION"
                if valid_shortlist_count
                else (
                    "BLOCKED_EXTERNAL"
                    if any(
                        _text(item.get("reason_code"))
                        in EXTERNAL_SOURCE_REASON_CODES
                        for item in gap_records
                    )
                    else "VERIFIED_DATA_GAP"
                )
            )
        ),
        "counts": {
            "occurrences": int(registry_summary.get("occurrence_count") or 0),
            "terminal_occurrences": int(
                registry_summary.get("terminal_occurrence_count") or 0
            ),
            "canonical_identity_groups": int(
                registry_summary.get("canonical_identity_count") or 0
            ),
            "procedure_bindings": int(
                registry_summary.get("procedure_binding_count") or 0
            ),
                "verified_data_gap": max(
                    0,
                    sum(
                        1
                        for item in gap_records
                        if _text(item.get("reason_code")) not in NON_GAP_SOURCE_REASONS
                    )
                    - blocked_external,
                ),
                "unresolved_identity": unresolved_identity,
                "ready_for_attestation": valid_shortlist_count,
                "invalid_shortlist_records": invalid_shortlist_records,
                "blocked_external": blocked_external,
            "runtime_approved": int(
                (baseline.get("counts") or {}).get("runtime_approved_forms") or 0
            ),
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "candidate_only": True,
        "automated_approval": False,
        "human_attestation_required": True,
        "feature_flag_enabled": False,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }


def load_campaign_status(
    path: Path | None = None,
    *,
    project_root: Path = ROOT,
) -> dict[str, Any]:
    path = path or STATUS_PATH
    bridge = _load(_bridge_report_path(project_root), {})
    bridge_checksums = bridge.get("checksums") or {}
    bridge_summary = bridge.get("summary") or {}
    expected_manifest = _text(
        bridge_checksums.get("requirement_manifest")
    )
    expected_source = _text(bridge_checksums.get("source_snapshot"))
    value = _load(path, {}) if path.is_file() else {}
    artifact_legal_as_of = _text(bridge.get("legal_as_of")) or _text(
        value.get("legal_as_of")
    )
    if artifact_legal_as_of:
        requirement_manifest_path, source_snapshot_path = completion_input_paths(
            project_root=project_root,
            legal_as_of=artifact_legal_as_of,
        )
        actual_manifest = _sha256_path(requirement_manifest_path)
        actual_source = _sha256_path(source_snapshot_path)
    else:
        actual_manifest = None
        actual_source = None
    if not path.is_file():
        return {
            "schema_version": "form-resolution-campaign-v1",
            "run_id": "not-started",
            "status": "not_started",
            "stage": "not_started",
            "counts": {
                "target_identities": int(
                    bridge_summary.get("target_identities") or 0
                ),
                "approved_runtime_identities": int(
                    bridge_summary.get("approved_identities") or 0
                ),
                "pending_identities": int(
                    bridge_summary.get("pending_identities") or 0
                ),
                "pending_procedure_bindings": int(
                    bridge_summary.get("pending_procedure_bindings") or 0
                ),
                "paper_or_file_pending": int(
                    bridge_summary.get("paper_or_file_pending") or 0
                ),
                "interactive_eform_pending": int(
                    bridge_summary.get("interactive_eform_pending") or 0
                ),
            },
            "reason_counts": {},
            "automated_approval": False,
            "human_attestation_required": True,
            "feature_flag_enabled": False,
            "manifest_sha256": expected_manifest or actual_manifest,
            "source_snapshot_sha256": expected_source or actual_source,
            "current_manifest_sha256": actual_manifest,
            "current_source_snapshot_sha256": actual_source,
            "manifest_drift": bool(
                expected_manifest and expected_manifest != actual_manifest
            ),
            "source_snapshot_drift": bool(
                expected_source and expected_source != actual_source
            ),
        }
    if value.get("feature_flag_enabled") is not False:
        raise ValueError("FORM_RESOLUTION_FEATURE_FLAG_MUST_REMAIN_FALSE")
    counts = dict(value.get("counts") or {})
    completion_counts = value.get("completion_counts") or {}
    for key, item in completion_counts.items():
        counts.setdefault(key, item)
    counts.setdefault(
        "target_identities", int(bridge_summary.get("target_identities") or 0)
    )
    counts.setdefault(
        "approved_runtime_identities",
        int(bridge_summary.get("approved_identities") or 0),
    )
    counts.setdefault(
        "pending_identities", int(bridge_summary.get("pending_identities") or 0)
    )
    run_manifest = _text(value.get("manifest_sha256")) or expected_manifest
    run_source = _text(value.get("source_snapshot_sha256")) or expected_source
    return {
        **value,
        "counts": counts,
        "manifest_sha256": run_manifest,
        "source_snapshot_sha256": run_source,
        "current_manifest_sha256": actual_manifest,
        "current_source_snapshot_sha256": actual_source,
        "manifest_drift": bool(run_manifest and run_manifest != actual_manifest),
        "source_snapshot_drift": bool(run_source and run_source != actual_source),
    }


def mark_campaign_attested(
    *,
    run_id: str,
    attestation_id: str,
    release_gate_status: str,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record the human decision and technical-gate handoff atomically.

    This does not create the legal decision. It is called only after the
    authenticated attestation transaction has already succeeded.
    """

    path = path or STATUS_PATH
    current = load_campaign_status(path)
    if _text(current.get("run_id")) != _text(run_id):
        raise ValueError("FORM_RESOLUTION_RUN_NOT_CURRENT")
    existing_attestation = _text(current.get("attestation_id"))
    if existing_attestation and existing_attestation != _text(attestation_id):
        raise ValueError("FORM_RESOLUTION_DIFFERENT_ATTESTATION_ALREADY_RECORDED")
    gate_status = _text(release_gate_status)
    if gate_status not in {"queued", "running", "PASS", "BLOCKED_RELEASE"}:
        raise ValueError("FORM_RESOLUTION_RELEASE_GATE_STATUS_INVALID")
    if gate_status == "PASS":
        status = "ATTESTED_RELEASE_GATES_PASS"
    elif gate_status == "BLOCKED_RELEASE":
        status = "ATTESTED_RELEASE_GATE_BLOCKED"
    else:
        status = "ATTESTED_PENDING_RELEASE_GATES"
    updated = {
        **current,
        "status": status,
        "stage": "post_attestation_release_gates",
        "attestation_id": _text(attestation_id),
        "human_attestation_required": False,
        "release_gate_status": gate_status,
        "automated_approval": False,
        "feature_flag_enabled": False,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_atomic(path, updated)
    return updated


def launch_form_resolution_campaign(
    *,
    project_root: Path = ROOT,
    legal_as_of: str,
    status_path: Path = STATUS_PATH,
    launcher: Any = subprocess.Popen,
    manifest_sha256: str | None = None,
    source_snapshot_sha256: str | None = None,
    requirement_manifest_path: Path | None = None,
    source_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    requirement_manifest_path, source_snapshot_path = (
        (
            requirement_manifest_path,
            source_snapshot_path,
        )
        if requirement_manifest_path is not None
        and source_snapshot_path is not None
        else completion_input_paths(
            project_root=project_root,
            legal_as_of=legal_as_of,
        )
    )
    current = load_campaign_status(status_path, project_root=project_root)
    if current.get("status") in {"queued", "running"}:
        return {**current, "launch_status": "already_running"}

    current_run_id = _text(current.get("run_id"))
    same_legal_date = _text(current.get("legal_as_of")) == _text(legal_as_of)
    requested_baseline_changed = bool(
        (
            manifest_sha256
            and _text(current.get("manifest_sha256"))
            != _text(manifest_sha256)
        )
        or (
            source_snapshot_sha256
            and _text(current.get("source_snapshot_sha256"))
            != _text(source_snapshot_sha256)
        )
    )
    if (
        same_legal_date
        and current_run_id not in {"", "queued", "not-started"}
        and not requested_baseline_changed
        and current.get("status")
        in {
            "READY_FOR_HUMAN_ATTESTATION",
            "VERIFIED_DATA_GAP",
            "ATTESTED_PENDING_RELEASE_GATES",
            "ATTESTED_RELEASE_GATES_PASS",
            "ATTESTED_RELEASE_GATE_BLOCKED",
        }
    ):
        return {**current, "launch_status": "already_available"}

    if not manifest_sha256 or not source_snapshot_sha256:
        raise ValueError("FORM_COMPLETION_CHECKSUMS_REQUIRED")
    validate_completion_run(
        expected_manifest_sha256=manifest_sha256,
        actual_manifest_sha256=_sha256_path(requirement_manifest_path) or "",
        expected_source_snapshot_sha256=source_snapshot_sha256,
        actual_source_snapshot_sha256=_sha256_path(source_snapshot_path) or "",
    )

    resume_run_id = None
    if (
        same_legal_date
        and current_run_id not in {"", "queued", "not-started"}
        and current.get("status")
        in {"BLOCKED_EXTERNAL", "BLOCKED_RELEASE", "failed_fail_closed"}
    ):
        resume_run_id = current_run_id

    queued = {
        "schema_version": "form-resolution-campaign-v1",
        "run_id": resume_run_id or "queued",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": _text(legal_as_of),
        "status": "queued",
        "stage": "queued",
        "counts": {},
        "reason_counts": {},
        "automated_approval": False,
        "human_attestation_required": True,
        "feature_flag_enabled": False,
        "manifest_sha256": _text(manifest_sha256) or None,
        "source_snapshot_sha256": _text(source_snapshot_sha256) or None,
    }
    _write_atomic(status_path, queued)
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_form_resolution_campaign.py"),
        "--legal-as-of",
        legal_as_of,
        "--requirement-manifest",
        str(requirement_manifest_path),
        "--source-snapshot",
        str(source_snapshot_path),
    ]
    if manifest_sha256:
        command.extend(["--manifest-sha256", manifest_sha256])
    if source_snapshot_sha256:
        command.extend(["--source-snapshot-sha256", source_snapshot_sha256])
    if resume_run_id:
        command.extend(["--resume-run-id", resume_run_id])
    kwargs: dict[str, Any] = {
        "cwd": project_root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        launcher(command, **kwargs)
    except Exception:
        failed = {**queued, "status": "failed_fail_closed", "stage": "launch_failed"}
        _write_atomic(status_path, failed)
        raise
    return {
        **queued,
        "launch_status": "resumed" if resume_run_id else "started",
    }


def build_privacy_safe_status(
    *,
    registry: Mapping[str, Any],
    baseline: Mapping[str, Any],
    gap_records: Sequence[Mapping[str, Any]],
    shortlist: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    report = build_campaign_report(
        registry=registry,
        baseline=baseline,
        gap_records=gap_records,
        shortlist=shortlist,
    )
    return {
        **report,
        "stage": "complete",
        "source_attempts": None,
        "forms": None,
    }
