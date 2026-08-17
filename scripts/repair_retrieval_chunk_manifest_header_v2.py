#!/usr/bin/env python3
"""Write a new draft with chunk-backed document/article header counts.

The chunk payload is copied byte-for-byte.  Only the compact draft header and
its manifest checksum are recomputed.  The source draft remains immutable.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256
from scripts.approve_retrieval_chunk_manifest_v2 import _load_manifest_header
from scripts.verify_retrieval_chunk_manifest_v2 import _canonical_bytes, _stream_header_and_chunks


MARKER = b',"chunks":['


def repair(*, source: Path, output: Path) -> dict[str, Any]:
    if source.resolve() == output.resolve():
        raise RuntimeError("repair_output_must_differ_from_source")
    header = _load_manifest_header(source)
    document_states: dict[int, str] = {}
    article_ids: set[int] = set()
    chunk_count = 0
    projection = hashlib.sha256()
    projection.update(b'{"chunks":[')
    first = True
    for kind, row in _stream_header_and_chunks(source):
        if kind != "chunk":
            continue
        chunk_count += 1
        document_id = int(row.get("document_id") or 0)
        article_ids.add(int(row.get("article_id") or 0))
        state = str(row.get("document_serving_state") or "")
        previous = document_states.get(document_id)
        if previous is not None and previous != state:
            raise RuntimeError(f"mixed_document_state:{document_id}")
        document_states[document_id] = state
        identity = {
            "chunk_revision_id": row.get("chunk_revision_id"),
            "document_id": row.get("document_id"),
            "article_id": row.get("article_id"),
            "content_sha256": row.get("content_sha256"),
            "embedding_text_sha256": row.get("embedding_text_sha256"),
            "passage_sha256": row.get("passage_sha256"),
            "token_count": row.get("token_count"),
        }
        if not first:
            projection.update(b",")
        projection.update(_canonical_bytes(identity))
        first = False
    state_counts = Counter(document_states.values())
    header["generated_at"] = datetime.now(timezone.utc).isoformat()
    header["included_document_count"] = len(document_states)
    header["included_article_count"] = len(article_ids)
    header["chunk_count"] = chunk_count
    header["vector_count"] = chunk_count
    if state_counts["current_retrievable"] != int(header.get("current_retrievable_document_count") or 0):
        raise RuntimeError("current_document_count_mismatch")
    if state_counts["historical_only"] != int(header.get("historical_only_document_count") or 0):
        raise RuntimeError("historical_document_count_mismatch")
    counts = {
        key: header.get(key)
        for key in (
            "inventory_document_count", "current_retrievable_document_count",
            "historical_only_document_count", "future_effective_document_count",
            "quarantined_document_count", "included_document_count",
            "included_article_count", "chunk_count", "vector_count",
        )
    }
    suffix = {
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
    projection.update(b"]")
    for key in sorted(suffix):
        projection.update(b",")
        projection.update(_canonical_bytes(key))
        projection.update(b":")
        projection.update(_canonical_bytes(suffix[key]))
    projection.update(b"}")
    header["manifest_sha256"] = projection.hexdigest()

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with source.open("rb") as src, temporary.open("wb") as dst:
        buffer = b""
        while MARKER not in buffer:
            block = src.read(1024 * 1024)
            if not block:
                raise RuntimeError("chunks_marker_missing")
            buffer += block
            if len(buffer) > 16 * 1024 * 1024:
                raise RuntimeError("manifest_header_too_large")
        _old_header, marker, tail = buffer.partition(MARKER)
        compact = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        dst.write(compact[:-1])
        dst.write(marker)
        dst.write(tail)
        shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
    temporary.replace(output)
    checksum = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n", encoding="ascii"
    )
    report = {
        "schema_version": "legal-retrieval-chunk-manifest-header-repair-v1",
        "source_manifest_file_sha256": file_sha256(source),
        "output_manifest_file_sha256": checksum,
        "manifest_sha256": header["manifest_sha256"],
        "included_document_count": len(document_states),
        "included_article_count": len(article_ids),
        "chunk_count": chunk_count,
        "document_state_counts": dict(state_counts),
        "chunk_payload_changed": False,
        "database_mutated": False,
        "chroma_mutated": False,
        "active_pointer_changed": False,
    }
    report_path = output.with_name(output.stem + ".repair-report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = repair(source=args.source.resolve(), output=args.output.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
