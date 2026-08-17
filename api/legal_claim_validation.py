"""Fail-closed validation for atomic model claims and their evidence quotes."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping, Sequence


_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?!\w)")
_ARTICLE_RE = re.compile(r"(?i)\bđiều\s+([0-9]+[a-z]?)")
_CLAUSE_RE = re.compile(r"(?i)\bkhoản\s+([0-9]+[a-z]?)")
_POINT_RE = re.compile(r"(?i)\bđiểm\s+([a-zđ]|[0-9]+)")
_LAW_NUMBER_RE = re.compile(r"(?i)\b([0-9]+/[0-9]{4}/[A-ZĐ-]+)\b")
_VALUE_CLAIM_TYPES = {"authority", "deadline", "fee", "condition", "procedure", "form"}
_NON_NUMERIC_VALUE_MARKERS = {
    "deadline": (
        "thoi han", "ngay lam viec", "trong ngay", "gio", "ke tu ngay",
    ),
    "fee": (
        "le phi", "muc thu", "mien phi", "khong thu",
    ),
}
_CLAIM_TYPE_QUOTE_MARKERS = {
    "authority": (
        "noi nop", "nop tai", "co quan tiep nhan", "uy ban nhan dan",
        "van phong dang ky", "bo phan mot cua", "co quan dang ky",
        "cong an cap xa", "cong an cap phuong", "chu tich uy ban",
        "chu tich ubnd", "nop ho so den", "gui ho so den", "tham quyen",
        "co quan", "ubnd",
    ),
    "documents": (
        "thanh phan ho so", "ho so gom", "ho so bao gom",
        "giay to ve", "giay to chung minh", "tai lieu chung minh",
        "ban sao", "ban chinh", "don dang ky", "nop kem",
        "to khai", "xuat trinh", "kem theo", "ho so", "giay to",
        "don", "van ban", "giay chung nhan", "chung tu", "so dia chinh",
    ),
    "procedure": (
        "trinh tu", "thu tuc", "thuc hien", "tiep nhan", "giai quyet",
        "dang ky", "cap", "nop", "luu tru", "xu ly", "kiem tra", "xac minh",
        "chuyen", "tra ket qua", "quy trinh", "cac buoc",
    ),
    "deadline": (
        "thoi han", "ngay lam viec", "trong ngay", "ke tu ngay", "thoi gian", "gio",
    ),
    "fee": (
        "le phi", "muc thu", "mien phi", "khong thu", "nghia vu tai chinh", "phi",
    ),
    "form": (
        "bieu mau", "mau don", "to khai", "mau so", "mau", "don",
    ),
    "condition": (
        "dieu kien", "truong hop", "phai", "duoc",
        "toi thieu", "khong vuot qua", "tro len", "can cu", "quy dinh",
    ),
}
_CLAIM_FRAME_TOKENS = {
    "la",
    "duoc",
    "theo",
    "quy",
    "dinh",
    "thoi",
    "han",
    "giai",
    "quyet",
    "ho",
    "so",
    "thu",
    "tuc",
    "trong",
    "tai",
    "ve",
    "doi",
    "voi",
    "nay",
    "mot",
    "cac",
    "co",
    "quan",
}
_DIRECT_ISSUE_STOPWORDS = _CLAIM_FRAME_TOKENS | {
    "cap", "giay", "chung", "nhan", "quyen", "su", "dung", "dat",
    "lan", "dau", "nguoi", "thoi", "han", "ho", "so", "thu", "tuc",
    "le", "phi", "noi", "nop", "co", "quan", "dieu", "kien",
}
_INTERNAL_CITATION_MARKER_RE = re.compile(
    r"(?i)(?:\[\s*(?:legal|source|citation|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id)\s*:?[^\]]*\]"
    r"|\b(?:legal|chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id)\s*:\s*\S+)"
)


@dataclass(frozen=True)
class RejectedClaim:
    claim: Mapping[str, Any]
    reason: str


@dataclass(frozen=True)
class ClaimValidationResult:
    accepted: tuple[dict[str, Any], ...]
    rejected: tuple[RejectedClaim, ...]


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _numbers(value: Any) -> set[str]:
    return {number.replace(",", ".").lstrip("0") or "0" for number in _NUMBER_RE.findall(str(value or ""))}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", _compact(value).casefold())
    return "".join(
        ch for ch in text if unicodedata.category(ch) != "Mn"
    ).replace("đ", "d")


def _material_numbers(value: Any) -> set[str]:
    """Ignore numbers that belong only to legal references."""
    text = str(value or "")
    for pattern in (_ARTICLE_RE, _CLAUSE_RE, _POINT_RE, _LAW_NUMBER_RE):
        text = pattern.sub("", text)
    return _numbers(text)


def _reference_matches(
    *, value: str, metadata_value: Any, quote: str, label: str
) -> bool:
    expected = _compact(metadata_value)
    if expected:
        return value.casefold() == expected.casefold()
    return f"{label} {value}".casefold() in quote.casefold()


def _is_supported_embedded_reference(
    *, pattern: re.Pattern[str], value: str, quote: str
) -> bool:
    """Recognise a source's own cross-reference without relabelling it.

    A provision quoted from clause 1 may legitimately contain text such as
    ``trừ ... tại khoản 2 Điều này`` or ``theo Điều 55 Nghị định này``.  That
    number is part of the legal proposition, not the identity of the evidence
    row.  Only references copied verbatim after an explicit cross-reference
    cue are allowed through this exception; a model-authored leading citation
    still has to match the evidence metadata.
    """

    for match in pattern.finditer(quote):
        if match.group(1).casefold() != value.casefold():
            continue
        prefix = _fold(quote[max(0, match.start() - 56) : match.start()])
        if re.search(
            r"(?:\btai|\btheo)(?:\s+[a-z0-9,]+){0,8}\s*$",
            prefix,
        ):
            return True
    return False


def _legal_references_supported(
    claim_text: str,
    quote: str,
    evidence: Mapping[str, Any],
) -> bool:
    checks = (
        (_ARTICLE_RE, "article_number", "điều"),
        (_CLAUSE_RE, "clause_number", "khoản"),
        (_POINT_RE, "point_number", "điểm"),
    )
    for pattern, field, label in checks:
        for match in pattern.finditer(claim_text):
            value = match.group(1)
            if not _reference_matches(
                value=value,
                metadata_value=evidence.get(field),
                quote=quote,
                label=label,
            ) and not _is_supported_embedded_reference(
                pattern=pattern,
                value=value,
                quote=quote,
            ):
                return False
    law_number = _compact(evidence.get("law_number"))
    for value in _LAW_NUMBER_RE.findall(claim_text):
        if law_number:
            if value.casefold() != law_number.casefold():
                return False
        elif value.casefold() not in quote.casefold():
            return False
    return True


def _non_numeric_value_supported(
    claim_type: str,
    claim_text: str,
    quote: str,
) -> bool:
    if _material_numbers(claim_text):
        return True
    markers = _NON_NUMERIC_VALUE_MARKERS.get(claim_type)
    if not markers:
        return True
    folded_quote = _fold(quote)
    return any(marker in folded_quote for marker in markers)


def _material_claim_tokens(value: Any) -> set[str]:
    """Remove citation/framing words while preserving the legal proposition."""

    text = str(value or "")
    for pattern in (_ARTICLE_RE, _CLAUSE_RE, _POINT_RE, _LAW_NUMBER_RE):
        text = pattern.sub("", text)
    return {
        token
        for token in re.findall(r"[a-z0-9đ]+", _fold(text))
        if len(token) >= 2 and token not in _CLAIM_FRAME_TOKENS
    }


def _claim_text_supported_by_quote(claim_text: str, quote: str) -> bool:
    """Require material proposition tokens to be grounded in the bound quote."""

    claim_tokens = _material_claim_tokens(claim_text)
    quote_tokens = _material_claim_tokens(quote)
    if not claim_tokens:
        return True
    overlap = len(claim_tokens & quote_tokens)
    ratio = overlap / len(claim_tokens)
    return ratio >= 0.35 or claim_tokens.issubset(quote_tokens)


def _claim_type_supported_by_quote(claim_type: str, quote: str) -> bool:
    markers = _CLAIM_TYPE_QUOTE_MARKERS.get(claim_type)
    if not markers:
        return True
    folded_quote = _fold(quote)
    if claim_type == "authority" and re.search(
        r"\b(?:giao )?(?:uy ban nhan(?: dan)?|ubnd)\b.{0,160}"
        r"\b(?:xem xet|quyet dinh)\b",
        folded_quote,
    ):
        # Mirror the eligibility gate for an official-page extraction artifact
        # that drops "dân" from "Ủy ban nhân dân". The direct assignment and
        # decision verb remain mandatory, so a bare agency name is insufficient.
        return True
    return any(marker in folded_quote for marker in markers)


def _quote_directly_matches_issue(issue_context: str, quote: str) -> bool:
    issue_tokens = {
        token
        for token in re.findall(r"[a-z0-9đ]+", _fold(issue_context))
        if len(token) >= 3 and token not in _DIRECT_ISSUE_STOPWORDS
    }
    if not issue_tokens:
        return True
    quote_tokens = set(re.findall(r"[a-z0-9đ]+", _fold(quote)))
    return bool(issue_tokens & quote_tokens)


def contains_internal_citation_marker(value: Any) -> bool:
    """Detect implementation-only citation identifiers before public rendering."""

    return bool(_INTERNAL_CITATION_MARKER_RE.search(str(value or "")))


def strip_internal_citation_markers(value: Any) -> str:
    """Remove implementation-only markers from bounded public excerpts."""

    return _INTERNAL_CITATION_MARKER_RE.sub("", str(value or ""))


def _normalize_procedure_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _fold(value))


def _form_mapping_supported(
    *,
    claim: Mapping[str, Any],
    evidence: Mapping[str, Any],
    expected_form: Mapping[str, Any] | None,
) -> str | None:
    if str(claim.get("claim_type") or "") != "form" or not expected_form:
        return None
    expected_procedure = _normalize_procedure_id(expected_form.get("procedure_id"))
    actual_procedure = _normalize_procedure_id(evidence.get("procedure_id"))
    if expected_procedure and actual_procedure != expected_procedure:
        return "form_procedure_mismatch"
    expected_code = _fold(expected_form.get("form_code"))
    actual_code = _fold(evidence.get("form_code"))
    if expected_code and actual_code and expected_code != actual_code:
        return "form_code_mismatch"
    return None


def validate_structured_claims(
    *,
    request_id: str,
    claims: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    expected_form: Mapping[str, Any] | None = None,
    issue_context: str | None = None,
    issue_intent: str = "unknown",
    relevance_topics: Sequence[str] = (),
) -> ClaimValidationResult:
    """Accept only claims proven by an exact quote in the bound evidence."""

    accepted: list[dict[str, Any]] = []
    rejected: list[RejectedClaim] = []
    for raw_claim in claims:
        claim = dict(raw_claim)
        evidence_id = str(claim.get("evidence_id") or "")
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            rejected.append(RejectedClaim(claim, "evidence_not_in_request"))
            continue
        if (
            str(evidence.get("request_id") or "") != request_id
            or str(evidence.get("issue_id") or "") != str(claim.get("issue_id") or "")
        ):
            rejected.append(RejectedClaim(claim, "request_or_issue_mismatch"))
            continue
        quote = _compact(claim.get("support_quote"))
        content = _compact(
            evidence.get("evidence_capsule")
            or evidence.get("clean_content")
            or evidence.get("content")
        )
        if not quote or quote.casefold() not in content.casefold():
            rejected.append(RejectedClaim(claim, "support_quote_not_in_evidence"))
            continue
        claim_text = _compact(claim.get("claim_text"))
        claim_type = str(claim.get("claim_type") or "unknown")
        supported_facets = evidence.get("supported_facets")
        if isinstance(supported_facets, (list, tuple, set)) and supported_facets:
            allowed = {str(value) for value in supported_facets}
            if claim_type not in allowed:
                rejected.append(
                    RejectedClaim(
                        claim,
                        "claim_type_not_supported_by_evidence_facet",
                    )
                )
                continue
        if contains_internal_citation_marker(claim_text) or contains_internal_citation_marker(quote):
            rejected.append(RejectedClaim(claim, "internal_citation_marker"))
            continue
        if claim_type in _VALUE_CLAIM_TYPES and not claim_text:
            rejected.append(RejectedClaim(claim, "empty_material_claim"))
            continue
        if not _legal_references_supported(claim_text, quote, evidence):
            rejected.append(RejectedClaim(claim, "legal_reference_not_supported"))
            continue
        if not _material_numbers(claim_text).issubset(_material_numbers(quote)):
            rejected.append(RejectedClaim(claim, "claim_value_not_supported"))
            continue
        if not _non_numeric_value_supported(claim_type, claim_text, quote):
            rejected.append(RejectedClaim(claim, "claim_value_not_supported"))
            continue
        if issue_context:
            # Validate the exact proposition quote, not the wider parent body.
            # This prevents an unrelated sibling in one long Article from
            # making an organization/house/replacement rule look applicable to
            # an individual first-registration issue.
            from api.legal_evidence_relevance import rank_issue_evidence

            ranked_quotes, quote_decisions = rank_issue_evidence(
                issue_context,
                [
                    {
                        "content": quote,
                        "score": 1.0,
                        "authority_rank": evidence.get("authority_rank"),
                        "authority_confidence": evidence.get("authority_confidence"),
                    }
                ],
                issue_intent=issue_intent,
                relevance_topics=relevance_topics,
            )
            if not ranked_quotes:
                reason = str(
                    (quote_decisions[0] if quote_decisions else {}).get("reason")
                    or "issue_mismatch"
                )
                rejected.append(
                    RejectedClaim(claim, f"claim_issue_mismatch:{reason}")
                )
                continue
        form_reason = _form_mapping_supported(
            claim=claim,
            evidence=evidence,
            expected_form=expected_form,
        )
        if form_reason:
            rejected.append(RejectedClaim(claim, form_reason))
            continue
        explicit_facets = evidence.get("supported_facets")
        facet_is_deterministically_bound = (
            isinstance(explicit_facets, (list, tuple, set))
            and claim_type in {str(value) for value in explicit_facets}
        )
        if (
            not facet_is_deterministically_bound
            and not _claim_type_supported_by_quote(claim_type, quote)
        ):
            rejected.append(RejectedClaim(claim, "claim_type_not_supported_by_quote"))
            continue
        if not _claim_text_supported_by_quote(claim_text, quote):
            rejected.append(
                RejectedClaim(claim, "claim_text_not_supported_by_quote")
            )
            continue
        accepted.append({**claim, "evidence": dict(evidence)})
    return ClaimValidationResult(tuple(accepted), tuple(rejected))
