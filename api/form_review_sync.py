"""Fail-closed synchronization for human legal-form review attestations.

The legacy review queue and the canonical runtime catalog are separate data
stores.  This module carries an explicit human decision across that boundary
without inventing reviewer identity, effectivity, procedure mapping, or source
metadata.  It is deliberately pure; callers are responsible for transactional
file persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from api.legal_form_catalog import _file_integrity, _is_official_url


class FormReviewSyncError(ValueError):
    """Raised when an attestation cannot safely update the runtime catalog."""


_AUTOMATED_REVIEWERS = {"", "automated", "automation", "system", "model", "ai"}
_BLOCKED_MARKERS = ("[demo]", "demo", "seed", "synthetic", "quarantine")


def apply_candidate_queue_decision(
    record: Mapping[str, Any],
    *,
    decision: str,
    reviewer_id: str | None,
    reviewed_at: str,
    review_note: str,
) -> dict[str, Any]:
    """Record queue review while keeping runtime legal approval fail-closed."""

    normalized_decision = str(decision or "").strip().casefold()
    if normalized_decision not in {"approved", "rejected"}:
        raise FormReviewSyncError("DECISION_INVALID")
    _valid_iso_date(reviewed_at, "REVIEWED_AT_REQUIRED")
    updated = deepcopy(dict(record))
    approved = normalized_decision == "approved"
    updated.update(
        {
            "review_status": normalized_decision,
            "is_approved": approved,
            "is_canonical": False,
            "legal_review_status": (
                "catalog_sync_required" if approved else "rejected"
            ),
            "catalog_status": (
                "legal_review_required" if approved else "rejected"
            ),
            "runtime_eligible": False,
            "reviewed_by": str(reviewer_id or "").strip() or None,
            "reviewed_at": reviewed_at,
            "review_note": str(review_note or "").strip(),
        }
    )
    return updated


def _require_text(value: Any, reason: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FormReviewSyncError(reason)
    return text


def _valid_iso_date(value: Any, reason: str, *, required: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise FormReviewSyncError(reason)
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FormReviewSyncError(reason) from exc
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _date_only(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_blocked_candidate(candidate: Mapping[str, Any]) -> bool:
    if any(
        candidate.get(key) is True
        for key in (
            "is_demo",
            "is_seed",
            "synthetic",
            "quarantined",
            "is_quarantined",
            "test_only",
        )
    ):
        return True
    material = " ".join(
        str(candidate.get(key) or "")
        for key in (
            "id",
            "detected_form_name",
            "form_title",
            "catalog_status",
            "preparation_status",
            "priority_path",
            "local_path",
        )
    ).casefold()
    return any(marker in material for marker in _BLOCKED_MARKERS)


def _candidate_procedure(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("procedure_id")
        or candidate.get("suggested_procedure_id")
        or ""
    ).strip()


def _candidate_source_page(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("source_page_url")
        or candidate.get("page_url")
        or candidate.get("source_url")
        or ""
    ).strip()


def _candidate_download_url(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("source_download_url")
        or candidate.get("official_download_url")
        or candidate.get("source_url")
        or ""
    ).strip()


def _select_candidate(
    form: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    provenance = form.get("provenance")
    explicit_ids = {
        str(value).strip()
        for value in (
            (provenance or {}).get("candidate_id")
            if isinstance(provenance, Mapping)
            else None,
        )
        if str(value or "").strip()
    }
    evidence = [
        item
        for item in (form.get("candidate_source_evidence") or [])
        if isinstance(item, Mapping)
    ]
    explicit_ids.update(
        str(item.get("candidate_id") or "").strip()
        for item in evidence
        if str(item.get("candidate_id") or "").strip()
    )
    hashes = {
        str(value or "").strip().casefold()
        for value in (
            form.get("sha256"),
            *(item.get("sha256") for item in evidence),
        )
        if str(value or "").strip()
    }
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    form_procedures = {
        str(value).strip() for value in form.get("procedure_ids") or [] if value
    }
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "").strip()
        candidate_hash = str(candidate.get("sha256") or "").strip().casefold()
        score = 0
        if candidate_id and candidate_id in explicit_ids:
            score += 100
        if candidate_hash and candidate_hash in hashes:
            score += 50
        if not score:
            continue
        if _candidate_procedure(candidate) in form_procedures:
            score += 10
        if candidate.get("review_status") == "approved":
            score += 2
        if candidate.get("legal_basis"):
            score += 1
        ranked.append((score, candidate_id, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2]


def _current_resolution_evidence(
    form: Mapping[str, Any],
    *,
    legal_as_of: str,
) -> Mapping[str, Any] | None:
    accepted_statuses = {
        "BLOCKED_EXTERNAL",
        "EXCLUDED_NO_OFFICIAL_FORM",
        "SOURCE_DOWNLOAD_RETRY_REQUIRED",
        "SOURCE_MAPPING_UNRESOLVED",
        "VERIFIED_DATA_GAP",
    }
    for item in reversed(form.get("candidate_source_evidence") or []):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") not in accepted_statuses:
            continue
        if str(item.get("legal_as_of") or "")[:10] != legal_as_of[:10]:
            continue
        return item
    return None


def _preview_item(
    form: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None,
    resolution_evidence: Mapping[str, Any] | None,
    procedure_id: str,
    reasons: list[str],
) -> dict[str, Any]:
    evidence = resolution_evidence or {}
    checked_sources = [
        str(value).strip()
        for value in evidence.get("official_sources_checked") or []
        if str(value or "").strip()
    ]
    return {
        "candidate_id": (
            str(candidate.get("id") or "").strip()
            if candidate
            else (str(evidence.get("candidate_id") or "").strip() or None)
        ),
        "canonical_form_id": str(form.get("form_id") or "").strip(),
        "procedure_id": procedure_id,
        "form_code": form.get("form_code")
        or (candidate or {}).get("form_code"),
        "canonical_name": form.get("canonical_name")
        or (candidate or {}).get("canonical_form_name")
        or (candidate or {}).get("detected_form_name"),
        "source_page_url": (
            _candidate_source_page(candidate)
            if candidate
            else (
                str(evidence.get("source_page") or "").strip()
                or (checked_sources[0] if checked_sources else None)
            )
        ),
        "source_download_url": (
            _candidate_download_url(candidate)
            if candidate
            else (
                str(evidence.get("download_url") or "").strip() or None
            )
        ),
        "local_path": (
            (candidate or {}).get("priority_path")
            or (candidate or {}).get("local_path")
            or (candidate or {}).get("file_path")
            or evidence.get("local_path")
        ),
        "sha256": (candidate or {}).get("sha256") or evidence.get("sha256"),
        "legal_basis": list(
            (candidate or {}).get("legal_basis")
            or evidence.get("legal_basis")
            or form.get("legal_basis")
            or []
        ),
        "effective_from": (candidate or {}).get("effective_from")
        or form.get("effective_from"),
        "effective_to": (candidate or {}).get("effective_to")
        or form.get("effective_to"),
        "jurisdiction": (candidate or {}).get("jurisdiction")
        or (candidate or {}).get("scope")
        or form.get("jurisdiction")
        or "Hai Phong",
        "administrative_level": (
            (candidate or {}).get("administrative_level")
            or form.get("administrative_level")
            or "commune"
        ),
        "review_status": (candidate or {}).get("review_status")
        or form.get("review_status"),
        "legal_review_status": (candidate or {}).get("legal_review_status")
        or form.get("legal_review_status"),
        "reason_codes": sorted(set(reasons)),
    }


def build_form_review_preview(
    *,
    candidate_payload: Mapping[str, Any],
    canonical_forms_payload: Mapping[str, Any],
    bindings_payload: Mapping[str, Any],
    project_root: Path,
    legal_as_of: str,
) -> dict[str, Any]:
    """Build a deterministic, fail-closed batch review preview.

    The preview never changes review state. It exposes every canonical form and
    the exact hard-gate reasons that prevent a human attestation.
    """

    active_date = _date_only(legal_as_of)
    if active_date is None:
        raise FormReviewSyncError("LEGAL_AS_OF_INVALID")
    candidate_rows = [
        dict(row)
        for row in candidate_payload.get("records") or []
        if isinstance(row, Mapping)
    ]
    form_rows = [
        dict(row)
        for row in canonical_forms_payload.get("forms") or []
        if isinstance(row, Mapping)
    ]
    binding_rows = [
        dict(row)
        for row in bindings_payload.get("bindings") or []
        if isinstance(row, Mapping)
    ]
    eligible_items: list[dict[str, Any]] = []
    excluded_items: list[dict[str, Any]] = []
    catalog_excluded_items: list[dict[str, Any]] = []
    already_approved = 0

    for form in sorted(
        form_rows, key=lambda row: str(row.get("form_id") or "")
    ):
        if form.get("catalog_disposition") == "excluded_no_official_form":
            resolution_evidence = _current_resolution_evidence(
                form,
                legal_as_of=legal_as_of,
            )
            form_procedures = [
                str(value).strip()
                for value in form.get("procedure_ids") or []
                if str(value or "").strip()
            ]
            procedure_id = form_procedures[0] if form_procedures else ""
            reason = str(
                (resolution_evidence or {}).get("reason_code")
                or (form.get("catalog_exclusion") or {}).get("reason_code")
                or "NO_OFFICIAL_STATE_FORM_CONFIRMED"
            ).strip()
            catalog_excluded_items.append(
                _preview_item(
                    form,
                    candidate=None,
                    resolution_evidence=resolution_evidence,
                    procedure_id=procedure_id,
                    reasons=[reason],
                )
            )
            continue
        if (
            form.get("approved") is True
            and form.get("review_status") == "approved"
            and form.get("legal_review_status") == "approved"
        ):
            already_approved += 1
            continue
        candidate = _select_candidate(form, candidate_rows)
        resolution_evidence = _current_resolution_evidence(
            form,
            legal_as_of=legal_as_of,
        )
        form_procedures = [
            str(value).strip()
            for value in form.get("procedure_ids") or []
            if str(value or "").strip()
        ]
        procedure_id = (
            _candidate_procedure(candidate)
            if candidate is not None
            else (form_procedures[0] if form_procedures else "")
        )
        reasons: list[str] = []
        if candidate is None:
            resolution_reason = str(
                (resolution_evidence or {}).get("reason_code") or ""
            ).strip()
            reasons.append(resolution_reason or "CANDIDATE_NOT_FOUND")
        if not form_procedures:
            reasons.append("FORM_PROCEDURE_ID_MISSING")
        if candidate is not None:
            technically_ready = (
                candidate.get("review_status") == "candidate_pending_review"
                and candidate.get("preparation_status")
                == "ready_for_human_review"
                and (candidate.get("technical_validation") or {}).get("status")
                == "passed"
            )
            if candidate.get("review_status") != "approved" and not technically_ready:
                reasons.append("CANDIDATE_NOT_QUEUE_APPROVED")
            if _is_blocked_candidate(candidate):
                reasons.append("SEED_DEMO_QUARANTINED")
            if (
                not procedure_id
                or procedure_id not in form_procedures
            ):
                reasons.append("PROCEDURE_MAPPING_MISMATCH")
            candidate_domain = str(
                candidate.get("domain")
                or candidate.get("suggested_domain")
                or ""
            ).strip()
            form_domain = str(form.get("domain") or "").strip()
            if candidate_domain and form_domain and candidate_domain != form_domain:
                reasons.append("DOMAIN_MAPPING_MISMATCH")
            if not _is_official_url(_candidate_source_page(candidate)):
                reasons.append("OFFICIAL_SOURCE_REQUIRED")
            if not _is_official_url(_candidate_download_url(candidate)):
                reasons.append("OFFICIAL_DOWNLOAD_REQUIRED")
            if not candidate.get("legal_basis") and not form.get("legal_basis"):
                reasons.append("LEGAL_BASIS_MISSING")
            if not (
                candidate.get("effective_from") or form.get("effective_from")
            ):
                reasons.append("EFFECTIVITY_UNKNOWN")
            start = _date_only(
                candidate.get("effective_from") or form.get("effective_from")
            )
            end = _date_only(
                candidate.get("effective_to") or form.get("effective_to")
            )
            if (
                candidate.get("supersedes_form_id")
                or form.get("supersedes_form_id")
                or "superseded"
                in str(candidate.get("effective_status") or "").casefold()
                or "superseded"
                in str(candidate.get("legacy_form_status") or "").casefold()
            ):
                reasons.append("FORM_SUPERSEDED")
            if start and start > active_date:
                reasons.append("FORM_NOT_YET_EFFECTIVE")
            if end and end < active_date:
                reasons.append("FORM_EXPIRED")
            try:
                path = _candidate_path(candidate, project_root)
            except FormReviewSyncError as exc:
                reasons.append(str(exc))
            else:
                expected_sha = str(candidate.get("sha256") or "").strip()
                if not expected_sha:
                    reasons.append("CHECKSUM_REQUIRED")
                elif _sha256(path) != expected_sha:
                    reasons.append("CHECKSUM_MISMATCH")
                valid_file, reason = _file_integrity(
                    path, path.suffix.lstrip(".").casefold()
                )
                if not valid_file:
                    reasons.append(reason)

        binding = next(
            (
                row
                for row in binding_rows
                if str(row.get("form_id") or "")
                == str(form.get("form_id") or "")
                and str(row.get("procedure_id") or "") == procedure_id
            ),
            None,
        )
        if binding is None:
            reasons.append("CANONICAL_BINDING_NOT_FOUND")

        item = _preview_item(
            form,
            candidate=candidate,
            resolution_evidence=resolution_evidence,
            procedure_id=procedure_id,
            reasons=reasons,
        )
        if reasons:
            excluded_items.append(item)
        else:
            eligible_items.append(item)

    represented_candidate_ids = {
        str(item.get("candidate_id") or "")
        for item in (*eligible_items, *excluded_items)
        if str(item.get("candidate_id") or "")
    }
    proposal_form_ids: set[str] = set()
    for candidate in sorted(
        candidate_rows, key=lambda row: str(row.get("id") or "")
    ):
        candidate_id = str(candidate.get("id") or "").strip()
        canonical_form_id = str(
            candidate.get("proposed_canonical_form_id") or ""
        ).strip()
        if (
            not candidate_id
            or candidate_id in represented_candidate_ids
            or not canonical_form_id
            or canonical_form_id in {
                str(row.get("form_id") or "") for row in form_rows
            }
        ):
            continue
        proposal_form_ids.add(canonical_form_id)
        procedure_id = _candidate_procedure(candidate)
        synthetic_form = {
            "form_id": canonical_form_id,
            "procedure_ids": [procedure_id] if procedure_id else [],
            "form_code": candidate.get("form_code"),
            "canonical_name": candidate.get("canonical_form_name")
            or candidate.get("detected_form_name"),
            "domain": candidate.get("domain")
            or candidate.get("suggested_domain"),
            "administrative_level": candidate.get("administrative_level")
            or "commune",
            "jurisdiction": candidate.get("jurisdiction") or "Hai Phong",
            "review_status": "candidate_pending_review",
            "legal_review_status": "not_reviewed",
            "approved": False,
        }
        reasons: list[str] = []
        technically_ready = (
            candidate.get("review_status") == "candidate_pending_review"
            and candidate.get("preparation_status") == "ready_for_human_review"
            and (candidate.get("technical_validation") or {}).get("status")
            == "passed"
        )
        if candidate.get("review_status") != "approved" and not technically_ready:
            reasons.append("CANDIDATE_NOT_QUEUE_APPROVED")
        if _is_blocked_candidate(candidate):
            reasons.append("SEED_DEMO_QUARANTINED")
        if not procedure_id or candidate.get("procedure_mapping_verified") is not True:
            reasons.append("PROCEDURE_MAPPING_MISMATCH")
        if not _is_official_url(_candidate_source_page(candidate)):
            reasons.append("OFFICIAL_SOURCE_REQUIRED")
        if not _is_official_url(_candidate_download_url(candidate)):
            reasons.append("OFFICIAL_DOWNLOAD_REQUIRED")
        if not candidate.get("legal_basis"):
            reasons.append("LEGAL_BASIS_MISSING")
        start = _date_only(candidate.get("effective_from"))
        end = _date_only(candidate.get("effective_to"))
        if start is None:
            reasons.append("EFFECTIVITY_UNKNOWN")
        elif start > active_date:
            reasons.append("FORM_NOT_YET_EFFECTIVE")
        if end and end < active_date:
            reasons.append("FORM_EXPIRED")
        if (
            candidate.get("supersedes_form_id")
            or "superseded"
            in str(candidate.get("effective_status") or "").casefold()
        ):
            reasons.append("FORM_SUPERSEDED")
        try:
            path = _candidate_path(candidate, project_root)
        except FormReviewSyncError as exc:
            reasons.append(str(exc))
        else:
            expected_sha = str(candidate.get("sha256") or "").strip()
            if not expected_sha:
                reasons.append("CHECKSUM_REQUIRED")
            elif _sha256(path) != expected_sha:
                reasons.append("CHECKSUM_MISMATCH")
            valid_file, reason = _file_integrity(
                path, path.suffix.lstrip(".").casefold()
            )
            if not valid_file:
                reasons.append(reason)
        item = _preview_item(
            synthetic_form,
            candidate=candidate,
            resolution_evidence=None,
            procedure_id=procedure_id,
            reasons=reasons,
        )
        if reasons:
            excluded_items.append(item)
        else:
            eligible_items.append(item)

    reason_counts = Counter(
        reason
        for item in excluded_items
        for reason in item.get("reason_codes") or []
    )
    catalog_exclusion_reason_counts = Counter(
        reason
        for item in catalog_excluded_items
        for reason in item.get("reason_codes") or []
    )
    fingerprint_material = {
        "legal_as_of": legal_as_of,
        "eligible_items": eligible_items,
    }
    preview_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "legal_as_of": legal_as_of,
        "preview_fingerprint": preview_fingerprint,
        "attestation_id": f"form-batch-{preview_fingerprint[:24]}",
        "summary": {
            "total_forms": len(form_rows) + len(proposal_form_ids),
            "active_catalog_forms": (
                len(form_rows)
                - len(catalog_excluded_items)
                + len(proposal_form_ids)
            ),
            "eligible_forms": len(eligible_items),
            "excluded_forms": len(excluded_items),
            "already_approved_forms": already_approved,
            "excluded_no_official_forms": len(catalog_excluded_items),
        },
        "reason_counts": dict(sorted(reason_counts.items())),
        "catalog_exclusion_reason_counts": dict(
            sorted(catalog_exclusion_reason_counts.items())
        ),
        "eligible_items": eligible_items,
        "excluded_items": excluded_items,
        "catalog_excluded_items": catalog_excluded_items,
    }


def _attestation_fingerprint(attestation: Mapping[str, Any]) -> str:
    material = {
        "attestation_id": attestation.get("attestation_id"),
        "reviewer_id": attestation.get("reviewer_id"),
        "reviewed_at": attestation.get("reviewed_at"),
        "decision": attestation.get("decision"),
        "items": attestation.get("items"),
        "batch_id": attestation.get("batch_id"),
        "preview_fingerprint": attestation.get("preview_fingerprint"),
        "manifest_sha256": attestation.get("manifest_sha256"),
        "source_snapshot_sha256": attestation.get("source_snapshot_sha256"),
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _attestation_item_tuple(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(item.get("candidate_id") or item.get("id") or "").strip(),
        str(
            item.get("canonical_form_id")
            or item.get("proposed_canonical_form_id")
            or ""
        ).strip(),
        str(item.get("procedure_id") or "").strip(),
        str(item.get("effective_from") or "").strip() or None,
        str(item.get("effective_to") or "").strip() or None,
        str(item.get("jurisdiction") or "Hai Phong").strip(),
        str(
            item.get("administrative_level")
            or item.get("executing_level")
            or "commune"
        ).strip(),
    )


def _validate_campaign_batch(
    attestation: Mapping[str, Any], campaign_batch: Mapping[str, Any]
) -> list[dict[str, Any]]:
    checks = (
        ("batch_id", "FORM_RESOLUTION_BATCH_DRIFT"),
        ("preview_fingerprint", "PREVIEW_STALE_OR_TAMPERED"),
        ("manifest_sha256", "FORM_REQUIREMENT_MANIFEST_DRIFT"),
        ("source_snapshot_sha256", "FORM_SOURCE_SNAPSHOT_DRIFT"),
    )
    for field, reason in checks:
        expected = _require_text(campaign_batch.get(field), reason)
        if _require_text(attestation.get(field), reason) != expected:
            raise FormReviewSyncError(reason)
    records = [
        dict(item)
        for item in campaign_batch.get("records") or []
        if isinstance(item, Mapping)
    ]
    if not records:
        raise FormReviewSyncError("ATTESTATION_ITEMS_REQUIRED")
    if campaign_batch.get("fingerprint_schema_version") == "form-review-batch-v2":
        from api.form_resolution_campaign import review_batch_fingerprint

        computed_fingerprint = review_batch_fingerprint(
            records,
            manifest_sha256=str(campaign_batch.get("manifest_sha256") or ""),
            source_snapshot_sha256=str(
                campaign_batch.get("source_snapshot_sha256") or ""
            ),
        )
        if computed_fingerprint != campaign_batch.get("preview_fingerprint"):
            raise FormReviewSyncError("PREVIEW_STALE_OR_TAMPERED")
    identity_ids = {
        str(value).strip()
        for item in records
        for value in (
            item.get("requirement_identity_ids")
            or [item.get("requirement_identity_id") or item.get("candidate_id")]
        )
        if str(value or "").strip()
    }
    declared_count = int(campaign_batch.get("identity_count") or len(identity_ids))
    if declared_count != len(identity_ids) or not 1 <= declared_count <= 25:
        raise FormReviewSyncError("FORM_REVIEW_BATCH_SIZE_INVALID")
    expected_items = {_attestation_item_tuple(item) for item in records}
    submitted_items = {
        _attestation_item_tuple(item)
        for item in attestation.get("items") or []
        if isinstance(item, Mapping)
    }
    if expected_items != submitted_items or len(submitted_items) != len(
        attestation.get("items") or []
    ):
        raise FormReviewSyncError("PREVIEW_STALE_OR_TAMPERED")
    return records


def _campaign_candidate(record: Mapping[str, Any]) -> dict[str, Any]:
    delivery_type = str(record.get("delivery_type") or "").strip()
    return {
        **deepcopy(dict(record)),
        "id": _require_text(
            record.get("candidate_id") or record.get("id"),
            "CANDIDATE_ID_REQUIRED",
        ),
        "proposed_canonical_form_id": _require_text(
            record.get("canonical_form_id")
            or record.get("proposed_canonical_form_id"),
            "CANONICAL_FORM_ID_REQUIRED",
        ),
        "detected_form_name": record.get("canonical_name")
        or record.get("form_name"),
        "canonical_form_name": record.get("canonical_name")
        or record.get("form_name"),
        "suggested_procedure_id": record.get("procedure_id"),
        "procedure_mapping_verified": True,
        "administrative_level": record.get("administrative_level")
        or record.get("executing_level")
        or "commune",
        "jurisdiction": record.get("jurisdiction") or "Hai Phong",
        "review_status": "candidate_pending_review",
        "legal_review_status": "candidate_pending_review",
        "preparation_status": "ready_for_human_review",
        "technical_validation": {
            "status": "passed",
            "reason_codes": [],
            "source": "checksum_bound_form_resolution_campaign",
        },
        "delivery_type": delivery_type,
        "file_type": "online" if delivery_type == "interactive_eform" else None,
        "approved": False,
        "runtime_eligible": False,
        "is_approved": False,
        "is_canonical": False,
    }


def _candidate_path(candidate: Mapping[str, Any], project_root: Path) -> Path:
    relative = str(
        candidate.get("priority_path")
        or candidate.get("local_path")
        or candidate.get("file_path")
        or ""
    ).replace("\\", "/").lstrip("/")
    if not relative:
        raise FormReviewSyncError("FORM_FILE_MISSING")
    path = (project_root / relative).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise FormReviewSyncError("FORM_FILE_OUTSIDE_PROJECT") from exc
    if not path.is_file():
        raise FormReviewSyncError("FORM_FILE_MISSING")
    return path


def build_form_review_sync(
    *,
    candidate_payload: Mapping[str, Any],
    canonical_forms_payload: Mapping[str, Any],
    bindings_payload: Mapping[str, Any],
    attestations_payload: Mapping[str, Any],
    attestation: Mapping[str, Any],
    project_root: Path,
    official_index_payload: Mapping[str, Any] | None = None,
    checksum_manifest_payload: Mapping[str, Any] | None = None,
    campaign_batch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return synchronized payloads after validating one human attestation."""

    reviewer_id = _require_text(
        attestation.get("reviewer_id"), "REVIEWER_ID_REQUIRED"
    )
    if reviewer_id.casefold() in _AUTOMATED_REVIEWERS:
        raise FormReviewSyncError("REVIEWER_ID_REQUIRED")
    reviewed_at = _valid_iso_date(
        attestation.get("reviewed_at"), "REVIEWED_AT_REQUIRED"
    )
    attestation_id = _require_text(
        attestation.get("attestation_id"), "ATTESTATION_ID_REQUIRED"
    )
    decision = str(attestation.get("decision") or "").strip().casefold()
    if decision not in {"approved", "rejected"}:
        raise FormReviewSyncError("DECISION_INVALID")
    items = list(attestation.get("items") or [])
    if not items:
        raise FormReviewSyncError("ATTESTATION_ITEMS_REQUIRED")

    campaign_records = (
        _validate_campaign_batch(attestation, campaign_batch)
        if campaign_batch is not None
        else []
    )

    candidates_out = deepcopy(dict(candidate_payload))
    forms_out = deepcopy(dict(canonical_forms_payload))
    bindings_out = deepcopy(dict(bindings_payload))
    attestations_out = deepcopy(dict(attestations_payload))
    official_index_out = deepcopy(dict(official_index_payload or {"forms": []}))
    checksum_manifest_out = deepcopy(
        dict(checksum_manifest_payload or {"entries": []})
    )
    candidate_rows = list(candidates_out.get("records") or [])
    form_rows = list(forms_out.get("forms") or [])
    binding_rows = list(bindings_out.get("bindings") or [])
    audit_rows = list(attestations_out.get("attestations") or [])
    official_index_rows = list(official_index_out.get("forms") or [])
    checksum_rows = list(checksum_manifest_out.get("entries") or [])
    existing_candidate_ids = {
        str(item.get("id") or "").strip()
        for item in candidate_rows
        if isinstance(item, Mapping)
    }
    for record in campaign_records:
        candidate_id = str(
            record.get("candidate_id") or record.get("id") or ""
        ).strip()
        if candidate_id and candidate_id not in existing_candidate_ids:
            candidate_rows.append(_campaign_candidate(record))
            existing_candidate_ids.add(candidate_id)
    fingerprint = _attestation_fingerprint(attestation)

    existing = next(
        (
            row
            for row in audit_rows
            if str(row.get("attestation_id") or "") == attestation_id
        ),
        None,
    )
    if existing:
        if existing.get("fingerprint") != fingerprint:
            raise FormReviewSyncError("ATTESTATION_ID_CONFLICT")
        return {
            "status": "already_applied",
            "candidate_payload": candidates_out,
            "canonical_forms_payload": forms_out,
            "bindings_payload": bindings_out,
            "attestations_payload": attestations_out,
            "official_index_payload": official_index_out,
            "checksum_manifest_payload": checksum_manifest_out,
        }

    seen_items: set[tuple[str, str, str]] = set()
    audit_items: list[dict[str, Any]] = []
    for raw_item in items:
        item = dict(raw_item)
        candidate_id = _require_text(
            item.get("candidate_id"), "CANDIDATE_ID_REQUIRED"
        )
        canonical_form_id = _require_text(
            item.get("canonical_form_id"), "CANONICAL_FORM_ID_REQUIRED"
        )
        procedure_id = _require_text(
            item.get("procedure_id"), "PROCEDURE_ID_REQUIRED"
        )
        item_key = (candidate_id, canonical_form_id, procedure_id)
        if item_key in seen_items:
            raise FormReviewSyncError("DUPLICATE_ATTESTATION_ITEM")
        seen_items.add(item_key)

        candidate = next(
            (row for row in candidate_rows if str(row.get("id")) == candidate_id),
            None,
        )
        form = next(
            (row for row in form_rows if str(row.get("form_id")) == canonical_form_id),
            None,
        )
        if candidate is None:
            raise FormReviewSyncError("CANDIDATE_NOT_FOUND")
        is_candidate_proposal = (
            str(candidate.get("proposed_canonical_form_id") or "").strip()
            == canonical_form_id
            and candidate.get("procedure_mapping_verified") is True
        )
        technically_ready = (
            candidate.get("review_status") == "candidate_pending_review"
            and candidate.get("preparation_status") == "ready_for_human_review"
            and (candidate.get("technical_validation") or {}).get("status")
            == "passed"
        )
        if candidate.get("review_status") != "approved" and not technically_ready:
            raise FormReviewSyncError("CANDIDATE_NOT_QUEUE_APPROVED")
        if _is_blocked_candidate(candidate):
            raise FormReviewSyncError("SEED_DEMO_QUARANTINED")
        is_online = (
            str(candidate.get("delivery_type") or "").strip()
            == "interactive_eform"
            or str(candidate.get("file_type") or "").strip() == "online"
        )

        candidate_procedure = _candidate_procedure(candidate)
        if candidate_procedure != procedure_id:
            raise FormReviewSyncError("PROCEDURE_MAPPING_MISMATCH")
        if form is None:
            if not is_candidate_proposal:
                raise FormReviewSyncError("CANONICAL_FORM_NOT_FOUND")
            form = {
                "form_id": canonical_form_id,
                "procedure_ids": [procedure_id],
                "form_code": candidate.get("form_code"),
                "canonical_name": candidate.get("canonical_form_name")
                or candidate.get("detected_form_name"),
                "aliases": [],
                "audience": "citizen",
                "form_type": "application",
                "usage": "applicant_form",
                "required_or_conditional": "required",
                "condition": None,
                "domain": candidate.get("domain")
                or candidate.get("suggested_domain"),
                "administrative_level": candidate.get(
                    "administrative_level"
                )
                or "commune",
                "jurisdiction": candidate.get("jurisdiction") or "Hai Phong",
                "review_status": "candidate_pending_review",
                "legal_review_status": "not_reviewed",
                "approved": False,
                "runtime_eligible": False,
            }
            form_rows.append(form)
        elif procedure_id not in (form.get("procedure_ids") or []):
            if not is_candidate_proposal:
                raise FormReviewSyncError("PROCEDURE_MAPPING_MISMATCH")
            form["procedure_ids"] = sorted(
                {
                    *(
                        str(value)
                        for value in form.get("procedure_ids") or []
                        if value
                    ),
                    procedure_id,
                }
            )
        binding = next(
            (
                row
                for row in binding_rows
                if str(row.get("form_id")) == canonical_form_id
                and str(row.get("procedure_id")) == procedure_id
            ),
            None,
        )
        if binding is None:
            if not is_candidate_proposal:
                raise FormReviewSyncError("CANONICAL_BINDING_NOT_FOUND")
            binding = {
                "procedure_id": procedure_id,
                "form_id": canonical_form_id,
                "requirement_id": f"{procedure_id}:{canonical_form_id}",
                "required_or_conditional": "required",
                "condition": None,
                "binding_status": "candidate_pending_review",
                "review_status": "pending",
                "approved": False,
            }
            binding_rows.append(binding)
        candidate_domain = str(
            candidate.get("domain") or candidate.get("suggested_domain") or ""
        ).strip()
        if candidate_domain and str(form.get("domain") or "").strip() != candidate_domain:
            raise FormReviewSyncError("DOMAIN_MAPPING_MISMATCH")

        source_page = _candidate_source_page(candidate)
        source_download = _candidate_download_url(candidate)
        if not _is_official_url(source_page) or (
            not is_online and not _is_official_url(source_download)
        ):
            raise FormReviewSyncError("OFFICIAL_SOURCE_REQUIRED")

        effective_from = _valid_iso_date(
            item.get("effective_from"),
            "EFFECTIVE_FROM_REQUIRED",
            required=not is_online,
        )
        effective_to = _valid_iso_date(
            item.get("effective_to"), "EFFECTIVE_TO_INVALID", required=False
        )
        if effective_to and effective_from and effective_to <= effective_from:
            raise FormReviewSyncError("EFFECTIVITY_RANGE_INVALID")
        source_effective_from = _valid_iso_date(
            candidate.get("effective_from") or form.get("effective_from"),
            "EFFECTIVITY_UNKNOWN",
            required=not is_online,
        )
        source_effective_to = _valid_iso_date(
            candidate.get("effective_to") or form.get("effective_to"),
            "EFFECTIVE_TO_INVALID",
            required=False,
        )
        if effective_from != source_effective_from or effective_to != source_effective_to:
            raise FormReviewSyncError("EFFECTIVITY_METADATA_MISMATCH")
        if is_online:
            if (
                str(candidate.get("effectivity_reason_code") or "").strip()
                != "OFFICIAL_EFORM_TECHNICALLY_VERIFIED"
            ):
                raise FormReviewSyncError("EFORM_EFFECTIVITY_UNVERIFIED")
            if not str(candidate.get("legal_as_of") or "").strip():
                raise FormReviewSyncError("LEGAL_AS_OF_INVALID")
        elif not candidate.get("legal_basis") and not form.get("legal_basis"):
            raise FormReviewSyncError("LEGAL_BASIS_MISSING")
        reviewed_date = _date_only(reviewed_at)
        if (
            reviewed_date
            and _date_only(effective_from)
            and _date_only(effective_from) > reviewed_date
        ):
            raise FormReviewSyncError("FORM_NOT_YET_EFFECTIVE")
        if (
            reviewed_date
            and _date_only(effective_to)
            and _date_only(effective_to) < reviewed_date
        ):
            raise FormReviewSyncError("FORM_EXPIRED")
        if (
            candidate.get("supersedes_form_id")
            or form.get("supersedes_form_id")
            or "superseded"
            in str(candidate.get("effective_status") or "").casefold()
            or "superseded"
            in str(candidate.get("legacy_form_status") or "").casefold()
        ):
            raise FormReviewSyncError("FORM_SUPERSEDED")

        path: Path | None = None
        expected_sha: str | None = None
        file_format = "online" if is_online else ""
        route_sha256: str | None = None
        if is_online:
            route_sha256 = hashlib.sha256(source_page.encode("utf-8")).hexdigest()
        else:
            path = _candidate_path(candidate, project_root)
            expected_sha = _require_text(
                candidate.get("sha256"), "CHECKSUM_REQUIRED"
            )
            if _sha256(path) != expected_sha:
                raise FormReviewSyncError("CHECKSUM_MISMATCH")
            file_format = path.suffix.lstrip(".").casefold()
            valid_file, _reason = _file_integrity(path, file_format)
            if not valid_file:
                raise FormReviewSyncError("FORM_FILE_INVALID")

        approved = decision == "approved"
        candidate["review_status"] = decision
        candidate["is_approved"] = approved
        candidate["approved"] = approved
        candidate["legal_review_status"] = decision
        candidate["reviewed_by"] = reviewer_id
        candidate["legal_reviewed_at"] = reviewed_at
        candidate["legal_review_attestation_id"] = attestation_id
        candidate["is_canonical"] = approved
        candidate["runtime_eligible"] = approved
        candidate["catalog_status"] = (
            "available_official_source" if approved else "rejected"
        )

        form.update(
            {
                "official_source_page": source_page,
                "official_download_url": source_download or None,
                "official_open_url": source_page if is_online else None,
                "local_path": (
                    str(path.relative_to(project_root)).replace("\\", "/")
                    if path is not None
                    else None
                ),
                "file_format": file_format,
                "file_type": "online" if is_online else file_format,
                "sha256": expected_sha,
                "route_sha256": route_sha256,
                "legal_basis": list(candidate.get("legal_basis") or []),
                "effective_from": effective_from,
                "effective_to": effective_to,
                "jurisdiction": item.get("jurisdiction") or form.get("jurisdiction"),
                "administrative_level": (
                    item.get("administrative_level")
                    or form.get("administrative_level")
                ),
                "source_classification": (
                    "official_eform" if is_online else "official_file"
                ),
                "review_status": decision,
                "legal_review_status": decision,
                "approved": approved,
                "runtime_eligible": approved,
                "url_status": "verified" if approved else "rejected",
                "provenance": {
                    **dict(candidate.get("provenance") or {}),
                    "candidate_id": candidate_id,
                    "attestation_id": attestation_id,
                    "reviewer_id": reviewer_id,
                    "reviewed_at": reviewed_at,
                    "batch_id": attestation.get("batch_id"),
                    "manifest_sha256": attestation.get("manifest_sha256"),
                    "source_snapshot_sha256": attestation.get(
                        "source_snapshot_sha256"
                    ),
                },
            }
        )
        binding.update(
            {
                "binding_status": decision,
                "review_status": decision,
                "approved": approved,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "attestation_id": attestation_id,
            }
        )
        official_entry = next(
            (
                row
                for row in official_index_rows
                if str(row.get("id") or "") == candidate_id
                or str(row.get("source_sha256") or row.get("sha256") or "")
                == expected_sha
            ),
            None,
        )
        if official_entry is None:
            official_entry = {
                "id": candidate_id,
                "form_title": form.get("canonical_name")
                or candidate.get("detected_form_name"),
                "source_page_url": source_page,
                "source_download_url": source_download,
                "source_sha256": expected_sha,
                "route_sha256": route_sha256,
                "file_type": "online" if is_online else file_format,
                "priority_path": (
                    str(path.relative_to(project_root)).replace("\\", "/")
                    if path is not None
                    else None
                ),
            }
            official_index_rows.append(official_entry)
        canonical_form_ids = {
            str(value)
            for value in official_entry.get("canonical_form_ids") or []
            if value
        }
        canonical_form_ids.add(canonical_form_id)
        official_procedure_ids = {
            str(value)
            for value in official_entry.get("procedure_ids") or []
            if value
        }
        official_procedure_ids.add(procedure_id)
        official_entry.update(
            {
                "canonical_form_ids": sorted(canonical_form_ids),
                "procedure_ids": sorted(official_procedure_ids),
                "review_status": decision,
                "legal_review_status": decision,
                "catalog_status": (
                    "available_official_source" if approved else "rejected"
                ),
                "is_canonical": approved,
                "reviewer_id": reviewer_id,
                "reviewed_at": reviewed_at,
                "attestation_id": attestation_id,
            }
        )
        checksum_entry = next(
            (
                row
                for row in checksum_rows
                if str(row.get("form_id") or "") == canonical_form_id
                and str(row.get("procedure_id") or "") == procedure_id
            ),
            None,
        )
        checksum_payload = {
            "form_id": canonical_form_id,
            "procedure_id": procedure_id,
            "candidate_id": candidate_id,
            "sha256": expected_sha,
            "route_sha256": route_sha256,
            "file_type": "online" if is_online else file_format,
            "local_path": (
                str(path.relative_to(project_root)).replace("\\", "/")
                if path is not None
                else None
            ),
            "attestation_id": attestation_id,
            "reviewed_at": reviewed_at,
            "status": decision,
        }
        if checksum_entry is None:
            checksum_rows.append(checksum_payload)
        else:
            checksum_entry.update(checksum_payload)
        audit_items.append(
            {
                "candidate_id": candidate_id,
                "canonical_form_id": canonical_form_id,
                "procedure_id": procedure_id,
                "sha256": expected_sha,
                "route_sha256": route_sha256,
                "decision": decision,
            }
        )

    candidates_out["records"] = candidate_rows
    forms_out["forms"] = form_rows
    bindings_out["bindings"] = binding_rows
    official_index_out["forms"] = official_index_rows
    official_index_out["legal_review_synced_at"] = reviewed_at
    checksum_rows.sort(
        key=lambda row: (
            str(row.get("form_id") or ""),
            str(row.get("procedure_id") or ""),
        )
    )
    checksum_manifest_out["entries"] = checksum_rows
    checksum_manifest_out["updated_at"] = reviewed_at
    checksum_manifest_out["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            checksum_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    audit_rows.append(
        {
            "attestation_id": attestation_id,
            "fingerprint": fingerprint,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at,
            "decision": decision,
            "review_note": str(attestation.get("review_note") or "").strip(),
            "item_count": len(audit_items),
            "items": audit_items,
            "batch_id": attestation.get("batch_id"),
            "preview_fingerprint": attestation.get("preview_fingerprint"),
            "manifest_sha256": attestation.get("manifest_sha256"),
            "source_snapshot_sha256": attestation.get(
                "source_snapshot_sha256"
            ),
            "automated_approval": False,
        }
    )
    attestations_out["attestations"] = audit_rows
    return {
        "status": "applied",
        "candidate_payload": candidates_out,
        "canonical_forms_payload": forms_out,
        "bindings_payload": bindings_out,
        "attestations_payload": attestations_out,
        "official_index_payload": official_index_out,
        "checksum_manifest_payload": checksum_manifest_out,
    }


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(default)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise FormReviewSyncError("CATALOG_PAYLOAD_INVALID")
    return payload


