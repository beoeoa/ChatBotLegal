"""Deterministic legal-validity normalization and serving decisions.

This module contains no I/O and never infers legal effect from model output.
Only exact official evidence is normalized; unknown or partial evidence remains
explicit so callers can fail closed at the retrieval boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

LEGAL_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def vietnam_legal_date(value: datetime | None = None) -> date:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(LEGAL_TIMEZONE).date()


class NormalizedValidityStatus(str, Enum):
    NOT_YET_EFFECTIVE = "not_yet_effective"
    ACTIVE = "active"
    EXPIRED = "expired"
    EXPIRED_PARTIAL = "expired_partial"
    SUSPENDED = "suspended"
    SUSPENDED_PARTIAL = "suspended_partial"
    AMENDED = "amended"
    REPLACED = "replaced"
    REPEALED = "repealed"
    UNKNOWN = "unknown"


class IdentityStatus(str, Enum):
    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    MISMATCH = "mismatch"
    MISSING = "missing"


class EvidenceStatus(str, Enum):
    SUFFICIENT = "sufficient"
    PARTIAL_SCOPE_MISSING = "partial_scope_missing"
    CONFLICTING = "conflicting"
    MALFORMED = "malformed"


_LAW_NUMBER = re.compile(
    r"\d{1,4}(?:\.\d+)?/\d{4}/[A-ZĐ0-9]{1,12}(?:-[A-ZĐ0-9]{1,12})*"
)
_SAFE_PROVISION_TOKEN = re.compile(r"[0-9]+[a-z]?|[a-z]", re.IGNORECASE)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def normalize_law_number(value: Any) -> str | None:
    """Normalize one exact Vietnamese instrument number, never a title."""

    text = unicodedata.normalize("NFKC", str(value or "")).upper().strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    return text if _LAW_NUMBER.fullmatch(text) else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_validity_status(
    raw_status: Any,
    *,
    effective_from: Any,
    effective_to: Any,
    as_of: date,
) -> NormalizedValidityStatus:
    """Normalize an explicit official label, with dates as deterministic gates."""

    if isinstance(raw_status, Mapping):
        raw_status = raw_status.get("name") or raw_status.get("code")
    folded = _fold(raw_status)
    start = _parse_date(effective_from)
    end = _parse_date(effective_to)

    if start and start > as_of:
        return NormalizedValidityStatus.NOT_YET_EFFECTIVE
    if "mot phan" in folded:
        if any(marker in folded for marker in ("ngung hieu luc", "dinh chi", "tam ngung")):
            return NormalizedValidityStatus.SUSPENDED_PARTIAL
        if any(marker in folded for marker in ("het hieu luc", "bai bo", "huy bo", "thay the")):
            return NormalizedValidityStatus.EXPIRED_PARTIAL
        if any(marker in folded for marker in ("sua doi", "bo sung")):
            return NormalizedValidityStatus.AMENDED
        return NormalizedValidityStatus.UNKNOWN
    if end and end <= as_of:
        return NormalizedValidityStatus.EXPIRED
    if any(marker in folded for marker in ("cho hieu luc", "chua co hieu luc")):
        return NormalizedValidityStatus.NOT_YET_EFFECTIVE
    if any(marker in folded for marker in ("ngung hieu luc", "dinh chi", "tam ngung")):
        return NormalizedValidityStatus.SUSPENDED
    if "thay the" in folded:
        return NormalizedValidityStatus.REPLACED
    if any(marker in folded for marker in ("bai bo", "huy bo")):
        return NormalizedValidityStatus.REPEALED
    if "het hieu luc" in folded:
        return NormalizedValidityStatus.EXPIRED
    if any(marker in folded for marker in ("sua doi", "bo sung")):
        return NormalizedValidityStatus.AMENDED
    if folded in {"con hieu luc", "dang co hieu luc", "in force", "active"}:
        return NormalizedValidityStatus.ACTIVE
    return NormalizedValidityStatus.UNKNOWN


def _provision_token(value: Any, labels: tuple[str, ...]) -> str | None:
    text = _fold(value)
    if not text:
        return None
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.fullmatch(rf"(?:(?:{label_pattern})\s+)?([0-9]+[a-z]?|[a-z])", text)
    if not match or not _SAFE_PROVISION_TOKEN.fullmatch(match.group(1)):
        return None
    return match.group(1).casefold()


@dataclass(frozen=True, order=True)
class ProvisionReference:
    article: str
    clause: str | None = None
    point: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"article": self.article, "clause": self.clause, "point": self.point}

    def matches(
        self,
        *,
        article_number: Any,
        clause_number: Any = None,
        point_number: Any = None,
    ) -> bool:
        article = _provision_token(article_number, ("dieu", "article"))
        clause = _provision_token(clause_number, ("khoan", "clause"))
        point = _provision_token(point_number, ("diem", "point"))
        if article != self.article:
            return False
        # An article-level hit may contain the narrower affected clause/point;
        # missing hit granularity therefore fails closed.
        if self.clause and clause and clause != self.clause:
            return False
        if self.point and point and point != self.point:
            return False
        return True


def normalize_provisions(values: Iterable[Any] | None) -> tuple[ProvisionReference, ...]:
    normalized: set[ProvisionReference] = set()
    for value in values or ():
        if not isinstance(value, Mapping):
            continue
        article = _provision_token(value.get("article"), ("dieu", "article"))
        clause = _provision_token(value.get("clause"), ("khoan", "clause"))
        point = _provision_token(value.get("point"), ("diem", "point"))
        if not article or (point and not clause):
            continue
        normalized.add(ProvisionReference(article=article, clause=clause, point=point))
    return tuple(sorted(normalized))


@dataclass(frozen=True)
class LegalValidityObservation:
    document_id: str | None
    law_number: str
    issuing_agency: str | None
    issued_date: str | None
    source_url: str
    source_kind: str
    raw_status: str | None
    normalized_status: NormalizedValidityStatus
    effective_from: str | None
    effective_to: str | None
    affecting_document_number: str | None
    affected_provisions: tuple[ProvisionReference, ...]
    identity_status: IdentityStatus
    evidence_status: EvidenceStatus
    observed_at: datetime
    source_updated_at: datetime | None
    fingerprint: str = field(init=False)
    observation_key: str = field(init=False)

    def __post_init__(self) -> None:
        normalized_number = normalize_law_number(self.law_number)
        if normalized_number is None:
            raise ValueError("exact_law_number_required")
        observed_at = _parse_datetime(self.observed_at)
        if observed_at is None:
            raise ValueError("observed_at_required")
        source_updated_at = _parse_datetime(self.source_updated_at)
        evidence = {
            "document_id": str(self.document_id or ""),
            "law_number": normalized_number,
            "issuing_agency": str(self.issuing_agency or "").strip(),
            "issued_date": _parse_date(self.issued_date).isoformat() if _parse_date(self.issued_date) else None,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "raw_status": str(self.raw_status or "").strip(),
            "normalized_status": self.normalized_status.value,
            "effective_from": _parse_date(self.effective_from).isoformat() if _parse_date(self.effective_from) else None,
            "effective_to": _parse_date(self.effective_to).isoformat() if _parse_date(self.effective_to) else None,
            "affecting_document_number": normalize_law_number(self.affecting_document_number),
            "affected_provisions": [item.to_dict() for item in self.affected_provisions],
            "identity_status": self.identity_status.value,
            "evidence_status": self.evidence_status.value,
        }
        canonical = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        key_material = f"{self.source_url}|{normalized_number}|{fingerprint}"
        observation_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        object.__setattr__(self, "law_number", normalized_number)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "source_updated_at", source_updated_at)
        object.__setattr__(self, "issued_date", evidence["issued_date"])
        object.__setattr__(self, "effective_from", evidence["effective_from"])
        object.__setattr__(self, "effective_to", evidence["effective_to"])
        object.__setattr__(self, "affecting_document_number", evidence["affecting_document_number"])
        object.__setattr__(self, "fingerprint", fingerprint)
        object.__setattr__(self, "observation_key", observation_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "law_number": self.law_number,
            "issuing_agency": self.issuing_agency,
            "issued_date": self.issued_date,
            "source_url": self.source_url,
            "source_kind": self.source_kind,
            "raw_status": self.raw_status,
            "normalized_status": self.normalized_status.value,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "affecting_document_number": self.affecting_document_number,
            "affected_provisions": [item.to_dict() for item in self.affected_provisions],
            "identity_status": self.identity_status.value,
            "evidence_status": self.evidence_status.value,
            "observed_at": self.observed_at.isoformat(),
            "source_updated_at": self.source_updated_at.isoformat() if self.source_updated_at else None,
            "fingerprint": self.fingerprint,
            "observation_key": self.observation_key,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LegalValidityObservation":
        return cls(
            document_id=str(value.get("document_id") or "") or None,
            law_number=str(value.get("law_number") or ""),
            issuing_agency=str(value.get("issuing_agency") or "") or None,
            issued_date=str(value.get("issued_date") or "") or None,
            source_url=str(value.get("source_url") or ""),
            source_kind=str(value.get("source_kind") or "vbpl"),
            raw_status=str(value.get("raw_status") or "") or None,
            normalized_status=NormalizedValidityStatus(str(value.get("normalized_status"))),
            effective_from=str(value.get("effective_from") or "") or None,
            effective_to=str(value.get("effective_to") or "") or None,
            affecting_document_number=str(value.get("affecting_document_number") or "") or None,
            affected_provisions=normalize_provisions(value.get("affected_provisions")),
            identity_status=IdentityStatus(str(value.get("identity_status") or "missing")),
            evidence_status=EvidenceStatus(str(value.get("evidence_status") or "malformed")),
            observed_at=_parse_datetime(value.get("observed_at")) or datetime.now(timezone.utc),
            source_updated_at=_parse_datetime(value.get("source_updated_at")),
        )


@dataclass(frozen=True)
class ServingDecision:
    serving_action: str
    blocked: bool
    would_block: bool
    reason_code: str | None
    warning_code: str | None


_ADVERSE_FULL = {
    NormalizedValidityStatus.EXPIRED,
    NormalizedValidityStatus.SUSPENDED,
    NormalizedValidityStatus.REPLACED,
    NormalizedValidityStatus.REPEALED,
}
_ADVERSE_PARTIAL = {
    NormalizedValidityStatus.EXPIRED_PARTIAL,
    NormalizedValidityStatus.SUSPENDED_PARTIAL,
    NormalizedValidityStatus.AMENDED,
}


def serving_decision(
    observation: LegalValidityObservation,
    *,
    as_of: date,
    mode: str,
    article_number: Any = None,
    clause_number: Any = None,
    point_number: Any = None,
) -> ServingDecision:
    """Return a deterministic projection without changing corpus state."""

    mode = mode if mode in {"observe", "protect", "strict"} else "protect"
    start = _parse_date(observation.effective_from)
    end = _parse_date(observation.effective_to)

    reason: str | None = None
    warning: str | None = None
    proposed_action = "allow"

    if observation.identity_status is not IdentityStatus.EXACT:
        warning = "validity_identity_unverified"
        if mode == "strict":
            reason = "identity_unverified"
            proposed_action = "block_document"
    elif observation.evidence_status in {EvidenceStatus.CONFLICTING, EvidenceStatus.MALFORMED}:
        warning = "validity_evidence_unverified"
        if mode == "strict":
            reason = "evidence_unverified"
            proposed_action = "block_document"
    elif start and as_of < start:
        reason = "not_yet_effective"
        proposed_action = "block_document"
    elif end and as_of < end and observation.normalized_status in _ADVERSE_FULL:
        warning = (
            "historical_validity"
            if observation.observed_at.date() >= end
            else "scheduled_change"
        )
    elif end and as_of < end and observation.normalized_status in _ADVERSE_PARTIAL:
        warning = (
            "historical_validity"
            if observation.observed_at.date() >= end
            else "scheduled_partial_change"
        )
    elif end and as_of >= end and observation.normalized_status in _ADVERSE_FULL:
        reason = observation.normalized_status.value
        proposed_action = "historical_only"
    elif end and as_of < end:
        warning = "historical_validity"
    elif observation.normalized_status in _ADVERSE_FULL:
        reason = observation.normalized_status.value
        proposed_action = "historical_only"
    elif observation.normalized_status in _ADVERSE_PARTIAL:
        if not observation.affected_provisions:
            reason = "partial_scope_unresolved"
            proposed_action = "block_document"
        elif any(
            provision.matches(
                article_number=article_number,
                clause_number=clause_number,
                point_number=point_number,
            )
            for provision in observation.affected_provisions
        ):
            reason = observation.normalized_status.value
            proposed_action = "block_provisions"
    elif observation.normalized_status is NormalizedValidityStatus.NOT_YET_EFFECTIVE:
        reason = "not_yet_effective"
        proposed_action = "block_document"
    elif observation.normalized_status is NormalizedValidityStatus.UNKNOWN:
        warning = "validity_unverified"
        if mode == "strict":
            reason = "validity_unverified"
            proposed_action = "block_document"

    would_block = proposed_action in {"block_document", "block_provisions", "historical_only"}
    blocked = would_block and mode != "observe"
    action = proposed_action if blocked else "allow"
    if mode == "observe" and would_block:
        warning = f"shadow_{reason or 'would_block'}"
    return ServingDecision(
        serving_action=action,
        blocked=blocked,
        would_block=would_block,
        reason_code=reason,
        warning_code=warning,
    )


__all__ = [
    "EvidenceStatus",
    "IdentityStatus",
    "LEGAL_TIMEZONE",
    "LegalValidityObservation",
    "NormalizedValidityStatus",
    "ProvisionReference",
    "ServingDecision",
    "normalize_law_number",
    "normalize_provisions",
    "normalize_validity_status",
    "serving_decision",
    "vietnam_legal_date",
]
