"""Build a privacy-safe, fail-closed research queue for unresolved form identities.

The queue only summarizes evidence already captured from allowlisted official
sources. It never turns a failed lookup into a non-existence claim and never
creates a legal approval or runtime-eligible form.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_resolution_registry import has_invalid_unicode_metadata
from api.form_source_resolution import (
    extract_appendix_identifier,
    extract_strict_form_code,
    normalize_document_number,
)
from api.legal_form_catalog import _is_official_url

REASON_PRIORITY = {
    "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW": 1,
    "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND": 2,
    "ISSUING_INSTRUMENT_UNRESOLVED": 3,
    "FORM_CODE_UNRESOLVED": 4,
    "OFFICIAL_FORM_FILE_NOT_FOUND": 5,
    "AMBIGUOUS_OFFICIAL_PDF_PACKAGE": 6,
    "INVALID_UNICODE_METADATA": 7,
    "ISSUING_INSTRUMENT_EXPIRED": 8,
    "FORM_IDENTITY_UNRESOLVED": 9,
}

RESEARCH_ACTIONS = {
    "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW": "VERIFY_EXACT_APPENDIX_EFFECTIVITY",
    "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND": "RETRY_EXACT_OFFICIAL_DOCUMENT_LOOKUP",
    "ISSUING_INSTRUMENT_UNRESOLVED": "RESOLVE_ISSUING_INSTRUMENT_FROM_OFFICIAL_PROCEDURE",
    "FORM_CODE_UNRESOLVED": "RESOLVE_EXACT_FORM_CODE_FROM_OFFICIAL_APPENDIX",
    "OFFICIAL_FORM_FILE_NOT_FOUND": "RETRY_OFFICIAL_FORM_ATTACHMENT_LOOKUP",
    "AMBIGUOUS_OFFICIAL_PDF_PACKAGE": "DISAMBIGUATE_EXACT_FORM_BOUNDARY",
    "INVALID_UNICODE_METADATA": "REPAIR_METADATA_FROM_OFFICIAL_SOURCE",
    "ISSUING_INSTRUMENT_EXPIRED": "FIND_OFFICIAL_REPLACEMENT_INSTRUMENT",
    "FORM_IDENTITY_UNRESOLVED": "RESOLVE_EXACT_FORM_IDENTITY",
}

EVIDENCE_REQUIREMENTS = {
    "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW": (
        "exact official appendix/form effectivity evidence"
    ),
    "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND": (
        "exact document-number match on an allowlisted official source"
    ),
    "ISSUING_INSTRUMENT_UNRESOLVED": (
        "official procedure or instrument metadata naming the issuing instrument"
    ),
    "FORM_CODE_UNRESOLVED": "exact form code in the official appendix or form file",
    "OFFICIAL_FORM_FILE_NOT_FOUND": "downloadable official form file or verified eForm route",
    "AMBIGUOUS_OFFICIAL_PDF_PACKAGE": "one unambiguous exact form boundary and checksum",
    "INVALID_UNICODE_METADATA": "NFC metadata copied from an allowlisted official source",
    "ISSUING_INSTRUMENT_EXPIRED": "official replacement instrument and exact form mapping",
    "FORM_IDENTITY_UNRESOLVED": "exact form identity from an allowlisted official source",
}

FUTURE_REVIEW_BATCH_LIMIT = 25

DVC_ATTACHMENT_PREVIEW_URL = (
    "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
)
MAX_RECORDED_ARTIFACT_BYTES = 512 * 1024 * 1024


def _strict_official_https_url(value: Any) -> str | None:
    """Return a credential-free, query-free allowlisted HTTPS URL only."""

    text = str(value or "").strip()
    parsed = urlparse(text)
    if (
        parsed.scheme != "https"
        or not _is_official_url(text)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return text


def _bounded_nonnegative_int(value: Any) -> int | None:
    """Accept a JSON integer suitable for a privacy-safe size/range field."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > MAX_RECORDED_ARTIFACT_BYTES:
        return None
    return value


