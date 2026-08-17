"""Deterministic legal-source inventory and corpus reconciliation.

The module is read-only with respect to the legal corpus.  It turns official
procedure legal bases and reviewed expected-source rows into an exact-number
ledger, classifies that ledger against current document metadata, and may
prepare candidate-only source-gap jobs after an exact official discovery.

It never approves, imports, indexes, embeds or changes the active collection.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
from urllib.parse import quote

from api.source_gap_jobs import create_source_gap_job
from scripts.crawl_canonical_forms import is_allowed_official_url


_INVALID_IDENTITY_MARKERS = ("\ufffd", "?", "Ãƒ", "Ã‚", "Ã„", "Ã¡Âº", "Ã¡Â»")
_LAW_NUMBER_RE = re.compile(
    r"(?<![0-9A-ZĐ.])"
    r"(\d{1,4}(?:\.\d+)?/\d{4}/[A-ZĐ0-9]{1,12}(?:-[A-ZĐ0-9]{1,12})*)"
    r"(?![0-9A-ZĐ-])",
    re.IGNORECASE,
)
_SERIALIZED_BASIS_RE = re.compile(
    r"^\s*@\{\s*code\s*=\s*(.*?);\s*name\s*=\s*(.*?)\s*\}\s*$",
    re.IGNORECASE | re.DOTALL,
)
_VBPL_FILE_API = (
    "https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/"
    "buckets/vbpl"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    decomposed = unicodedata.normalize("NFD", _text(value).casefold())
    ascii_text = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def _iso_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return ""


def normalize_law_number(value: Any) -> str | None:
    """Return one exact legal number, never a title-only approximation."""

    text = unicodedata.normalize("NFKC", _text(value)).upper()
    if not text or any(marker.upper() in text for marker in _INVALID_IDENTITY_MARKERS):
        return None
    text = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("‐", "-")
        .replace("−", "-")
    )
    match = _LAW_NUMBER_RE.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1)).upper()


def _basis_parts(value: Any) -> tuple[str, str]:
    if isinstance(value, Mapping):
        return _text(value.get("code") or value.get("law_number")), _text(
            value.get("name") or value.get("title")
        )
    serialized = _SERIALIZED_BASIS_RE.match(_text(value))
    if serialized:
        return serialized.group(1).strip(), serialized.group(2).strip()
    return _text(value), ""


def extract_legal_basis(
    value: Any,
    *,
    legal_as_of: date,
) -> tuple[str | None, str, str | None]:
    """Extract source identity plus a fail-closed identity reason."""

    raw_code, name = _basis_parts(value)
    if any(marker in raw_code for marker in _INVALID_IDENTITY_MARKERS):
        return None, name, "INVALID_OFFICIAL_IDENTITY"
    law_number = normalize_law_number(raw_code)
    if law_number is None:
        return None, name, "LEGAL_INSTRUMENT_NUMBER_UNRESOLVED"
    try:
        issue_year = int(law_number.split("/", 2)[1])
    except (IndexError, ValueError):
        return law_number, name, "INVALID_OFFICIAL_IDENTITY"
    if issue_year > legal_as_of.year:
        return law_number, name, "FUTURE_INSTRUMENT_IDENTITY"
    return law_number, name, None


def legal_basis_context_reason(
    *,
    law_number: str,
    name: str,
    expected_jurisdiction: str,
) -> str | None:
    """Fail closed for local instruments without the expected locality."""

    folded_number = _fold(law_number)
    if not (
        folded_number.endswith("qd ubnd")
        or folded_number.endswith("nq hdnd")
    ):
        return None
    expected = _fold(expected_jurisdiction or "Hải Phòng")
    if expected and expected in _fold(name):
        return None
    return "LOCAL_INSTRUMENT_JURISDICTION_UNVERIFIED"


def build_requirements(
    *,
    procedures: Sequence[Mapping[str, Any]],
    expected_source_rows: Sequence[Mapping[str, Any]],
    legal_as_of: str,
) -> list[dict[str, Any]]:
    """Build a deduplicated, privacy-safe exact legal-source ledger."""

    as_of = date.fromisoformat(legal_as_of)
    grouped: dict[str, dict[str, Any]] = {}

    def add(
        law_number: str,
        name: str,
        origin_id: str,
        *,
        domain: str = "",
        jurisdiction: str = "",
    ) -> None:
        record = grouped.setdefault(
            law_number,
            {
                "law_number": law_number,
                "official_name": name,
                "origin_ids": set(),
                "domains": set(),
                "jurisdictions": set(),
            },
        )
        record["origin_ids"].add(origin_id)
        if name and not record.get("official_name"):
            record["official_name"] = name
        if domain:
            record["domains"].add(domain)
        if jurisdiction:
            record["jurisdictions"].add(jurisdiction)

    for procedure in procedures:
        if _text(procedure.get("status")) != "OFFICIAL_PROCEDURE_MATCHED":
            continue
        procedure_id = _text(procedure.get("procedure_id"))
        for raw_basis in procedure.get("legal_basis") or []:
            law_number, name, reason = extract_legal_basis(
                raw_basis,
                legal_as_of=as_of,
            )
            context_reason = (
                legal_basis_context_reason(
                    law_number=law_number,
                    name=name,
                    expected_jurisdiction=_text(
                        procedure.get("jurisdiction") or "Hải Phòng"
                    ),
                )
                if law_number and reason is None
                else None
            )
            if law_number and reason is None and context_reason is None:
                add(
                    law_number,
                    name,
                    f"procedure:{procedure_id}",
                    domain=_text(procedure.get("domain") or procedure.get("category")),
                    jurisdiction=_text(procedure.get("jurisdiction")),
                )

    for row in expected_source_rows:
        raw_number = row.get("law_number") or row.get("expected_law_number")
        law_number = normalize_law_number(raw_number)
        if law_number is None:
            continue
        origin = ":".join(
            filter(
                None,
                (
                    "expected",
                    _text(row.get("set")),
                    _text(row.get("case_id")),
                ),
            )
        )
        add(
            law_number,
            _text(row.get("official_name") or row.get("title")),
            origin,
            domain=_text(row.get("domain")),
            jurisdiction=_text(row.get("jurisdiction")),
        )

    result: list[dict[str, Any]] = []
    for law_number in sorted(grouped):
        record = grouped[law_number]
        requirement_id = "lsr-" + hashlib.sha256(
            f"{law_number}\n{legal_as_of}".encode("utf-8")
        ).hexdigest()[:24]
        result.append(
            {
                "requirement_id": requirement_id,
                "law_number": law_number,
                "official_name": record["official_name"],
                "origin_ids": sorted(record["origin_ids"]),
                "domains": sorted(record["domains"]),
                "jurisdictions": sorted(record["jurisdictions"]),
                "legal_as_of": legal_as_of,
            }
        )
    return result


def _metadata_gap(row: Mapping[str, Any]) -> str | None:
    if not _text(row.get("source_url")):
        return "OFFICIAL_SOURCE_URL_REQUIRED"
    if not is_allowed_official_url(_text(row.get("source_url"))):
        return "OFFICIAL_SOURCE_URL_NOT_ALLOWED"
    if not _iso_date(row.get("effective_date")):
        return "DOCUMENT_EFFECTIVE_DATE_REQUIRED"
    if not _text(row.get("scope")):
        return "DOCUMENT_SCOPE_REQUIRED"
    if not _text(row.get("sector") or row.get("field_name")):
        return "DOCUMENT_DOMAIN_REQUIRED"
    if int(row.get("valid_chunk_count") or 0) <= 0:
        return "RETRIEVABLE_CHUNK_REQUIRED"
    return None


def classify_requirement(
    requirement: Mapping[str, Any],
    corpus_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify by exact normalized number; never use title/broad content LIKE."""

    expected = normalize_law_number(requirement.get("law_number"))
    matches = [
        row
        for row in corpus_rows
        if normalize_law_number(row.get("law_number")) == expected
    ]
    base = {
        "requirement_id": _text(requirement.get("requirement_id")),
        "law_number": expected,
        "legal_as_of": _text(requirement.get("legal_as_of")),
        "corpus_match_count": len(matches),
    }
    if expected is None:
        return {
            **base,
            "classification": "VERIFIED_DATA_GAP",
            "reason_code": "INVALID_OFFICIAL_IDENTITY",
        }
    if not matches:
        return {
            **base,
            "classification": "MISSING_LEGAL_SOURCE",
            "reason_code": "EXACT_LAW_NUMBER_NOT_IN_CORPUS",
        }
    if len(matches) > 1:
        return {
            **base,
            "classification": "AMBIGUOUS_CORPUS_MATCH",
            "reason_code": "DUPLICATE_LAW_NUMBER_REQUIRES_IDENTITY_REVIEW",
            "document_ids": sorted(int(row["id"]) for row in matches),
        }

    row = matches[0]
    document_id = int(row["id"])
    as_of = date.fromisoformat(_text(requirement.get("legal_as_of")))
    effective = _iso_date(row.get("effective_date"))
    expired = _iso_date(row.get("expired_date"))
    status = _text(row.get("status")).casefold()
    if status in {"expired", "superseded", "repealed", "inactive"} or (
        expired and date.fromisoformat(expired) <= as_of
    ):
        return {
            **base,
            "document_id": document_id,
            "classification": "EXPIRED_OR_SUPERSEDED",
            "reason_code": "DOCUMENT_NOT_CURRENT_AS_OF",
        }
    if effective and date.fromisoformat(effective) > as_of:
        return {
            **base,
            "document_id": document_id,
            "classification": "EXPIRED_OR_SUPERSEDED",
            "reason_code": "DOCUMENT_NOT_YET_EFFECTIVE_AS_OF",
        }
    gap = _metadata_gap(row)
    if gap:
        return {
            **base,
            "document_id": document_id,
            "classification": "METADATA_INCOMPLETE",
            "reason_code": gap,
        }
    return {
        **base,
        "document_id": document_id,
        "classification": "FOUND_AND_INDEXED",
        "reason_code": "EXACT_CURRENT_DOCUMENT_WITH_RETRIEVABLE_CHUNK",
    }


