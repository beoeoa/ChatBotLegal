"""Deterministic orchestration helpers for completing the canonical form queue.

The campaign may discover official sources, download candidate files, verify
checksums and prepare technical metadata.  It never creates a legal-review
decision and never makes a form runtime-eligible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from api.form_source_resolution import current_dvc_attachment_rule_for_candidate
from api.legal_form_catalog import _is_official_url
from api.source_gap_jobs import create_source_gap_job

_DOWNLOADABLE_FINDING_STATUSES = {
    "AVAILABLE_OFFICIAL_FILE",
    "OFFICIAL_PACKAGE_PAGE",
}
_FINDING_EVIDENCE_STRENGTH = {
    "BLOCKED_EXTERNAL": 0,
    "NEEDS_SOURCE_MAPPING": 1,
    "OFFICIAL_PACKAGE_PAGE": 3,
    "OFFICIAL_EFORM": 4,
    "AVAILABLE_OFFICIAL_FILE": 5,
    "NO_PUBLIC_DOWNLOAD_VERIFIED": 6,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legal_basis(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        if isinstance(value, Mapping):
            text = _text(value.get("code") or value.get("name"))
        else:
            text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _is_approved(form: Mapping[str, Any]) -> bool:
    return (
        form.get("approved") is True
        and form.get("review_status") == "approved"
        and form.get("legal_review_status") == "approved"
    )


def build_form_completion_jobs(
    *,
    canonical_forms_payload: Mapping[str, Any],
    form_source_findings_payload: Mapping[str, Any],
    procedure_sources_payload: Mapping[str, Any],
    legal_as_of: str,
) -> dict[str, Any]:
    """Build stable source-gap jobs for every safely downloadable active form."""

    findings = {
        _text(item.get("form_id")): item
        for item in form_source_findings_payload.get("findings") or []
        if isinstance(item, Mapping) and _text(item.get("form_id"))
    }
    procedure_sources = {
        _text(item.get("procedure_id")): item
        for item in procedure_sources_payload.get("procedures") or []
        if isinstance(item, Mapping) and _text(item.get("procedure_id"))
    }
    jobs: list[dict[str, Any]] = []
    unresolved_reasons: Counter[str] = Counter()
    skipped_approved = 0
    skipped_catalog_excluded = 0

    for form in sorted(
        (
            item
            for item in canonical_forms_payload.get("forms") or []
            if isinstance(item, Mapping)
        ),
        key=lambda item: _text(item.get("form_id")),
    ):
        if form.get("catalog_disposition") == "excluded_no_official_form":
            skipped_catalog_excluded += 1
            continue
        if _is_approved(form):
            skipped_approved += 1
            continue

        form_id = _text(form.get("form_id"))
        finding = findings.get(form_id, {})
        procedure_id = _text(
            finding.get("procedure_id")
            or next(iter(form.get("procedure_ids") or []), "")
        )
        procedure = procedure_sources.get(procedure_id, {})
        finding_status = _text(finding.get("status"))

        if finding_status == "OFFICIAL_EFORM":
            unresolved_reasons["OFFICIAL_EFORM_REQUIRES_SEPARATE_GATE"] += 1
            continue
        if finding_status not in _DOWNLOADABLE_FINDING_STATUSES:
            unresolved_reasons["NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH"] += 1
            continue

        download_url = _text(finding.get("download_url"))
        source_page = _text(
            finding.get("source_page")
            or procedure.get("official_procedure_url")
        )
        seeds = [
            value
            for value in (download_url, source_page)
            if value and _is_official_url(value)
        ]
        seeds = list(dict.fromkeys(seeds))
        if not seeds:
            unresolved_reasons["OFFICIAL_SOURCE_URL_MISSING"] += 1
            continue

        official_procedure_code = _text(
            finding.get("official_procedure_code")
            or procedure.get("official_procedure_code")
        )
        legal_basis = _legal_basis(
            finding.get("legal_basis") or procedure.get("legal_basis")
        )
        job = create_source_gap_job(
            case_id=form_id,
            gap_type="MISSING_FORM_SOURCE",
            source_pages=seeds,
            legal_as_of=legal_as_of,
            procedure_id=procedure_id,
            expected_name=_text(form.get("canonical_name")),
            expected_code=_text(form.get("form_code")),
            source_pages_zero_based=finding.get("source_pages_zero_based") or [],
        )
        if job is None:  # pragma: no cover - MISSING_FORM_SOURCE always creates
            continue
        job.update(
            {
                "canonical_form_id": form_id,
                "official_procedure_code": official_procedure_code,
                "official_metadata": {
                    "domain": _text(form.get("domain")) or "unknown",
                    "legal_basis": legal_basis,
                    "confirmed_official_source": True,
                    "official_procedure_code": official_procedure_code,
                },
                "automated_approval": False,
            }
        )
        jobs.append(job)

    return {
        "jobs": jobs,
        "unresolved_reason_counts": dict(sorted(unresolved_reasons.items())),
        "skipped_approved_count": skipped_approved,
        "skipped_catalog_excluded_count": skipped_catalog_excluded,
        "auto_approved_count": 0,
    }


def merge_source_findings_preserving_stronger_evidence(
    previous_payload: Mapping[str, Any],
    refreshed_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Prevent transient/ambiguous discovery from weakening verified evidence."""

    result = deepcopy(dict(refreshed_payload))
    previous = {
        _text(item.get("form_id")): dict(item)
        for item in previous_payload.get("findings") or []
        if isinstance(item, Mapping) and _text(item.get("form_id"))
    }
    refreshed = {
        _text(item.get("form_id")): dict(item)
        for item in refreshed_payload.get("findings") or []
        if isinstance(item, Mapping) and _text(item.get("form_id"))
    }
    merged: list[dict[str, Any]] = []
    for form_id in sorted(set(previous) | set(refreshed)):
        old = previous.get(form_id)
        new = refreshed.get(form_id)
        if new is None:
            merged.append(dict(old or {}))
            continue
        if old is None:
            merged.append(dict(new))
            continue
        old_strength = _FINDING_EVIDENCE_STRENGTH.get(
            _text(old.get("status")), -1
        )
        new_strength = _FINDING_EVIDENCE_STRENGTH.get(
            _text(new.get("status")), -1
        )
        merged.append(dict(old if old_strength > new_strength else new))
    result["findings"] = merged
    result["preserved_stronger_evidence_count"] = sum(
        1
        for form_id, old in previous.items()
        if form_id in refreshed
        and _FINDING_EVIDENCE_STRENGTH.get(_text(old.get("status")), -1)
        > _FINDING_EVIDENCE_STRENGTH.get(
            _text(refreshed[form_id].get("status")), -1
        )
    )
    return result


