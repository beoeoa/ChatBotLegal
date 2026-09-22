"""Deterministic M4 query normalization, classification and temporal guard.

The classifier describes the user's request; it never supplies legal facts.
Temporal ambiguity fails closed before retrieval so current law cannot be
presented as though it governed an unspecified historical date.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Mapping

from api.administrative_query_signals import administrative_domain


M4_QUERY_SCHEMA_VERSION = "legal-query-classification-m4-v1"
M4_INTENTS = frozenset(
    {
        "PROCEDURE",
        "ELIGIBILITY",
        "REQUIRED_DOCUMENTS",
        "AUTHORITY",
        "PROCESS",
        "DEADLINE",
        "FEE",
        "FORM",
        "LEGAL_BASIS",
        "VALIDITY",
        "SPECIFIC_DOCUMENT",
        "HISTORICAL",
        "COMPARISON",
        "COMPLAINT",
        "OUT_OF_SCOPE",
        "UNKNOWN",
    }
)
M4_TEMPORAL_SCOPES = frozenset({"current", "historical", "unknown"})

_WHITESPACE_RE = re.compile(r"\s+")
_EXACT_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)"),
)
_DATE_LIKE_RE = re.compile(
    r"(?<!\d)(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})(?!\d)"
)
_YEAR_RE = re.compile(
    r"\b(?:nam|vao nam|thoi diem nam|quy dinh nam)\s+(19\d{2}|20\d{2})\b"
)
_EVENT_YEAR_PREFIX_RE = re.compile(
    r"\b(?:sinh|duoc cap|cap|mua|xay|ket hon|chuyen den|mat)\s+$"
)
_INSTRUMENT_DATE_YEAR_PREFIX_RE = re.compile(
    r"\bngay\s+\d{1,2}\s+thang\s+\d{1,2}\s+$"
)
_DATE_SCOPED_EFFECTIVITY_RE = re.compile(
    r"\b(?:dang|con|dang con)\s+(?:co\s+)?hieu luc\s+"
    r"(?:tai|vao|den)\s+(?:ngay\s+)?"
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b"
)


def normalize_legal_query(query: str) -> str:
    """Return stable human-readable Unicode without adding search terms."""

    normalized = unicodedata.normalize("NFC", str(query or "")).casefold()
    normalized = "".join(
        char for char in normalized if char in "\n\t" or unicodedata.category(char) != "Cc"
    )
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "")).casefold()
    folded = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    return _WHITESPACE_RE.sub(" ", folded).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


_AMBIGUOUS_PAST_MARKERS = (
    "truoc day",
    "hoi do",
    "thoi diem do",
    "ngay truoc",
    "truoc kia",
)
_PAST_EVENT_TERMS = (
    "dang ky",
    "cu tru",
    "sinh song",
    "ket hon",
    "duoc cap",
    "noi o",
    "cho o",
    "dia chi",
    "so ho tich",
)
_HISTORICAL_RULE_CUES = (
    "quy dinh",
    "phap luat",
    "theo luat",
    "hieu luc",
    "ap dung",
)


def _past_marker_is_event_context(text: str) -> bool:
    """Distinguish an old event/location from a historical-law request."""

    for marker in _AMBIGUOUS_PAST_MARKERS:
        offset = 0
        while True:
            index = text.find(marker, offset)
            if index < 0:
                break
            before = text[:index].strip()
            # ``Trước đây đăng ký khai sinh cần gì?`` is intentionally still
            # ambiguous. The event-context exception requires a current
            # request clause before the past marker.
            if before:
                before_window = before[-80:]
                after_window = text[index + len(marker) : index + len(marker) + 80]
                if not _has_any(before_window, _HISTORICAL_RULE_CUES) and (
                    _has_any(before_window, _PAST_EVENT_TERMS)
                    or _has_any(after_window, _PAST_EVENT_TERMS)
                ):
                    return True
            offset = index + len(marker)
    return False


def _extract_exact_date(text: str) -> date | None:
    for index, pattern in enumerate(_EXACT_DATE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        values = tuple(int(part) for part in match.groups())
        year, month, day = values if index == 0 else (values[2], values[1], values[0])
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _classify_domain(text: str, requested_domain: str | None) -> str:
    requested = _fold(requested_domain or "")
    requested_map = {
        "ho_tich": "ho_tich_chung_thuc",
        "tu_phap_ho_tich": "ho_tich_chung_thuc",
        "ho_tich_chung_thuc": "ho_tich_chung_thuc",
        "dat_dai": "dat_dai_xay_dung",
        "dat_dai_xay_dung": "dat_dai_xay_dung",
        "cu_tru": "cu_tru_an_ninh",
        "cu_tru_an_ninh": "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat": "khieu_nai_to_cao_xu_phat",
        "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
    }
    if requested in requested_map:
        return requested_map[requested]
    # Count and weight topic signals instead of returning the first matching
    # domain.  A supporting fact such as "dữ liệu cư trú" must not override
    # the actual request for a social-pension benefit.
    domains = (
        ("ho_tich_chung_thuc", ("khai sinh", "khai tu", "ket hon", "ho tich", "chung thuc", "tinh trang hon nhan", "gks", "so ht")),
        ("dat_dai_xay_dung", ("dat dai", "quyen su dung dat", "so do", "thua dat", "tach thua", "xay dung", "gpxd", "tang cho")),
        ("cu_tru_an_ninh", ("cu tru", "thuong tru", "tam tru", "luu tru", "tam vang", "can cuoc", "chu ho", "chu so huu")),
        ("khieu_nai_to_cao_xu_phat", (
            "khieu nai", "to cao", "xu phat", "quyet dinh hanh chinh",
            # Common citizen shorthand used in the 50-turn replay.  The
            # surrounding deadline/decision terms still determine intent.
            "thoi hieu kn", "thoi han kn",
        )),
        ("an_sinh_y_te_giao_duc", ("tro cap huu tri xa hoi", "tro cap huu tri xh", "huu tri xa hoi", "huu tri xh", "tro cap", "bao tro xa hoi", "khuyet tat", "ho ngheo", "bao hiem y te")),
    )
    # CT01 is the reviewed official residence declaration identity.  An exact
    # form code is stronger than surrounding conversational topic, so it must
    # route independently even when the citizen asks only "CT01 dùng để làm gì?".
    if re.search(r"\bct\s*0?1\b", text):
        return "cu_tru_an_ninh"
    shared_domain = administrative_domain(text)
    if shared_domain:
        # Preserve the historical M4 public label while every serving caller
        # canonicalizes it at the single legal-domain boundary.
        return "ho_tich" if shared_domain == "ho_tich_chung_thuc" else shared_domain
    scored: list[tuple[int, int, str]] = []
    for position, (domain, phrases) in enumerate(domains):
        matched = [phrase for phrase in phrases if phrase in text]
        if not matched:
            continue
        # A longer, procedure-specific signal is more informative than a
        # generic supporting noun.  Keep declaration order only as a stable
        # final tiebreaker.
        score = sum(len(phrase.split()) ** 2 for phrase in matched)
        scored.append((score, -position, domain))
    if scored:
        selected = max(scored)[2]
        # Keep the historical M4 public label for generic hộ tịch queries;
        # the remediation router canonicalizes it once into
        # ``ho_tich_chung_thuc`` before retrieval/authorization.
        return "ho_tich" if selected == "ho_tich_chung_thuc" else selected
    return "unknown"


def _classify_scope(text: str, requested_scope: str | None) -> str:
    requested = _fold(requested_scope or "")
    if requested in {"commune", "xa", "phuong", "cap xa", "cap phuong"}:
        return "commune"
    if requested in {"district", "huyen", "quan", "cap huyen", "cap quan"}:
        return "district"
    if requested in {"province", "tinh", "thanh pho", "cap tinh"}:
        return "province"
    if _has_any(text, ("ubnd phuong", "ubnd xa", "cap phuong", "cap xa", "phuong", " xa ")):
        return "commune"
    if _has_any(text, ("ubnd quan", "ubnd huyen", "cap quan", "cap huyen")):
        return "district"
    if _has_any(text, ("ubnd thanh pho", "cap thanh pho", "cap tinh")):
        return "province"
    return "unknown"


def _intent_matches(text: str, temporal_scope: str) -> dict[str, bool]:
    out_of_scope = _has_any(
        text,
        (
            "dang kiem o to",
            "dang kiem xe",
            "thue doanh nghiep",
            "chung khoan",
            "ho chieu nuoc ngoai",
            "vu an hinh su",
        ),
    )
    return {
        "OUT_OF_SCOPE": out_of_scope,
        "COMPLAINT": _has_any(text, ("khieu nai", "to cao", "khieu kien", "phan anh quyet dinh")),
        "COMPARISON": _has_any(text, ("so sanh", "khac nhau", "phan biet", "giong va khac")),
        "HISTORICAL": temporal_scope == "historical",
        "VALIDITY": _has_any(text, ("con hieu luc", "het hieu luc", "hieu luc khong", "bi bai bo", "thay the chua")),
        "SPECIFIC_DOCUMENT": bool(
            re.search(r"\b(?:dieu|khoan)\s+\d+[a-z]?\b", text)
            or re.search(r"\b\d{1,4}/\d{4}/[a-z0-9-]+\b", text)
        ),
        "FORM": _has_any(text, ("bieu mau", "mau don", "to khai", "tai mau", "mau nao")),
        "FEE": _has_any(text, ("le phi", "muc phi", "phi bao nhieu", "chi phi", "mien phi")),
        "DEADLINE": _has_any(text, ("thoi han", "bao lau", "may ngay", "khi nao co ket qua", "ngay lam viec")),
        "AUTHORITY": _has_any(
            text,
            (
                "tham quyen",
                "co quan nao",
                "ai giai quyet",
                "noi nop",
                "nop o dau",
                "chuyen den ai",
                "chuyen cho ai",
                "ai tiep nhan",
            ),
        ),
        "REQUIRED_DOCUMENTS": _has_any(text, ("ho so gi", "ho so nao", "giay to gi", "can gi", "can giay to", "can ho so", "can chuan bi", "can mang"))
        or bool(re.search(r"\bho so\b.{0,100}\bgom (?:nhung )?gi\b", text)),
        "ELIGIBILITY": _has_any(
            text,
            ("dieu kien", "du dieu kien", "co duoc", "co the", "truong hop nao duoc"),
        ),
        "PROCESS": _has_any(text, ("cac buoc", "quy trinh", "trinh tu", "giai quyet ra sao", "xu ly the nao")),
        "LEGAL_BASIS": _has_any(text, ("can cu phap ly", "co so phap ly", "quy dinh nao", "theo van ban nao", "dieu luat nao")),
        "PROCEDURE": _has_any(text, ("thu tuc", "lam the nao", "thuc hien the nao", "dang ky the nao")),
    }


_INTENT_PRECEDENCE = (
    "OUT_OF_SCOPE",
    "COMPLAINT",
    "COMPARISON",
    "HISTORICAL",
    "VALIDITY",
    "SPECIFIC_DOCUMENT",
    "FORM",
    "FEE",
    "DEADLINE",
    "AUTHORITY",
    "REQUIRED_DOCUMENTS",
    "ELIGIBILITY",
    "PROCESS",
    "LEGAL_BASIS",
    "PROCEDURE",
)

_ANSWER_TYPES = {
    "PROCEDURE": "instructional",
    "ELIGIBILITY": "eligibility",
    "REQUIRED_DOCUMENTS": "instructional",
    "AUTHORITY": "authority",
    "PROCESS": "instructional",
    "DEADLINE": "factual",
    "FEE": "factual",
    "FORM": "form",
    "LEGAL_BASIS": "legal_basis",
    "VALIDITY": "validity",
    "SPECIFIC_DOCUMENT": "document",
    "HISTORICAL": "historical",
    "COMPARISON": "comparison",
    "COMPLAINT": "complaint",
    "OUT_OF_SCOPE": "refusal",
    "UNKNOWN": "clarification",
}


def classify_legal_query(
    query: str,
    *,
    requested_domain: str | None = None,
    requested_scope: str | None = None,
    as_of: date | None = None,
    as_of_explicit: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the M4 structure and an enforceable temporal retrieval decision."""

    today = today or date.today()
    normalized = normalize_legal_query(query)
    text = _fold(normalized)
    exact_date = _extract_exact_date(text)
    invalid_date = exact_date is None and bool(_DATE_LIKE_RE.search(text))
    year_match = _YEAR_RE.search(text)
    year_is_event_fact = bool(
        year_match
        and _EVENT_YEAR_PREFIX_RE.search(text[: year_match.start()])
    )
    year_is_instrument_metadata = bool(
        year_match
        and _INSTRUMENT_DATE_YEAR_PREFIX_RE.search(text[: year_match.start()])
    )
    date_scoped_effectivity = bool(_DATE_SCOPED_EFFECTIVITY_RE.search(text))
    absolute_current_marker = _has_any(
        text, ("hien nay", "hien hanh", "bay gio", "hom nay")
    )
    generic_effectivity_marker = _has_any(
        text, ("dang co hieu luc", "dang con hieu luc")
    )
    current_marker = absolute_current_marker or (
        generic_effectivity_marker and not date_scoped_effectivity
    )
    insufficient_facts_marker = (
        "he thong co the ket luan ngay khong" in text
        and "can toi bo sung" in text
    )
    ambiguous_past = _has_any(text, _AMBIGUOUS_PAST_MARKERS)
    past_event_context = ambiguous_past and _past_marker_is_event_context(text)

    temporal_error: str | None = None
    temporal_precision = "day"
    reference: date | None = None
    temporal_reason = "default_current"

    if invalid_date:
        temporal_scope = "unknown"
        temporal_precision = "unknown"
        temporal_reason = "query_invalid_date"
        temporal_error = "TEMPORAL_DATE_INVALID"
    elif exact_date is not None:
        reference = exact_date
        temporal_reason = "query_exact_date"
        if exact_date > today:
            temporal_scope = "unknown"
            temporal_error = "TEMPORAL_DATE_IN_FUTURE"
        elif exact_date < today:
            temporal_scope = "historical"
        else:
            temporal_scope = "current"
    elif year_match:
        year = int(year_match.group(1))
        temporal_precision = "year"
        temporal_reason = (
            "event_year_not_legal_as_of"
            if year_is_event_fact
            else (
                "instrument_date_not_legal_as_of"
                if year_is_instrument_metadata
                else "query_year_only"
            )
        )
        if year_is_event_fact or year_is_instrument_metadata:
            # A birth/registration/property event is a fact used to answer a
            # current-law question. Likewise, the promulgation date embedded
            # in a document title identifies that instrument. Neither is a
            # request to apply historical law unless the user says so.
            temporal_scope = "current"
            reference = today
        elif year < today.year:
            temporal_scope = "historical"
            if as_of_explicit and as_of is not None and as_of.year == year:
                reference = as_of
                temporal_precision = "day"
                temporal_reason = "request_as_of_resolves_query_year"
            elif as_of_explicit and as_of is not None:
                temporal_error = "TEMPORAL_AS_OF_CONFLICT"
            else:
                temporal_error = "HISTORICAL_AS_OF_REQUIRED"
        elif year > today.year:
            temporal_scope = "unknown"
            temporal_error = "TEMPORAL_DATE_IN_FUTURE"
        else:
            temporal_scope = "current"
            reference = today
    elif past_event_context:
        reference = today
        temporal_scope = "current"
        temporal_reason = "past_event_context_not_legal_as_of"
    elif ambiguous_past:
        if as_of_explicit and as_of is not None and as_of <= today:
            reference = as_of
            temporal_scope = "historical" if as_of < today else "current"
            temporal_reason = "request_as_of_resolves_ambiguous_past"
        else:
            temporal_scope = "unknown"
            temporal_precision = "unknown"
            temporal_reason = "ambiguous_past_language"
            temporal_error = "TEMPORAL_SCOPE_CLARIFICATION_REQUIRED"
    elif as_of_explicit and as_of is not None:
        reference = as_of
        temporal_reason = "request_as_of"
        if as_of > today:
            temporal_scope = "unknown"
            temporal_error = "TEMPORAL_DATE_IN_FUTURE"
        else:
            temporal_scope = "historical" if as_of < today else "current"
    else:
        reference = today
        temporal_scope = "current"

    if exact_date is not None and as_of_explicit and as_of != exact_date:
        temporal_error = "TEMPORAL_AS_OF_CONFLICT"
    if current_marker and reference is not None and reference < today:
        temporal_error = "TEMPORAL_AS_OF_CONFLICT"

    matches = _intent_matches(text, temporal_scope)
    intent = (
        "UNKNOWN"
        if insufficient_facts_marker
        else next((name for name in _INTENT_PRECEDENCE if matches[name]), "UNKNOWN")
    )
    secondary = [
        name for name in _INTENT_PRECEDENCE if matches[name] and name != intent
    ]
    if insufficient_facts_marker:
        temporal_error = "QUERY_FACTS_INSUFFICIENT"
    retrieval_allowed = temporal_error is None
    retrieval_as_of = reference if retrieval_allowed else None
    domain = _classify_domain(text, requested_domain)
    scope = _classify_scope(f" {text} ", requested_scope)

    return {
        "schema_version": M4_QUERY_SCHEMA_VERSION,
        "raw_query": str(query or ""),
        "normalized_query": normalized,
        "domain": domain,
        "intent": intent,
        "secondary_intents": secondary,
        "scope": scope,
        "temporal_scope": temporal_scope,
        "temporal_reference": reference.isoformat() if reference else None,
        "temporal_precision": temporal_precision,
        "answer_type": _ANSWER_TYPES[intent],
        "retrieval_allowed": retrieval_allowed,
        "retrieval_as_of": retrieval_as_of.isoformat() if retrieval_as_of else None,
        "temporal_error_code": temporal_error,
        "classification_method": "deterministic_rules_v1",
        "confidence": "rule_based",
        "signals": {
            "current_marker": current_marker,
            "date_scoped_effectivity": date_scoped_effectivity,
            "ambiguous_past": ambiguous_past,
            "past_event_context": past_event_context,
            "insufficient_facts_marker": insufficient_facts_marker,
            "explicit_request_as_of": as_of_explicit,
            "temporal_reason": temporal_reason,
        },
    }


