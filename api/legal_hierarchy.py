"""Deterministic Vietnamese legal-authority classification and ordering.

The policy deliberately reads metadata only.  It does not inspect passage text,
call a model, access storage, or decide that two provisions semantically
conflict.  Effectivity and verified document relationships must be evaluated by
the caller before candidates enter this ordering layer.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from heapq import heappop, heappush
from typing import Any, Mapping, Sequence

POLICY_VERSION = "vn-lvbqppl-64-87-2025-v1"
ORDERING_DESCRIPTION = (
    "authority>same_issuer_later>scope_fit>relevance>stable_key"
)


@dataclass(frozen=True, slots=True)
class AuthorityClassification:
    level: str
    rank: int
    label_vi: str
    confidence: str
    issuing_authority: str | None
    reason_code: str
    policy_version: str = POLICY_VERSION


_LEVELS: dict[str, tuple[int, str]] = {
    "constitution": (140, "Hiến pháp"),
    "national_assembly": (130, "Luật, bộ luật hoặc nghị quyết của Quốc hội"),
    "standing_committee": (
        120,
        "Pháp lệnh hoặc nghị quyết của Ủy ban Thường vụ Quốc hội",
    ),
    "president": (110, "Lệnh hoặc quyết định của Chủ tịch nước"),
    "government": (100, "Nghị định hoặc nghị quyết của Chính phủ"),
    "prime_minister": (90, "Quyết định của Thủ tướng Chính phủ"),
    "judicial_council": (
        85,
        "Nghị quyết của Hội đồng Thẩm phán Tòa án nhân dân tối cao",
    ),
    "ministerial": (80, "Thông tư của cơ quan trung ương có thẩm quyền"),
    "provincial_people_council": (
        70,
        "Nghị quyết của Hội đồng nhân dân cấp tỉnh",
    ),
    "provincial_people_committee": (
        60,
        "Quyết định của Ủy ban nhân dân cấp tỉnh",
    ),
    # Retained only for documents that continue to apply during the two-tier
    # local-government transition.  It is never inferred from scope alone.
    "legacy_district_people_council": (
        55,
        "Nghị quyết cấp huyện còn được áp dụng theo quy định chuyển tiếp",
    ),
    "legacy_district_people_committee": (
        50,
        "Quyết định cấp huyện còn được áp dụng theo quy định chuyển tiếp",
    ),
    "commune_people_council": (
        45,
        "Nghị quyết của Hội đồng nhân dân cấp xã",
    ),
    "commune_people_committee": (
        40,
        "Quyết định của Ủy ban nhân dân cấp xã",
    ),
    "reference_document": (-10, "Văn bản hợp nhất hoặc tài liệu tra cứu"),
    "authority_unverified": (-20, "Chưa xác minh được cấp hiệu lực pháp lý"),
}

_EXCEPTION_KEYS = (
    "legal_precedence_exception",
    "precedence_requires_review",
    "special_legal_regime",
)


@lru_cache(maxsize=16384)
def _normalize_text(value: str) -> str:
    text = value.strip().casefold().replace("đ", "d")
    if not text.isascii():
        text = "".join(
            character
            for character in unicodedata.normalize("NFD", text)
            if unicodedata.category(character) != "Mn"
        )
    return " ".join(text.split())


def _normalize(value: Any) -> str:
    return _normalize_text(str(value or ""))


def _canonical_issuer(value: Any) -> str | None:
    issuer = _normalize(value)
    if not issuer:
        return None
    issuer = re.sub(r"[^a-z0-9]+", " ", issuer)
    aliases = {
        "ubnd": "uy ban nhan dan",
        "hdnd": "hoi dong nhan dan",
        "tp": "thanh pho",
        "ttg": "thu tuong",
        "ctn": "chu tich nuoc",
    }
    return " ".join(aliases.get(token, token) for token in issuer.split()) or None


def _metadata_value(
    row: Mapping[str, Any], metadata: Mapping[str, Any], key: str
) -> Any:
    value = row.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return metadata.get(key)
    return value


def _classification(
    level: str,
    issuer: str | None,
    reason_code: str,
    *,
    confidence: str = "verified",
) -> AuthorityClassification:
    rank, label = _LEVELS[level]
    return AuthorityClassification(
        level=level,
        rank=rank,
        label_vi=label,
        confidence=confidence,
        issuing_authority=issuer,
        reason_code=reason_code,
    )


def _unverified(
    issuer: str | None, reason_code: str = "AUTHORITY_METADATA_INSUFFICIENT"
) -> AuthorityClassification:
    return _classification(
        "authority_unverified",
        issuer,
        reason_code,
        confidence="unverified",
    )


def _local_government_level(issuer: str | None, scope: str) -> str | None:
    issuer = issuer or ""
    if any(
        marker in issuer
        for marker in (" phuong ", " xa ", " thi tran ", " cap xa ")
    ) or issuer.startswith(("phuong ", "xa ", "thi tran ")):
        return "commune"
    if any(
        marker in issuer
        for marker in (" quan ", " huyen ", " thi xa ", " cap huyen ")
    ) or issuer.startswith(("quan ", "huyen ", "thi xa ")):
        return "district"
    if (
        " tinh " in f" {issuer} "
        or " cap tinh " in f" {issuer} "
        or "thanh pho hai phong" in issuer
        or "thanh pho truc thuoc trung uong" in issuer
        or scope == "haiphong"
    ):
        return "provincial"
    return None


def _classify_legal_authority_uncached(
    row: Mapping[str, Any], metadata: Mapping[str, Any] | None = None
) -> AuthorityClassification:
    """Classify authority using only explicit legal metadata."""

    metadata = metadata or {}
    document_type = _normalize(_metadata_value(row, metadata, "document_type"))
    law_number = _normalize(_metadata_value(row, metadata, "law_number"))
    scope = _normalize(_metadata_value(row, metadata, "scope"))
    issuer = _canonical_issuer(
        _metadata_value(row, metadata, "issuing_agency")
        or _metadata_value(row, metadata, "issuing_authority")
    )
    issuer_text = issuer or ""
    compact_number = re.sub(r"\s+", "", law_number)

    if "van ban hop nhat" in document_type or "/vbhn" in compact_number:
        return _classification(
            "reference_document",
            issuer,
            "AUTHORITY_REFERENCE_DOCUMENT",
            confidence="unverified",
        )
    if any(
        marker in document_type
        for marker in ("cong van", "van ban hanh chinh", "huong dan ap dung")
    ):
        return _unverified(issuer, "AUTHORITY_ADMINISTRATIVE_GUIDANCE")

    if document_type == "hien phap":
        return _classification("constitution", issuer, "AUTHORITY_CONSTITUTION_EXACT")
    if document_type in {"bo luat", "luat"}:
        return _classification(
            "national_assembly", issuer, "AUTHORITY_NATIONAL_ASSEMBLY_LAW_EXACT"
        )
    if document_type == "phap lenh":
        return _classification(
            "standing_committee", issuer, "AUTHORITY_STANDING_COMMITTEE_EXACT"
        )
    if document_type == "lenh":
        if "chu tich nuoc" in issuer_text or "-ctn" in compact_number:
            return _classification("president", issuer, "AUTHORITY_PRESIDENT_EXACT")
        return _unverified(issuer)
    if document_type == "nghi dinh":
        return _classification("government", issuer, "AUTHORITY_GOVERNMENT_DECREE_EXACT")
    if document_type.startswith("thong tu"):
        return _classification("ministerial", issuer, "AUTHORITY_MINISTERIAL_CIRCULAR_EXACT")

    if document_type.startswith("nghi quyet"):
        if (
            issuer_text == "quoc hoi"
            or re.search(r"/qh\d*$", compact_number)
        ):
            return _classification(
                "national_assembly",
                issuer,
                "AUTHORITY_NATIONAL_ASSEMBLY_RESOLUTION_EXACT",
            )
        if "uy ban thuong vu quoc hoi" in issuer_text or "/ubtvqh" in compact_number:
            return _classification(
                "standing_committee",
                issuer,
                "AUTHORITY_STANDING_COMMITTEE_RESOLUTION_EXACT",
            )
        if "chinh phu" in issuer_text or "/nq-cp" in compact_number:
            return _classification(
                "government", issuer, "AUTHORITY_GOVERNMENT_RESOLUTION_EXACT"
            )
        if "hoi dong tham phan" in issuer_text:
            return _classification(
                "judicial_council", issuer, "AUTHORITY_JUDICIAL_COUNCIL_EXACT"
            )
        if "hoi dong nhan dan" in issuer_text or "/nq-hdnd" in compact_number:
            local_level = _local_government_level(issuer, scope)
            if local_level == "provincial":
                return _classification(
                    "provincial_people_council",
                    issuer,
                    "AUTHORITY_PROVINCIAL_HDND_EXACT",
                )
            if local_level == "district":
                return _classification(
                    "legacy_district_people_council",
                    issuer,
                    "AUTHORITY_LEGACY_DISTRICT_HDND_EXACT",
                )
            if local_level == "commune":
                return _classification(
                    "commune_people_council",
                    issuer,
                    "AUTHORITY_COMMUNE_HDND_EXACT",
                )
        return _unverified(issuer)

    if document_type in {"quyet dinh", "quyetđinh", "quyetdinh"}:
        if "chu tich nuoc" in issuer_text or "-ctn" in compact_number:
            return _classification("president", issuer, "AUTHORITY_PRESIDENT_EXACT")
        if "thu tuong" in issuer_text or "-ttg" in compact_number:
            return _classification(
                "prime_minister", issuer, "AUTHORITY_PRIME_MINISTER_EXACT"
            )
        if "uy ban nhan dan" in issuer_text or "-ubnd" in compact_number:
            local_level = _local_government_level(issuer, scope)
            if local_level == "provincial":
                return _classification(
                    "provincial_people_committee",
                    issuer,
                    "AUTHORITY_PROVINCIAL_UBND_EXACT",
                )
            if local_level == "district":
                return _classification(
                    "legacy_district_people_committee",
                    issuer,
                    "AUTHORITY_LEGACY_DISTRICT_UBND_EXACT",
                )
            if local_level == "commune":
                return _classification(
                    "commune_people_committee",
                    issuer,
                    "AUTHORITY_COMMUNE_UBND_EXACT",
                )
        return _unverified(issuer)

    return _unverified(issuer)


@lru_cache(maxsize=4096)
def _classify_legal_authority_cached(
    document_type: str,
    law_number: str,
    scope: str,
    issuing_agency: str,
    issuing_authority: str,
) -> AuthorityClassification:
    return _classify_legal_authority_uncached(
        {
            "document_type": document_type,
            "law_number": law_number,
            "scope": scope,
            "issuing_agency": issuing_agency,
            "issuing_authority": issuing_authority,
        }
    )


def classify_legal_authority(
    row: Mapping[str, Any], metadata: Mapping[str, Any] | None = None
) -> AuthorityClassification:
    """Classify authority from explicit metadata with a bounded pure cache."""

    nested = metadata or {}
    values = (
        str(_metadata_value(row, nested, key) or "")
        for key in (
            "document_type",
            "law_number",
            "scope",
            "issuing_agency",
            "issuing_authority",
        )
    )
    return _classify_legal_authority_cached(*values)


@lru_cache(maxsize=4096)
def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_iso_date(text)


def _score(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _stable_part(value: Any) -> tuple[int, int | str]:
    text = str(value or "").strip()
    try:
        return (0, int(text))
    except ValueError:
        return (1, _normalize(text))


def _stable_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _normalize(item.get("law_number")),
        _stable_part(item.get("document_id")),
        _stable_part(item.get("article_id") or item.get("article_number")),
        _stable_part(item.get("chunk_id")),
    )


def _scope_fit(item: Mapping[str, Any], requested: str) -> int:
    if not requested:
        return 0
    scope = _normalize(item.get("scope"))
    if requested == "haiphong":
        return 2 if scope == "haiphong" else (1 if scope == "local" else 0)
    if requested == "local":
        return 2 if scope == "local" else (1 if scope == "haiphong" else 0)
    if requested == "central":
        return 2 if scope == "central" else 0
    return 1 if scope == requested else 0


def _baseline_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -int(item.get("_hierarchy_scope_fit") or 0),
        -float(item.get("_hierarchy_score") or 0.0),
        item.get("_hierarchy_stable_key"),
    )


def _order_authority_bucket(
    bucket: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply same-issuer date constraints over a deterministic baseline."""

    outgoing: list[list[int]] = [[] for _ in bucket]
    indegree = [0 for _ in bucket]
    dated_by_issuer: dict[str, dict[date, list[int]]] = {}
    for index, item in enumerate(bucket):
        issuer = str(item.get("_hierarchy_issuer") or "")
        issued_date = item.get("_hierarchy_issued_date")
        if issuer and isinstance(issued_date, date):
            dated_by_issuer.setdefault(issuer, {}).setdefault(issued_date, []).append(index)

    for date_groups in dated_by_issuer.values():
        previous_group: list[int] | None = None
        for issued_date in sorted(date_groups, reverse=True):
            current_group = date_groups[issued_date]
            if previous_group is not None:
                for newer in previous_group:
                    for older in current_group:
                        outgoing[newer].append(older)
                        indegree[older] += 1
            previous_group = current_group

    available: list[tuple[tuple[Any, ...], int]] = []
    for index, item in enumerate(bucket):
        if indegree[index] == 0:
            heappush(available, (_baseline_key(item), index))

    ordered: list[dict[str, Any]] = []
    while available:
        _, index = heappop(available)
        ordered.append(bucket[index])
        for target in outgoing[index]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(available, (_baseline_key(bucket[target]), target))
    return ordered


