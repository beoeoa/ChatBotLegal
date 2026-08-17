"""Deterministic registry for the three-tier official-form campaign.

The registry separates source occurrences, canonical identities and procedure
bindings so progress reports cannot confuse the 715 source occurrences with
the eventual number of official forms.  This module performs no model call,
network request or legal approval.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from api.form_source_resolution import (
    extract_issuing_instruments,
    extract_strict_form_code,
    normalize_document_number,
)
from api.legal_form_catalog import _is_official_url

TERMINAL_IDENTITY_STATES = {
    "IDENTITY_RESOLVED",
    "FORM_IDENTITY_UNRESOLVED",
    "EXCLUDED_SUPPORTING_DOCUMENT",
    "EXCLUDED_ISSUED_RESULT",
    "EXCLUDED_NO_OFFICIAL_FORM",
}
_MOJIBAKE_MARKERS = (
    "\ufffd",
    "\u00c3",
    "\u00c2",
    "\u00c4",
    "\u00c6",
    "\u00e1\u00ba",
    "\u00e1\u00bb",
    "\u00e2\u20ac",
)
_APPENDIX_RE = re.compile(
    r"(?iu)\bph[ụu]\s*l[ụu]c\s+"
    r"((?:[IVXLCDM]+|\d+|[A-ZĐ])(?:[.\-/]\d+|[.\-/][A-ZĐ0-9]+)*)"
)
_ISSUED_WITH_RE = re.compile(
    r"(?iu)(?:ban\s+h[aà]nh\s+k[eè]m\s+theo|theo\s+m[ẫa]u\s+t[ạa]i)"
    r".{0,120}?"
    r"(\d{1,4}(?:\.\d{1,4})*/\d{4}/[A-ZĐ]+(?:-[A-ZĐ0-9]+)+)"
)
_LEADING_FORM_WORDS_RE = re.compile(
    r"(?iu)^\s*[-–—:;.\d)]*\s*"
    r"(?:m[ẫa]u(?:\s+s[ốo])?\s+[A-ZĐ0-9./-]+\s*[-:;.,]?\s*)?"
)
_GENERIC_TITLES = {
    "don",
    "don de nghi",
    "to khai",
    "phieu",
    "bao cao",
    "danh sach",
    "van ban",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestIntegrityError(ValueError):
    """Raised when a versioned campaign artifact fails its checksum."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _text(value).casefold())
    ascii_text = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text).split())


def has_invalid_unicode_metadata(value: Any) -> bool:
    """Reject replacement characters, non-NFC text and UTF-8 mojibake."""

    text = _text(value)
    if not text:
        return False
    if unicodedata.normalize("NFC", text) != text:
        return True
    if any(marker in text for marker in _MOJIBAKE_MARKERS):
        return True
    return any(0x80 <= ord(character) <= 0x9F for character in text)


def _has_invalid_unicode(value: Any) -> bool:
    return has_invalid_unicode_metadata(value)


def extract_appendix_identifier(value: Any) -> str | None:
    """Extract an explicit appendix identity without inferring one."""

    match = _APPENDIX_RE.search(_text(value))
    if match is None:
        folded = _fold(value)
        fallback = re.search(
            r"\bphu luc ((?:[ivxlcdm]+|\d+|[a-z])(?:[./-][a-z0-9]+)*)",
            folded,
            re.IGNORECASE,
        )
        if fallback is None:
            return None
        raw = fallback.group(1)
    else:
        raw = match.group(1)
    return re.sub(r"\s+", "", raw).upper()


def normalize_form_title(value: Any) -> str:
    text = _LEADING_FORM_WORDS_RE.sub("", _text(value))
    text = re.split(
        r"(?iu)\b(?:ban\s+hành\s+kèm\s+theo|theo\s+mẫu\s+tại|"
        r"nghị\s+định\s+số|thông\s+tư\s+số)\b",
        text,
        maxsplit=1,
    )[0]
    return _fold(text.rstrip(" .;,:-"))


def _meaningful_title(value: str) -> bool:
    tokens = value.split()
    return len(tokens) >= 4 and value not in _GENERIC_TITLES


def _explicit_issuing_instrument(
    occurrence: Mapping[str, Any],
) -> str | None:
    supplied_directly = normalize_document_number(
        occurrence.get("issuing_instrument")
    )
    if supplied_directly:
        return supplied_directly
    form_name = unicodedata.normalize("NFC", _text(occurrence.get("form_name")))
    match = _ISSUED_WITH_RE.search(form_name)
    if match:
        return normalize_document_number(match.group(1))

    explicit = extract_issuing_instruments(form_name)
    if len(explicit) == 1:
        return explicit[0]

    supplied = {
        normalize_document_number(value)
        for value in occurrence.get("issuing_instruments") or []
        if normalize_document_number(value)
    }
    return next(iter(supplied)) if len(supplied) == 1 else None


