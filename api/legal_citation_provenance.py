"""Deterministic citation proof verification for Feature 016.

The verifier derives public proof from source bytes/text and stored provenance;
it never trusts page, offsets or quotes declared by a language model.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


VerificationLevel = Literal[
    "physical_span", "content_quote", "metadata_only", "rejected"
]
VerificationStatus = Literal["verified", "unverified", "rejected"]
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PUBLIC_FIELDS = (
    "document_title",
    "law_number",
    "article_number",
    "clause_number",
    "point_number",
    "effective_status",
    "legal_as_of",
    "source_url",
    "internal_url",
    "label",
    "authority_level",
    "authority_label",
)


def _text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _viewer_url(
    base: str,
    *,
    page_number: int | None,
    char_start: int | None,
    char_end: int | None,
) -> str:
    if not base:
        return ""
    split = urlsplit(base)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    if page_number is not None:
        query["page"] = str(page_number)
    if char_start is not None and char_end is not None:
        query["char_start"] = str(char_start)
        query["char_end"] = str(char_end)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


@dataclass(frozen=True)
class CitationVerification:
    status: VerificationStatus
    verification_level: VerificationLevel
    reason_code: str | None = None
    support_quote: str | None = None
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    source_asset_sha256: str | None = None
    text_hash: str | None = None
    extractor: str | None = None
    extractor_version: str | None = None
    viewer_url: str = ""

    def public_proof(self) -> dict[str, Any] | None:
        if self.verification_level not in {"physical_span", "content_quote"}:
            return None
        return {
            key: value
            for key, value in asdict(self).items()
            if key
            in {
                "support_quote",
                "page_number",
                "char_start",
                "char_end",
                "bounding_box",
                "source_asset_sha256",
                "text_hash",
                "extractor",
                "extractor_version",
            }
            and value is not None
        }


def _rejected(reason: str, *, quote: str | None = None) -> CitationVerification:
    return CitationVerification(
        status="rejected",
        verification_level="rejected",
        reason_code=reason,
        support_quote=quote,
    )


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        return None
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    x0, y0, x1, y1 = result
    return result if x1 > x0 and y1 > y0 else None


def verify_citation(
    *,
    source_text: str | None,
    support_quote: str | None,
    provenance: Mapping[str, Any] | None,
    source_pages: Mapping[int | str, str] | None = None,
    source_asset_bytes: bytes | None = None,
    observed_source_asset_sha256: str | None = None,
    internal_url: str = "",
) -> CitationVerification:
    quote = _text(support_quote)
    source = unicodedata.normalize("NFC", str(source_text or ""))
    proof = dict(provenance or {})
    if not quote or not source:
        return CitationVerification(
            status="unverified",
            verification_level="metadata_only",
            reason_code="quote_or_source_missing",
            viewer_url=internal_url,
        )
    if _text(proof.get("verification_status")).casefold() in {"rejected", "invalid"}:
        return _rejected("provenance_not_verified", quote=quote)

    asset_hash = _text(proof.get("source_asset_sha256")).casefold()
    if asset_hash:
        if not _SHA256_RE.fullmatch(asset_hash):
            return _rejected("source_asset_hash_invalid", quote=quote)
        observed_asset_hash = _text(observed_source_asset_sha256).casefold()
        if source_asset_bytes is not None:
            observed_asset_hash = hashlib.sha256(source_asset_bytes).hexdigest()
        if observed_asset_hash and not _SHA256_RE.fullmatch(observed_asset_hash):
            return _rejected("observed_source_asset_hash_invalid", quote=quote)
        if observed_asset_hash and asset_hash != observed_asset_hash:
            return _rejected("source_asset_hash_mismatch", quote=quote)

    text_hash = _text(proof.get("text_hash")).casefold()
    if text_hash and not _SHA256_RE.fullmatch(text_hash):
        return _rejected("quote_text_hash_invalid", quote=quote)

    raw_start, raw_end, raw_page = (
        proof.get("char_start"),
        proof.get("char_end"),
        proof.get("page_number"),
    )
    coordinate_values = (raw_start, raw_end, raw_page)
    if any(value is not None for value in coordinate_values):
        if not all(value is not None for value in coordinate_values):
            return _rejected("physical_span_incomplete", quote=quote)
        try:
            start, end, page = int(raw_start), int(raw_end), int(raw_page)
        except (TypeError, ValueError):
            return _rejected("physical_span_invalid", quote=quote)
        if page < 1:
            return _rejected("physical_span_invalid", quote=quote)
        if not asset_hash:
            return _rejected("physical_span_hash_missing", quote=quote)
        observed_asset_hash = _text(observed_source_asset_sha256).casefold()
        if source_asset_bytes is not None:
            observed_asset_hash = hashlib.sha256(source_asset_bytes).hexdigest()
        if not observed_asset_hash:
            return _rejected("source_asset_not_observed", quote=quote)
        pages = dict(source_pages or {})
        page_source = pages.get(page)
        if page_source is None:
            page_source = pages.get(str(page))
        if page_source is None:
            return _rejected("source_page_not_observed", quote=quote)
        page_source = unicodedata.normalize("NFC", str(page_source))
        if start < 0 or end <= start or end > len(page_source):
            return _rejected("physical_span_out_of_bounds", quote=quote)
        if page_source[start:end] != quote:
            return _rejected("physical_span_quote_mismatch", quote=quote)
        if text_hash != _sha256(quote):
            return _rejected("quote_text_hash_mismatch", quote=quote)
        bounding_box = _bbox(proof.get("bounding_box"))
        if proof.get("bounding_box") is not None and bounding_box is None:
            return _rejected("bounding_box_invalid", quote=quote)
        if not text_hash:
            return _rejected("physical_span_hash_missing", quote=quote)
        return CitationVerification(
            status="verified",
            verification_level="physical_span",
            support_quote=quote,
            page_number=page,
            char_start=start,
            char_end=end,
            bounding_box=bounding_box,
            source_asset_sha256=asset_hash,
            text_hash=text_hash,
            extractor=_text(proof.get("extractor")) or None,
            extractor_version=_text(proof.get("extractor_version")) or None,
            viewer_url=_viewer_url(
                internal_url,
                page_number=page,
                char_start=start,
                char_end=end,
            ),
        )

    occurrences = [match.start() for match in re.finditer(re.escape(quote), source)]
    if not occurrences:
        return _rejected("content_quote_not_found", quote=quote)
    if len(occurrences) != 1:
        return _rejected("content_quote_ambiguous", quote=quote)
    if text_hash and text_hash != _sha256(quote):
        return _rejected("quote_text_hash_mismatch", quote=quote)
    start = occurrences[0]
    end = start + len(quote)
    return CitationVerification(
        status="verified",
        verification_level="content_quote",
        support_quote=quote,
        char_start=start,
        char_end=end,
        source_asset_sha256=asset_hash or None,
        text_hash=text_hash or _sha256(quote),
        extractor=_text(proof.get("extractor")) or None,
        extractor_version=_text(proof.get("extractor_version")) or None,
        viewer_url=internal_url,
    )


def citation_can_support_claim(value: CitationVerification | Mapping[str, Any]) -> bool:
    if isinstance(value, CitationVerification):
        return value.status == "verified" and value.verification_level in {
            "physical_span",
            "content_quote",
        }
    return (
        _text(value.get("verification_status") or value.get("status")).casefold()
        == "verified"
        and _text(value.get("verification_level")).casefold()
        in {"physical_span", "content_quote"}
    )


def physical_citations_enabled() -> bool:
    return str(
        os.getenv("LEGAL_PHYSICAL_CITATIONS_ENABLED") or "false"
    ).strip().casefold() in {"1", "true", "yes", "on"}


def enrich_public_citation(source: Mapping[str, Any]) -> dict[str, Any]:
    verification = verify_citation(
        source_text=source.get("source_text"),
        support_quote=source.get("support_quote"),
        provenance=(
            source.get("provenance")
            if isinstance(source.get("provenance"), Mapping)
            else None
        ),
        source_pages=(
            source.get("source_pages")
            if isinstance(source.get("source_pages"), Mapping)
            else None
        ),
        source_asset_bytes=(
            bytes(source.get("source_asset_bytes"))
            if isinstance(source.get("source_asset_bytes"), (bytes, bytearray))
            else None
        ),
        observed_source_asset_sha256=_text(
            source.get("observed_source_asset_sha256")
        ) or None,
        internal_url=_text(source.get("internal_url")),
    )
    if (
        verification.verification_level == "physical_span"
        and not physical_citations_enabled()
    ):
        # Safe rollback before sidecar activation: keep the independently
        # verified quote but do not expose page/offset proof as active.
        verification = verify_citation(
            source_text=source.get("source_text"),
            support_quote=source.get("support_quote"),
            provenance={"verification_status": "verified"},
            internal_url=_text(source.get("internal_url")),
        )
    public = {
        field: source.get(field)
        for field in _PUBLIC_FIELDS
        if source.get(field) not in (None, "")
    }
    public["verification_level"] = verification.verification_level
    public["verification_status"] = verification.status
    if verification.reason_code:
        public["verification_reason"] = verification.reason_code
    if verification.viewer_url:
        public["viewer_url"] = verification.viewer_url
    proof = verification.public_proof()
    if proof:
        public["proof"] = proof
    return public
