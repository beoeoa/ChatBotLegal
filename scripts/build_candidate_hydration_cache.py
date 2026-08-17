"""Materialize immutable candidate chunk rows for benchmark hydration.

Rows are fetched from PostgreSQL through the production retriever's existing
quality/status/scope SQL, then stored with the candidate manifest checksum.
The cache is an acceleration layer only; it never writes PostgreSQL or Chroma
and is rejected when used with another manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pickle
import sys

from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("status") != "ready_for_candidate_build" or manifest.get("required_source_blockers"):
        raise RuntimeError("candidate_manifest_not_ready")
    import os
    os.environ["LEGAL_BENCHMARK_MODE"] = "1"
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST"] = str(manifest_path)
    os.environ["LEGAL_CHROMA_COLLECTION"] = str(manifest["candidate_collection"])
    os.environ["LEGAL_CHROMA_SOURCE_COLLECTION"] = str(manifest["candidate_collection"])
    from scripts.legal_search_server import retriever

    document_ids = {int(row["document_id"]) for row in manifest["documents"]}
    chunk_ids = [
        int(value)
        for row in manifest["documents"]
        for value in (row.get("expected_chunk_ids") or [])
    ]
    retriever._serving_allowed_document_ids = document_ids
    retriever._shadow_allowed_chunk_ids = {"core": set(chunk_ids), "expanded": set(chunk_ids)}
    retriever._benchmark_allow_staging = True
    rows: dict[int, dict] = {}
    batch_size = max(100, int(args.batch_size))
    for start in range(0, len(chunk_ids), batch_size):
        batch = chunk_ids[start : start + batch_size]
        fetched = retriever._fetch_chunks(batch, "core")
        for row in fetched:
            rows[int(row["chunk_id"])] = dict(row)
        print(f"fetched={min(start + batch_size, len(chunk_ids))}/{len(chunk_ids)}", file=sys.stderr)
    missing = sorted(set(chunk_ids) - set(rows))
    article_ids = sorted({int(row["article_id"]) for row in rows.values() if row.get("article_id")})
    parents: dict[int, dict] = {}
    for start in range(0, len(article_ids), batch_size):
        batch = article_ids[start : start + batch_size]
        for row in retriever._fetch_parent_contexts(batch):
            parents[int(row["article_id"])] = dict(row)
    article_chunk_counts: dict[int, int] = {}
    if article_ids:
        statement = text(
            "SELECT article_id, COUNT(*) AS chunk_count "
            "FROM legal_article_chunks "
            "WHERE article_id IN :article_ids "
            "GROUP BY article_id"
        ).bindparams(bindparam("article_ids", expanding=True))
        with retriever._engine.connect() as connection:
            article_chunk_counts = {
                int(row["article_id"]): int(row["chunk_count"] or 0)
                for row in connection.execute(statement, {"article_ids": article_ids}).mappings()
            }
    payload = {
        "schema_version": "legal-candidate-hydration-cache-v2",
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": sha256(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "collection": manifest.get("candidate_collection"),
        "row_count": len(rows),
        "missing_chunk_ids": missing,
        "parent_count": len(parents),
        "parents": parents,
        "article_chunk_counts": article_chunk_counts,
        "rows": rows,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
    print(json.dumps({"output": str(output), "row_count": len(rows), "missing": len(missing)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