def _jurisdiction(source_tier: Any) -> str:
    return {
        "central": "central",
        "hai_phong_override": "hai_phong",
        "le_chan_local": "le_chan",
    }.get(_text(source_tier), "unverified")


def canonical_identity_key(
    *,
    form_code: str | None,
    issuing_instrument: str,
    appendix_identifier: str | None,
    jurisdiction: str,
    normalized_title: str,
) -> str:
    """Return a stable identity hash without exposing legal metadata publicly."""

    code = _text(form_code).upper()
    instrument = normalize_document_number(issuing_instrument)
    appendix = _text(appendix_identifier).upper()
    title = _text(normalized_title)
    if not instrument or not jurisdiction or not (code or title):
        raise ValueError("CANONICAL_IDENTITY_FIELDS_REQUIRED")
    canonical = json.dumps(
        {
            "appendix": appendix,
            "code": code,
            "instrument": instrument,
            "jurisdiction": jurisdiction,
            "title": "" if code else title,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_occurrence_identity(
    occurrence: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify one source occurrence without guessing missing metadata."""

    occurrence_id = _text(
        occurrence.get("candidate_id") or occurrence.get("occurrence_id")
    )
    procedure_id = _text(
        occurrence.get("procedure_id") or occurrence.get("procedure_code")
    )
    # Canonically equivalent Vietnamese input is safe to normalize. Mojibake,
    # replacement characters and other invalid metadata remain fail-closed.
    form_name = unicodedata.normalize("NFC", _text(occurrence.get("form_name")))
    form_code_value = unicodedata.normalize(
        "NFC", _text(occurrence.get("form_code"))
    )
    jurisdiction = _jurisdiction(occurrence.get("source_tier"))
    base = {
        "occurrence_id": occurrence_id,
        "requirement_identity_id": _text(
            occurrence.get("requirement_identity_id")
        )
        or None,
        "procedure_id": procedure_id or None,
        "domain": _text(occurrence.get("domain")) or None,
        "source_tier": _text(occurrence.get("source_tier")) or None,
        "jurisdiction": jurisdiction,
        "canonical_identity_key": None,
        "resolved_form_code": None,
        "issuing_instrument": None,
        "appendix_identifier": None,
    }

    component_kind = _text(occurrence.get("component_kind"))
    if component_kind == "supporting_document":
        return {
            **base,
            "identity_status": "EXCLUDED_SUPPORTING_DOCUMENT",
            "reason_code": "EXCLUDED_SUPPORTING_DOCUMENT",
        }
    if component_kind == "official_result":
        return {
            **base,
            "identity_status": "EXCLUDED_ISSUED_RESULT",
            "reason_code": "EXCLUDED_ISSUED_RESULT",
        }
    if occurrence.get("catalog_disposition") == "excluded_no_official_form":
        return {
            **base,
            "identity_status": "EXCLUDED_NO_OFFICIAL_FORM",
            "reason_code": "EXCLUDED_NO_OFFICIAL_FORM",
        }
    if _has_invalid_unicode(form_name) or _has_invalid_unicode(form_code_value):
        return {
            **base,
            "identity_status": "FORM_IDENTITY_UNRESOLVED",
            "reason_code": "INVALID_UNICODE_METADATA",
        }

    form_code = (
        extract_strict_form_code(form_name)
        or extract_strict_form_code(form_code_value)
    )
    issuing_instrument = _explicit_issuing_instrument(occurrence)
    appendix = extract_appendix_identifier(form_name)
    normalized_title = normalize_form_title(form_name)

    if not procedure_id:
        reason_code = "MISSING_PROCEDURE_ID"
    elif jurisdiction == "unverified":
        reason_code = "JURISDICTION_UNRESOLVED"
    elif not issuing_instrument:
        reason_code = "ISSUING_INSTRUMENT_UNRESOLVED"
    elif not form_code and not (
        appendix and _meaningful_title(normalized_title)
    ):
        reason_code = "FORM_IDENTITY_UNRESOLVED"
    else:
        key = canonical_identity_key(
            form_code=form_code,
            issuing_instrument=issuing_instrument,
            appendix_identifier=appendix,
            jurisdiction=jurisdiction,
            normalized_title=normalized_title,
        )
        return {
            **base,
            "identity_status": "IDENTITY_RESOLVED",
            "reason_code": "IDENTITY_RESOLVED",
            "canonical_identity_key": key,
            "resolved_form_code": form_code,
            "issuing_instrument": issuing_instrument,
            "appendix_identifier": appendix,
            "normalized_title_fingerprint": hashlib.sha256(
                normalized_title.encode("utf-8")
            ).hexdigest(),
        }

    return {
        **base,
        "identity_status": "FORM_IDENTITY_UNRESOLVED",
        "reason_code": reason_code,
        "resolved_form_code": form_code,
        "issuing_instrument": issuing_instrument,
        "appendix_identifier": appendix,
    }


def build_occurrence_registry(
    *,
    occurrences: Iterable[Mapping[str, Any]],
    run_id: str,
    legal_as_of: str,
) -> dict[str, Any]:
    decisions = [resolve_occurrence_identity(item) for item in occurrences]
    decisions.sort(key=lambda item: item["occurrence_id"])

    groups: dict[str, dict[str, Any]] = {}
    for item in decisions:
        key = item.get("canonical_identity_key")
        if not key:
            continue
        group = groups.setdefault(
            str(key),
            {
                "canonical_identity_key": key,
                "canonical_form_id": f"form-three-tier-{str(key)[:24]}",
                "form_code": item.get("resolved_form_code"),
                "issuing_instrument": item.get("issuing_instrument"),
                "appendix_identifier": item.get("appendix_identifier"),
                "jurisdiction": item.get("jurisdiction"),
                "occurrence_ids": [],
                "procedure_ids": [],
            },
        )
        occurrence_id = _text(item.get("occurrence_id"))
        procedure_id = _text(item.get("procedure_id"))
        if occurrence_id and occurrence_id not in group["occurrence_ids"]:
            group["occurrence_ids"].append(occurrence_id)
        if procedure_id and procedure_id not in group["procedure_ids"]:
            group["procedure_ids"].append(procedure_id)

    identity_groups = sorted(
        groups.values(), key=lambda item: item["canonical_identity_key"]
    )
    for group in identity_groups:
        group["occurrence_ids"].sort()
        group["procedure_ids"].sort()

    reason_counts = Counter(item["reason_code"] for item in decisions)
    terminal_count = sum(
        item["identity_status"] in TERMINAL_IDENTITY_STATES
        and bool(item.get("reason_code"))
        for item in decisions
    )
    binding_count = sum(len(item["procedure_ids"]) for item in identity_groups)
    return {
        "schema_version": "form-occurrence-resolution-v1",
        "run_id": _text(run_id),
        "generated_at": _utcnow(),
        "legal_as_of": _text(legal_as_of),
        "candidate_only": True,
        "automated_approval": False,
        "occurrences": decisions,
        "identity_groups": identity_groups,
        "summary": {
            "occurrence_count": len(decisions),
            "terminal_occurrence_count": terminal_count,
            "unreasoned_occurrence_count": sum(
                not item.get("reason_code") for item in decisions
            ),
            "canonical_identity_count": len(identity_groups),
            "procedure_binding_count": binding_count,
            "identity_status_counts": dict(
                sorted(
                    Counter(
                        item["identity_status"] for item in decisions
                    ).items()
                )
            ),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
    }


def _payload_checksum(payload: Mapping[str, Any]) -> str:
    value = dict(payload)
    value.pop("payload_sha256", None)
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_campaign_manifest(
    path: Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    value = dict(payload)
    value["payload_sha256"] = _payload_checksum(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return value


def read_campaign_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = _text(value.get("payload_sha256"))
    if not _SHA256_RE.fullmatch(expected) or expected != _payload_checksum(value):
        raise ManifestIntegrityError("MANIFEST_CHECKSUM_MISMATCH")
    return value


def build_privacy_safe_campaign_summary(
    *,
    registry: Mapping[str, Any],
    source_attempts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    summary = dict(registry.get("summary") or {})
    source_status_counts = Counter(
        _text(item.get("status")) or "UNKNOWN" for item in source_attempts
    )
    source_domain_counts = Counter(
        _text(item.get("source_domain")) or "unknown"
        for item in source_attempts
    )
    return {
        "schema_version": "form-resolution-campaign-summary-v1",
        "run_id": _text(registry.get("run_id")),
        "generated_at": _utcnow(),
        "legal_as_of": _text(registry.get("legal_as_of")),
        "counts": summary,
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "source_domain_counts": dict(sorted(source_domain_counts.items())),
        "candidate_only": True,
        "automated_approval": False,
        "human_attestation_required": True,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }


def validate_review_ready_candidate(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply technical hard gates without making a legal-review decision."""

    reasons: list[str] = []

    def require(condition: bool, reason: str) -> None:
        if not condition and reason not in reasons:
            reasons.append(reason)

    procedure_ids = [
        _text(value)
        for value in (
            candidate.get("procedure_ids")
            or [candidate.get("procedure_id")]
        )
        if _text(value)
    ]
    source_sha = _text(
        candidate.get("source_sha256") or candidate.get("sha256")
    ).casefold()
    asset_sha = _text(candidate.get("sha256")).casefold()
    require(
        bool(candidate.get("canonical_identity_key")),
        "CANONICAL_IDENTITY_REQUIRED",
    )
    require(bool(procedure_ids), "MISSING_PROCEDURE_ID")
    require(bool(_text(candidate.get("canonical_name"))), "FORM_NAME_REQUIRED")
    require(
        not any(
            has_invalid_unicode_metadata(candidate.get(field))
            for field in (
                "canonical_form_name",
                "canonical_name",
                "detected_form_name",
                "form_title",
                "publisher",
            )
        ),
        "INVALID_UNICODE_METADATA",
    )
    require(
        bool(_text(candidate.get("issuing_instrument"))),
        "ISSUING_INSTRUMENT_REQUIRED",
    )
    require(
        _is_official_url(_text(candidate.get("official_source_page"))),
        "OFFICIAL_SOURCE_URL_REQUIRED",
    )
    require(
        _is_official_url(_text(candidate.get("official_download_url"))),
        "OFFICIAL_FILE_URL_REQUIRED",
    )
    require(bool(_text(candidate.get("local_path"))), "LOCAL_FILE_REQUIRED")
    require(bool(_SHA256_RE.fullmatch(source_sha)), "SOURCE_CHECKSUM_INVALID")
    require(bool(_SHA256_RE.fullmatch(asset_sha)), "ASSET_CHECKSUM_INVALID")
    require(
        bool(_text(candidate.get("effective_from"))),
        "EFFECTIVITY_METADATA_MISSING",
    )
    require(
        bool(_text(candidate.get("jurisdiction"))),
        "JURISDICTION_UNRESOLVED",
    )
    require(bool(_text(candidate.get("scope"))), "SCOPE_UNRESOLVED")

    provenance = candidate.get("provenance")
    require(isinstance(provenance, Mapping), "PROVENANCE_REQUIRED")
    if isinstance(provenance, Mapping):
        require(bool(_text(provenance.get("publisher"))), "PUBLISHER_REQUIRED")
        require(
            not has_invalid_unicode_metadata(provenance.get("publisher")),
            "INVALID_UNICODE_METADATA",
        )
        require(
            bool(_text(provenance.get("retrieved_at"))),
            "PROVENANCE_TIMESTAMP_REQUIRED",
        )

    extraction = candidate.get("extraction")
    if isinstance(extraction, Mapping):
        if extraction.get("kind") == "extracted_from_official_package":
            require(
                extraction.get("complete") is True,
                "PARTIAL_EXTRACTION_BLOCKED",
            )
            page_range = extraction.get("page_range")
            require(
                isinstance(page_range, Sequence)
                and not isinstance(page_range, (str, bytes))
                and len(page_range) == 2,
                "EXTRACTION_PAGE_RANGE_REQUIRED",
            )
        elif extraction.get("kind") == "structural_docx_form_boundary":
            require(
                extraction.get("complete") is True,
                "PARTIAL_EXTRACTION_BLOCKED",
            )
            element_range = extraction.get("element_range")
            require(
                isinstance(element_range, Sequence)
                and not isinstance(element_range, (str, bytes))
                and len(element_range) == 2
                and all(isinstance(value, int) for value in element_range)
                and element_range[0] < element_range[1],
                "EXTRACTION_ELEMENT_RANGE_REQUIRED",
            )
    elif (
        isinstance(provenance, Mapping)
        and _text(provenance.get("kind")) == "official_dvc_attachment"
    ):
        require(False, "EXTRACTION_BOUNDARY_REQUIRED")

    for flag, reason in (
        ("is_seed", "SEED_OR_DEMO_BLOCKED"),
        ("is_demo", "SEED_OR_DEMO_BLOCKED"),
        ("is_quarantined", "QUARANTINED_BLOCKED"),
    ):
        require(candidate.get(flag) is not True, reason)
    require(
        _text(candidate.get("effective_status")).casefold()
        not in {"expired", "superseded", "hết hiệu lực", "bị thay thế"},
        "EXPIRED_OR_SUPERSEDED_BLOCKED",
    )
    require(
        _text(candidate.get("review_status"))
        in {"candidate_pending_review", "ready_for_human_review"},
        "CANDIDATE_REVIEW_STATUS_REQUIRED",
    )

    return {
        "eligible": not reasons,
        "reason_codes": sorted(reasons),
        "automated_approval": False,
        "runtime_eligible": False,
    }