def validate_m4_query_classification(value: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "schema_version",
        "raw_query",
        "normalized_query",
        "domain",
        "intent",
        "secondary_intents",
        "scope",
        "temporal_scope",
        "temporal_reference",
        "temporal_precision",
        "answer_type",
        "retrieval_allowed",
        "retrieval_as_of",
        "temporal_error_code",
    )
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError(f"m4_query_fields_missing:{','.join(missing)}")
    if value.get("schema_version") != M4_QUERY_SCHEMA_VERSION:
        raise ValueError("m4_query_schema_invalid")
    if value.get("intent") not in M4_INTENTS:
        raise ValueError("m4_query_intent_invalid")
    if value.get("temporal_scope") not in M4_TEMPORAL_SCOPES:
        raise ValueError("m4_query_temporal_scope_invalid")
    secondary = value.get("secondary_intents")
    if not isinstance(secondary, list) or any(item not in M4_INTENTS for item in secondary):
        raise ValueError("m4_query_secondary_intents_invalid")
    if not isinstance(value.get("retrieval_allowed"), bool):
        raise ValueError("m4_query_retrieval_allowed_invalid")
    if not value.get("retrieval_allowed") and value.get("retrieval_as_of") is not None:
        raise ValueError("m4_query_blocked_as_of_forbidden")
    return {
        "status": "pass",
        "schema_version": M4_QUERY_SCHEMA_VERSION,
        "intent_count": len(M4_INTENTS),
    }


__all__ = [
    "M4_INTENTS",
    "M4_QUERY_SCHEMA_VERSION",
    "M4_TEMPORAL_SCOPES",
    "classify_legal_query",
    "normalize_legal_query",
    "validate_m4_query_classification",
]
