"""Bounded, exact official-source adapter for legal validity observations.

The VBPL web action is treated as an unstable transport detail.  Every result
is validated as an exact, unique instrument identity before it can influence
serving.  Failures return reason codes and never guessed metadata.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    LegalValidityObservation,
    NormalizedValidityStatus,
    normalize_law_number,
    normalize_provisions,
    normalize_validity_status,
)
from api.official_http import build_verified_ssl_context
from api.official_source_diagnostics import (
    classify_official_source_failure,
    require_official_component_response,
)

VBPL_VALIDITY_SEARCH_URL = "https://vbpl.vn/van-ban/trung-uong"
VBPL_VALIDITY_SEARCH_ACTION = "c529d164f28418e5898a834422629e64c6816af1"
USER_AGENT = "HaiPhongLegalAssistant/1.0 (+legal-validity-sync; admin-contact)"


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _value(document: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = document.get(key)
        if value not in (None, ""):
            return value
    return None


def _iso_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _slug(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _fold(value)).strip("-")
    return slug[:180] or "van-ban"


def _official_detail_url(value: Any) -> str | None:
    text = str(value or "").strip()
    parsed = urlparse(text)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or not (
        hostname == "vbpl.vn" or hostname.endswith(".vbpl.vn")
    ):
        return None
    return text


def _raw_status(document: Mapping[str, Any]) -> str | None:
    value = _value(document, "effStatus", "effectiveStatus", "validityStatus")
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("code")
    text = str(value or "").strip()
    return text or None


def _rsc_json(content: bytes, predicate) -> Any:
    text = content.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\d+:", text):
        try:
            value, _ = decoder.raw_decode(text[match.end() :])
        except json.JSONDecodeError:
            continue
        if predicate(value):
            return value
    # Some test doubles and future official responses may return plain JSON.
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("VBPL_VALIDITY_RSC_PAYLOAD_MISSING") from exc
    if predicate(value):
        return value
    raise ValueError("VBPL_VALIDITY_RSC_PAYLOAD_MISSING")


@dataclass(frozen=True)
class ValidityFetchResult:
    reason_code: str
    observation: LegalValidityObservation | None
    source_status: str


class VBPLValiditySource:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        page_size: int = 100,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.search_url = VBPL_VALIDITY_SEARCH_URL
        self.timeout_seconds = min(max(float(timeout_seconds), 2.0), 60.0)
        self.page_size = min(max(int(page_size), 1), 100)
        self._client = client

    @staticmethod
    def _placeholder_observation(
        *,
        instrument: str,
        document_id: str | None,
        observed_at: datetime,
        identity_status: IdentityStatus,
        evidence_status: EvidenceStatus,
    ) -> LegalValidityObservation:
        return LegalValidityObservation(
            document_id=document_id,
            law_number=instrument,
            issuing_agency=None,
            issued_date=None,
            source_url=VBPL_VALIDITY_SEARCH_URL,
            source_kind="vbpl",
            raw_status=None,
            normalized_status=NormalizedValidityStatus.UNKNOWN,
            effective_from=None,
            effective_to=None,
            affecting_document_number=None,
            affected_provisions=(),
            identity_status=identity_status,
            evidence_status=evidence_status,
            observed_at=observed_at,
            source_updated_at=None,
        )

    def parse_items(
        self,
        *,
        instrument: str,
        items: Any,
        document_id: str | None,
        observed_at: datetime,
        as_of: date,
        expected_issuing_agency: str | None = None,
        expected_issued_date: str | None = None,
        expected_title: str | None = None,
    ) -> ValidityFetchResult:
        expected = normalize_law_number(instrument)
        if expected is None:
            raise ValueError("exact_law_number_required")
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
            return ValidityFetchResult(
                reason_code="OFFICIAL_VALIDITY_PAYLOAD_MALFORMED",
                observation=self._placeholder_observation(
                    instrument=expected,
                    document_id=document_id,
                    observed_at=observed_at,
                    identity_status=IdentityStatus.MISSING,
                    evidence_status=EvidenceStatus.MALFORMED,
                ),
                source_status="malformed",
            )
        matches = [
            item
            for item in items
            if isinstance(item, Mapping)
            and normalize_law_number(_value(item, "docNum", "soKyHieu", "lawNumber")) == expected
        ]
        if len(matches) > 1 and expected_title:
            # VBPL may return an English mirror or different instruments sharing
            # the same local/assembly number. Use the governance title only to
            # disambiguate identity; never to manufacture metadata.
            non_translation = [
                item for item in matches
                if not str(_value(item, "id", "documentId") or "").casefold().startswith("vbpqta_")
            ]
            if len(non_translation) == 1:
                matches = non_translation
            else:
                stop = {
                    "luat", "bo", "nghi", "quyet", "dinh", "thong", "tu", "so",
                    "quy", "dinh", "ve", "cua", "va", "mot", "cac", "nam",
                }
                expected_tokens = {
                    token for token in re.findall(r"[a-z0-9]+", _fold(expected_title))
                    if len(token) > 1 and token not in stop
                }
                ranked: list[tuple[float, Mapping[str, Any]]] = []
                for item in matches:
                    title_tokens = {
                        token for token in re.findall(
                            r"[a-z0-9]+", _fold(_value(item, "title", "name", "subject") or "")
                        ) if len(token) > 1 and token not in stop
                    }
                    score = (
                        len(expected_tokens & title_tokens) / len(expected_tokens)
                        if expected_tokens else 0.0
                    )
                    ranked.append((score, item))
                ranked.sort(key=lambda pair: pair[0], reverse=True)
                if ranked and ranked[0][0] >= 0.5 and (
                    len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.2
                ):
                    matches = [ranked[0][1]]
        if len(matches) != 1:
            identity = IdentityStatus.MISSING if not matches else IdentityStatus.AMBIGUOUS
            code = (
                "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND"
                if not matches
                else "OFFICIAL_DOCUMENT_IDENTITY_AMBIGUOUS"
            )
            return ValidityFetchResult(
                reason_code=code,
                observation=self._placeholder_observation(
                    instrument=expected,
                    document_id=document_id,
                    observed_at=observed_at,
                    identity_status=identity,
                    evidence_status=(
                        EvidenceStatus.CONFLICTING
                        if identity is IdentityStatus.AMBIGUOUS
                        else EvidenceStatus.MALFORMED
                    ),
                ),
                source_status="identity_unverified",
            )

        document = matches[0]
        source_agency = str(
            _value(document, "publisher", "agencyName", "coQuanBanHanh", "issuingAgency") or ""
        ).strip()
        source_issued = _iso_date(
            _value(document, "issueDate", "ngayBanHanh", "issuedDate")
        )
        mismatch = bool(
            expected_issuing_agency
            and source_agency
            and _fold(expected_issuing_agency) != _fold(source_agency)
        ) or bool(
            expected_issued_date
            and source_issued
            and _iso_date(expected_issued_date) != source_issued
        )
        identity_status = IdentityStatus.MISMATCH if mismatch else IdentityStatus.EXACT
        raw_status = _raw_status(document)
        effective_from = _iso_date(
            _value(document, "effFrom", "ngayCoHieuLuc", "effectiveDate")
        )
        effective_to = _iso_date(
            _value(document, "effTo", "ngayHetHieuLuc", "expiryDate")
        )
        normalized_status = normalize_validity_status(
            raw_status,
            effective_from=effective_from,
            effective_to=effective_to,
            as_of=as_of,
        )
        provisions = normalize_provisions(
            _value(document, "affectedProvisions", "invalidatedSections")
        )
        if normalized_status in {
            NormalizedValidityStatus.EXPIRED_PARTIAL,
            NormalizedValidityStatus.SUSPENDED_PARTIAL,
            NormalizedValidityStatus.AMENDED,
        } and not provisions:
            evidence_status = EvidenceStatus.PARTIAL_SCOPE_MISSING
        elif not raw_status and not effective_to and normalized_status is NormalizedValidityStatus.UNKNOWN:
            evidence_status = EvidenceStatus.MALFORMED
        else:
            evidence_status = EvidenceStatus.SUFFICIENT

        official_id = str(_value(document, "id", "documentId") or "").strip()
        detail_url = _official_detail_url(_value(document, "detailUrl", "sourceUrl"))
        if not detail_url:
            detail_url = (
                f"https://vbpl.vn/van-ban/chi-tiet/{_slug(document.get('title'))}--{official_id}"
                if official_id
                else VBPL_VALIDITY_SEARCH_URL
            )
        observation = LegalValidityObservation(
            document_id=document_id,
            law_number=expected,
            issuing_agency=source_agency or None,
            issued_date=source_issued,
            source_url=detail_url,
            source_kind="vbpl",
            raw_status=raw_status,
            normalized_status=(
                normalized_status if not mismatch else NormalizedValidityStatus.UNKNOWN
            ),
            effective_from=effective_from,
            effective_to=effective_to,
            affecting_document_number=normalize_law_number(
                _value(
                    document,
                    "affectingDocumentNumber",
                    "sourceDocumentNumber",
                    "replacedBy",
                )
            ),
            affected_provisions=provisions,
            identity_status=identity_status,
            evidence_status=(EvidenceStatus.CONFLICTING if mismatch else evidence_status),
            observed_at=observed_at,
            source_updated_at=_datetime(
                _value(document, "updatedAt", "lastUpdated", "modifiedAt")
            ),
        )
        return ValidityFetchResult(
            reason_code=(
                "OFFICIAL_DOCUMENT_IDENTITY_MISMATCH"
                if mismatch
                else "EXACT_OFFICIAL_VALIDITY_OBSERVED"
            ),
            observation=observation,
            source_status="observed" if not mismatch else "identity_unverified",
        )

    async def fetch(
        self,
        *,
        instrument: str,
        document_id: str | None,
        as_of: date,
        expected_issuing_agency: str | None = None,
        expected_issued_date: str | None = None,
        expected_title: str | None = None,
    ) -> ValidityFetchResult:
        observed_at = datetime.now(timezone.utc)
        payload = [{"keyword": instrument, "pageNumber": 0, "pageSize": self.page_size}]
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            verify=build_verified_ssl_context(),
        )
        try:
            response = await client.post(
                self.search_url,
                headers={
                    "Accept": "text/x-component",
                    "Content-Type": "text/plain;charset=UTF-8",
                    "Origin": "https://vbpl.vn",
                    "User-Agent": USER_AGENT,
                    "Next-Action": VBPL_VALIDITY_SEARCH_ACTION,
                },
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            response.raise_for_status()
            require_official_component_response(response)
            result = _rsc_json(
                response.content,
                lambda value: isinstance(value, Mapping) and "items" in value,
            )
            return self.parse_items(
                instrument=instrument,
                items=result.get("items"),
                document_id=document_id,
                expected_issuing_agency=expected_issuing_agency,
                expected_issued_date=expected_issued_date,
                expected_title=expected_title,
                observed_at=observed_at,
                as_of=as_of,
            )
        except Exception as exc:  # structured failure, never guessed legal data
            return ValidityFetchResult(
                reason_code=classify_official_source_failure(exc),
                observation=None,
                source_status="failed",
            )
        finally:
            if owns_client:
                await client.aclose()


__all__ = ["VBPLValiditySource", "ValidityFetchResult"]
