"""Deterministic, allowlisted fallback discovery for official source records.

The adapter never infers a document identity from a page title.  It only
returns government-owned URLs that visibly contain the exact instrument token;
the caller must still perform exact metadata, effectivity and file hard gates.
"""

from __future__ import annotations

import json
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
import re
from typing import Any, Iterable, Mapping

from api.form_source_resolution import (
    extract_strict_form_codes,
    normalize_appendix_identifier,
)
from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES


ROOT = Path(__file__).resolve().parents[1]
CURATED_GOVERNMENT_DOCUMENT_SOURCES_PATH = (
    ROOT
    / "notebook_data"
    / "forms"
    / "official_government_document_sources_v1.json"
)


_INSTRUMENT_TOKEN = re.compile(r"\d{1,4}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*", re.I)


def is_allowlisted_official_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_HOST_SUFFIXES)


def _compact_instrument(value: Any) -> str:
    """Normalize a document number only enough for an exact equality check."""

    return re.sub(r"\s+", "", str(value or "")).upper()


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _validated_explicit_form_page_bindings(
    values: Any,
    *,
    source_page_url: str,
    download_urls: list[str],
    verified_as_of: str,
) -> list[dict[str, Any]]:
    """Accept only exact, checksum-pinned ranges from a curated source record.

    These bindings are reserved for official scanned packages whose form pages
    were manually verified.  They identify source pages; they do not express a
    legal approval and callers must still validate the downloaded package.
    """

    if values is None:
        return []
    if not isinstance(values, list):
        return []
    accepted: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for raw in values:
        if not isinstance(raw, Mapping):
            return []
        codes = extract_strict_form_codes(f"Mẫu {raw.get('form_code') or ''}")
        appendix_identifier = normalize_appendix_identifier(
            raw.get("appendix_identifier")
        )
        binding_source_page = str(raw.get("source_page_url") or "").strip()
        download_url = str(raw.get("official_download_url") or "").strip()
        checksum = str(raw.get("source_package_sha256") or "").strip().casefold()
        try:
            size_bytes = int(raw.get("source_package_size_bytes") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        procedure_ids = raw.get("procedure_ids")
        pages = raw.get("source_pages_zero_based")
        binding_verified_as_of = _iso_date(raw.get("verified_as_of"))
        if (
            len(codes) != 1
            or not appendix_identifier
            or binding_source_page != source_page_url
            or download_url not in download_urls
            or not is_allowlisted_official_url(download_url)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or size_bytes <= 256
            or size_bytes > 25 * 1024 * 1024
            or not isinstance(procedure_ids, list)
            or not procedure_ids
            or any(
                not isinstance(procedure_id, str)
                or not re.fullmatch(r"\d+\.\d{6}", procedure_id)
                for procedure_id in procedure_ids
            )
            or procedure_ids != sorted(set(procedure_ids))
            or not isinstance(pages, list)
            or not pages
            or any(
                not isinstance(page, int) or isinstance(page, bool) or page < 0
                for page in pages
            )
            or pages != sorted(set(pages))
            or pages != list(range(pages[0], pages[-1] + 1))
            or binding_verified_as_of != verified_as_of
        ):
            return []
        identity = (codes[0], appendix_identifier, tuple(procedure_ids))
        if identity in identities:
            return []
        identities.add(identity)
        accepted.append(
            {
                "form_code": codes[0],
                "appendix_identifier": appendix_identifier,
                "source_page_url": binding_source_page,
                "official_download_url": download_url,
                "source_package_sha256": checksum,
                "source_package_size_bytes": size_bytes,
                "procedure_ids": list(procedure_ids),
                "source_pages_zero_based": list(pages),
                "verified_as_of": binding_verified_as_of,
            }
        )
    return accepted


def load_curated_government_document(
    instrument: str,
    *,
    legal_as_of: str,
    path: Path = CURATED_GOVERNMENT_DOCUMENT_SOURCES_PATH,
) -> dict[str, Any] | None:
    """Return one strict, checksum-independent government-source fallback.

    The modern VBPL endpoint does not currently index every older document.
    This adapter accepts only an explicitly curated exact-number record from an
    allowlisted Government/Cong Bao page.  It never performs title matching,
    and a record is usable only for the legal date on which its current-status
    evidence was verified.

    The result is source metadata only.  The caller must still download,
    validate, extract and review the actual official package.
    """

    expected = _compact_instrument(instrument)
    as_of = _iso_date(legal_as_of)
    if not expected or not as_of or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema_version") != "official-government-document-sources-v1":
        return None
    matches = [
        item
        for item in payload.get("records") or []
        if isinstance(item, Mapping)
        and _compact_instrument(item.get("instrument")) == expected
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    source_page_url = str(item.get("source_page_url") or "").strip()
    status_source_url = str(item.get("status_source_url") or "").strip()
    download_urls = [
        str(value).strip()
        for value in item.get("download_urls") or []
        if str(value).strip()
    ]
    effective_from = _iso_date(item.get("effective_from"))
    effective_to_raw = item.get("effective_to")
    effective_to = _iso_date(effective_to_raw) if effective_to_raw else None
    package_verified_as_of = _iso_date(item.get("verified_as_of"))
    status_verified_as_of = _iso_date(
        item.get("status_verified_as_of") or item.get("verified_as_of")
    )
    status = str(item.get("effective_status") or "").strip()
    publisher = str(item.get("publisher") or "").strip()
    document_id = str(item.get("document_id") or "").strip()
    title = str(item.get("title") or "").strip()
    if (
        not document_id
        or not title
        or not publisher
        or not effective_from
        or status_verified_as_of != as_of
        or status.casefold() not in {"còn hiệu lực", "con hieu luc", "current"}
        or not is_allowlisted_official_url(source_page_url)
        or not is_allowlisted_official_url(status_source_url)
        or not download_urls
        or any(not is_allowlisted_official_url(value) for value in download_urls)
        or (effective_to_raw and not effective_to)
        or (effective_to and effective_to < effective_from)
    ):
        return None
    explicit_form_page_bindings = _validated_explicit_form_page_bindings(
        item.get("explicit_form_page_bindings"),
        source_page_url=source_page_url,
        download_urls=download_urls,
        verified_as_of=package_verified_as_of or "",
    )
    return {
        "instrument": str(item.get("instrument") or "").strip(),
        "status": "found",
        "reason_code": "EXACT_CURATED_GOVERNMENT_DOCUMENT_FOUND",
        "checked_url": status_source_url,
        "document": {
            "id": document_id,
            "docNum": str(item.get("instrument") or "").strip(),
            "title": title,
            "detailUrl": source_page_url,
            "effFrom": effective_from,
            "effTo": effective_to,
            "effStatus": "Còn hiệu lực",
            "publisher": publisher,
            "source_adapter": "curated_government_document",
            "statusSourceUrl": status_source_url,
            "verifiedAsOf": status_verified_as_of,
            "packageVerifiedAsOf": package_verified_as_of,
        },
        "files": [],
        "direct_download_urls": list(dict.fromkeys(download_urls)),
        "explicit_form_page_bindings": explicit_form_page_bindings,
        "source_adapter": "curated_government_document",
    }


def ordered_fallback_urls(
    urls: Iterable[str],
    *,
    instrument: str,
) -> list[str]:
    """Deduplicate and order known official URLs after primary VBPL lookup."""

    token = "".join(str(instrument or "").split()).casefold()
    seen: set[str] = set()
    scored: list[tuple[int, str]] = []
    for raw in urls:
        url = str(raw or "").strip()
        if not url or url in seen or not is_allowlisted_official_url(url):
            continue
        seen.add(url)
        compact = url.replace(" ", "").casefold()
        # Exact instrument-bearing URLs first; otherwise preserve a stable
        # government-source order without guessing that a page is the document.
        scored.append((0 if token and token in compact else 1, url))
    return [url for _score, url in sorted(scored, key=lambda item: (item[0], item[1]))]


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def extract_exact_instrument_links(
    html: bytes,
    *,
    page_url: str,
    instrument: str,
) -> list[str]:
    """Extract only allowlisted links whose URL/text contains exact instrument."""

    try:
        text = html.decode("utf-8", errors="replace")
    except Exception:
        return []
    compact_instrument = "".join(str(instrument or "").split()).casefold()
    parser = _LinkParser()
    parser.feed(text)
    results: list[str] = []
    for href in parser.links:
        candidate = urljoin(page_url, href)
        if not is_allowlisted_official_url(candidate):
            continue
        compact = candidate.replace(" ", "").casefold()
        if compact_instrument and compact_instrument not in compact:
            continue
        if candidate not in results:
            results.append(candidate)
    return results


__all__ = [
    "CURATED_GOVERNMENT_DOCUMENT_SOURCES_PATH",
    "_validated_explicit_form_page_bindings",
    "extract_exact_instrument_links",
    "is_allowlisted_official_url",
    "load_curated_government_document",
    "ordered_fallback_urls",
]
