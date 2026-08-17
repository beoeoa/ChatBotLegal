"""Deterministic, read-only vector serving reconciliation for Feature 018.

The module accepts already-read SQL expectations and vector observations.  It
does not import a database or vector client and therefore has no write path.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "feature018.vector-serving-manifest.v1"
SERVING_STATES = (
    "active",
    "historical",
    "staging",
    "missing",
    "orphan",
    "duplicate",
    "fingerprint_mismatch",
)
FINGERPRINT_KEYS = (
    "embedding",
    "splitter",
    "pipeline",
    "validity_snapshot",
)
HISTORICAL_STATUSES = {
    "expired",
    "replaced",
    "repealed",
    "superseded",
    "suspended",
    "historical",
    "archived",
    "inactive",
}
STAGING_STATUSES = {
    "staging",
    "draft",
    "submitted",
    "indexing",
    "pending",
    "candidate",
}
HISTORICAL_ACTIONS = {"historical_only", "block_document", "block_provisions"}


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _chunk_id(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _normalize_fingerprints(value: Mapping[str, Any] | None) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    aliases = {
        "embedding": ("embedding", "embedding_fingerprint", "model_fingerprint"),
        "splitter": ("splitter", "splitter_fingerprint"),
        "pipeline": ("pipeline", "pipeline_fingerprint", "pipeline_version"),
        "validity_snapshot": (
            "validity_snapshot",
            "validity_snapshot_sha256",
            "validity_fingerprint",
        ),
    }
    result: dict[str, str] = {}
    for key, candidates in aliases.items():
        result[key] = next(
            (_text(source.get(candidate)) for candidate in candidates if _text(source.get(candidate))),
            "",
        )
    return result


def _normalized_expected(row: Mapping[str, Any]) -> dict[str, Any] | None:
    chunk_id = _chunk_id(row.get("chunk_id"))
    if chunk_id is None:
        return None
    canonical = _chunk_id(row.get("canonical_chunk_id"))
    return {
        "chunk_id": chunk_id,
        "document_id": _text(row.get("document_id")),
        "document_status": _text(row.get("document_status") or "active").casefold(),
        "article_status": _text(row.get("article_status") or "active").casefold(),
        "serving_action": _text(row.get("serving_action") or "allow").casefold(),
        "included": row.get("included") is not False,
        "eligible": row.get("eligible") is not False,
        "canonical_chunk_id": canonical,
        "content_sha256": _text(row.get("content_sha256")) or None,
    }


def _normalized_collection(value: Mapping[str, Any]) -> dict[str, Any]:
    name = _text(value.get("name"))
    role = _text(value.get("role") or "other").casefold()
    collection_fingerprints = _normalize_fingerprints(value.get("fingerprints"))
    records: list[dict[str, Any]] = []
    for raw in value.get("records") or []:
        if not isinstance(raw, Mapping):
            continue
        chunk_id = _chunk_id(raw.get("chunk_id"))
        vector_id = _text(raw.get("vector_id"))
        if chunk_id is None and vector_id.startswith("chunk-"):
            chunk_id = _chunk_id(vector_id[6:])
        record_fingerprints = _normalize_fingerprints(raw.get("fingerprints"))
        effective = {
            key: record_fingerprints[key] or collection_fingerprints[key]
            for key in FINGERPRINT_KEYS
        }
        records.append(
            {
                "collection": name,
                "collection_role": role,
                "vector_id": vector_id,
                "chunk_id": chunk_id,
                "document_id": _text(raw.get("document_id")),
                "fingerprints": effective,
            }
        )
    records.sort(key=lambda item: (item["chunk_id"] or 0, item["vector_id"]))
    return {
        "name": name,
        "role": role,
        "fingerprints": collection_fingerprints,
        "records": records,
    }


def _fingerprint_findings(
    actual: Mapping[str, str], expected: Mapping[str, str]
) -> list[str]:
    findings: list[str] = []
    for key in FINGERPRINT_KEYS:
        expected_value = _text(expected.get(key))
        actual_value = _text(actual.get(key))
        if not expected_value:
            findings.append(f"expected_{key}_missing")
        elif not actual_value:
            findings.append(f"{key}_missing")
        elif actual_value != expected_value:
            findings.append(f"{key}_mismatch")
    return findings


def build_vector_serving_manifest(
    *,
    expected_chunks: Sequence[Mapping[str, Any]],
    collections: Sequence[Mapping[str, Any]],
    active_collection: str | None,
    expected_fingerprints: Mapping[str, Any],
    active_pointer_before: str | None,
    active_pointer_after: str | None,
) -> dict[str, Any]:
    """Classify canonical chunk expectations against immutable observations."""

    active_name = _text(active_collection)
    expected_fp = _normalize_fingerprints(expected_fingerprints)
    normalized_collections = sorted(
        (_normalized_collection(item) for item in collections),
        key=lambda item: item["name"],
    )
    collection_names = {item["name"] for item in normalized_collections}
    records = [
        record
        for collection in normalized_collections
        for record in collection["records"]
    ]
    by_chunk: dict[int, list[dict[str, Any]]] = defaultdict(list)
    invalid_vector_ids: list[str] = []
    for record in records:
        if record["chunk_id"] is None:
            invalid_vector_ids.append(record["vector_id"])
            continue
        by_chunk[record["chunk_id"]].append(record)

    normalized_expected = [
        item
        for row in expected_chunks
        if (item := _normalized_expected(row)) is not None
    ]
    expected_counts = Counter(item["chunk_id"] for item in normalized_expected)
    expected_by_chunk: dict[int, dict[str, Any]] = {}
    for item in sorted(normalized_expected, key=lambda row: row["chunk_id"]):
        expected_by_chunk.setdefault(item["chunk_id"], item)

    state_chunk_ids = {state: [] for state in SERVING_STATES}
    findings_by_chunk: dict[str, list[str]] = {}
    decisions: list[dict[str, Any]] = []
    safety_findings: list[str] = []

    for chunk_id, expectation in expected_by_chunk.items():
        observed = by_chunk.get(chunk_id, [])
        active_records = [item for item in observed if item["collection"] == active_name]
        findings: list[str] = []
        canonical = expectation["canonical_chunk_id"]

        if expected_counts[chunk_id] > 1:
            state = "duplicate"
            findings.append("duplicate_canonical_expectation")
        elif not expectation["eligible"] or (
            canonical is not None and canonical != chunk_id
        ):
            state = "duplicate"
            findings.append("noncanonical_or_ineligible_chunk")
        elif (
            expectation["serving_action"] in HISTORICAL_ACTIONS
            or expectation["document_status"] in HISTORICAL_STATUSES
            or expectation["article_status"] in HISTORICAL_STATUSES
        ):
            state = "historical"
            if active_records:
                findings.append("historical_vector_in_active")
                safety_findings.append(f"historical_vector_in_active:{chunk_id}")
        elif (
            expectation["document_status"] in STAGING_STATUSES
            or expectation["article_status"] in STAGING_STATUSES
            or not expectation["included"]
        ):
            state = "staging"
            if active_records:
                findings.append("staging_vector_in_active")
                safety_findings.append(f"staging_vector_in_active:{chunk_id}")
        elif len(active_records) > 1:
            state = "duplicate"
            findings.append("duplicate_active_vector")
        elif not active_records:
            state = "missing"
            findings.append("active_vector_missing")
            if any(item["collection_role"] == "staging" for item in observed):
                findings.append("staging_copy_present")
        else:
            findings.extend(
                _fingerprint_findings(active_records[0]["fingerprints"], expected_fp)
            )
            state = "fingerprint_mismatch" if findings else "active"

        state_chunk_ids[state].append(chunk_id)
        findings_by_chunk[str(chunk_id)] = sorted(set(findings))
        decisions.append(
            {
                "chunk_id": chunk_id,
                "document_id": expectation["document_id"],
                "state": state,
                "findings": sorted(set(findings)),
                "observed_collections": sorted(
                    {item["collection"] for item in observed if item["collection"]}
                ),
            }
        )

    expected_ids = set(expected_by_chunk)
    for chunk_id in sorted(set(by_chunk) - expected_ids):
        state_chunk_ids["orphan"].append(chunk_id)
        findings_by_chunk[str(chunk_id)] = ["vector_without_canonical_chunk"]
        decisions.append(
            {
                "chunk_id": chunk_id,
                "document_id": "",
                "state": "orphan",
                "findings": ["vector_without_canonical_chunk"],
                "observed_collections": sorted(
                    {item["collection"] for item in by_chunk[chunk_id]}
                ),
            }
        )

    for values in state_chunk_ids.values():
        values.sort()
    decisions.sort(key=lambda item: (item["chunk_id"], item["state"]))
    pointer_before = _text(active_pointer_before)
    pointer_after = _text(active_pointer_after)
    pointer_unchanged = pointer_before == pointer_after
    reason_codes: list[str] = []
    for state in ("missing", "orphan", "duplicate", "fingerprint_mismatch"):
        if state_chunk_ids[state]:
            reason_codes.append(f"{state}_detected")
    if safety_findings:
        reason_codes.append("noncurrent_vector_in_active")
    if not pointer_unchanged:
        reason_codes.append("active_pointer_changed")
    if not active_name:
        reason_codes.append("active_collection_missing")
    elif active_name not in collection_names:
        reason_codes.append("active_collection_not_observed")
    if invalid_vector_ids:
        reason_codes.append("unparseable_vector_id")
    if any(not expected_fp[key] for key in FINGERPRINT_KEYS):
        reason_codes.append("expected_fingerprint_incomplete")

    collection_inventory = [
        {
            "name": item["name"],
            "role": item["role"],
            "record_count": len(item["records"]),
            "fingerprints": item["fingerprints"],
            "record_inventory_sha256": canonical_sha256(
                [
                    {
                        "vector_id": record["vector_id"],
                        "chunk_id": record["chunk_id"],
                        "document_id": record["document_id"],
                        "fingerprints": record["fingerprints"],
                    }
                    for record in item["records"]
                ]
            ),
        }
        for item in normalized_collections
    ]
    inventory_projection = {
        "decisions": decisions,
        "collections": collection_inventory,
        "invalid_vector_ids": sorted(invalid_vector_ids),
    }
    inventory_sha256 = canonical_sha256(inventory_projection)
    counts = {state: len(state_chunk_ids[state]) for state in SERVING_STATES}
    counts.update(
        {
            "canonical_chunks": len(expected_by_chunk),
            "observed_vectors": len(records),
            "collections": len(normalized_collections),
            "invalid_vector_ids": len(invalid_vector_ids),
        }
    )
    deterministic_projection = {
        "schema_version": SCHEMA_VERSION,
        "active_collection": active_name,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "expected_fingerprints": expected_fp,
        "counts": counts,
        "state_chunk_ids": state_chunk_ids,
        "findings_by_chunk": findings_by_chunk,
        "collection_inventory": collection_inventory,
        "inventory_sha256": inventory_sha256,
        "reason_codes": sorted(set(reason_codes)),
    }
    gate_passed = not deterministic_projection["reason_codes"]
    return {
        **deterministic_projection,
        "manifest_fingerprint": canonical_sha256(deterministic_projection),
        "gate_passed": gate_passed,
        "read_only": True,
        "corpus_mutated": False,
        "vectors_mutated": False,
        "active_pointer_unchanged": pointer_unchanged,
        "write_operations_invoked": 0,
        "safety_findings": sorted(safety_findings),
        "invalid_vector_ids": sorted(invalid_vector_ids),
    }


__all__ = [
    "FINGERPRINT_KEYS",
    "SCHEMA_VERSION",
    "SERVING_STATES",
    "build_vector_serving_manifest",
    "canonical_sha256",
]
