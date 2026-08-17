"""Deterministic resolution of public-service form occurrences to official files.

This module deliberately contains no model calls and no approval operation.
It turns exact form/instrument identities into pending review records; runtime
eligibility remains false until an authenticated legal-review transaction.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from api.legal_form_catalog import _is_official_url

_DOCUMENT_NUMBER_RE = re.compile(
    r"(?iu)(?<![\w./])"
    r"(\d{1,4}(?:\s*\.\s*\d{1,4})*\s*/\s*\d{4}\s*/\s*"
    r"[A-ZĐ]+(?:\s*-\s*[A-ZĐ0-9]+)+)"
    r"(?![\w/])"
)
_ISSUED_WITH_RE = re.compile(
    r"(?iu)(?:ban\s+hành\s+kèm\s+theo|theo\s+mẫu\s+tại)"
    r".{0,160}?"
    r"(\d{1,4}(?:\s*\.\s*\d{1,4})*\s*/\s*\d{4}\s*/\s*"
    r"[A-ZĐ]+(?:\s*-\s*[A-ZĐ0-9]+)+)",
    re.DOTALL,
)
_FORM_CODE_TOKEN = (
    r"(?:[A-ZĐ]{1,10}(?:-[A-ZĐ]{1,10})*-?)?"
    r"\d{1,4}[A-ZĐ]?(?:/[A-ZĐ0-9]{1,8})?"
)
_FORM_CODE_RE = re.compile(
    rf"(?iu)\bm(?:ẫu|ấu)(?:\s+s(?:ố|o))?\s+({_FORM_CODE_TOKEN})(?![\w/])"
)
_BARE_CODE_RE = re.compile(
    r"(?iu)(?<![\w/])"
    r"((?:(?:CT|NA|M)\d{1,4}[A-ZĐ]?(?:/[A-ZĐ0-9]{1,8})?"
    r"|[A-ZĐ]{2,10}(?:-[A-ZĐ]{2,10})+-\d{1,4}[A-ZĐ]?))"
    r"(?![\w/])"
)
_FORM_CODE_CONTINUATION_RE = re.compile(
    rf"(?iu)^\s*(?:,|;|\bvà\b|\bhoặc\b)\s*"
    rf"(?:m(?:ẫu|ấu)(?:\s+s(?:ố|o))?\s*)?({_FORM_CODE_TOKEN})(?![\w/])"
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return " ".join(
        re.sub(
            r"[^a-z0-9]+",
            " ",
            "".join(char for char in text if not unicodedata.combining(char)),
        ).split()
    )


def normalize_document_number(value: Any) -> str:
    text = str(value or "")
    matches = _DOCUMENT_NUMBER_RE.findall(text)
    if len(matches) == 1:
        text = matches[0]
    return re.sub(r"\s+", "", text).upper().replace("Ð", "Đ")


def normalize_appendix_identifier(value: Any) -> str | None:
    normalized = re.sub(r"\s+", "", str(value or "")).upper()
    if not normalized or not re.fullmatch(r"(?:[IVXLCDM]+|\d{1,3})", normalized):
        return None
    return normalized


def extract_appendix_identifier(value: Any) -> str | None:
    """Return one exact appendix identifier only when the text is unambiguous."""

    identifiers: list[str] = []
    for match in re.finditer(
        r"\bph[uy] luc(?:\s+so)?\s+([ivxlcdm]+|\d{1,3})\b",
        _fold(value),
        re.IGNORECASE,
    ):
        identifier = normalize_appendix_identifier(match.group(1))
        if identifier and identifier not in identifiers:
            identifiers.append(identifier)
    return identifiers[0] if len(identifiers) == 1 else None


def extract_appendix_heading_identifier(value: Any) -> str | None:
    """Recognize a short appendix heading, not a prose citation."""

    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    for line in lines[:6]:
        folded = _fold(line)
        if len(folded) > 120:
            continue
        match = re.match(
            r"^ph[uy] luc(?:\s+so)?\s+([ivxlcdm]+|\d{1,3})(?:\b|$)",
            folded,
            re.IGNORECASE,
        )
        if match:
            return normalize_appendix_identifier(match.group(1))
    return None


def extract_strict_form_code(value: Any) -> str | None:
    """Extract a form identifier only when it contains a numeric identity."""

    text = str(value or "")
    match = _FORM_CODE_RE.search(text) or _BARE_CODE_RE.search(text)
    if match is None:
        return None
    code = re.sub(r"\s+", "", match.group(1)).upper().replace("Ð", "Đ")
    return code if any(character.isdigit() for character in code) else None


def extract_strict_form_codes(value: Any) -> list[str]:
    text = re.sub(
        r"(?iu)(\d)(ban\s+hành\b)",
        r"\1 \2",
        str(value or ""),
    )
    found: list[str] = []
    explicit_matches = list(_FORM_CODE_RE.finditer(text))
    for match in explicit_matches:
        code = re.sub(r"\s+", "", match.group(1)).upper().replace("Ð", "Đ")
        if any(character.isdigit() for character in code) and code not in found:
            found.append(code)

        # The portal frequently compresses several forms into one component,
        # for example "Mẫu số 06, 07 và 08".  Continue only through explicit
        # list separators so ordinary numbers later in the sentence are not
        # mistaken for form identities.
        cursor = match.end()
        while continuation := _FORM_CODE_CONTINUATION_RE.match(text[cursor:]):
            code = (
                re.sub(r"\s+", "", continuation.group(1))
                .upper()
                .replace("Ð", "Đ")
            )
            if any(character.isdigit() for character in code) and code not in found:
                found.append(code)
            cursor += continuation.end()

    for match in _BARE_CODE_RE.finditer(text):
            code = re.sub(r"\s+", "", match.group(1)).upper().replace("Ð", "Đ")
            if any(character.isdigit() for character in code) and code not in found:
                found.append(code)
    return found


def extract_issuing_instruments(value: Any) -> list[str]:
    """Return only explicit legal-document numbers present in the text."""

    found: list[str] = []
    for match in _DOCUMENT_NUMBER_RE.finditer(str(value or "")):
        normalized = normalize_document_number(match.group(1))
        if normalized not in found:
            found.append(normalized)
    return found


def resolve_single_issuing_instrument(occurrence: Mapping[str, Any]) -> str | None:
    """Return one issuing instrument only when the occurrence proves it."""

    form_name = str(occurrence.get("form_name") or "")
    issued_with = _ISSUED_WITH_RE.search(form_name)
    if issued_with is not None:
        # This is an explicit identity statement, not an inference: later
        # document numbers in the same sentence may be amendments or related
        # legal bases and must not make the issuing instrument ambiguous.
        return normalize_document_number(issued_with.group(1))

    explicit = extract_issuing_instruments(form_name)
    if len(explicit) == 1:
        return explicit[0]
    supplied = {
        normalize_document_number(value)
        for value in occurrence.get("issuing_instruments") or []
        if normalize_document_number(value)
    }
    return next(iter(supplied)) if len(supplied) == 1 else None


def group_occurrences(
    occurrences: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group only identities proven by both form code and issuing instrument."""

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for source in occurrences:
        occurrence = dict(source)
        code = (
            extract_strict_form_code(occurrence.get("form_code"))
            or extract_strict_form_code(occurrence.get("form_name"))
        )
        instrument = resolve_single_issuing_instrument(occurrence)
        supplied_appendix = normalize_appendix_identifier(
            occurrence.get("appendix_identifier")
        )
        inferred_appendix = extract_appendix_identifier(
            occurrence.get("form_name")
        )
        if (
            supplied_appendix
            and inferred_appendix
            and supplied_appendix != inferred_appendix
        ):
            unresolved.append(
                {
                    **occurrence,
                    "reason_code": "APPENDIX_IDENTITY_CONFLICT",
                    "resolved_form_code": code,
                    "resolved_issuing_instrument": instrument,
                    "resolved_appendix_identifier": None,
                }
            )
            continue
        appendix_identifier = supplied_appendix or inferred_appendix
        if not code or not instrument:
            unresolved.append(
                {
                    **occurrence,
                    "reason_code": "FORM_IDENTITY_UNRESOLVED",
                    "resolved_form_code": code,
                    "resolved_issuing_instrument": instrument,
                }
            )
            continue
        key = (code, instrument, appendix_identifier or "")
        identity_material = f"{code}|{instrument}"
        if appendix_identifier:
            identity_material = f"{identity_material}|appendix:{appendix_identifier}"
        group = grouped.setdefault(
            key,
            {
                "group_id": hashlib.sha256(
                    identity_material.encode("utf-8")
                ).hexdigest()[:24],
                "form_code": code,
                "form_name": str(occurrence.get("form_name") or code),
                "issuing_instrument": instrument,
                "appendix_identifier": appendix_identifier,
                "procedure_ids": [],
                "occurrence_ids": [],
                "domains": [],
                "source_tiers": [],
                "executing_levels": [],
                "official_source_urls": [],
                "official_download_urls": [],
                "procedure_metadata": {},
            },
        )
        procedure_id = str(occurrence.get("procedure_id") or "").strip()
        occurrence_id = str(occurrence.get("candidate_id") or "").strip()
        domain = str(occurrence.get("domain") or "").strip()
        tier = str(occurrence.get("source_tier") or "").strip()
        executing_level = str(occurrence.get("executing_level") or "").strip()
        if procedure_id and procedure_id not in group["procedure_ids"]:
            group["procedure_ids"].append(procedure_id)
        if procedure_id:
            group["procedure_metadata"][procedure_id] = {
                "domain": domain or None,
                "source_tier": tier or None,
                "executing_level": executing_level or None,
                "requirement_identity_id": str(
                    occurrence.get("requirement_identity_id") or ""
                ).strip()
                or None,
                "occurrence_id": occurrence_id or None,
            }
        if occurrence_id and occurrence_id not in group["occurrence_ids"]:
            group["occurrence_ids"].append(occurrence_id)
        if domain and domain not in group["domains"]:
            group["domains"].append(domain)
        if tier and tier not in group["source_tiers"]:
            group["source_tiers"].append(tier)
        if (
            executing_level
            and executing_level not in group["executing_levels"]
        ):
            group["executing_levels"].append(executing_level)
        for field, target in (
            ("official_source_page", "official_source_urls"),
            ("official_source_url", "official_source_urls"),
            ("official_download_url", "official_download_urls"),
            ("source_download_url", "official_download_urls"),
        ):
            value = str(occurrence.get(field) or "").strip()
            if value and value not in group[target]:
                group[target].append(value)
        for provenance in occurrence.get("provenance") or []:
            if not isinstance(provenance, Mapping):
                continue
            for field, target in (
                ("source_page_url", "official_source_urls"),
                ("source_download_url", "official_download_urls"),
            ):
                value = str(provenance.get(field) or "").strip()
                if value and value not in group[target]:
                    group[target].append(value)

    groups: list[dict[str, Any]] = []
    for group in grouped.values():
        group["procedure_ids"].sort()
        group["occurrence_ids"].sort()
        group["domains"].sort()
        group["source_tiers"].sort()
        group["executing_levels"].sort()
        group["official_source_urls"].sort()
        group["official_download_urls"].sort()
        group["domain"] = (
            group["domains"][0] if len(group["domains"]) == 1 else None
        )
        group["source_tier"] = (
            group["source_tiers"][0]
            if len(group["source_tiers"]) == 1
            else None
        )
        group["executing_level"] = (
            group["executing_levels"][0]
            if len(group["executing_levels"]) == 1
            else None
        )
        groups.append(group)
    groups.sort(
        key=lambda item: (
            item["issuing_instrument"],
            item["form_code"],
            str(item.get("appendix_identifier") or ""),
        )
    )
    unresolved.sort(key=lambda item: str(item.get("candidate_id") or ""))
    return {"groups": groups, "unresolved": unresolved}