def _safe_publication_decision_number(value: Any) -> str | None:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 160
        or any(ord(character) < 32 for character in text)
    ):
        return None
    return text


def _strict_dvc_extraction(
    value: Any,
    *,
    form_code: str,
    appendix_identifier: str,
) -> dict[str, Any] | None:
    """Project only an exact structural boundary from a DVC candidate."""

    if not isinstance(value, Mapping):
        return None
    if value.get("kind") != "structural_docx_form_boundary":
        return None
    if value.get("complete") is not True:
        return None
    element_range = value.get("element_range")
    if (
        not isinstance(element_range, list)
        or len(element_range) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in element_range)
        or element_range[0] < 0
        or element_range[1] <= element_range[0]
    ):
        return None
    if _normalize_form_code(value.get("form_code")) != _normalize_form_code(form_code):
        return None
    if str(value.get("appendix_identifier") or "").strip().upper() != (
        appendix_identifier.strip().upper()
    ):
        return None
    return {
        "kind": "structural_docx_form_boundary",
        "complete": True,
        "element_range": list(element_range),
        "form_code": _normalize_form_code(form_code),
        "appendix_identifier": appendix_identifier.strip(),
    }


def _strict_dvc_provenance(
    item: Mapping[str, Any],
    provenance: Mapping[str, Any],
    *,
    form_code: str,
    appendix_identifier: str,
) -> dict[str, Any]:
    """Retain a small, checksum-bound DVC chain or fail closed.

    The research queue never needs local paths, raw request data or document
    body text. It does need enough immutable official metadata for a human
    reviewer to validate the original attachment and structural extraction.
    """

    kind = str(
        item.get("provenance_kind") or provenance.get("kind") or ""
    ).strip()
    if not kind:
        return {}
    if kind != "official_dvc_attachment":
        return {}

    attachment_id = str(
        item.get("source_attachment_id")
        or provenance.get("source_attachment_id")
        or ""
    ).strip().lower()
    package_sha256 = str(
        item.get("source_package_sha256")
        or provenance.get("source_package_sha256")
        or ""
    ).strip().lower()
    package_size = _bounded_nonnegative_int(
        item.get("source_package_size_bytes")
        if item.get("source_package_size_bytes") is not None
        else provenance.get("source_package_size_bytes")
    )
    retrieval_url = _strict_official_https_url(
        item.get("source_retrieval_url")
        or provenance.get("source_retrieval_url")
    )
    retrieval_method = str(
        item.get("source_retrieval_method")
        or provenance.get("source_retrieval_method")
        or ""
    ).strip().upper()
    publication_decision_number = _safe_publication_decision_number(
        item.get("publication_decision_number")
        or provenance.get("publication_decision_number")
    )
    artifact_size = _bounded_nonnegative_int(item.get("size_bytes"))
    extraction = _strict_dvc_extraction(
        item.get("extraction"),
        form_code=form_code,
        appendix_identifier=appendix_identifier,
    )
    if (
        not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            attachment_id,
        )
        or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)
        or package_size is None
        or retrieval_url != DVC_ATTACHMENT_PREVIEW_URL
        or retrieval_method != "POST"
        or publication_decision_number is None
        or artifact_size is None
        or extraction is None
    ):
        raise ValueError("FORM_GAP_RESEARCH_PROBE_DVC_PROVENANCE_INVALID")
    return {
        "provenance_kind": kind,
        "source_attachment_id": attachment_id,
        "source_package_sha256": package_sha256,
        "source_package_size_bytes": package_size,
        "source_retrieval_url": retrieval_url,
        "source_retrieval_method": retrieval_method,
        "publication_decision_number": publication_decision_number,
        "artifact_size_bytes": artifact_size,
        "extraction": extraction,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"FORM_GAP_RESEARCH_INPUT_MISSING:{path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"FORM_GAP_RESEARCH_INPUT_INVALID:{path.name}")
    return payload


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cache_path(cache_dir: Path, instrument: str) -> Path:
    digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{digest}.json"


def _date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _effectivity_status(document: Mapping[str, Any]) -> str | None:
    value = document.get("effStatus")
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("code")
    text = str(value or "").strip()
    return text or None


def _official_document_evidence(
    *, cache_dir: Path, instrument: str | None
) -> dict[str, Any]:
    if not instrument or has_invalid_unicode_metadata(instrument):
        return {
            "official_source_page": None,
            "official_source_domain": None,
            "official_document_id": None,
            "official_effectivity_status": None,
            "official_effective_from": None,
            "official_effective_to": None,
            "related_document_reference_count": 0,
            "evidence_reason_code": "OFFICIAL_DOCUMENT_CACHE_NOT_APPLICABLE",
        }
    path = _cache_path(cache_dir, instrument)
    if not path.is_file():
        return {
            "official_source_page": None,
            "official_source_domain": None,
            "official_document_id": None,
            "official_effectivity_status": None,
            "official_effective_from": None,
            "official_effective_to": None,
            "related_document_reference_count": 0,
            "evidence_reason_code": "OFFICIAL_DOCUMENT_CACHE_MISS",
        }
    cached = _read_json(path)
    document = cached.get("document")
    if cached.get("status") != "found" or not isinstance(document, Mapping):
        return {
            "official_source_page": None,
            "official_source_domain": None,
            "official_document_id": None,
            "official_effectivity_status": None,
            "official_effective_from": None,
            "official_effective_to": None,
            "related_document_reference_count": 0,
            "evidence_reason_code": str(
                cached.get("reason_code") or "OFFICIAL_DOCUMENT_NOT_CACHED"
            ),
        }
    document_number = str(document.get("docNum") or "").strip()
    if normalize_document_number(document_number) != normalize_document_number(
        instrument
    ):
        return {
            "official_source_page": None,
            "official_source_domain": None,
            "official_document_id": None,
            "official_effectivity_status": None,
            "official_effective_from": None,
            "official_effective_to": None,
            "related_document_reference_count": 0,
            "evidence_reason_code": "OFFICIAL_DOCUMENT_IDENTITY_MISMATCH",
        }
    document_id = str(document.get("id") or "").strip() or None
    source_page = str(document.get("detailUrl") or "").strip()
    if not source_page and document_id:
        source_page = f"https://vbpl.vn/van-ban/chi-tiet/--{document_id}"
    if not _is_official_url(source_page):
        source_page = ""
    domain = urlparse(source_page).hostname if source_page else None
    related = [
        item
        for item in document.get("documentRelatedList") or []
        if isinstance(item, Mapping) and str(item.get("id") or "").strip()
    ]
    return {
        "official_source_page": source_page or None,
        "official_source_domain": domain,
        "official_document_id": document_id,
        "official_effectivity_status": _effectivity_status(document),
        "official_effective_from": _date_text(document.get("effFrom")),
        "official_effective_to": _date_text(document.get("effTo")),
        "related_document_reference_count": len(related),
        "evidence_reason_code": (
            "EXACT_OFFICIAL_DOCUMENT_CACHE_MATCH"
            if source_page
            else "OFFICIAL_SOURCE_URL_NOT_ALLOWLISTED"
        ),
    }


def _reason_for_rows(rows: Sequence[Mapping[str, Any]]) -> str:
    reasons = {
        str(item.get("reason_code") or "").strip()
        for item in rows
        if str(item.get("reason_code") or "").strip()
    }
    if not reasons:
        return "UNCLASSIFIED_GAP"
    return min(reasons, key=lambda value: (REASON_PRIORITY.get(value, 100), value))


def _single_or_none(values: Sequence[str]) -> str | None:
    unique = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
    return unique[0] if len(unique) == 1 else None


def _manifest_appendix_identifiers(
    identity: Mapping[str, Any],
) -> list[str]:
    identifiers = {
        identifier
        for title in identity.get("canonical_titles") or []
        if (identifier := extract_appendix_identifier(title))
    }
    return sorted(identifiers)


def _official_url_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _is_official_url(text) else None


def _normalize_form_code(value: Any) -> str:
    extracted = extract_strict_form_code(value)
    if extracted:
        return extracted
    return re.sub(r"\s+", "", str(value or "")).upper().replace("Ã", "Ä")


def _load_probe_evidence(
    probe_dirs: Sequence[Path],
    *,
    identity_ids_by_procedure: Mapping[str, set[str]],
    identity_ids_by_exact_binding: Mapping[tuple[str, str, str], set[str]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    evidence_by_identity: dict[str, dict[str, Any]] = {}
    checksums: list[dict[str, str]] = []
    for probe_dir in probe_dirs:
        groups_path = probe_dir / "code-groups.json"
        resolution_path = probe_dir / "code-resolution.json"
        groups_payload = _read_json(groups_path)
        resolution_payload = _read_json(resolution_path)
        checksums.append(
            {
                "probe": str(probe_dir.name),
                "code_groups_sha256": _sha256_path(groups_path),
                "code_resolution_sha256": _sha256_path(resolution_path),
            }
        )
        groups = {
            str(item.get("group_id") or ""): item
            for item in groups_payload.get("groups") or []
            if isinstance(item, Mapping) and str(item.get("group_id") or "")
        }
        for item in resolution_payload.get("pending_records") or []:
            if not isinstance(item, Mapping):
                continue
            if item.get("approved") is True or item.get("runtime_eligible") is True:
                raise ValueError("FORM_GAP_RESEARCH_PROBE_PROMOTION_FORBIDDEN")
            if "HUMAN_LEGAL_REVIEW_REQUIRED" not in (
                item.get("hard_gate_reason_codes") or []
            ):
                raise ValueError("FORM_GAP_RESEARCH_PROBE_HUMAN_GATE_MISSING")
            group_id = str(item.get("three_tier_group_id") or "").strip()
            group = groups.get(group_id, {})
            procedure_id = str(item.get("procedure_id") or "").strip()
            procedure_meta = (group.get("procedure_metadata") or {}).get(
                procedure_id, {}
            )
            provenance = item.get("provenance") or {}
            if not isinstance(provenance, Mapping):
                provenance = {}
            form_code = str(
                item.get("form_code") or group.get("form_code") or ""
            ).strip()
            appendix_identifier = str(
                item.get("appendix_identifier")
                or group.get("appendix_identifier")
                or ""
            ).strip()
            issuing_instrument = str(
                item.get("issuing_instrument")
                or provenance.get("document_number")
                or group.get("issuing_instrument")
                or ""
            ).strip()
            identity_id = str(
                item.get("requirement_identity_id")
                or procedure_meta.get("requirement_identity_id")
                or ""
            ).strip()
            if not identity_id:
                exact_key = (
                    procedure_id,
                    normalize_document_number(issuing_instrument),
                    _normalize_form_code(form_code),
                )
                exact_identities = identity_ids_by_exact_binding.get(
                    exact_key,
                    set(),
                )
                if all(exact_key) and len(exact_identities) == 1:
                    identity_id = next(iter(exact_identities))
            if not identity_id:
                procedure_identities = identity_ids_by_procedure.get(
                    procedure_id,
                    set(),
                )
                if len(procedure_identities) == 1:
                    identity_id = next(iter(procedure_identities))
            if not identity_id:
                raise ValueError("FORM_GAP_RESEARCH_PROBE_IDENTITY_MISSING")
            sha256 = str(item.get("sha256") or "").casefold().strip()
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise ValueError("FORM_GAP_RESEARCH_PROBE_CHECKSUM_INVALID")
            source_page = _official_url_or_none(item.get("source_page_url"))
            source_download = _official_url_or_none(
                item.get("source_download_url")
            )
            if not source_page or not source_download:
                raise ValueError("FORM_GAP_RESEARCH_PROBE_SOURCE_NOT_OFFICIAL")
            effectivity_reason = str(
                item.get("effectivity_reason_code") or ""
            ).strip()
            effectivity_source = _official_url_or_none(
                item.get("effectivity_source_url")
            )
            if effectivity_reason in {
                "APPENDIX_EFFECTIVITY_VERIFIED",
                "FORM_EFFECTIVITY_VERIFIED",
            } and not effectivity_source:
                raise ValueError(
                    "FORM_GAP_RESEARCH_PROBE_EFFECTIVITY_SOURCE_NOT_OFFICIAL"
                )
            candidate_id = str(item.get("id") or "").strip()
            canonical_form_id = str(
                item.get("proposed_canonical_form_id") or ""
            ).strip()
            dvc_provenance = _strict_dvc_provenance(
                item,
                provenance,
                form_code=form_code,
                appendix_identifier=appendix_identifier,
            )
            evidence = {
                "candidate_id": candidate_id or None,
                "candidate_ids": [candidate_id] if candidate_id else [],
                "canonical_form_id": canonical_form_id or None,
                "canonical_form_ids": (
                    [canonical_form_id] if canonical_form_id else []
                ),
                "group_id": group_id or None,
                "procedure_id": procedure_id,
                "procedure_ids": [procedure_id] if procedure_id else [],
                "form_code": form_code or None,
                "appendix_identifier": appendix_identifier or None,
                "issuing_instrument": issuing_instrument or None,
                "source_page_url": source_page,
                "source_download_url": source_download,
                "sha256": sha256,
                "effective_from": item.get("effective_from"),
                "effective_to": item.get("effective_to"),
                "effectivity_reason_code": effectivity_reason or None,
                "effectivity_source_url": effectivity_source,
                "technical_reason_code": "TECHNICAL_HARD_GATES_PASS_AFTER_RESEARCH",
                "approved": False,
                "runtime_eligible": False,
                "human_attestation_required": True,
                **dvc_provenance,
            }
            existing = evidence_by_identity.get(identity_id)
            if existing:
                binding_keys = {
                    "candidate_id",
                    "candidate_ids",
                    "canonical_form_id",
                    "canonical_form_ids",
                    "procedure_id",
                    "procedure_ids",
                }
                existing_core = {
                    key: value
                    for key, value in existing.items()
                    if key not in binding_keys
                }
                evidence_core = {
                    key: value
                    for key, value in evidence.items()
                    if key not in binding_keys
                }
                if existing_core != evidence_core:
                    raise ValueError("FORM_GAP_RESEARCH_PROBE_EVIDENCE_CONFLICT")
                for plural, singular in (
                    ("candidate_ids", "candidate_id"),
                    ("canonical_form_ids", "canonical_form_id"),
                    ("procedure_ids", "procedure_id"),
                ):
                    values = sorted(
                        {
                            str(value)
                            for value in [
                                *(existing.get(plural) or []),
                                *(evidence.get(plural) or []),
                            ]
                            if str(value)
                        }
                    )
                    existing[plural] = values
                    existing[singular] = values[0] if len(values) == 1 else None
            else:
                evidence_by_identity[identity_id] = evidence
    return evidence_by_identity, checksums


def _future_review_batches(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Partition candidate-only technical resolutions for human review."""

    identity_ids = [
        str(item.get("requirement_identity_id") or "").strip()
        for item in records
        if item.get("research_status")
        == "TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH"
        and str(item.get("requirement_identity_id") or "").strip()
    ]
    batches: list[dict[str, Any]] = []
    for offset in range(0, len(identity_ids), FUTURE_REVIEW_BATCH_LIMIT):
        batch_ids = identity_ids[offset : offset + FUTURE_REVIEW_BATCH_LIMIT]
        batches.append(
            {
                "batch_id": f"future-review-{len(batches) + 1:02d}",
                "status": "PENDING_HUMAN_ATTESTATION",
                "identity_count": len(batch_ids),
                "requirement_identity_ids": batch_ids,
                "human_attestation_required": True,
                "approved": False,
                "runtime_eligible": False,
            }
        )
    return batches


def build_research_queue(
    *,
    run_dir: Path,
    cache_dir: Path,
    legal_as_of: str,
    probe_dirs: Sequence[Path] = (),
    requirement_manifest_path: Path | None = None,
) -> dict[str, Any]:
    report_path = run_dir / "report.json"
    gaps_path = run_dir / "gaps.json"
    registry_path = run_dir / "occurrence-registry.json"
    report = _read_json(report_path)
    gaps = _read_json(gaps_path)
    registry = _read_json(registry_path)
    counts = report.get("counts") or {}
    if str(report.get("legal_as_of") or "") != legal_as_of:
        raise ValueError("FORM_GAP_RESEARCH_LEGAL_DATE_DRIFT")
    if bool(report.get("feature_flag_enabled")):
        raise ValueError("FORM_GAP_RESEARCH_FEATURE_FLAG_MUST_BE_FALSE")
    if int(counts.get("unaccounted_pending_identities") or 0) != 0:
        raise ValueError("FORM_COMPLETION_IDENTITIES_UNACCOUNTED")

    occurrence_by_id = {
        str(item.get("occurrence_id") or ""): item
        for item in registry.get("occurrences") or []
        if isinstance(item, Mapping) and str(item.get("occurrence_id") or "")
    }
    rows_by_identity: dict[str, list[dict[str, Any]]] = {}
    for source in gaps.get("records") or []:
        if not isinstance(source, Mapping):
            continue
        identity_id = str(source.get("requirement_identity_id") or "").strip()
        if not identity_id:
            raise ValueError("FORM_GAP_RESEARCH_IDENTITY_MISSING")
        occurrence_id = str(source.get("occurrence_id") or "").strip()
        registry_row = occurrence_by_id.get(occurrence_id, {})
        rows_by_identity.setdefault(identity_id, []).append(
            {**dict(registry_row), **dict(source)}
        )

    expected_count = int(counts.get("terminal_gap_identities") or 0)
    if len(rows_by_identity) != expected_count:
        raise ValueError("FORM_GAP_RESEARCH_IDENTITY_COUNT_DRIFT")

    manifest_identities: dict[str, Mapping[str, Any]] = {}
    if requirement_manifest_path is not None:
        expected_manifest_sha256 = str(report.get("manifest_sha256") or "").strip()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256)
            or _sha256_path(requirement_manifest_path) != expected_manifest_sha256
        ):
            raise ValueError("FORM_GAP_RESEARCH_MANIFEST_DRIFT")
        requirement_manifest = _read_json(requirement_manifest_path)
        for item in requirement_manifest.get("form_identities") or []:
            if not isinstance(item, Mapping):
                continue
            identity_id = str(item.get("identity_id") or "").strip()
            if identity_id:
                manifest_identities[identity_id] = item
        if set(rows_by_identity).difference(manifest_identities):
            raise ValueError("FORM_GAP_RESEARCH_MANIFEST_IDENTITY_MISSING")

    identity_ids_by_procedure: dict[str, set[str]] = {}
    identity_ids_by_exact_binding: dict[tuple[str, str, str], set[str]] = {}
    for identity_id, identity_rows in rows_by_identity.items():
        for row in identity_rows:
            procedure_id = str(row.get("procedure_id") or "").strip()
            if procedure_id:
                identity_ids_by_procedure.setdefault(procedure_id, set()).add(
                    identity_id
                )
            exact_key = (
                procedure_id,
                normalize_document_number(row.get("issuing_instrument")),
                _normalize_form_code(row.get("resolved_form_code")),
            )
            if all(exact_key):
                identity_ids_by_exact_binding.setdefault(exact_key, set()).add(
                    identity_id
                )
    manifest_identity_ids_by_exact_binding: dict[
        tuple[str, str, str], set[str]
    ] = {}
    for identity_id, item in manifest_identities.items():
        instrument = normalize_document_number(item.get("issuing_instrument"))
        form_code = _normalize_form_code(item.get("form_code"))
        for procedure_id_value in item.get("procedure_ids") or []:
            exact_key = (
                str(procedure_id_value or "").strip(),
                instrument,
                form_code,
            )
            if all(exact_key):
                manifest_identity_ids_by_exact_binding.setdefault(
                    exact_key,
                    set(),
                ).add(identity_id)
    # The checksum-bound requirement manifest is the authoritative identity
    # source. It replaces legacy occurrence keys that may have collapsed a
    # multi-code title to its first code; missing manifest keys retain the
    # registry fallback for backwards-compatible synthetic/offline queues.
    identity_ids_by_exact_binding.update(
        manifest_identity_ids_by_exact_binding
    )
    probe_evidence, probe_checksums = _load_probe_evidence(
        probe_dirs,
        identity_ids_by_procedure=identity_ids_by_procedure,
        identity_ids_by_exact_binding=identity_ids_by_exact_binding,
    )
    unknown_probe_identities = set(probe_evidence).difference(rows_by_identity)
    if unknown_probe_identities:
        raise ValueError("FORM_GAP_RESEARCH_PROBE_IDENTITY_NOT_IN_BASELINE_GAPS")

    records: list[dict[str, Any]] = []
    for identity_id, identity_rows in rows_by_identity.items():
        reason_code = _reason_for_rows(identity_rows)
        instruments = sorted(
            {
                str(item.get("issuing_instrument") or "").strip()
                for item in identity_rows
                if str(item.get("issuing_instrument") or "").strip()
            }
        )
        instrument = _single_or_none(instruments)
        if instrument and has_invalid_unicode_metadata(instrument):
            instrument = None
        baseline_form_codes = sorted(
            {
                str(item.get("resolved_form_code") or "").strip()
                for item in identity_rows
                if str(item.get("resolved_form_code") or "").strip()
            }
        )
        manifest_form_code = str(
            manifest_identities.get(identity_id, {}).get("form_code") or ""
        ).strip()
        manifest_appendices = _manifest_appendix_identifiers(
            manifest_identities.get(identity_id, {})
        )
        has_manifest_identity_collision = bool(
            manifest_form_code
            and manifest_identities.get(identity_id, {}).get(
                "issuing_instrument"
            )
            and len(manifest_appendices) > 1
        )
        form_codes = (
            [manifest_form_code] if manifest_form_code else baseline_form_codes
        )
        appendices = sorted(
            {
                str(item.get("appendix_identifier") or "").strip()
                for item in identity_rows
                if str(item.get("appendix_identifier") or "").strip()
            }
        )
        evidence = _official_document_evidence(
            cache_dir=cache_dir,
            instrument=instrument,
        )
        record = {
                "requirement_identity_id": identity_id,
                "priority_rank": REASON_PRIORITY.get(reason_code, 100),
                "reason_code": reason_code,
                "research_action": RESEARCH_ACTIONS.get(
                    reason_code, "MANUAL_OFFICIAL_SOURCE_RESEARCH"
                ),
                "evidence_required": EVIDENCE_REQUIREMENTS.get(
                    reason_code, "exact evidence from an allowlisted official source"
                ),
                "procedure_ids": sorted(
                    {
                        str(item.get("procedure_id") or "").strip()
                        for item in identity_rows
                        if str(item.get("procedure_id") or "").strip()
                    }
                ),
                "occurrence_count": len(
                    {
                        str(item.get("occurrence_id") or "").strip()
                        for item in identity_rows
                        if str(item.get("occurrence_id") or "").strip()
                    }
                ),
                "issuing_instrument": instrument,
                "instrument_conflict": len(instruments) > 1,
                "form_codes": form_codes,
                "appendix_identifiers": appendices,
                **evidence,
                "research_status": "PENDING_EXACT_OFFICIAL_EVIDENCE",
                "serving_status": "FAIL_CLOSED",
                "approved": False,
                "runtime_eligible": False,
                "automated_legal_decision": False,
        }
        if form_codes != baseline_form_codes:
            record.update(
                {
                    "baseline_form_codes": baseline_form_codes,
                    "identity_metadata_correction": (
                        "CHECKSUM_BOUND_MANIFEST_FORM_CODE_PRECEDENCE"
                    ),
                }
            )
        if has_manifest_identity_collision:
            record.update(
                {
                    "priority_rank": 0,
                    "research_action": "SPLIT_COLLIDING_FORM_IDENTITY",
                    "evidence_required": (
                        "procedure-level appendix-aware identity split with "
                        "one exact official artifact per resulting identity"
                    ),
                    "identity_collision_appendix_identifiers": (
                        manifest_appendices
                    ),
                    "research_status": "PENDING_IDENTITY_MODEL_CORRECTION",
                }
            )
        if identity_id in probe_evidence and not has_manifest_identity_collision:
            technical_evidence = probe_evidence[identity_id]
            technical_form_code = str(
                technical_evidence.get("form_code") or ""
            ).strip()
            if technical_form_code and form_codes != [technical_form_code]:
                record.update(
                    {
                        "baseline_form_codes": form_codes,
                        "form_codes": [technical_form_code],
                        "identity_metadata_correction": (
                            "STRUCTURED_PROBE_FORM_CODE_PRECEDENCE"
                        ),
                    }
                )
            record.update(
                {
                    "research_action": "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION",
                    "research_status": (
                        "TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH"
                    ),
                    "technical_evidence": technical_evidence,
                }
            )
        records.append(record)

    records.sort(
        key=lambda item: (
            int(item["priority_rank"]),
            str(item["reason_code"]),
            str(item["requirement_identity_id"]),
        )
    )
    reason_counts = Counter(str(item["reason_code"]) for item in records)
    action_counts = Counter(str(item["research_action"]) for item in records)
    fingerprint = hashlib.sha256(
        json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    input_checksums: dict[str, Any] = {
        "report": _sha256_path(report_path),
        "gaps": _sha256_path(gaps_path),
        "occurrence_registry": _sha256_path(registry_path),
        "probes": probe_checksums,
    }
    if requirement_manifest_path is not None:
        input_checksums["requirement_manifest"] = _sha256_path(
            requirement_manifest_path
        )
    future_review_batches = _future_review_batches(records)
    return {
        "schema_version": "form-gap-research-queue-v1",
        "run_id": str(report.get("run_id") or run_dir.name),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of,
        "manifest_sha256": report.get("manifest_sha256"),
        "source_snapshot_sha256": report.get("source_snapshot_sha256"),
        "input_checksums": input_checksums,
        "queue_fingerprint": fingerprint,
        "summary": {
            "identity_count": len(records),
            "reason_counts": dict(sorted(reason_counts.items())),
            "research_action_counts": dict(sorted(action_counts.items())),
            "official_source_page_count": sum(
                bool(item.get("official_source_page")) for item in records
            ),
            "runtime_eligible_count": 0,
            "technically_resolved_identity_count": sum(
                item.get("research_status")
                == "TECHNICALLY_RESOLVED_PENDING_FUTURE_HUMAN_BATCH"
                for item in records
            ),
            "future_review_batch_count": len(future_review_batches),
            "future_review_batch_limit": FUTURE_REVIEW_BATCH_LIMIT,
            "unaccounted_identity_count": 0,
        },
        "candidate_only": True,
        "automated_approval": False,
        "human_attestation_created": False,
        "feature_flag_enabled": False,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
        "future_review_batches": future_review_batches,
        "records": records,
    }


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--legal-as-of", required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "data" / "source_cache" / "vbpl_form_documents",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "feature006" / "form-gap-research-queue.json",
    )
    parser.add_argument("--requirement-manifest", type=Path)
    parser.add_argument("--probe-dir", type=Path, action="append", default=[])
    args = parser.parse_args()
    payload = build_research_queue(
        run_dir=args.run_dir.resolve(),
        cache_dir=args.cache_dir.resolve(),
        legal_as_of=args.legal_as_of,
        probe_dirs=[path.resolve() for path in args.probe_dir],
        requirement_manifest_path=(
            args.requirement_manifest.resolve()
            if args.requirement_manifest is not None
            else None
        ),
    )
    _write_atomic(args.output.resolve(), payload)
    print(json.dumps({"output": str(args.output), **payload["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
