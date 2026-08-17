"""Contracts shared by the read-only Retrieval Release V2 tooling.

The release tooling deliberately distinguishes an observed database state from
an approved legal serving decision.  Nothing in this module edits PostgreSQL,
Chroma, source history or the active pointer.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


INVENTORY_DOCUMENT_COUNT = 12_236
RELEASE_SCHEMA_VERSION = "retrieval-release-v2"
EVAL_SUITE_SCHEMA_VERSION = "retrieval-eval-suite-v1"
DOMAINS = (
    "Hộ tịch/chứng thực",
    "Đất đai/xây dựng/môi trường",
    "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục",
)
SPLITS = ("golden-regression", "hard-negative", "production-holdout")
SPLIT_COUNTS = {
    "golden-regression": 1_000,
    "hard-negative": 500,
    "production-holdout": 500,
}
DOMAIN_BLOCK_SIZE = {
    "golden-regression": 200,
    "hard-negative": 100,
    "production-holdout": 100,
}


def read_active_collection_pointer(chroma_path: str | Path) -> str | None:
    """Read the active Chroma pointer without changing it."""

    pointer_path = Path(chroma_path) / "active_core_collection.txt"
    if not pointer_path.is_file():
        return None
    value = pointer_path.read_text(encoding="utf-8").strip()
    return value or None


def require_staging_collection_target(target_name: str, chroma_path: str | Path) -> str | None:
    """Reject any writer target that aliases the active collection."""

    active = read_active_collection_pointer(chroma_path)
    if active and str(target_name).strip() == active:
        raise RuntimeError("staging_target_must_differ_from_active_pointer")
    return active


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


def file_sha256(path: Any) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify_inventory_document(row: Mapping[str, Any], *, legal_as_of: date) -> dict[str, Any]:
    """Classify one document conservatively for a release draft.

    This is an *observed/provisional* classification.  A current or historical
    label is never treated as official legal review; the returned
    ``legal_review_required`` flag stays true until an attested source review
    is attached.
    """

    status = str(row.get("status") or "").strip().casefold()
    effective = row.get("effective_date")
    expired = row.get("expired_date")
    if isinstance(effective, str) and effective:
        effective = date.fromisoformat(effective[:10])
    if isinstance(expired, str) and expired:
        expired = date.fromisoformat(expired[:10])
    included = bool(row.get("scope_included"))
    has_source = bool(str(row.get("source_url") or "").strip())
    has_law_number = bool(str(row.get("law_number") or "").strip())
    article_count = int(row.get("article_count") or 0)
    chunk_count = int(row.get("chunk_count") or 0)
    reasons: list[str] = []

    if not has_law_number:
        reasons.append("missing_law_number")
    if not has_source:
        reasons.append("missing_source_url")
    if article_count <= 0:
        reasons.append("missing_articles")
    if chunk_count <= 0:
        reasons.append("missing_chunks")
    if not included:
        reasons.append("outside_search_scope")

    is_effective = (
        (effective is None or effective <= legal_as_of)
        and (expired is None or expired > legal_as_of)
    )
    historical_statuses = {
        "expired", "repealed", "replaced", "superseded", "suspended",
        "historical", "archived", "inactive",
    }
    historical_status = status in historical_statuses
    is_future_effective = (
        effective is not None
        and effective > legal_as_of
        and status not in {"staging", *historical_statuses}
    )
    if not reasons and is_future_effective:
        state = "future_effective"
        basis = "effective_date_after_legal_as_of"
    elif not reasons and is_effective and not historical_status and status != "staging":
        state = "current_retrievable"
        basis = "scope_and_effectivity_observed"
    elif not reasons and (historical_status or (expired is not None and expired <= legal_as_of)):
        state = "historical_only"
        basis = "expired_or_historical_status_observed"
    else:
        state = "quarantined"
        basis = ";".join(sorted(set(reasons))) or "unresolved_status_or_effectivity"

    return {
        "document_id": int(row["document_id"]),
        "law_number": row.get("law_number"),
        "status_observed": row.get("status"),
        "serving_state": state,
        "classification_basis": basis,
        "legal_review_required": True,
        "scope_included_observed": included,
        "effective_date": effective.isoformat() if isinstance(effective, date) else None,
        "expired_date": expired.isoformat() if isinstance(expired, date) else None,
        "article_count": article_count,
        "chunk_count": chunk_count,
        "source_url": row.get("source_url"),
    }


def inventory_counts(documents: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("serving_state") or "") for row in documents)
    return {
        "inventory_document_count": len(documents),
        "current_retrievable": int(counts["current_retrievable"]),
        "historical_only": int(counts["historical_only"]),
        "future_effective": int(counts["future_effective"]),
        "quarantined": int(counts["quarantined"]),
        "state_sum": sum(
            int(counts[state])
            for state in (
                "current_retrievable",
                "historical_only",
                "future_effective",
                "quarantined",
            )
        ),
    }


def validate_inventory_partition(documents: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    raw_ids = [row.get("document_id") for row in documents]
    normalized_ids: list[int] = []
    for value in raw_ids:
        try:
            document_id = int(value)
        except (TypeError, ValueError):
            normalized_ids.append(0)
            continue
        normalized_ids.append(document_id)
        if document_id <= 0:
            errors.append("invalid_document_id")
    if len(normalized_ids) != len(set(normalized_ids)):
        errors.append("duplicate_document_id")
    allowed = {
        "current_retrievable",
        "historical_only",
        "future_effective",
        "quarantined",
    }
    unknown = sorted({str(row.get("serving_state") or "") for row in documents} - allowed)
    if unknown:
        errors.append(f"unknown_serving_state:{','.join(unknown)}")
    counts = inventory_counts(documents)
    if counts["state_sum"] != counts["inventory_document_count"]:
        errors.append("serving_state_partition_incomplete")
    if counts["inventory_document_count"] != INVENTORY_DOCUMENT_COUNT:
        errors.append(
            f"inventory_document_count:{counts['inventory_document_count']}!={INVENTORY_DOCUMENT_COUNT}"
        )
    return errors
