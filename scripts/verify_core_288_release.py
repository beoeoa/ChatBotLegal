"""Fail-closed verification for the portable 288-document legal release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_serving_scope import load_serving_manifest_pointer  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payload(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _member(root: Path, raw: object) -> Path:
    relative = Path(str(raw or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe core release path: {relative}")
    resolved = (root / relative).resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"Core release path escapes root: {relative}")
    return resolved


def verify_core_release(
    release_root: Path, *, verify_postgres_dump: bool = True
) -> dict[str, Any]:
    release_root = release_root.resolve()
    descriptor = _payload(release_root / "legal/core-288-release.json")
    if descriptor.get("schema_version") != "chatbotlegal-core-release-v1":
        raise ValueError("Unsupported core release descriptor")

    collection = str(descriptor.get("collection_name") or "")
    pointer_path = _member(release_root, descriptor.get("serving_pointer"))
    serving = load_serving_manifest_pointer(
        pointer_path, configured_collection=collection
    )
    expected_documents = int(descriptor.get("baseline_document_count") or 0)
    expected_chunks = int(descriptor.get("baseline_chunk_count") or 0)
    if len(serving.document_ids) != expected_documents:
        raise ValueError("Core serving document count mismatch")
    if len(serving.chunk_ids) != expected_chunks:
        raise ValueError("Core serving chunk count mismatch")

    if verify_postgres_dump:
        dump_path = _member(release_root, descriptor.get("postgres_dump"))
        if dump_path.stat().st_size != int(descriptor.get("postgres_dump_bytes") or -1):
            raise ValueError("Core PostgreSQL dump size mismatch")
        if _sha256(dump_path) != str(descriptor.get("postgres_dump_sha256") or ""):
            raise ValueError("Core PostgreSQL dump checksum mismatch")

    chroma_root = _member(release_root, descriptor.get("chroma_directory"))
    chroma_files = [path for path in chroma_root.rglob("*") if path.is_file()]
    if len(chroma_files) != int(descriptor.get("chroma_file_count") or -1):
        raise ValueError("Core Chroma file count mismatch")
    if sum(path.stat().st_size for path in chroma_files) != int(
        descriptor.get("chroma_bytes") or -1
    ):
        raise ValueError("Core Chroma byte count mismatch")
    sqlite_path = chroma_root / "chroma.sqlite3"
    if _sha256(sqlite_path) != str(descriptor.get("chroma_sqlite_sha256") or ""):
        raise ValueError("Core Chroma SQLite checksum mismatch")

    uri = f"file:{sqlite_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        counts = dict(
            connection.execute(
                """
                SELECT c.name, COUNT(e.id)
                FROM collections c
                JOIN segments s ON s.collection = c.id
                LEFT JOIN embeddings e ON e.segment_id = s.id
                GROUP BY c.name
                """
            ).fetchall()
        )
    if int(counts.get(collection, -1)) != expected_chunks:
        raise ValueError("Core Chroma collection count mismatch")
    incremental = str(descriptor.get("incremental_collection_name") or "")
    if int(counts.get(incremental, -1)) != int(
        descriptor.get("incremental_index_record_count") or -1
    ):
        raise ValueError("Core Chroma incremental collection count mismatch")

    verification = descriptor.get("verification") or {}
    if not verification or not all(value is True for value in verification.values()):
        raise ValueError("Core release verification receipts are incomplete")
    return {
        "status": "PASS",
        "release_id": descriptor.get("release_id"),
        "serving_manifest_sha256": serving.manifest_sha256,
        "document_count": len(serving.document_ids),
        "baseline_chunk_count": len(serving.chunk_ids),
        "database_document_count": int(descriptor["database_document_count"]),
        "database_chunk_count": int(descriptor["database_chunk_count"]),
        "incremental_index_record_count": int(
            descriptor["incremental_index_record_count"]
        ),
        "checksums_verified": True,
        "postgres_dump_verified": verify_postgres_dump,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=ROOT / "release-data")
    parser.add_argument(
        "--skip-postgres-dump",
        action="store_true",
        help="Verify the disk seed after the intentionally excluded mixed PostgreSQL dump.",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            verify_core_release(
                args.release_root,
                verify_postgres_dump=not args.skip_postgres_dump,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
