"""Deterministic reconciliation of canonical forms and official candidates.

The reconciliation layer is deliberately candidate-only.  It may attach an
explicit candidate identifier and source-backed effectivity provenance, but it
cannot approve a canonical form or create a legal-review decision.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from api.form_source_resolution import current_dvc_attachment_rule_for_candidate
from api.legal_form_catalog import _is_official_url, fold_text

_BLOCKED_MARKERS = ("[demo]", " demo", "seed", "synthetic", "quarantine")
_RECONCILIATION_STATUSES = {
    "AVAILABLE_OFFICIAL_FILE",
    "BLOCKED_EXTERNAL",
    "EXCLUDED_NO_OFFICIAL_FORM",
    "MAPPED_OFFICIAL_CANDIDATE",
    "NEEDS_SOURCE_MAPPING",
    "NO_PUBLIC_DOWNLOAD_VERIFIED",
    "OFFICIAL_EFORM",
    "OFFICIAL_PACKAGE_PAGE",
    "SOURCE_DOWNLOAD_RETRY_REQUIRED",
    "SOURCE_MAPPING_UNRESOLVED",
    "VERIFIED_DATA_GAP",
}
_NO_OFFICIAL_FORM_DISPOSITION = "excluded_no_official_form"


def _date_only(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _candidate_procedure(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("procedure_id")
        or candidate.get("suggested_procedure_id")
        or ""
    ).strip()


def _candidate_name(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("canonical_form_name")
        or candidate.get("form_title")
        or candidate.get("detected_form_name")
        or ""
    ).strip()


def _candidate_source(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("source_page_url")
        or candidate.get("page_url")
        or candidate.get("source_url")
        or ""
    ).strip()


def _candidate_download(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("source_download_url")
        or candidate.get("official_download_url")
        or candidate.get("source_url")
        or ""
    ).strip()


def _candidate_path(
    candidate: Mapping[str, Any],
    project_root: Path,
) -> Path | None:
    value = str(
        candidate.get("priority_path")
        or candidate.get("local_path")
        or candidate.get("file_path")
        or ""
    ).replace("\\", "/").lstrip("/")
    if not value:
        return None
    root = project_root.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_blocked(candidate: Mapping[str, Any]) -> bool:
    if any(
        candidate.get(key) is True
        for key in (
            "is_demo",
            "is_seed",
            "synthetic",
            "quarantined",
            "test_only",
        )
    ):
        return True
    material = " ".join(
        str(candidate.get(key) or "")
        for key in (
            "id",
            "canonical_form_name",
            "detected_form_name",
            "catalog_status",
            "preparation_status",
            "legacy_form_status",
        )
    ).casefold()
    return any(marker in f" {material}" for marker in _BLOCKED_MARKERS)


def _candidate_is_verifiable(
    candidate: Mapping[str, Any],
    project_root: Path,
) -> bool:
    if _is_blocked(candidate):
        return False
    technically_ready = (
        candidate.get("review_status") == "candidate_pending_review"
        and candidate.get("preparation_status") == "ready_for_human_review"
        and (candidate.get("technical_validation") or {}).get("status")
        == "passed"
    )
    if candidate.get("review_status") != "approved" and not technically_ready:
        return False
    if not _is_official_url(_candidate_source(candidate)):
        return False
    if not _is_official_url(_candidate_download(candidate)):
        return False
    path = _candidate_path(candidate, project_root)
    expected = str(candidate.get("sha256") or "").strip().casefold()
    return bool(path and expected and _sha256(path) == expected)


def _form_code(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", fold_text(str(value or "")))


def _code_from_name(value: Any) -> str:
    text = fold_text(str(value or ""))
    patterns = (
        r"\bct\s*0?([0-9]{1,2})\b",
        r"\bmau\s*(?:so\s*)?([0-9]{1,2}[a-z]?)\s*/\s*dk\b",
        r"\bmau\s*(?:so\s*)?([0-9]{1,2}[a-z]?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            if pattern.startswith(r"\bct"):
                return f"ct{int(match.group(1)):02d}"
            if "/\\s*dk" in pattern:
                return f"{match.group(1)}dk"
            return match.group(1)
    return ""


def _normalized_identity(value: Any, code: Any = None) -> str:
    text = fold_text(str(value or ""))
    normalized_code = _form_code(code) or _code_from_name(text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(
        r"^\s*mau\s*(?:so\s*)?(?:[0-9]{1,2}[a-z]?(?:\s*/\s*dk)?|ct\s*[0-9]{1,2})?\s*[-:]*\s*",
        " ",
        text,
    )
    if normalized_code:
        variants = {normalized_code}
        if normalized_code.startswith("ct") and normalized_code[2:].isdigit():
            variants.add(f"ct {int(normalized_code[2:])}")
        if normalized_code.endswith("dk"):
            variants.add(f"{normalized_code[:-2]} dk")
        for variant in variants:
            text = re.sub(
                rf"\b{re.escape(variant)}\b",
                " ",
                text,
            )
    text = re.sub(r"[^0-9a-z\s]", " ", text)
    return " ".join(text.split())


def _evidence_hashes(form: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    if form.get("sha256"):
        values.add(str(form["sha256"]).strip().casefold())
    for item in form.get("candidate_source_evidence") or []:
        if (
            isinstance(item, Mapping)
            and item.get("status") not in _RECONCILIATION_STATUSES
            and item.get("sha256")
        ):
            values.add(str(item["sha256"]).strip().casefold())
    return values


def _replace_reconciliation_evidence(
    form: dict[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    current = [
        dict(item)
        for item in form.get("candidate_source_evidence") or []
        if isinstance(item, Mapping)
        and item.get("status") not in _RECONCILIATION_STATUSES
    ]
    current.append(dict(evidence))
    form["candidate_source_evidence"] = current


def _exclude_form_without_official_template(
    form: dict[str, Any],
    *,
    legal_as_of: str,
    official_sources: list[str],
) -> None:
    """Remove a non-form reference from serving/review while retaining audit."""

    form["catalog_disposition"] = _NO_OFFICIAL_FORM_DISPOSITION
    form["catalog_status"] = _NO_OFFICIAL_FORM_DISPOSITION
    form["review_queue_eligible"] = False
    form["runtime_eligible"] = False
    if form.get("review_status") != "approved":
        form["approved"] = False
    form["catalog_exclusion"] = {
        "reason_code": "NO_OFFICIAL_STATE_FORM_CONFIRMED",
        "legal_as_of": legal_as_of,
        "official_sources_checked": official_sources,
        "data_retained_for_audit": True,
        "auto_approved": False,
    }


def _reopen_previously_excluded_form(form: dict[str, Any]) -> None:
    """Reopen a record only when newer official evidence supersedes exclusion."""

    if form.get("catalog_disposition") != _NO_OFFICIAL_FORM_DISPOSITION:
        return
    form["catalog_disposition"] = "candidate_pending_review"
    form["catalog_status"] = "legal_review_required"
    form["review_queue_eligible"] = True
    form["runtime_eligible"] = False
    form.pop("catalog_exclusion", None)


def _select_match(
    form: Mapping[str, Any],
    candidates: Iterable[dict[str, Any]],
    *,
    project_root: Path,
    official_code: str,
) -> tuple[dict[str, Any] | None, str, list[str]]:
    procedure_ids = {
        str(value).strip()
        for value in form.get("procedure_ids") or []
        if str(value or "").strip()
    }
    expected_code = _form_code(form.get("form_code"))
    expected_identity = _normalized_identity(
        form.get("canonical_name"),
        form.get("form_code"),
    )
    expected_hashes = _evidence_hashes(form)
    ranked: list[tuple[int, str, str, dict[str, Any]]] = []
    for candidate in candidates:
        if _candidate_procedure(candidate) not in procedure_ids:
            continue
        if not _candidate_is_verifiable(candidate, project_root):
            continue
        candidate_code = _form_code(candidate.get("form_code")) or _code_from_name(
            _candidate_name(candidate)
        )
        candidate_identity = _normalized_identity(
            _candidate_name(candidate),
            candidate_code,
        )
        candidate_hash = str(candidate.get("sha256") or "").strip().casefold()
        score = 0
        reason = ""
        if expected_code and candidate_code == expected_code:
            score = 400
            reason = "EXACT_PROCEDURE_AND_FORM_CODE"
        elif expected_hashes and candidate_hash in expected_hashes:
            score = 350
            reason = "EXACT_PROCEDURE_AND_CHECKSUM"
        elif expected_identity and candidate_identity == expected_identity:
            score = 300
            reason = "EXACT_PROCEDURE_AND_FORM_IDENTITY"
        if not score:
            continue
        if (
            official_code
            and str(candidate.get("official_procedure_code") or "").strip()
            == official_code
        ):
            score += 25
        ranked.append(
            (
                score,
                reason,
                str(candidate.get("id") or ""),
                candidate,
            )
        )
    if not ranked:
        return None, "NO_EXACT_OFFICIAL_CANDIDATE", []
    ranked.sort(key=lambda item: (-item[0], item[2]))
    best_score, best_reason, _candidate_id, best = ranked[0]
    best_rows = [item for item in ranked if item[0] == best_score]
    if len(best_rows) == 1:
        return best, best_reason, []
    identities = {
        (
            str(item[3].get("sha256") or "").casefold(),
            _candidate_download(item[3]),
            _candidate_source(item[3]),
        )
        for item in best_rows
    }
    duplicate_ids = sorted(item[2] for item in best_rows)
    if len(identities) == 1:
        return best_rows[0][3], best_reason, duplicate_ids
    return None, "AMBIGUOUS_EXACT_CANDIDATES", duplicate_ids


def _effectivity_rule_for_candidate(
    candidate: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
    *,
    legal_as_of: date,
) -> Mapping[str, Any] | None:
    rules = list(rules)
    dvc_rule = current_dvc_attachment_rule_for_candidate(candidate, rules)
    if dvc_rule is not None:
        return dvc_rule
    official_code = str(candidate.get("official_procedure_code") or "").strip()
    procedure_id = _candidate_procedure(candidate)
    candidate_id = str(candidate.get("id") or "").strip()
    for rule in rules:
        if str(rule.get("evidence_basis") or "") == (
            "official_current_dvc_attachment"
        ):
            continue
        source_url = str(rule.get("official_source_url") or "").strip()
        start = _date_only(rule.get("effective_from"))
        if not _is_official_url(source_url) or start is None or start > legal_as_of:
            continue
        candidate_ids = {
            str(value).strip()
            for value in rule.get("candidate_ids") or []
            if str(value or "").strip()
        }
        procedure_ids = {
            str(value).strip()
            for value in rule.get("procedure_ids") or []
            if str(value or "").strip()
        }
        procedure_codes = {
            str(value).strip()
            for value in rule.get("official_procedure_codes") or []
            if str(value or "").strip()
        }
        if candidate_ids and candidate_id not in candidate_ids:
            continue
        if procedure_ids and procedure_id not in procedure_ids:
            continue
        if procedure_codes and official_code not in procedure_codes:
            continue
        if not (candidate_ids or procedure_ids or procedure_codes):
            continue
        return rule
    return None


def reconcile_form_candidates(
    *,
    canonical_forms_payload: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    procedure_sources_payload: Mapping[str, Any],
    effectivity_evidence_payload: Mapping[str, Any],
    form_source_findings_payload: Mapping[str, Any] | None = None,
    project_root: Path,
    legal_as_of: str,
) -> dict[str, Any]:
    """Reconcile all canonical forms without creating a legal approval."""

    active_date = _date_only(legal_as_of)
    if active_date is None:
        raise ValueError("LEGAL_AS_OF_INVALID")
    forms_out = deepcopy(dict(canonical_forms_payload))
    candidates_out = deepcopy(dict(candidate_payload))
    forms = [
        item for item in forms_out.get("forms") or [] if isinstance(item, dict)
    ]
    candidates = [
        item
        for item in candidates_out.get("records") or []
        if isinstance(item, dict)
    ]
    procedure_sources = {
        str(item.get("procedure_id") or ""): item
        for item in procedure_sources_payload.get("procedures") or []
        if isinstance(item, Mapping)
    }
    form_source_findings = {
        str(item.get("form_id") or ""): item
        for item in (form_source_findings_payload or {}).get("findings") or []
        if isinstance(item, Mapping)
    }
    rules = [
        item
        for item in effectivity_evidence_payload.get("rules") or []
        if isinstance(item, Mapping)
    ]
    rules.extend(
        item
        for item in effectivity_evidence_payload.get("form_effectivity") or []
        if isinstance(item, Mapping)
    )

    mapped_candidate_ids: set[str] = set()
    outcomes: list[dict[str, Any]] = []
    for form in sorted(forms, key=lambda item: str(item.get("form_id") or "")):
        procedure_id = str((form.get("procedure_ids") or [""])[0] or "")
        source = procedure_sources.get(procedure_id, {})
        official_code = str(source.get("official_procedure_code") or "").strip()
        candidate, reason, duplicate_ids = _select_match(
            form,
            candidates,
            project_root=project_root,
            official_code=official_code,
        )
        if candidate is None:
            prior = [
                item
                for item in form.get("candidate_source_evidence") or []
                if isinstance(item, Mapping)
                and item.get("status") not in _RECONCILIATION_STATUSES
            ]
            source_finding = form_source_findings.get(
                str(form.get("form_id") or ""),
                {},
            )
            source_material = [*prior]
            if source_finding:
                source_material.append(source_finding)
            official_urls = sorted(
                {
                    str(value).strip()
                    for item in source_material
                    for value in (
                        item.get("source_page"),
                        item.get("download_url"),
                        item.get("eform_url"),
                    )
                    if _is_official_url(value)
                }
                | {
                    str(source.get("official_procedure_url") or "").strip()
                    for _ in (0,)
                    if _is_official_url(source.get("official_procedure_url"))
                }
            )
            gap_reason = reason
            outcome_status = "VERIFIED_DATA_GAP"
            if gap_reason == "NO_EXACT_OFFICIAL_CANDIDATE":
                statuses = {
                    str(item.get("status") or "") for item in source_material
                }
                if "NO_PUBLIC_DOWNLOAD_VERIFIED" in statuses:
                    gap_reason = "NO_OFFICIAL_STATE_FORM_CONFIRMED"
                    outcome_status = "EXCLUDED_NO_OFFICIAL_FORM"
                elif "OFFICIAL_PACKAGE_PAGE" in statuses:
                    if source_finding.get("source_gap_status") == "verified_gap":
                        gap_reason = "OFFICIAL_PACKAGE_HAS_NO_SERVABLE_FILE"
                    else:
                        gap_reason = "OFFICIAL_PACKAGE_FILE_RETRY_REQUIRED"
                        outcome_status = "SOURCE_DOWNLOAD_RETRY_REQUIRED"
                elif "OFFICIAL_EFORM" in statuses:
                    gap_reason = "OFFICIAL_EFORM_REQUIRES_SEPARATE_GATE"
                    outcome_status = "SOURCE_MAPPING_UNRESOLVED"
                elif "AVAILABLE_OFFICIAL_FILE" in statuses:
                    gap_reason = "OFFICIAL_FILE_MAPPING_UNRESOLVED"
                    outcome_status = "SOURCE_MAPPING_UNRESOLVED"
                elif "NEEDS_SOURCE_MAPPING" in statuses:
                    gap_reason = "NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH"
                    outcome_status = "SOURCE_MAPPING_UNRESOLVED"
                elif "BLOCKED_EXTERNAL" in statuses:
                    gap_reason = "OFFICIAL_SOURCE_BLOCKED_EXTERNAL"
                    outcome_status = "BLOCKED_EXTERNAL"
            if not official_urls and gap_reason == "NO_EXACT_OFFICIAL_CANDIDATE":
                gap_reason = "NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH"
                outcome_status = "SOURCE_MAPPING_UNRESOLVED"
            evidence = {
                "status": outcome_status,
                "reason_code": gap_reason,
                "legal_as_of": legal_as_of,
                "official_sources_checked": official_urls,
                "candidate_ids_considered": duplicate_ids,
                "source_finding_status": (
                    source_finding.get("status") if source_finding else None
                ),
                "source_gap_job_id": source_finding.get("source_gap_job_id"),
                "source_gap_status": source_finding.get("source_gap_status"),
                "source_gap_reason_code": source_finding.get(
                    "source_gap_reason_code"
                ),
                "source_gap_checked_dates": list(
                    source_finding.get("source_gap_checked_dates") or []
                ),
                "auto_approved": False,
            }
            if outcome_status == "EXCLUDED_NO_OFFICIAL_FORM":
                _exclude_form_without_official_template(
                    form,
                    legal_as_of=legal_as_of,
                    official_sources=official_urls,
                )
            else:
                _reopen_previously_excluded_form(form)
            _replace_reconciliation_evidence(form, evidence)
            outcomes.append(
                {
                    "form_id": form.get("form_id"),
                    "procedure_id": procedure_id,
                    "status": outcome_status,
                    "reason_code": gap_reason,
                }
            )
            continue

        candidate_id = str(candidate.get("id") or "")
        _reopen_previously_excluded_form(form)
        mapped_candidate_ids.add(candidate_id)
        evidence = {
            "status": "MAPPED_OFFICIAL_CANDIDATE",
            "reason_code": reason,
            "candidate_id": candidate_id,
            "procedure_id": procedure_id,
            "official_procedure_code": (
                candidate.get("official_procedure_code") or official_code or None
            ),
            "sha256": candidate.get("sha256"),
            "source_page": _candidate_source(candidate),
            "download_url": _candidate_download(candidate),
            "legal_as_of": legal_as_of,
            "duplicate_candidate_ids": duplicate_ids,
            "auto_approved": False,
        }
        _replace_reconciliation_evidence(form, evidence)
        outcomes.append(
            {
                "form_id": form.get("form_id"),
                "procedure_id": procedure_id,
                "candidate_id": candidate_id,
                "status": "MAPPED_OFFICIAL_CANDIDATE",
                "reason_code": reason,
            }
        )

    enriched = 0
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "")
        if candidate_id not in mapped_candidate_ids:
            continue
        rule = _effectivity_rule_for_candidate(
            candidate,
            rules,
            legal_as_of=active_date,
        )
        if rule is None:
            continue
        additions = [
            str(value).strip()
            for value in rule.get("legal_basis_additions") or []
            if str(value or "").strip()
        ]
        legal_basis = [
            str(value).strip()
            for value in candidate.get("legal_basis") or []
            if str(value or "").strip()
        ]
        for value in additions:
            if value not in legal_basis:
                legal_basis.append(value)
        new_provenance = {
            "rule_id": rule.get("rule_id") or rule.get("evidence_id"),
            "official_source_url": rule.get("official_source_url"),
            "source_status": rule.get("source_status")
            or rule.get("evidence_basis"),
            "verified_as_of": rule.get("verified_as_of") or legal_as_of,
            "legal_basis": additions,
        }
        is_dvc_attachment_rule = (
            str(rule.get("evidence_basis") or "")
            == "official_current_dvc_attachment"
        )
        if is_dvc_attachment_rule:
            attachment = rule.get("canonical_artifact_attachment") or {}
            new_provenance.update(
                {
                    "source_attachment_id": attachment.get("attachment_id"),
                    "source_package_sha256": attachment.get("sha256"),
                    "publication_decision_number": rule.get(
                        "publication_decision_number"
                    ),
                }
            )
        effective_from = (
            candidate.get("effective_from")
            if is_dvc_attachment_rule and candidate.get("effective_from")
            else rule.get("effective_from")
        )
        changed = (
            candidate.get("effective_from") != effective_from
            or candidate.get("effectivity_provenance") != new_provenance
            or candidate.get("legal_basis") != legal_basis
        )
        candidate["effective_from"] = effective_from
        candidate["legal_basis"] = legal_basis
        candidate["effectivity_provenance"] = new_provenance
        if changed:
            enriched += 1

    mapped = sum(
        item["status"] == "MAPPED_OFFICIAL_CANDIDATE" for item in outcomes
    )
    gaps = sum(item["status"] == "VERIFIED_DATA_GAP" for item in outcomes)
    unresolved = sum(
        item["status"] == "SOURCE_MAPPING_UNRESOLVED" for item in outcomes
    )
    blocked_external = sum(
        item["status"] == "BLOCKED_EXTERNAL" for item in outcomes
    )
    retryable_downloads = sum(
        item["status"] == "SOURCE_DOWNLOAD_RETRY_REQUIRED"
        for item in outcomes
    )
    excluded_no_official = sum(
        item["status"] == "EXCLUDED_NO_OFFICIAL_FORM" for item in outcomes
    )
    return {
        "canonical_forms_payload": forms_out,
        "candidate_payload": candidates_out,
        "report": {
            "schema_version": "canonical-form-reconciliation-v1",
            "legal_as_of": legal_as_of,
            "summary": {
                "total_forms": len(forms),
                "mapped_forms": mapped,
                "verified_data_gaps": gaps,
                "unresolved_source_mappings": unresolved,
                "blocked_external": blocked_external,
                "retryable_source_downloads": retryable_downloads,
                "excluded_no_official_forms": excluded_no_official,
                "effectivity_enriched_candidates": enriched,
                "auto_approved": 0,
                "runtime_promoted": 0,
                "corpus_modified": False,
                "embedding_run": False,
                "active_collection_changed": False,
            },
            "outcomes": outcomes,
        },
    }