def _order_decorated(
    decorated: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[bool, int], list[dict[str, Any]]] = {}
    for item in decorated:
        key = (
            bool(item.get("_hierarchy_verified")),
            int(item.get("authority_rank") or -20),
        )
        buckets.setdefault(key, []).append(item)

    ordered: list[dict[str, Any]] = []
    for key in sorted(buckets, reverse=True):
        ordered.extend(_order_authority_bucket(buckets[key]))
    return ordered


def _reason_against_previous(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> str:
    if previous is None or previous.get("authority_rank") != current.get("authority_rank"):
        return "authority_rank"
    previous_issuer = previous.get("_hierarchy_issuer")
    current_issuer = current.get("_hierarchy_issuer")
    if previous_issuer and previous_issuer == current_issuer:
        previous_date = previous.get("_hierarchy_issued_date")
        current_date = current.get("_hierarchy_issued_date")
        if previous_date and current_date and previous_date != current_date:
            return "same_issuer_later"
    if previous.get("_hierarchy_scope_fit") != current.get("_hierarchy_scope_fit"):
        return "scope_fit"
    if previous.get("_hierarchy_score") != current.get("_hierarchy_score"):
        return "relevance"
    return "stable_key"


def rank_legal_evidence(
    items: Sequence[Mapping[str, Any]],
    *,
    scope_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return copied, annotated candidates in hard legal-authority order."""

    decorated: list[dict[str, Any]] = []
    requested_scope = _normalize(scope_filter)
    for item in items:
        projected = dict(item)
        metadata = item.get("metadata")
        nested = metadata if isinstance(metadata, Mapping) else {}
        classification = classify_legal_authority(item, nested)
        projected.update(
            {
                "authority_level": classification.level,
                "authority_label": classification.label_vi,
                "authority_rank": classification.rank,
                "authority_confidence": classification.confidence,
                "authority_reason_code": classification.reason_code,
                "authority_policy_version": classification.policy_version,
                "_hierarchy_issuer": classification.issuing_authority,
                "_hierarchy_verified": classification.confidence == "verified",
                "_hierarchy_issued_date": _parse_date(item.get("issued_date")),
                "_hierarchy_scope_fit": _scope_fit(item, requested_scope),
                "_hierarchy_score": _score(item.get("score")),
                "_hierarchy_stable_key": _stable_key(item),
            }
        )
        review_required = any(bool(item.get(key)) for key in _EXCEPTION_KEYS)
        if review_required:
            projected["authority_warning_code"] = (
                "legal_precedence_requires_review"
            )
        decorated.append(projected)

    decorated = _order_decorated(decorated)
    previous: Mapping[str, Any] | None = None
    for position, item in enumerate(decorated, start=1):
        item["hierarchy_position"] = position
        item["hierarchy_rule"] = _reason_against_previous(previous, item)
        previous = item
    for item in decorated:
        for key in (
            "_hierarchy_issuer",
            "_hierarchy_verified",
            "_hierarchy_issued_date",
            "_hierarchy_scope_fit",
            "_hierarchy_score",
            "_hierarchy_stable_key",
        ):
            item.pop(key, None)
    return decorated


def hierarchy_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    verified = sum(
        1 for item in items if item.get("authority_confidence") == "verified"
    )
    unverified = len(items) - verified
    review_required = sum(
        1
        for item in items
        if item.get("authority_warning_code")
        == "legal_precedence_requires_review"
    )
    return {
        "policy_version": POLICY_VERSION,
        "ordering": ORDERING_DESCRIPTION,
        "verified_count": verified,
        "unverified_count": unverified,
        "review_required_count": review_required,
    }


def public_authority_projection(item: Mapping[str, Any]) -> dict[str, str]:
    classification = None
    level = str(item.get("authority_level") or "")
    label = str(item.get("authority_label") or "")
    policy_version = str(item.get("authority_policy_version") or "")
    if not level or not label:
        classification = classify_legal_authority(item)
        level = classification.level
        label = classification.label_vi
        policy_version = classification.policy_version
    return {
        "level": level,
        "label": label,
        "policy_version": policy_version or POLICY_VERSION,
    }


__all__ = [
    "AuthorityClassification",
    "ORDERING_DESCRIPTION",
    "POLICY_VERSION",
    "classify_legal_authority",
    "hierarchy_summary",
    "public_authority_projection",
    "rank_legal_evidence",
]
