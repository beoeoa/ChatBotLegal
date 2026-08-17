"""Deterministic corpus thinning primitives for read-only candidate builds.

This module has no database or vector-store write path.  It classifies and
scores already-read document inventories, then builds a bounded serving
candidate while preserving mandatory legal sources and coverage cells.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from api.legal_domains import canonicalize_legal_domain


SCHEMA_VERSION = "legal-serving-candidate-v1"
BASELINE_SCHEMA_VERSION = "legal-serving-baseline-v1"

ELIGIBLE_STATUSES = {"eligible"}
HARD_EXCLUSION_STATUSES = {
    "expired",
    "out_of_scope",
    "duplicate",
    "internal_only",
    "empty_or_corrupt",
    "metadata_unverified",
    "superseded",
    "pending_review",
}

CANONICAL_SERVING_DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "hanh_chinh_cong",
)

FOUNDATION_TYPES = {
    "bo luat",
    "hien phap",
    "luat",
    "nghi dinh",
    "phap lenh",
    "nghi quyet cua quoc hoi",
    "nghi quyet cua uy ban thuong vu quoc hoi",
}

FACET_TERMS: dict[str, tuple[str, ...]] = {
    "authority": ("tham quyen", "uy ban nhan dan", "chu tich uy ban"),
    "documents": ("ho so", "giay to", "to khai"),
    "procedure": ("thu tuc", "trinh tu", "tiep nhan", "giai quyet"),
    "deadline": ("thoi han", "thoi gian giai quyet"),
    "fee": ("le phi", "muc phi", "thu phi"),
    "form": ("bieu mau", "mau so", "to khai"),
    "effectivity": ("hieu luc", "sua doi", "bo sung", "thay the"),
}

SCOPE_RELEVANCE = {
    "hai_phong_commune_specific": 100,
    "central_commune_specific": 96,
    "current_core_law": 94,
    "hai_phong_relevant_domain": 92,
    "step2_current_official_scope_override": 90,
    "metadata_domain_corrected_2026-08-11": 88,
    "related_current_instrument": 84,
    "central_relevant_framework": 80,
    "core_domain_expansion_2026_07_14": 72,
    "embedding_completed": 65,
}

SCORE_WEIGHTS = {
    "commune_local_relevance": 0.18,
    "legal_authority": 0.14,
    "answerability": 0.14,
    "official_source_trust": 0.12,
    "metadata_quality": 0.10,
    "procedure_coverage": 0.10,
    "citation_value": 0.08,
    "local_haiphong_priority": 0.05,
    "cross_procedure_reuse": 0.05,
    "validity_confidence": 0.04,
}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalized_law_number(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalized_text(value).upper())


def legal_identity_key(row: Mapping[str, Any]) -> tuple[str, str]:
    """Return the minimum safe identity for duplicate detection.

    Vietnamese instruments can legally share a printed number.  A number-only
    key can therefore collapse a Law into an unrelated Resolution/Decision.
    Official ItemID is validated by the remediation/import boundary; within an
    inventory, number plus document type is the safe deterministic minimum.
    """

    return (
        normalized_law_number(row.get("law_number")),
        normalized_text(row.get("document_type")),
    )


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _official_source_score(url: Any) -> int:
    value = str(url or "").strip().casefold()
    if not value:
        return 0
    if any(
        host in value
        for host in (
            "vbpl.vn",
            "dichvucong.gov.vn",
            "vanban.chinhphu.vn",
            "congbao.chinhphu.vn",
            "haiphong.gov.vn",
        )
    ):
        return 100
    if ".gov.vn" in value or ".chinhphu.vn" in value:
        return 94
    return 45


def _domain(row: Mapping[str, Any]) -> str | None:
    raw = row.get("canonical_domain") or row.get("domain")
    value = canonicalize_legal_domain(raw)
    return value if value in CANONICAL_SERVING_DOMAINS else None


def facet_tags(row: Mapping[str, Any]) -> list[str]:
    explicit = row.get("facet_tags")
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        return sorted({str(item) for item in explicit if str(item).strip()})
    haystack = normalized_text(
        " ".join(
            str(row.get(key) or "")
            for key in ("title", "document_type", "searchable_text")
        )
    )
    return sorted(
        facet
        for facet, terms in FACET_TERMS.items()
        if any(term in haystack for term in terms)
    )


def _metadata_missing(row: Mapping[str, Any]) -> list[str]:
    missing = []
    for key in ("title", "law_number", "issuing_agency", "source_url"):
        if not str(row.get(key) or "").strip():
            missing.append(key)
    if _as_date(row.get("effective_date")) is None:
        missing.append("effective_date")
    if _domain(row) is None:
        missing.append("domain")
    return missing


def classify_document(
    row: Mapping[str, Any], *, legal_as_of: date
) -> tuple[str, str, bool, list[str]]:
    """Return status, reason code, serving eligibility and repair fields."""

    stored_status = normalized_text(row.get("status"))
    scope_reason = str(row.get("scope_reason") or "").strip().casefold()
    effective = _as_date(row.get("effective_date"))
    expired = _as_date(row.get("expired_date"))
    if stored_status != "active" or (expired is not None and expired <= legal_as_of):
        return "expired", "expired_at_legal_as_of", False, []
    if scope_reason.startswith("expired"):
        return "expired", "scope_marks_expired", False, []
    if effective is not None and effective > legal_as_of:
        return "pending_review", "not_yet_effective", False, []
    if scope_reason in {"out_of_commune_scope", "other_province"}:
        return "out_of_scope", scope_reason, False, []
    if scope_reason == "known_superseded":
        return "superseded", "known_superseded", False, []
    if scope_reason == "internal_only":
        return "internal_only", "scope_marks_internal_only", False, []
    if _int(row.get("nonempty_chunk_count")) <= 0:
        return "empty_or_corrupt", "no_usable_chunk_content", False, []

    missing = _metadata_missing(row)
    hard_missing = sorted(set(missing).intersection({"title", "law_number", "source_url", "domain"}))
    if hard_missing:
        return "metadata_unverified", "required_metadata_missing", False, hard_missing
    if missing:
        # Missing effectivity is not a soft serving condition.  It is a repair
        # queue state only: the document becomes selectable after an official
        # source supplies the value.  Treating this state as eligible made an
        # unresolved date indistinguishable from verified current law.
        return "metadata_repairable", "official_metadata_repair_required", False, missing
    return "eligible", "hard_filters_passed", True, []


def _authority_score(row: Mapping[str, Any]) -> int:
    doc_type = normalized_text(row.get("document_type"))
    agency = normalized_text(row.get("issuing_agency"))
    if doc_type in {"hien phap", "bo luat", "luat"}:
        return 100
    if doc_type == "phap lenh":
        return 95
    if doc_type == "nghi dinh":
        return 90
    if doc_type.startswith("nghi quyet"):
        return 84 if any(x in agency for x in ("quoc hoi", "uy ban thuong vu")) else 78
    if doc_type in {"thong tu", "thong tu lien tich", "thong tu lien bo"}:
        return 74
    if doc_type == "quyet dinh":
        return 78 if "hai phong" in agency else 68
    if doc_type == "chi thi":
        return 52
    if doc_type == "cong van":
        return 34
    return 48


def score_document(row: Mapping[str, Any], *, legal_as_of: date) -> dict[str, int | float]:
    chunks = max(_int(row.get("chunk_count")), 1)
    nonempty = _int(row.get("nonempty_chunk_count"))
    eligible_chunks = _int(row.get("eligible_chunk_count")) or nonempty
    facets = facet_tags(row)
    metadata_present = 6 - len(_metadata_missing(row))
    relation_count = _int(row.get("relationship_count"))
    scope_reason = str(row.get("scope_reason") or "").strip()
    issued_agency = normalized_text(row.get("issuing_agency"))
    title = normalized_text(row.get("title"))
    effective = _as_date(row.get("effective_date"))
    expired = _as_date(row.get("expired_date"))

    components: dict[str, int | float] = {
        "commune_local_relevance": SCOPE_RELEVANCE.get(scope_reason, 60),
        "legal_authority": _authority_score(row),
        "answerability": round(
            min(100.0, 65.0 * min(nonempty / chunks, 1.0) + 5.0 * len(facets)), 2
        ),
        "official_source_trust": _official_source_score(row.get("source_url")),
        "metadata_quality": round(max(0.0, metadata_present / 6 * 100), 2),
        "procedure_coverage": min(100, len(facets) * 16 + (20 if "thu tuc" in title else 0)),
        "citation_value": round(min(eligible_chunks / chunks, 1.0) * 100, 2),
        "local_haiphong_priority": (
            100
            if "hai phong" in issued_agency or "hai phong" in title
            else 55
        ),
        "cross_procedure_reuse": min(100, 35 + int(math.log2(relation_count + 1) * 12)),
        "validity_confidence": (
            100
            if effective is not None
            and effective <= legal_as_of
            and (expired is None or expired > legal_as_of)
            else 72
        ),
    }
    components["total_score"] = round(
        sum(float(components[key]) * weight for key, weight in SCORE_WEIGHTS.items()),
        4,
    )
    return components


def _foundation(row: Mapping[str, Any]) -> bool:
    return normalized_text(row.get("document_type")) in FOUNDATION_TYPES


def _duplicate_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    score = row.get("score_components") or {}
    return (
        bool(row.get("golden_required")),
        _foundation(row),
        float(score.get("metadata_quality") or 0),
        float(score.get("official_source_trust") or 0),
        _int(row.get("nonempty_chunk_count")),
        _int(row.get("relationship_count")),
        -_int(row.get("document_id")),
    )


def prepare_documents(
    rows: Sequence[Mapping[str, Any]],
    *,
    legal_as_of: date,
    required_law_numbers: Iterable[str] = (),
) -> list[dict[str, Any]]:
    required = {normalized_law_number(item) for item in required_law_numbers if item}
    prepared: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        row["document_id"] = _int(row.get("document_id"))
        row["canonical_domain"] = _domain(row)
        row["normalized_law_number"] = normalized_law_number(row.get("law_number"))
        row["golden_required"] = row["normalized_law_number"] in required
        row["foundation_document"] = _foundation(row)
        row["facet_tags"] = facet_tags(row)
        status, reason, eligible, repair_fields = classify_document(
            row, legal_as_of=legal_as_of
        )
        row.update(
            {
                "status": status,
                "decision_reason": reason,
                "candidate_eligible": eligible,
                "metadata_repair_fields": repair_fields,
                "score_components": score_document(row, legal_as_of=legal_as_of),
            }
        )
        prepared.append(row)

    duplicates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in prepared:
        if row["candidate_eligible"] and row["normalized_law_number"]:
            duplicates[legal_identity_key(row)].append(row)
    for group in duplicates.values():
        if len(group) <= 1:
            continue
        canonical = max(group, key=_duplicate_rank)
        for row in group:
            row["canonical_duplicate_document_id"] = canonical["document_id"]
            if row is canonical:
                continue
            row.update(
                {
                    "status": "duplicate",
                    "decision_reason": "duplicate_law_number_noncanonical",
                    "candidate_eligible": False,
                }
            )
    return sorted(prepared, key=lambda item: item["document_id"])


def _selection_rank(row: Mapping[str, Any]) -> tuple[Any, ...]:
    score = row.get("score_components") or {}
    return (
        bool(row.get("golden_required")),
        bool(row.get("foundation_document")),
        float(score.get("total_score") or 0),
        float(score.get("commune_local_relevance") or 0),
        float(score.get("legal_authority") or 0),
        -_int(row.get("document_id")),
    )


def select_candidate(
    prepared: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
    coverage_per_cell: int = 3,
    max_domain_share: float = 0.45,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    eligible = [dict(row) for row in prepared if row.get("candidate_eligible")]
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    if len(eligible) < target_count:
        raise ValueError(
            f"eligible corpus smaller than target: eligible={len(eligible)} target={target_count}"
        )

    ranked = sorted(eligible, key=_selection_rank, reverse=True)
    selected: dict[int, dict[str, Any]] = {}
    selection_reasons: dict[int, set[str]] = defaultdict(set)

    def add(row: Mapping[str, Any], reason: str) -> None:
        doc_id = _int(row.get("document_id"))
        if doc_id not in selected and len(selected) < target_count:
            selected[doc_id] = dict(row)
        if doc_id in selected:
            selection_reasons[doc_id].add(reason)

    for row in ranked:
        if row.get("golden_required"):
            add(row, "required_golden_source")
        if row.get("foundation_document"):
            add(row, "mandatory_foundation_document")

    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ranked:
        domain = str(row.get("canonical_domain") or "unknown")
        tags = row.get("facet_tags") or ["general"]
        for tag in tags:
            cells[(domain, str(tag))].append(row)
    for cell in sorted(cells):
        for row in cells[cell][:coverage_per_cell]:
            add(row, f"coverage_reserve:{cell[0]}:{cell[1]}")

    cap = max(1, int(math.ceil(target_count * max_domain_share)))
    domain_counts = Counter(
        str(row.get("canonical_domain") or "unknown") for row in selected.values()
    )
    for row in ranked:
        if len(selected) >= target_count:
            break
        doc_id = _int(row.get("document_id"))
        if doc_id in selected:
            continue
        domain = str(row.get("canonical_domain") or "unknown")
        if domain_counts[domain] >= cap:
            continue
        add(row, "score_ranked_with_domain_cap")
        domain_counts[domain] += 1
    for row in ranked:
        if len(selected) >= target_count:
            break
        doc_id = _int(row.get("document_id"))
        if doc_id not in selected:
            add(row, "capacity_fill_after_domain_cap")

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for source in prepared:
        row = dict(source)
        doc_id = _int(row.get("document_id"))
        if doc_id in selected:
            row["reason_kept"] = sorted(selection_reasons[doc_id])
            kept.append(row)
        else:
            if row.get("candidate_eligible"):
                row["status"] = "eligible"
                row["decision_reason"] = "below_candidate_cutline"
            excluded.append(row)

    kept.sort(key=lambda row: row["document_id"])
    excluded.sort(key=lambda row: row["document_id"])
    matrix = coverage_matrix(prepared, kept)
    selection_summary = {
        "target_count": target_count,
        "eligible_before_scoring": len(eligible),
        "selected_count": len(kept),
        "excluded_count": len(excluded),
        "coverage_per_cell": coverage_per_cell,
        "max_domain_share": max_domain_share,
        "selected_domain_counts": dict(
            sorted(Counter(row.get("canonical_domain") or "unknown" for row in kept).items())
        ),
        "status_counts": dict(
            sorted(Counter(str(row.get("status")) for row in prepared).items())
        ),
        "decision_reason_counts": dict(
            sorted(Counter(str(row.get("decision_reason")) for row in excluded).items())
        ),
        "coverage_matrix_sha256": canonical_sha256(matrix),
    }
    return kept, excluded, {"matrix": matrix, "summary": selection_summary}


def coverage_matrix(
    all_rows: Sequence[Mapping[str, Any]],
    kept_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    before: Counter[tuple[str, str]] = Counter()
    after: Counter[tuple[str, str]] = Counter()
    for row in all_rows:
        if not row.get("candidate_eligible"):
            continue
        domain = str(row.get("canonical_domain") or "unknown")
        for facet in row.get("facet_tags") or ["general"]:
            before[(domain, str(facet))] += 1
    for row in kept_rows:
        domain = str(row.get("canonical_domain") or "unknown")
        for facet in row.get("facet_tags") or ["general"]:
            after[(domain, str(facet))] += 1
    return [
        {
            "domain": domain,
            "facet": facet,
            "eligible_documents": before[(domain, facet)],
            "selected_documents": after[(domain, facet)],
            "coverage_retention": round(
                after[(domain, facet)] / before[(domain, facet)], 6
            ),
        }
        for domain, facet in sorted(before)
    ]


def required_source_blockers(
    prepared: Sequence[Mapping[str, Any]], required_law_numbers: Iterable[str]
) -> list[dict[str, Any]]:
    rows_by_law: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in prepared:
        rows_by_law[str(row.get("normalized_law_number") or "")].append(row)
    blockers = []
    for raw in sorted({str(item) for item in required_law_numbers if str(item).strip()}):
        normalized = normalized_law_number(raw)
        rows = rows_by_law.get(normalized) or []
        if any(row.get("candidate_eligible") for row in rows):
            continue
        blockers.append(
            {
                "law_number": raw,
                "normalized_law_number": normalized,
                "reason_code": "required_source_missing_or_ineligible",
                "observed_documents": [
                    {
                        "document_id": row.get("document_id"),
                        "status": row.get("status"),
                        "decision_reason": row.get("decision_reason"),
                    }
                    for row in rows
                ],
            }
        )
    return blockers


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "CANONICAL_SERVING_DOMAINS",
    "ELIGIBLE_STATUSES",
    "HARD_EXCLUSION_STATUSES",
    "SCHEMA_VERSION",
    "SCORE_WEIGHTS",
    "canonical_sha256",
    "classify_document",
    "coverage_matrix",
    "facet_tags",
    "legal_identity_key",
    "normalized_law_number",
    "prepare_documents",
    "required_source_blockers",
    "score_document",
    "select_candidate",
]
