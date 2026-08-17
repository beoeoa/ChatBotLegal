"""Build a memory-bounded benchmark allowlist from the approved V2 release.

The approved chunk manifest is intentionally a complete legal artifact and is
large.  The benchmark runtime only needs its immutable release fingerprints and
the chunk identity/eligibility allowlist.  This script derives that allowlist
from the same Kaggle input shards and the read-only release SQLite index, while
retaining the approved manifest file and logical SHA-256 in the output.

This is a staging benchmark artifact.  It is not a replacement serving
manifest, does not write PostgreSQL/Chroma, and cannot be used to activate a
release by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _iter_input_rows(input_dir: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(input_dir.glob("input-*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                    raise RuntimeError(f"invalid_input_json:{path}:{line_number}") from exc
                yield row


def _identity_row(row: sqlite3.Row, input_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_revision_id": str(row["chunk_revision_id"]),
        "document_id": int(row["document_id"]),
        "article_id": int(row["article_id"]),
        "content_sha256": str(row["content_sha256"]),
        "embedding_text_sha256": str(input_row["embedding_text_sha256"]),
        "passage_sha256": str(row["passage_sha256"]),
        "token_count": int(row["token_count"]),
    }


def _update_list_sha(digest: hashlib._Hash, first: bool, value: dict[str, Any]) -> bool:
    if first:
        digest.update(b"[")
    else:
        digest.update(b",")
    digest.update(_canonical(value))
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--lexical-index", type=Path, required=True)
    parser.add_argument("--approved-manifest-file-sha256", required=True)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--serving-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    index_path = args.lexical_index.resolve()
    serving_path = args.serving_manifest.resolve()
    output_path = args.output.resolve()
    input_manifest_path = input_dir / "embedding-input-manifest.json"
    if not input_manifest_path.is_file() or not index_path.is_file() or not serving_path.is_file():
        raise SystemExit("required_release_artifact_missing")

    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    serving = json.loads(serving_path.read_text(encoding="utf-8"))
    expected_file_sha = str(args.approved_manifest_file_sha256)
    expected_manifest_sha = str(args.approved_manifest_sha256)
    if input_manifest.get("source_manifest_file_sha256") != expected_file_sha:
        raise SystemExit("input_source_manifest_file_sha_mismatch")
    if input_manifest.get("source_manifest_sha256") != expected_manifest_sha:
        raise SystemExit("input_source_manifest_sha_mismatch")
    if serving.get("chunk_manifest_sha256") != expected_manifest_sha:
        raise SystemExit("serving_chunk_manifest_sha_mismatch")

    input_rows: dict[str, dict[str, Any]] = {}
    for row in _iter_input_rows(input_dir):
        chunk_id = str(row.get("chunk_revision_id") or "")
        if not chunk_id or chunk_id in input_rows:
            raise SystemExit("input_chunk_id_missing_or_duplicate")
        input_rows[chunk_id] = {
            "document_id": int(row["document_id"]),
            "article_id": int(row["article_id"]),
            "document_serving_state": str(row["document_serving_state"]),
            "embedding_text_sha256": str(row["embedding_text_sha256"]),
            "token_count": int(row["token_count"]),
        }

    db = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows_sql = (
        "SELECT chunk_revision_id,document_id,article_id,document_serving_state,"
        "content_sha256,passage_sha256,token_count "
        "FROM chunks ORDER BY chunk_revision_id"
    )
    count = 0
    state_counts: dict[str, int] = {}
    digest = hashlib.sha256()
    first = True
    for row in db.execute(rows_sql):
        chunk_id = str(row["chunk_revision_id"])
        input_row = input_rows.get(chunk_id)
        if input_row is None:
            raise SystemExit(f"input_missing_chunk:{chunk_id}")
        for key in ("document_id", "article_id", "token_count"):
            if int(row[key]) != int(input_row[key]):
                raise SystemExit(f"input_sqlite_identity_mismatch:{chunk_id}:{key}")
        if str(row["document_serving_state"]) != input_row["document_serving_state"]:
            raise SystemExit(f"input_sqlite_state_mismatch:{chunk_id}")
        identity = _identity_row(row, input_row)
        first = _update_list_sha(digest, first, identity)
        count += 1
        state = str(row["document_serving_state"])
        state_counts[state] = state_counts.get(state, 0) + 1
    digest.update(b"]")
    if count != len(input_rows):
        raise SystemExit(f"input_sqlite_count_mismatch:{len(input_rows)}:{count}")
    chunk_identity_sha = digest.hexdigest()

    release_id = str(input_manifest.get("release_id") or "")
    if not release_id or release_id != str(serving.get("release_id") or ""):
        raise SystemExit("release_id_mismatch")
    top: dict[str, Any] = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "release_id": release_id,
        "dataset_version": str(input_manifest.get("dataset_version") or ""),
        "legal_as_of": str(serving.get("legal_as_of") or ""),
        "source_snapshot_sha256": str(input_manifest.get("source_snapshot_sha256") or ""),
        "source_manifest_path": str(input_manifest.get("source_manifest_path") or ""),
        "source_manifest_file_sha256": expected_file_sha,
        "manifest_sha256": expected_manifest_sha,
        "approved": True,
        "legal_review_attestation": True,
        "model_artifact_fingerprint": str(input_manifest.get("model_artifact_fingerprint") or ""),
        "tokenizer_fingerprint": str(input_manifest.get("tokenizer_fingerprint") or ""),
        "embedding_recipe_fingerprint": str(input_manifest.get("embedding_recipe_fingerprint") or ""),
        "passage_recipe_fingerprint": str(input_manifest.get("passage_recipe_fingerprint") or ""),
        "splitter_fingerprint": str(input_manifest.get("splitter_fingerprint") or ""),
        "dependency_lock_fingerprint": str(input_manifest.get("dependency_lock_fingerprint") or ""),
        "quality_policy_version": str(input_manifest.get("quality_policy_version") or ""),
        "chunk_count": count,
        "state_counts": state_counts,
        "chunk_identity_sha256": chunk_identity_sha,
        "derived_allowlist": {
            "kind": "benchmark_allowlist_only",
            "approved_manifest_file_sha256": expected_file_sha,
            "approved_manifest_sha256": expected_manifest_sha,
            "input_manifest_file_sha256": _sha256_file(input_manifest_path),
            "lexical_index_file_sha256": _sha256_file(index_path),
            "active_pointer_changed": False,
            "chroma_mutated": False,
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(top, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:-1])
        handle.write(',"chunks":[')
        first = True
        for row in db.execute(rows_sql):
            chunk_id = str(row["chunk_revision_id"])
            input_row = input_rows[chunk_id]
            item = _identity_row(row, input_row)
            item.update({"eligible": True, "serving_state": "retrievable", "release_id": release_id})
            if not first:
                handle.write(",")
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            first = False
        handle.write("]}")
    db.close()
    print(json.dumps({
        "status": "PASS",
        "output": str(output_path),
        "output_file_sha256": _sha256_file(output_path),
        "chunk_count": count,
        "state_counts": state_counts,
        "chunk_identity_sha256": chunk_identity_sha,
        "approved_manifest_file_sha256": expected_file_sha,
        "approved_manifest_sha256": expected_manifest_sha,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