def _document_value(document: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = document.get(key)
        if value not in (None, ""):
            return value
    return None


def _document_type(document: Mapping[str, Any]) -> str:
    value = _document_value(document, "loaiVanBan", "document_type", "docType")
    if isinstance(value, Mapping):
        return _text(value.get("name") or value.get("code"))
    return _text(value)


def _select_official_file(files: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    supported = []
    for item in files:
        name = _text(item.get("fileName") or item.get("file_name"))
        if name.casefold().endswith((".pdf", ".doc", ".docx")):
            supported.append(item)
    if not supported:
        return None
    return sorted(
        supported,
        key=lambda item: (
            0 if int(item.get("relatedType") or 0) == 1 else 1,
            0 if _text(item.get("fileName")).casefold().endswith(".pdf") else 1,
            _text(item.get("fileName")),
        ),
    )[0]


def build_legal_source_gap_job(
    requirement: Mapping[str, Any],
    discovery: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a queued, unapproved job from an exact official discovery."""

    if (
        _text(discovery.get("status")) != "found"
        or _text(discovery.get("reason_code")) != "EXACT_OFFICIAL_DOCUMENT_FOUND"
    ):
        raise ValueError("exact_official_document_required")
    document = discovery.get("document")
    files = discovery.get("files")
    if not isinstance(document, Mapping) or not isinstance(files, Sequence):
        raise ValueError("official_document_payload_required")
    file_record = _select_official_file(
        item for item in files if isinstance(item, Mapping)
    )
    if file_record is None:
        raise ValueError("official_document_file_required")

    document_id = _text(_document_value(document, "id", "document_id"))
    file_name = _text(file_record.get("fileName") or file_record.get("file_name"))
    detail_url = _text(
        _document_value(document, "detailUrl", "detail_url", "source_url")
    )
    if not document_id or not file_name or not is_allowed_official_url(detail_url):
        raise ValueError("official_document_provenance_required")
    download_url = (
        f"{_VBPL_FILE_API}/{quote(document_id, safe='')}/"
        f"{quote(file_name, safe='')}/download"
    )
    if not is_allowed_official_url(download_url):
        raise ValueError("official_document_file_host_not_allowed")

    issued_date = _iso_date(
        _document_value(document, "ngayBanHanh", "issueDate", "issued_date")
    )
    effective_date = _iso_date(
        _document_value(document, "ngayCoHieuLuc", "effFrom", "effective_date")
    )
    issuing_agency = _text(
        _document_value(document, "coQuanBanHanh", "agencyName", "issuing_agency")
    )
    document_type = _document_type(document)
    if not issued_date or not effective_date or not issuing_agency or not document_type:
        raise ValueError("official_effectivity_metadata_required")

    job = create_source_gap_job(
        case_id=_text(requirement.get("requirement_id")),
        gap_type="MISSING_LEGAL_SOURCE",
        source_pages=[detail_url, download_url],
        legal_as_of=_text(requirement.get("legal_as_of")),
        expected_name=_text(
            _document_value(document, "title", "official_name")
            or requirement.get("official_name")
        ),
        expected_code=_text(requirement.get("law_number")),
    )
    if job is None:  # Defensive: this gap type must always create a job.
        raise RuntimeError("legal_source_gap_job_not_created")
    return {
        **job,
        "official_metadata": {
            "law_number": _text(requirement.get("law_number")),
            "title": _text(
                _document_value(document, "title", "official_name")
                or requirement.get("official_name")
            ),
            "issuing_agency": issuing_agency,
            "document_type": document_type,
            "issued_date": issued_date,
            "effective_date": effective_date,
            "scope": "central",
            "domain": (_text((requirement.get("domains") or [""])[0])),
            "confirmed_official_source": True,
        },
        "approved": False,
        "runtime_eligible": False,
        "automated_approval": False,
    }
