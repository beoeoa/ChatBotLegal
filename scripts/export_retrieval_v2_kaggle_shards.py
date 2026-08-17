#!/usr/bin/env python3
"""Export checksum-bound V2 passage shards for an isolated Kaggle GPU job.

The exporter never reads or mutates Chroma.  A draft manifest is accepted only
with ``--allow-provisional-staging`` and produces artifacts that are explicitly
not release eligible.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256


SCHEMA_VERSION = "legal-retrieval-kaggle-input-v2"
LEGACY_SMOKE_SCHEMA_VERSION = "legal-retrieval-kaggle-input-v1"
MAX_LENGTH = 512
ALLOWED_STATES = {"current_retrievable", "historical_only"}
FINGERPRINT_KEYS = (
    "model_artifact_fingerprint",
    "tokenizer_fingerprint",
    "embedding_recipe_fingerprint",
    "passage_recipe_fingerprint",
    "splitter_fingerprint",
    "dependency_lock_fingerprint",
)
WORKER_PATH = ROOT / "scripts" / "kaggle_retrieval_v2_worker.py"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _streaming_header(path: Path) -> tuple[dict[str, Any], bool]:
    """Read top-level contract fields without materialising the chunks array."""

    try:
        import ijson  # type: ignore[import-not-found]
    except ImportError:
        return _load(path), False
    header: dict[str, Any] = {}
    with path.open("rb") as stream:
        for prefix, event, value in ijson.parse(stream):
            if prefix == "chunks" and event == "start_array":
                break
            if "." not in prefix and event in {
                "string",
                "number",
                "boolean",
                "null",
            }:
                header[prefix] = value
    return header, True


def _streaming_chunks(
    path: Path, payload: dict[str, Any], *, streaming: bool
) -> Iterator[dict[str, Any]]:
    if not streaming:
        yield from (dict(row) for row in payload.get("chunks") or [])
        return
    import ijson  # type: ignore[import-not-found]

    with path.open("rb") as stream:
        for row in ijson.items(stream, "chunks.item"):
            yield dict(row)


def _validate_manifest(payload: dict[str, Any], *, allow_provisional_staging: bool) -> bool:
    if payload.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        raise RuntimeError("v2_manifest_required")
    provisional = payload.get("approved") is not True
    if provisional and not allow_provisional_staging:
        raise RuntimeError("approved_v2_manifest_required")
    if provisional and (
        payload.get("legal_review_attestation") is not False
        or not str(payload.get("approval_blocker") or "").strip()
    ):
        raise RuntimeError("provisional_manifest_review_marker_required")
    for key in ("release_id", "source_snapshot_sha256", *FINGERPRINT_KEYS):
        if not str(payload.get(key) or "").strip():
            raise RuntimeError(f"manifest_contract_missing:{key}")
    return provisional


def _eligible_rows(
    payload: dict[str, Any], raw_rows: Iterable[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows if raw_rows is not None else payload.get("chunks") or []:
        row = dict(raw)
        if row.get("eligible") is not True or row.get("serving_state") != "retrievable":
            continue
        identifier = str(row.get("chunk_revision_id") or "").strip()
        if not identifier:
            raise RuntimeError("chunk_revision_id_required")
        if identifier in seen:
            raise RuntimeError(f"duplicate_chunk_revision_id:{identifier}")
        seen.add(identifier)
        state = str(row.get("document_serving_state") or "")
        if state not in ALLOWED_STATES:
            raise RuntimeError(f"invalid_document_serving_state:{identifier}:{state}")
        text = str(row.get("embedding_text") or "")
        if not text.strip():
            raise RuntimeError(f"empty_embedding_text:{identifier}")
        expected_sha = str(row.get("embedding_text_sha256") or "")
        actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if expected_sha != actual_sha:
            raise RuntimeError(f"embedding_text_checksum_mismatch:{identifier}")
        token_count = int(row.get("token_count") or 0)
        if token_count < 1 or token_count > MAX_LENGTH:
            raise RuntimeError(f"embedding_token_budget:{identifier}:{token_count}")
        if str(row.get("release_id") or "") != str(payload["release_id"]):
            raise RuntimeError(f"chunk_release_mismatch:{identifier}")
        rows.append(
            {
                "chunk_revision_id": identifier,
                "document_id": int(row["document_id"]),
                "article_id": int(row["article_id"]),
                "document_serving_state": state,
                "embedding_text": text,
                "embedding_text_sha256": expected_sha,
                "token_count": token_count,
                # Preserve only source metadata already present in the V2
                # manifest.  The Kaggle worker treats this as opaque data; it
                # is needed locally to rebuild temporal and citation filters.
                "metadata": dict(row.get("metadata") or {}),
            }
        )
    if not rows:
        raise RuntimeError("no_eligible_v2_chunks")
    return rows


def export_kaggle_input(
    *,
    manifest_path: Path,
    output_dir: Path,
    shard_size: int = 25_000,
    allow_provisional_staging: bool = False,
    limit: int | None = None,
    embedding_job_id: str | None = None,
    kaggle_kernel_version: str = "retrieval-v2-kernel-bundle-v2",
    accelerator: str = "NvidiaTeslaT4",
) -> dict[str, Any]:
    if shard_size < 1:
        raise ValueError("shard_size_must_be_positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    input_manifest_path = output_dir / "embedding-input-manifest.json"
    if input_manifest_path.exists() or any(output_dir.glob("input-*.jsonl")):
        raise RuntimeError(f"output_artifacts_exist:{output_dir}")

    payload, streaming = _streaming_header(manifest_path)
    provisional = _validate_manifest(
        payload, allow_provisional_staging=allow_provisional_staging
    )
    if not provisional and not str(embedding_job_id or "").strip():
        raise RuntimeError("approved_embedding_job_id_required")
    smoke_limited = limit is not None
    if limit is not None:
        if limit < 1:
            raise ValueError("limit_must_be_positive")

    shards: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    seen: set[str] = set()
    chunk_count = 0
    shard_index = -1
    shard_stream: Any = None
    shard_path: Path | None = None
    shard_count = 0
    shard_first: str | None = None
    shard_last: str | None = None

    def close_shard() -> None:
        nonlocal shard_stream, shard_path, shard_count, shard_first, shard_last
        if shard_stream is None or shard_path is None:
            return
        shard_stream.close()
        shards.append(
            {
                "path": shard_path.name,
                "count": shard_count,
                "sha256": file_sha256(shard_path),
                "first_chunk_revision_id": shard_first,
                "last_chunk_revision_id": shard_last,
            }
        )
        shard_stream = None
        shard_path = None
        shard_count = 0
        shard_first = None
        shard_last = None

    try:
        raw_rows = _streaming_chunks(manifest_path, payload, streaming=streaming)
        for raw in raw_rows:
            if raw.get("eligible") is not True or raw.get("serving_state") != "retrievable":
                continue
            normalized = _eligible_rows(payload, [raw])[0]
            identifier = normalized["chunk_revision_id"]
            if identifier in seen:
                raise RuntimeError(f"duplicate_chunk_revision_id:{identifier}")
            seen.add(identifier)
            if shard_stream is None:
                shard_index += 1
                shard_path = output_dir / f"input-{shard_index:05d}.jsonl"
                shard_stream = shard_path.open("w", encoding="utf-8", newline="\n")
            shard_stream.write(
                json.dumps(
                    normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            shard_first = shard_first or identifier
            shard_last = identifier
            shard_count += 1
            chunk_count += 1
            state_counts[normalized["document_serving_state"]] += 1
            if shard_count >= shard_size:
                close_shard()
            if limit is not None and chunk_count >= int(limit):
                break
    finally:
        close_shard()
    if chunk_count == 0:
        raise RuntimeError("no_eligible_v2_chunks")

    resolved_job_id = str(embedding_job_id or "").strip()
    if not resolved_job_id:
        # Provisional smoke exports remain reproducible without pretending to
        # be the final logical job.  A release export must provide this ID
        # explicitly so checkpoints cannot silently cross releases.
        resolved_job_id = (
            f"provisional-{payload['release_id']}-"
            f"{str(payload.get('manifest_sha256') or '')[:16]}"
        )
    if not resolved_job_id or len(resolved_job_id) > 200:
        raise RuntimeError("embedding_job_id_invalid")
    worker_fingerprint = file_sha256(WORKER_PATH)
    if len(worker_fingerprint) != 64:
        raise RuntimeError("worker_fingerprint_missing")

    report: dict[str, Any] = {
        # Existing provisional/smoke artifacts remain readable and immutable.
        # Only an approved, non-limited export is issued as the V2 contract.
        "schema_version": (
            LEGACY_SMOKE_SCHEMA_VERSION
            if provisional or smoke_limited
            else SCHEMA_VERSION
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": payload["release_id"],
        "dataset_version": payload.get("dataset_version"),
        "source_manifest_path": str(manifest_path.resolve()),
        "source_manifest_file_sha256": file_sha256(manifest_path),
        "source_manifest_sha256": payload.get("manifest_sha256"),
        "source_snapshot_sha256": payload["source_snapshot_sha256"],
        "embedding_job_id": resolved_job_id,
        "worker_fingerprint": worker_fingerprint,
        "kaggle_kernel_version": str(kaggle_kernel_version or "").strip(),
        "accelerator": str(accelerator or "").strip(),
        "internet_enabled": False,
        "quality_policy_version": payload.get("quality_policy_version"),
        **{key: payload[key] for key in FINGERPRINT_KEYS},
        "embedding_dimension": 1024,
        "embedding_max_length": MAX_LENGTH,
        "embedding_dtype": "float32",
        "chunk_count": chunk_count,
        "document_state_counts": dict(sorted(state_counts.items())),
        "shard_size": shard_size,
        "shards": shards,
        "provisional_staging": provisional or smoke_limited,
        "release_eligible": bool(payload.get("approved") is True and not smoke_limited),
        "smoke_limited": smoke_limited,
        "active_pointer_changed": False,
        "chroma_mutated": False,
    }
    input_manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "embedding-input-manifest.json.sha256").write_text(
        f"{file_sha256(input_manifest_path)}  {input_manifest_path.name}\n",
        encoding="ascii",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--shard-size", type=int, default=25_000)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--allow-provisional-staging", action="store_true")
    parser.add_argument("--embedding-job-id")
    parser.add_argument("--kaggle-kernel-version", default="retrieval-v2-kernel-bundle-v2")
    parser.add_argument("--accelerator", default="NvidiaTeslaT4")
    args = parser.parse_args(argv)
    report = export_kaggle_input(
        manifest_path=args.manifest.resolve(),
        output_dir=args.output_dir.resolve(),
        shard_size=args.shard_size,
        allow_provisional_staging=args.allow_provisional_staging,
        limit=args.limit,
        embedding_job_id=args.embedding_job_id,
        kaggle_kernel_version=args.kaggle_kernel_version,
        accelerator=args.accelerator,
    )
    print(
        json.dumps(
            {
                "status": "PROVISIONAL_EXPORTED"
                if report["provisional_staging"]
                else "EXPORTED",
                "chunk_count": report["chunk_count"],
                "shard_count": len(report["shards"]),
                "output_dir": str(args.output_dir.resolve()),
                "active_pointer_changed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
