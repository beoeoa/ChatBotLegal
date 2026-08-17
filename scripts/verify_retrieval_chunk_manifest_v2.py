#!/usr/bin/env python3
"""Verify the structural/provenance gates of a V2 chunk manifest."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256

DEFAULT_SCHEMA = (
    ROOT
    / "specs"
    / "018-production-release-readiness"
    / "contracts"
    / "retrieval-chunk-manifest-v2.schema.json"
)
STREAMING_THRESHOLD_BYTES = 512 * 1024 * 1024


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _stream_header_and_chunks(path: Path):
    """Yield the compact header first and then each chunk without full loading."""

    marker = ',"chunks":['
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8-sig") as handle:
        buffer = ""
        while marker not in buffer:
            block = handle.read(1024 * 1024)
            if not block:
                raise RuntimeError("chunks_marker_missing")
            buffer += block
            if len(buffer) > 16 * 1024 * 1024:
                raise RuntimeError("manifest_header_too_large")
        header_text, buffer = buffer.split(marker, 1)
        header = json.loads(header_text + "}")
        yield ("header", header)
        position = 0
        while True:
            while position < len(buffer) and buffer[position] in " \r\n\t,":
                position += 1
            if position < len(buffer) and buffer[position] == "]":
                yield ("end", None)
                return
            try:
                row, end = decoder.raw_decode(buffer, position)
            except json.JSONDecodeError:
                if position:
                    buffer = buffer[position:]
                    position = 0
                block = handle.read(1024 * 1024)
                if not block:
                    raise RuntimeError("truncated_chunks_array")
                buffer += block
                continue
            if not isinstance(row, dict):
                raise RuntimeError("chunk_object_required")
            yield ("chunk", row)
            position = end
            if position > 4 * 1024 * 1024:
                buffer = buffer[position:]
                position = 0


def _verify_streaming(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    invalid: set[str] = set()
    ids: set[str] = set()
    content_seen: defaultdict[int, set[str]] = defaultdict(set)
    document_states: dict[int, str] = {}
    article_ids: set[int] = set()
    state_counts: Counter[str] = Counter()
    max_tokens = 0
    min_tokens = None
    chunk_count = 0
    header: dict[str, Any] | None = None
    projection_digest = hashlib.sha256()
    projection_digest.update(b'{"chunks":[')
    first_projection = True
    required = (
        "release_id", "document_id", "article_id", "structural_path",
        "embedding_text", "embedding_text_sha256", "source_content_sha256",
        "passage_sha256", "quality_policy_version",
    )
    try:
        for kind, value in _stream_header_and_chunks(path):
            if kind == "header":
                header = value
                if header.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
                    errors.append("schema_version")
                continue
            if kind == "end":
                break
            row = value
            chunk_count += 1
            identifier = str(row.get("chunk_revision_id") or "")
            if not identifier or identifier in ids:
                invalid.add(identifier)
            ids.add(identifier)
            content = str(row.get("content") or "")
            content_sha = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
            token_count = int(row.get("token_count") or 0)
            max_tokens = max(max_tokens, token_count)
            min_tokens = token_count if min_tokens is None else min(min_tokens, token_count)
            if not content.strip() or token_count < 1 or token_count > 512:
                invalid.add(identifier)
            if content_sha != row.get("content_sha256"):
                invalid.add(identifier)
            document_id = int(row.get("document_id") or 0)
            article_id = int(row.get("article_id") or 0)
            article_ids.add(article_id)
            row_document_state = str(row.get("document_serving_state") or "")
            prior_state = document_states.get(document_id)
            if prior_state is not None and prior_state != row_document_state:
                invalid.add(identifier)
            document_states[document_id] = row_document_state
            if content_sha in content_seen[document_id]:
                invalid.add(identifier)
            content_seen[document_id].add(content_sha)
            if any(row.get(key) in (None, "") for key in required):
                invalid.add(identifier)
            if row.get("eligible") is not True or row.get("serving_state") != "retrievable":
                invalid.add(identifier)
            state_counts[str(row.get("document_serving_state") or "")] += 1
            identity = {
                "chunk_revision_id": row.get("chunk_revision_id"),
                "document_id": row.get("document_id"),
                "article_id": row.get("article_id"),
                "content_sha256": row.get("content_sha256"),
                "embedding_text_sha256": row.get("embedding_text_sha256"),
                "passage_sha256": row.get("passage_sha256"),
                "token_count": row.get("token_count"),
            }
            if not first_projection:
                projection_digest.update(b",")
            projection_digest.update(_canonical_bytes(identity))
            first_projection = False
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"stream_parse:{type(exc).__name__}:{exc}")
    if header is None:
        header = {}
    expected_count = int(header.get("chunk_count") or 0)
    if expected_count != chunk_count or int(header.get("vector_count") or 0) != chunk_count:
        errors.append("count_matches_chunks")
    if int(header.get("included_document_count") or 0) != len(document_states):
        errors.append("included_document_count_matches_chunks")
    if int(header.get("included_article_count") or 0) != len(article_ids):
        errors.append("included_article_count_matches_chunks")
    document_state_counts = Counter(document_states.values())
    if (
        document_state_counts["current_retrievable"]
        != int(header.get("current_retrievable_document_count") or 0)
        or document_state_counts["historical_only"]
        != int(header.get("historical_only_document_count") or 0)
    ):
        errors.append("document_state_counts_match_header")
    if state_counts["current_retrievable"] + state_counts["historical_only"] != chunk_count:
        errors.append("chunk_document_state_partition")
    if invalid:
        errors.append("chunk_quality_or_provenance")
    counts = {
        key: header.get(key)
        for key in (
            "inventory_document_count", "current_retrievable_document_count",
            "historical_only_document_count", "future_effective_document_count",
            "quarantined_document_count", "included_document_count",
            "included_article_count", "chunk_count", "vector_count",
        )
    }
    partition = sum(int(counts.get(key) or 0) for key in (
        "current_retrievable_document_count", "historical_only_document_count",
        "future_effective_document_count", "quarantined_document_count",
    ))
    if int(counts.get("inventory_document_count") or 0) != 12_236 or partition != 12_236:
        errors.append("inventory_partition_12236")
    projection_suffix = {
        "counts": counts,
        "document_state_filter": header.get("document_state_filter"),
        "fingerprints": {
            key: header.get(key)
            for key in (
                "model_artifact_fingerprint", "tokenizer_fingerprint",
                "embedding_recipe_fingerprint", "passage_recipe_fingerprint",
                "splitter_fingerprint", "dependency_lock_fingerprint",
            )
        },
        "metadata_overlay_attestation_sha256": header.get("metadata_overlay_attestation_sha256"),
        "release_id": header.get("release_id"),
        "source_snapshot_sha256": header.get("source_snapshot_sha256"),
    }
    projection_digest.update(b"]")
    for key in sorted(projection_suffix):
        projection_digest.update(b",")
        projection_digest.update(_canonical_bytes(key))
        projection_digest.update(b":")
        projection_digest.update(_canonical_bytes(projection_suffix[key]))
    projection_digest.update(b"}")
    declared = str(header.get("manifest_sha256") or "")
    if declared != projection_digest.hexdigest():
        errors.append("manifest_sha256")
    return {
        "schema_version": "legal-retrieval-chunk-manifest-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(path.resolve()),
        "manifest_file_sha256": file_sha256(path),
        "manifest_sha256": declared,
        "chunk_count": chunk_count,
        "state_counts": dict(state_counts),
        "document_state_counts": dict(document_state_counts),
        "included_document_count": len(document_states),
        "included_article_count": len(article_ids),
        "max_token_count": max_tokens,
        "min_token_count": min_tokens,
        "invalid_chunk_count": len(invalid),
        "invalid_chunk_ids_sample": sorted(invalid)[:100],
        "approved": bool(header.get("approved")),
        "legal_review_attestation": bool(header.get("legal_review_attestation")),
        "structural_gate_passed": not errors,
        "release_gate_passed": not errors and header.get("approved") is True and header.get("legal_review_attestation") is True,
        "errors": sorted(set(errors)),
        "validation_mode": "streaming_contract_checks",
        "mutation": {"database_mutated": False, "vector_collections_mutated": False, "active_pointer_changed": False},
    }


def _schema_errors(payload: dict[str, Any], schema_path: Path) -> list[str]:
    """Return a bounded list of JSON-Schema errors for the manifest.

    The manifest is intentionally large, so do not materialize every error.
    The hand-written checks below remain useful for release-specific invariants
    (for example the 12,236-document partition), while this check keeps the
    file contract aligned with the checked-in schema.
    """

    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema_dependency_missing"]
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"schema_unreadable:{type(exc).__name__}"]
    errors: list[str] = []
    for error in validator.iter_errors(payload):
        location = ".".join(str(item) for item in error.absolute_path)
        errors.append(f"{location or '$'}:{error.validator}:{error.message}")
        if len(errors) >= 20:
            break
    return errors


def verify(path: Path, *, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    if path.stat().st_size >= STREAMING_THRESHOLD_BYTES:
        return _verify_streaming(path)
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    errors.extend(f"schema:{item}" for item in _schema_errors(payload, schema_path))
    if payload.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        errors.append("schema_version")
    chunks = [row for row in payload.get("chunks") or [] if isinstance(row, dict)]
    ids = [str(row.get("chunk_revision_id") or "") for row in chunks]
    if not ids or len(ids) != len(set(ids)):
        errors.append("chunk_revision_id_unique")
    expected_count = int(payload.get("chunk_count") or 0)
    if expected_count != len(chunks) or int(payload.get("vector_count") or 0) != len(chunks):
        errors.append("count_matches_chunks")
    state_counts = Counter(str(row.get("document_serving_state") or "") for row in chunks)
    if state_counts["current_retrievable"] + state_counts["historical_only"] != len(chunks):
        errors.append("chunk_document_state_partition")
    content_seen: defaultdict[int, set[str]] = defaultdict(set)
    invalid: list[str] = []
    max_tokens = 0
    min_tokens = None
    for row in chunks:
        identifier = str(row.get("chunk_revision_id") or "")
        content = str(row.get("content") or "")
        content_sha = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        token_count = int(row.get("token_count") or 0)
        max_tokens = max(max_tokens, token_count)
        min_tokens = token_count if min_tokens is None else min(min_tokens, token_count)
        if not content.strip() or token_count < 1 or token_count > 512:
            invalid.append(identifier)
        if content_sha != row.get("content_sha256"):
            invalid.append(identifier)
        document_id = int(row.get("document_id") or 0)
        if content_sha in content_seen[document_id]:
            invalid.append(identifier)
        content_seen[document_id].add(content_sha)
        required = (
            "release_id", "document_id", "article_id", "structural_path",
            "embedding_text", "embedding_text_sha256", "source_content_sha256",
            "passage_sha256", "quality_policy_version",
        )
        if any(row.get(key) in (None, "") for key in required):
            invalid.append(identifier)
        if row.get("eligible") is not True or row.get("serving_state") != "retrievable":
            invalid.append(identifier)
    if invalid:
        errors.append("chunk_quality_or_provenance")
    # The streamed builder keeps the count fields flat in the manifest header
    # (so it does not duplicate a large header object in memory).  Accept the
    # canonical flat form and the nested form used by small fixtures.
    counts = payload.get("counts") or {
        key: payload.get(key)
        for key in (
            "inventory_document_count",
            "current_retrievable_document_count",
            "historical_only_document_count",
            "future_effective_document_count",
            "quarantined_document_count",
            "included_document_count",
            "included_article_count",
            "chunk_count",
            "vector_count",
        )
        if payload.get(key) is not None
    }
    partition = (
        int(counts.get("current_retrievable_document_count") or 0)
        + int(counts.get("historical_only_document_count") or 0)
        + int(counts.get("future_effective_document_count") or 0)
        + int(counts.get("quarantined_document_count") or 0)
    )
    if int(counts.get("inventory_document_count") or 0) != 12_236 or partition != 12_236:
        errors.append("inventory_partition_12236")
    projection = {
        "release_id": payload.get("release_id"),
        "source_snapshot_sha256": payload.get("source_snapshot_sha256"),
        "metadata_overlay_attestation_sha256": payload.get("metadata_overlay_attestation_sha256"),
        "document_state_filter": payload.get("document_state_filter"),
        "counts": counts,
        "chunks": [
            {
                "chunk_revision_id": row.get("chunk_revision_id"),
                "document_id": row.get("document_id"),
                "article_id": row.get("article_id"),
                "content_sha256": row.get("content_sha256"),
                "embedding_text_sha256": row.get("embedding_text_sha256"),
                "passage_sha256": row.get("passage_sha256"),
                "token_count": row.get("token_count"),
            }
            for row in chunks
        ],
        "fingerprints": {
            key: payload.get(key)
            for key in (
                "model_artifact_fingerprint", "tokenizer_fingerprint",
                "embedding_recipe_fingerprint", "passage_recipe_fingerprint",
                "splitter_fingerprint", "dependency_lock_fingerprint",
            )
        },
    }
    declared = str(payload.get("manifest_sha256") or "")
    if declared != canonical_sha256(projection):
        errors.append("manifest_sha256")
    report = {
        "schema_version": "legal-retrieval-chunk-manifest-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(path.resolve()),
        "manifest_file_sha256": file_sha256(path),
        "manifest_sha256": declared,
        "chunk_count": len(chunks),
        "state_counts": dict(state_counts),
        "max_token_count": max_tokens,
        "min_token_count": min_tokens,
        "invalid_chunk_count": len(set(invalid)),
        "invalid_chunk_ids_sample": sorted(set(invalid))[:100],
        "approved": bool(payload.get("approved")),
        "legal_review_attestation": bool(payload.get("legal_review_attestation")),
        "structural_gate_passed": not errors,
        "release_gate_passed": not errors and payload.get("approved") is True and payload.get("legal_review_attestation") is True,
        "errors": sorted(set(errors)),
        "mutation": {"database_mutated": False, "vector_collections_mutated": False, "active_pointer_changed": False},
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    report = verify(args.manifest.resolve(), schema_path=args.schema.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PASS" if report["release_gate_passed"] else "DRAFT_STRUCTURAL_PASS" if report["structural_gate_passed"] else "FAIL",
        "chunk_count": report["chunk_count"],
        "structural_gate_passed": report["structural_gate_passed"],
        "release_gate_passed": report["release_gate_passed"],
        "errors": report["errors"],
    }, ensure_ascii=False))
    return 0 if report["structural_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
