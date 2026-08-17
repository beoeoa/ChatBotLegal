"""Read-only PostgreSQL/vector/validity reconciliation primitives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

_HISTORICAL = {"expired", "superseded", "repealed", "inactive", "archived"}
_STAGING = {"staging", "draft", "submitted", "indexing", "pending"}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _chunk_class(row: Mapping[str, Any], vector_ids: set[int]) -> str:
    chunk_id = int(row["chunk_id"])
    canonical = row.get("canonical_chunk_id")
    eligible = row.get("eligible")
    if canonical not in (None, "", chunk_id, str(chunk_id)) or eligible is False:
        return "duplicate"
    document_status = str(row.get("document_status") or "").strip().casefold()
    article_status = str(row.get("article_status") or "active").strip().casefold()
    if document_status in _STAGING or article_status in _STAGING:
        return "staging"
    if document_status in _HISTORICAL or article_status in _HISTORICAL:
        return "historical"
    active = bool(
        document_status == "active"
        and article_status == "active"
        and row.get("included") is True
        and eligible is not False
        and row.get("has_content") is True
    )
    if active:
        return "active_vectorized" if chunk_id in vector_ids else "missing_vector"
    return "inactive_other"


def build_read_only_manifest(
    *,
    postgres_rows: Sequence[Mapping[str, Any]],
    vector_chunk_ids: Iterable[int],
    collection_name: str,
    validity_snapshot: Mapping[str, Any] | None,
    embedding_fingerprint: str | None = None,
    pipeline_version: str | None = None,
) -> dict[str, Any]:
    """Partition every SQL chunk without changing any serving store."""

    vector_ids = {int(value) for value in vector_chunk_ids}
    categories: dict[str, list[int]] = {
        "active_vectorized": [],
        "missing_vector": [],
        "historical": [],
        "staging": [],
        "duplicate": [],
        "inactive_other": [],
    }
    postgres_ids: set[int] = set()
    for row in postgres_rows:
        chunk_id = int(row["chunk_id"])
        postgres_ids.add(chunk_id)
        categories[_chunk_class(row, vector_ids)].append(chunk_id)
    for values in categories.values():
        values.sort()
    orphan = sorted(vector_ids - postgres_ids)
    counts = {
        "postgres_total": len(postgres_ids),
        "vector_total": len(vector_ids),
        **{key: len(value) for key, value in categories.items()},
        "orphan_vector": len(orphan),
    }
    projection = {
        "schema_version": "legal-data-reconciliation-v1",
        "collection_name": str(collection_name),
        "counts": counts,
        "category_chunk_ids": categories,
        "orphan_vector_chunk_ids": orphan,
        "validity_snapshot_sha256": (
            _canonical_sha256(validity_snapshot) if validity_snapshot else None
        ),
        "embedding_fingerprint": embedding_fingerprint,
        "pipeline_version": pipeline_version,
    }
    return {
        **projection,
        "manifest_fingerprint": _canonical_sha256(projection),
        "read_only": True,
        "corpus_mutated": False,
        "vectors_mutated": False,
        "partition_complete": sum(
            counts[key]
            for key in (
                "active_vectorized",
                "missing_vector",
                "historical",
                "staging",
                "duplicate",
                "inactive_other",
            )
        )
        == counts["postgres_total"],
        "missing_chunk_ids": categories["missing_vector"],
        "orphan_vector_chunk_ids": orphan,
        "embedding_fingerprint": embedding_fingerprint,
        "pipeline_version": pipeline_version,
    }