def build_privacy_safe_campaign_report(
    *,
    run_id: str,
    legal_as_of: str,
    status: str,
    counts: Mapping[str, int],
    reason_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Return aggregate-only progress suitable for the Admin UI and reports."""

    return {
        "schema_version": "form-completion-campaign-v1",
        "run_id": _text(run_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": _text(legal_as_of),
        "status": _text(status),
        "counts": {
            _text(key): int(value)
            for key, value in sorted(counts.items())
        },
        "reason_counts": {
            _text(key): int(value)
            for key, value in sorted(reason_counts.items())
        },
        "automated_approval": False,
        "human_attestation_required": True,
    }


def apply_candidate_technical_validation(
    candidate: Mapping[str, Any],
    *,
    form: Mapping[str, Any],
    finding: Mapping[str, Any],
    effectivity_rules: list[Mapping[str, Any]],
    validated_at: str | None = None,
) -> dict[str, Any]:
    """Attach deterministic readiness evidence without changing review state."""

    result = deepcopy(dict(candidate))
    reasons: list[str] = []
    procedure_id = _text(
        result.get("procedure_id") or result.get("suggested_procedure_id")
    )
    form_procedures = {
        _text(value) for value in form.get("procedure_ids") or [] if _text(value)
    }
    if not procedure_id or procedure_id not in form_procedures:
        reasons.append("PROCEDURE_MAPPING_MISMATCH")
    if not _is_official_url(
        result.get("source_page_url") or result.get("source_url")
    ):
        reasons.append("OFFICIAL_SOURCE_REQUIRED")
    if not _is_official_url(result.get("source_download_url")):
        reasons.append("OFFICIAL_DOWNLOAD_REQUIRED")
    checksum = _text(result.get("sha256")).casefold()
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        reasons.append("CHECKSUM_REQUIRED")
    if not _legal_basis(result.get("legal_basis")):
        reasons.append("LEGAL_BASIS_MISSING")

    official_code = _text(result.get("official_procedure_code"))
    matched_rule = current_dvc_attachment_rule_for_candidate(
        result,
        effectivity_rules,
    )
    for rule in effectivity_rules:
        if matched_rule is not None:
            break
        if str(rule.get("evidence_basis") or "") == (
            "official_current_dvc_attachment"
        ):
            continue
        codes = {_text(value) for value in rule.get("official_procedure_codes") or []}
        procedures = {_text(value) for value in rule.get("procedure_ids") or []}
        if codes and official_code in codes:
            matched_rule = rule
            break
        if procedures and procedure_id in procedures:
            matched_rule = rule
            break
    is_dvc_attachment_rule = (
        matched_rule is not None
        and _text(matched_rule.get("evidence_basis"))
        == "official_current_dvc_attachment"
    )
    effective_from = _text(result.get("effective_from")) or (
        _text(matched_rule.get("effective_from")) if matched_rule else ""
    )
    if matched_rule is None or not effective_from:
        reasons.append("EFFECTIVITY_UNKNOWN")
    elif not _is_official_url(matched_rule.get("official_source_url")):
        reasons.append("EFFECTIVITY_SOURCE_NOT_OFFICIAL")
    else:
        result["effective_from"] = effective_from
        if not is_dvc_attachment_rule:
            result["effective_to"] = (
                _text(matched_rule.get("effective_to")) or None
            )
        basis = _legal_basis(
            [
                *list(result.get("legal_basis") or []),
                *list(matched_rule.get("legal_basis_additions") or []),
            ]
        )
        result["legal_basis"] = basis
        result["effectivity_provenance"] = {
            "rule_id": matched_rule.get("rule_id")
            or matched_rule.get("evidence_id"),
            "official_source_url": matched_rule.get("official_source_url"),
            "verified_as_of": matched_rule.get("verified_as_of"),
            "evidence_basis": matched_rule.get("evidence_basis") or None,
            "source_attachment_id": (
                (matched_rule.get("canonical_artifact_attachment") or {}).get(
                    "attachment_id"
                )
                if is_dvc_attachment_rule
                else None
            ),
            "publication_decision_number": (
                matched_rule.get("publication_decision_number")
                if is_dvc_attachment_rule
                else None
            ),
        }

    extraction = result.get("extraction")
    extraction_is_complete = isinstance(extraction, Mapping) and (
        extraction.get("complete") is True
    )
    if (
        _text(finding.get("status")) == "OFFICIAL_PACKAGE_PAGE"
        and not list(
            (result.get("provenance") or {}).get("source_pages_zero_based")
            or result.get("source_pages_zero_based")
            or finding.get("source_pages_zero_based")
            or []
        )
        and not extraction_is_complete
    ):
        reasons.append("OFFICIAL_PACKAGE_CONTENT_NOT_ISOLATED")

    result["technical_validation"] = {
        "status": "passed" if not reasons else "failed",
        "validated_at": validated_at or datetime.now(timezone.utc).isoformat(),
        "reason_codes": sorted(set(reasons)),
        "automated_approval": False,
    }
    result["preparation_status"] = (
        "ready_for_human_review"
        if not reasons
        else "technical_evidence_incomplete"
    )
    result["review_status"] = "candidate_pending_review"
    result["legal_review_status"] = "candidate_pending_review"
    result["approved"] = False
    result["is_approved"] = False
    result["runtime_eligible"] = False
    return result


def load_campaign_status(status_path: Path) -> dict[str, Any]:
    if not status_path.is_file():
        return build_privacy_safe_campaign_report(
            run_id="not-started",
            legal_as_of="",
            status="not_started",
            counts={},
            reason_counts={},
        ) | {"stage": "not_started"}
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("FORM_COMPLETION_STATUS_INVALID")
    return payload


def is_form_completion_campaign_due(
    current: Mapping[str, Any],
    *,
    legal_as_of: str,
) -> bool:
    """Return true once per legal date, independent of the prior outcome.

    Manual Admin launches may still rerun the campaign on the same day.  This
    policy applies only to the background scheduler and prevents network retry
    storms when an official publisher remains unavailable.
    """

    date.fromisoformat(legal_as_of)
    return _text(current.get("legal_as_of")) != legal_as_of


def launch_form_completion_campaign(
    *,
    project_root: Path,
    legal_as_of: str,
    status_path: Path,
    launcher: Any = subprocess.Popen,
) -> dict[str, Any]:
    """Start one detached campaign runner and return aggregate queued state."""

    date.fromisoformat(legal_as_of)
    current = load_campaign_status(status_path)
    if current.get("status") in {"queued", "running"}:
        return {**current, "launch_status": "already_running"}
    queued = build_privacy_safe_campaign_report(
        run_id="queued",
        legal_as_of=legal_as_of,
        status="queued",
        counts={},
        reason_counts={},
    )
    queued["stage"] = "queued"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_suffix(f"{status_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(queued, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, status_path)

    command = [
        sys.executable,
        str(project_root / "scripts" / "run_form_completion_campaign.py"),
        "--legal-as-of",
        legal_as_of,
    ]
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
        process = launcher(command, **kwargs)
    except Exception:
        failed = {**queued, "status": "failed_fail_closed", "stage": "launch_failed"}
        temporary.write_text(
            json.dumps(failed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, status_path)
        raise
    return {
        **queued,
        "launch_status": "started",
    }