def apply_form_review_sync_files(
    *,
    candidate_path: Path,
    canonical_forms_path: Path,
    bindings_path: Path,
    attestations_path: Path,
    attestation: Mapping[str, Any],
    project_root: Path,
    official_index_path: Path | None = None,
    checksum_manifest_path: Path | None = None,
    campaign_batch: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate first, then replace all review catalogs with rollback on error."""

    paths = {
        "candidate_payload": candidate_path,
        "canonical_forms_payload": canonical_forms_path,
        "bindings_payload": bindings_path,
        "attestations_payload": attestations_path,
    }
    if official_index_path is not None:
        paths["official_index_payload"] = official_index_path
    if checksum_manifest_path is not None:
        paths["checksum_manifest_payload"] = checksum_manifest_path
    payloads = {
        "candidate_payload": _load_json(candidate_path, {"records": []}),
        "canonical_forms_payload": _load_json(
            canonical_forms_path, {"forms": []}
        ),
        "bindings_payload": _load_json(bindings_path, {"bindings": []}),
        "attestations_payload": _load_json(
            attestations_path, {"attestations": []}
        ),
        "official_index_payload": _load_json(
            official_index_path, {"forms": []}
        )
        if official_index_path is not None
        else {"forms": []},
        "checksum_manifest_payload": _load_json(
            checksum_manifest_path, {"entries": []}
        )
        if checksum_manifest_path is not None
        else {"entries": []},
    }
    result = build_form_review_sync(
        **payloads,
        attestation=attestation,
        project_root=project_root,
        campaign_batch=campaign_batch,
    )
    if result["status"] == "already_applied":
        return result

    original_bytes = {
        key: path.read_bytes() if path.exists() else None for key, path in paths.items()
    }
    temp_paths: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for key, path in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temp.write_text(
                json.dumps(result[key], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp_paths[key] = temp
        for key, path in paths.items():
            os.replace(temp_paths[key], path)
            replaced.append(key)
    except Exception as exc:
        for key in reversed(replaced):
            path = paths[key]
            original = original_bytes[key]
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(original)
        raise FormReviewSyncError("CATALOG_TRANSACTION_FAILED") from exc
    finally:
        for temp in temp_paths.values():
            if temp.exists():
                temp.unlink()
    return result