def select_exact_document(
    records: Iterable[Mapping[str, Any]],
    document_number: str,
) -> dict[str, Any] | None:
    expected = normalize_document_number(document_number)
    matches = [
        dict(record)
        for record in records
        if normalize_document_number(record.get("docNum")) == expected
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def classify_effectivity(
    document: Mapping[str, Any],
    *,
    legal_as_of: str,
) -> dict[str, Any]:
    raw_status = document.get("effStatus")
    if isinstance(raw_status, Mapping):
        raw_status = raw_status.get("name") or raw_status.get("code")
    status = _fold(raw_status)
    as_of = _parse_iso_date(legal_as_of)
    effective_from = _parse_iso_date(document.get("effFrom"))
    effective_to = _parse_iso_date(document.get("effTo"))
    base = {
        "eligible": False,
        "effective_from": effective_from.isoformat() if effective_from else None,
        "effective_to": effective_to.isoformat() if effective_to else None,
        "status": str(raw_status or "").strip() or None,
    }
    if not as_of or not effective_from:
        return {**base, "reason_code": "EFFECTIVITY_METADATA_MISSING"}
    if effective_from > as_of:
        return {**base, "reason_code": "ISSUING_INSTRUMENT_NOT_YET_EFFECTIVE"}
    if "mot phan" in status:
        return {**base, "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW"}
    if (
        effective_to
        and effective_to < as_of
        or any(
            marker in status
            for marker in ("het hieu luc", "ngung hieu luc", "bai bo", "thay the")
        )
    ):
        return {**base, "reason_code": "ISSUING_INSTRUMENT_EXPIRED"}
    if status not in {"con hieu luc", "dang co hieu luc"}:
        return {**base, "reason_code": "EFFECTIVITY_STATUS_UNVERIFIED"}
    return {**base, "eligible": True, "reason_code": "CURRENT_AS_OF_DATE"}


def classify_form_effectivity(
    document: Mapping[str, Any],
    *,
    legal_as_of: str,
    appendix_identifier: str | None,
    form_code: str | None = None,
) -> dict[str, Any]:
    """Resolve partial effectivity only with exact official form evidence."""

    decision = classify_effectivity(document, legal_as_of=legal_as_of)
    if decision.get("reason_code") != "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW":
        return decision
    appendix = str(appendix_identifier or "").strip().upper()
    normalized_form_code = (
        extract_strict_form_code(f"Mẫu {form_code}") if form_code else None
    )
    if not appendix and not normalized_form_code:
        return decision
    matches = []
    for item in document.get("appendix_effectivity") or []:
        if not isinstance(item, Mapping):
            continue
        evidence_appendix = str(
            item.get("appendix_identifier") or ""
        ).strip().upper()
        evidence_form_code = extract_strict_form_code(
            f"Mẫu {item.get('target_form_code') or ''}"
        )
        if evidence_appendix and evidence_appendix != appendix:
            continue
        if evidence_form_code and evidence_form_code != normalized_form_code:
            continue
        if not evidence_appendix and not evidence_form_code:
            continue
        matches.append(item)
    if len(matches) != 1:
        return decision
    evidence = matches[0]
    source_url = str(evidence.get("official_source_url") or "").strip()
    verified_as_of = _parse_iso_date(evidence.get("verified_as_of"))
    as_of = _parse_iso_date(legal_as_of)
    if (
        not _is_official_url(source_url)
        or verified_as_of is None
        or as_of is None
        or verified_as_of > as_of
    ):
        return decision

    status = str(evidence.get("status") or "").strip().casefold()
    effective_to = _parse_iso_date(evidence.get("effective_to"))
    if status in {"superseded", "expired", "replaced"} or (
        effective_to is not None and effective_to < as_of
    ):
        return {
            **decision,
            "eligible": False,
            "effective_to": (
                effective_to.isoformat() if effective_to else None
            ),
            "reason_code": "FORM_APPENDIX_SUPERSEDED",
            "replacement_document_number": (
                str(evidence.get("replacement_document_number") or "").strip()
                or None
            ),
            "effectivity_source_url": source_url,
        }
    if status not in {"active", "current", "con hieu luc", "còn hiệu lực"}:
        return decision
    return {
        **decision,
        "eligible": True,
        "reason_code": (
            "FORM_EFFECTIVITY_VERIFIED"
            if evidence.get("target_form_code")
            else "APPENDIX_EFFECTIVITY_VERIFIED"
        ),
        "effectivity_source_url": source_url,
        "verified_as_of": verified_as_of.isoformat(),
    }


def current_dvc_attachment_rule_for_candidate(
    candidate: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Return one exact DVC rule only for its checksum-bound candidate.

    DVC evidence rules bind a procedure profile to a canonical attachment. They
    are intentionally not generic procedure-code rules: reusing one for a
    different form, attachment or appendix would make a later review gate
    appear grounded when it is not. More than one matching rule is ambiguous
    and therefore fails closed.
    """

    procedure_id = str(
        candidate.get("procedure_id")
        or candidate.get("suggested_procedure_id")
        or ""
    ).strip()
    official_procedure_code = str(
        candidate.get("official_procedure_code") or ""
    ).strip()
    candidate_code = str(candidate.get("form_code") or "").strip().upper()
    provenance = candidate.get("provenance")
    provenance = provenance if isinstance(provenance, Mapping) else {}
    provenance_kind = str(
        candidate.get("provenance_kind") or provenance.get("kind") or ""
    ).strip()
    source_attachment_id = str(
        candidate.get("source_attachment_id")
        or provenance.get("source_attachment_id")
        or ""
    ).strip()
    source_package_sha256 = str(
        candidate.get("source_package_sha256")
        or provenance.get("source_package_sha256")
        or ""
    ).strip().casefold()
    candidate_source_page = str(
        candidate.get("source_page_url")
        or candidate.get("page_url")
        or candidate.get("source_url")
        or ""
    ).strip()
    candidate_basis = {
        normalize_document_number(value)
        for value in candidate.get("legal_basis") or []
        if normalize_document_number(value)
    }
    matches: list[Mapping[str, Any]] = []
    for rule in rules:
        if str(rule.get("evidence_basis") or "").strip() != (
            "official_current_dvc_attachment"
        ):
            continue
        target_code = str(rule.get("target_form_code") or "").strip().upper()
        attachment = rule.get("canonical_artifact_attachment")
        if (
            not candidate_code
            or candidate_code != target_code
            or provenance_kind != "official_dvc_attachment"
            or not isinstance(attachment, Mapping)
            or source_attachment_id
            != str(attachment.get("attachment_id") or "").strip()
            or source_package_sha256
            != str(attachment.get("sha256") or "").strip().casefold()
        ):
            continue
        instrument = normalize_document_number(rule.get("issuing_instrument"))
        if not instrument or instrument not in candidate_basis:
            continue
        binding_urls: set[str] = set()
        bound_procedures: set[str] = set()
        for binding in rule.get("procedure_bindings") or []:
            if not isinstance(binding, Mapping):
                continue
            bound_procedures.add(str(binding.get("procedure_id") or "").strip())
            binding_url = str(binding.get("official_source_url") or "").strip()
            if binding_url:
                binding_urls.add(binding_url)
        canonical_source_page = str(
            attachment.get("source_page_url") or ""
        ).strip()
        if canonical_source_page:
            binding_urls.add(canonical_source_page)
        if (
            not bound_procedures
            or (procedure_id not in bound_procedures
                and official_procedure_code not in bound_procedures)
            or not _is_official_url(candidate_source_page)
            or candidate_source_page not in binding_urls
        ):
            continue
        matches.append(rule)
    return matches[0] if len(matches) == 1 else None


def select_standalone_form_file(
    files: Sequence[Mapping[str, Any]],
    form_code: str,
) -> dict[str, Any] | None:
    expected = extract_strict_form_code(f"Mẫu {form_code}")
    if not expected:
        return None
    matches = []
    for source in files:
        item = dict(source)
        filename = str(item.get("fileName") or item.get("originalFileName") or "")
        if extract_strict_form_code(filename) == expected:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def _fragmented_numeric_form_heading_codes(value: Any) -> list[str]:
    """Recover only an exact numeric heading whose Vietnamese glyphs split."""

    compact = re.sub(r"\s+", "", _fold(value))
    match = re.fullmatch(r"mauso(\d{1,4}[a-z]?)", compact)
    return [match.group(1).upper()] if match else []


def _exact_page_form_heading_codes(lines: Sequence[str]) -> list[str]:
    """Recover an exact form heading near the top of a scanned page."""

    heading_index = (
        1
        if len(lines) > 1 and re.fullmatch(r"\d{1,4}", lines[0])
        else 0
    )
    for line in lines[heading_index : heading_index + 12]:
        folded = _fold(line)
        if not re.match(r"^m(?:au|iu)(?:\s+so)?\s+", folded):
            continue
        codes = extract_strict_form_codes(line)
        if codes:
            return codes
        # Tesseract occasionally reads the standalone heading "Mẫu số" as
        # "Miu so". Accept only that exact numeric heading prefix, never a
        # prose reference in the form body.
        match = re.match(r"^miu\s+so\s+(\d{1,4}[a-z]?)(?:\b|$)", folded)
        if match:
            return [match.group(1).upper()]
    fragmented_heading = " ".join(
        lines[heading_index : heading_index + 5]
    )
    return _fragmented_numeric_form_heading_codes(fragmented_heading)


def locate_pdf_form_page_range(
    page_texts: Sequence[str],
    form_code: str,
    *,
    appendix_identifier: str | None = None,
    max_pages: int = 12,
) -> tuple[int, int] | None:
    """Locate a conservative zero-based half-open page range in a PDF bundle."""

    expected = extract_strict_form_code(f"Mẫu {form_code}")
    if not expected:
        return None
    requested_appendix = normalize_appendix_identifier(appendix_identifier)
    if appendix_identifier and not requested_appendix:
        return None
    page_codes = [extract_strict_form_codes(text) for text in page_texts]
    page_heading_codes: list[list[str]] = []
    for text in page_texts:
        lines = [
            line.strip()
            for line in str(text or "").splitlines()
            if line.strip()
        ]
        page_heading_codes.append(_exact_page_form_heading_codes(lines))
    current_appendix: str | None = None
    appendix_by_page: list[str | None] = []
    appendix_heading_pages: set[int] = set()
    for index, text in enumerate(page_texts):
        heading = extract_appendix_heading_identifier(text)
        if heading:
            current_appendix = heading
            appendix_heading_pages.add(index)
        appendix_by_page.append(current_appendix)

    def in_requested_appendix(index: int) -> bool:
        return (
            requested_appendix is None
            or appendix_by_page[index] == requested_appendix
        )

    if requested_appendix and requested_appendix not in appendix_by_page:
        return None
    header_starts = []
    for index in range(len(page_texts)):
        lines = [
            line.strip()
            for line in str(page_texts[index] or "").splitlines()
            if line.strip()
        ]
        heading_codes = page_heading_codes[index]
        if (
            lines
            and in_requested_appendix(index)
            and heading_codes == [expected]
        ):
            header_starts.append(index)
    # A page that merely contains one reference such as "theo Mẫu số 06"
    # is a legal-provision page, not the downloadable form itself.  Only an
    # exact form heading near the top of the page may start an extracted form.
    # This intentionally fails closed when OCR cannot prove that heading.
    starts = header_starts
    if len(starts) != 1:
        return None
    start = starts[0]
    end = min(len(page_texts), start + max_pages)
    for index in range(start + 1, end):
        if index in appendix_heading_pages:
            end = index
            break
        if requested_appendix and appendix_by_page[index] != requested_appendix:
            end = index
            break
        boundary_codes = page_codes[index] or page_heading_codes[index]
        if boundary_codes and expected not in boundary_codes:
            end = index
            break
    if end <= start:
        return None
    return start, end


def build_pending_candidate(
    *,
    group: Mapping[str, Any],
    document: Mapping[str, Any],
    artifact: Mapping[str, Any],
    legal_as_of: str,
    procedure_id: str | None = None,
) -> dict[str, Any]:
    """Build one queue record for one exact procedure binding."""

    resolved_procedure_id = str(
        procedure_id
        or next(iter(group.get("procedure_ids") or []), "")
    ).strip()
    if not resolved_procedure_id:
        raise ValueError("procedure_id_required")
    effectivity = classify_form_effectivity(
        document,
        legal_as_of=legal_as_of,
        appendix_identifier=str(group.get("appendix_identifier") or "") or None,
        form_code=str(group.get("form_code") or "") or None,
    )
    if effectivity["eligible"] is not True:
        raise ValueError(str(effectivity["reason_code"]))
    checksum = str(artifact.get("sha256") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("candidate_checksum_invalid")
    local_path = str(artifact.get("local_path") or "").strip()
    download_url = str(artifact.get("download_url") or "").strip()
    if not local_path or not download_url:
        raise ValueError("official_file_required")

    group_id = str(group.get("group_id") or "").strip()
    document_id = str(document.get("id") or "").strip()
    code = str(group.get("form_code") or "").strip()
    record_id = "three-tier-" + hashlib.sha256(
        f"{group_id}|{resolved_procedure_id}|{checksum}".encode("utf-8")
    ).hexdigest()[:24]
    canonical_form_id = f"form-three-tier-{group_id}"
    source_page = str(artifact.get("source_page_url") or "").strip() or (
        str(document.get("detailUrl") or "").strip()
        or (
            f"https://vbpl.vn/van-ban/chi-tiet/--{document_id}"
            if document_id
            else "https://vbpl.vn/van-ban/trung-uong"
        )
    )
    publisher = str(artifact.get("publisher") or "").strip() or (
        "Cơ sở dữ liệu quốc gia về pháp luật - Bộ Tư pháp"
    )
    provenance_kind = str(
        artifact.get("provenance_kind") or ""
    ).strip() or "official_vbpl_attachment"
    provenance = {
        "kind": provenance_kind,
        "publisher": publisher,
        "document_id": document_id,
        "document_number": str(document.get("docNum") or ""),
        "source_page_url": source_page,
        "source_download_url": download_url,
        "sha256": checksum,
        "legal_as_of": legal_as_of,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    source_retrieval_url = str(
        artifact.get("source_retrieval_url") or ""
    ).strip()
    source_attachment_id = str(
        artifact.get("source_attachment_id") or ""
    ).strip()
    source_package_sha256 = str(
        artifact.get("source_package_sha256") or ""
    ).strip().casefold()
    source_package_size_bytes = artifact.get("source_package_size_bytes")
    source_pages_zero_based = artifact.get("source_pages_zero_based")
    source_file_name = str(artifact.get("source_file_name") or "").strip()
    source_retrieval_method = str(
        artifact.get("source_retrieval_method") or ""
    ).strip()
    publication_decision_number = str(
        artifact.get("publication_decision_number") or ""
    ).strip()
    extraction = artifact.get("extraction")
    if source_retrieval_url:
        provenance["source_retrieval_url"] = source_retrieval_url
    if source_attachment_id:
        provenance["source_attachment_id"] = source_attachment_id
    if source_package_sha256:
        provenance["source_package_sha256"] = source_package_sha256
    if source_package_size_bytes is not None:
        provenance["source_package_size_bytes"] = source_package_size_bytes
    if source_file_name:
        provenance["source_file_name"] = source_file_name
    if source_retrieval_method:
        provenance["source_retrieval_method"] = source_retrieval_method
    if publication_decision_number:
        provenance["publication_decision_number"] = publication_decision_number
    form_name = str(group.get("form_name") or code)
    result = {
        "id": record_id,
        "three_tier_group_id": group_id,
        "proposed_canonical_form_id": canonical_form_id,
        "detected_form_name": form_name,
        "canonical_form_name": form_name,
        "form_title": form_name,
        "form_code": code,
        "appendix_identifier": str(group.get("appendix_identifier") or "") or None,
        "issuing_instrument": str(document.get("docNum") or "") or None,
        "procedure_id": resolved_procedure_id,
        "suggested_procedure_id": resolved_procedure_id,
        "procedure_mapping_verified": True,
        "official_procedure_code": resolved_procedure_id,
        "domain": group.get("domain") or "unknown",
        "suggested_domain": group.get("domain") or "unknown",
        "source_tier": group.get("source_tier"),
        "administrative_level": group.get("executing_level") or "commune",
        "jurisdiction": "Hai Phong",
        "file_name": str(artifact.get("file_name") or ""),
        "file_path": local_path,
        "local_path": local_path,
        "sha256": checksum,
        "source_sha256": checksum,
        "size_bytes": int(artifact.get("size_bytes") or 0),
        "source_url": source_page,
        "page_url": source_page,
        "source_page_url": source_page,
        "source_download_url": download_url,
        "source_retrieval_url": source_retrieval_url or None,
        "source_retrieval_method": source_retrieval_method or None,
        "source_attachment_id": source_attachment_id or None,
        "source_file_name": source_file_name or None,
        "source_package_sha256": source_package_sha256 or None,
        "source_package_size_bytes": source_package_size_bytes,
        "provenance_kind": provenance_kind,
        "publication_decision_number": publication_decision_number or None,
        "publisher": publisher,
        "official_level": "official",
        "legal_basis": [str(document.get("docNum") or "")],
        "effective_from": effectivity["effective_from"],
        "effective_to": effectivity["effective_to"],
        "effective_status": effectivity["status"],
        "effectivity_reason_code": effectivity["reason_code"],
        "effectivity_source_url": effectivity.get("effectivity_source_url"),
        "effectivity_verified_as_of": effectivity.get("verified_as_of"),
        "provenance": provenance,
        "preparation_status": "ready_for_human_review",
        "technical_validation": {
            "status": "passed",
            "reason_codes": [],
        },
        "catalog_status": "candidate_pending_review",
        "legal_review_status": "candidate_pending_review",
        "review_status": "candidate_pending_review",
        "is_approved": False,
        "approved": False,
        "is_canonical": False,
        "runtime_eligible": False,
        "has_official_file": True,
        "has_download": True,
        "download_url": download_url,
        "hard_gate_reason_codes": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
    }
    if source_pages_zero_based is not None:
        result["source_pages_zero_based"] = list(source_pages_zero_based)
    if isinstance(extraction, Mapping):
        result["extraction"] = dict(extraction)
    return result


def merge_pending_records(
    existing_records: Sequence[Mapping[str, Any]],
    incoming_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Idempotently merge pending candidates without weakening prior gates.

    A resolver may reproduce an already human-triaged candidate while updating
    only retrievable-source metadata.  If its immutable artifact and binding
    still match, retain that human disposition instead of silently replacing
    it with ``candidate_pending_review``.  This is deliberately conservative:
    any checksum or provenance drift remains fail-closed.
    """

    records = [dict(item) for item in existing_records]
    index_by_id = {
        str(item.get("id") or ""): index
        for index, item in enumerate(records)
        if str(item.get("id") or "")
    }
    actions = {"created": 0, "updated": 0, "unchanged": 0}
    preserved_review_dispositions = 0

    def _same_or_missing(
        existing: Mapping[str, Any],
        incoming: Mapping[str, Any],
        field: str,
    ) -> bool:
        """Permit an absent legacy value, never conflicting immutable values."""

        old = str(existing.get(field) or "").strip().casefold()
        new = str(incoming.get(field) or "").strip().casefold()
        return not old or not new or old == new

    def _has_human_disposition(record: Mapping[str, Any]) -> bool:
        return bool(
            record.get("approved")
            or record.get("runtime_eligible")
            or record.get("is_approved")
            or str(record.get("reviewed_by") or "").strip()
            or str(record.get("reviewed_at") or "").strip()
            or str(record.get("review_note") or "").strip()
            or str(record.get("review_status") or "").strip()
            not in {"", "candidate_pending_review"}
        )

    def _preserve_review_disposition(
        existing: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = dict(incoming)
        # Review decisions and service eligibility belong to the human workflow,
        # not to source re-resolution.  Keep them only after the immutable
        # source/binding comparison above has succeeded.
        for field in (
            "catalog_status",
            "legal_review_status",
            "review_status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "is_approved",
            "approved",
            "runtime_eligible",
            "is_canonical",
            "priority_path",
            "local_path",
        ):
            if field in existing:
                merged[field] = existing[field]
        old_gates = [
            str(value).strip()
            for value in existing.get("hard_gate_reason_codes") or []
            if str(value).strip()
        ]
        new_gates = [
            str(value).strip()
            for value in incoming.get("hard_gate_reason_codes") or []
            if str(value).strip()
        ]
        merged["hard_gate_reason_codes"] = list(
            dict.fromkeys([*old_gates, *new_gates])
        )
        return merged
    for source in incoming_records:
        incoming = dict(source)
        record_id = str(incoming.get("id") or "")
        if not record_id:
            raise ValueError("candidate_id_required")
        if incoming.get("approved") is not False or incoming.get(
            "runtime_eligible"
        ) is not False:
            raise ValueError("pending_candidate_gate_required")
        existing_index = index_by_id.get(record_id)
        if existing_index is None:
            records.append(incoming)
            index_by_id[record_id] = len(records) - 1
            actions["created"] += 1
            continue
        existing = records[existing_index]
        old_checksum = str(existing.get("sha256") or "").casefold()
        new_checksum = str(incoming.get("sha256") or "").casefold()
        if old_checksum and new_checksum and old_checksum != new_checksum:
            raise ValueError("candidate_checksum_drift")
        for field in (
            "source_package_sha256",
            "official_procedure_code",
            "procedure_id",
            "proposed_canonical_form_id",
        ):
            if not _same_or_missing(existing, incoming, field):
                raise ValueError("candidate_provenance_drift")
        if existing == incoming:
            actions["unchanged"] += 1
            continue
        if _has_human_disposition(existing):
            records[existing_index] = _preserve_review_disposition(
                existing, incoming
            )
            preserved_review_dispositions += 1
        else:
            records[existing_index] = incoming
        actions["updated"] += 1
    return {
        "records": records,
        "action_counts": actions,
        "preserved_review_dispositions": preserved_review_dispositions,
    }


def quarantine_stale_pending_records(
    records: Sequence[Mapping[str, Any]],
    *,
    active_ids: set[str],
) -> dict[str, Any]:
    """Fail-close generated records that no longer reproduce from source."""

    output: list[dict[str, Any]] = []
    quarantined = 0
    for source in records:
        item = dict(source)
        record_id = str(item.get("id") or "")
        if (
            record_id.startswith("three-tier-")
            and record_id not in active_ids
            and item.get("approved") is not True
        ):
            item.update(
                {
                    "is_quarantined": True,
                    "review_status": "changes_requested",
                    "legal_review_status": "changes_requested",
                    "catalog_status": "quarantined",
                    "runtime_eligible": False,
                    "hard_gate_reason_codes": ["SOURCE_RESOLUTION_STALE"],
                }
            )
            quarantined += 1
        output.append(item)
    return {"records": output, "quarantined_count": quarantined}
