#!/usr/bin/env python3
"""Build the V2 exact/lexical staging index from one approved manifest.

The index is a separate SQLite artifact.  It is never built from PostgreSQL's
legacy ``legal_chunks`` table and it does not alter the live SQL search path.
Every row is copied from an eligible chunk in the approved V2 manifest, so
exact lookup, FTS, hydration and date filtering share the same release scope.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_MANIFEST = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-v2-exact-lexical.sqlite3"
LAW_RE = re.compile(r"\b(?:QĐ|QD|NĐ|ND|TT|CT|NQ|LB|BL|LUẬT|LUAT|PHÁP LỆNH|PHAP LENH)\b[^\n,;:]{0,100}", re.IGNORECASE)
ARTICLE_RE = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)
PARAGRAPH_RE = re.compile(r"\b(?:khoản|khoan)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)


def normalize_exact(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).replace("Đ", "D").replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _load_manifest(path: Path, *, allow_provisional_staging: bool = False) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("manifest_object_required")
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
    if not provisional and payload.get("legal_review_attestation") is not True:
        raise RuntimeError("approved_v2_manifest_required")
    if not str(payload.get("release_id") or "") or not str(payload.get("source_snapshot_sha256") or ""):
        raise RuntimeError("manifest_fingerprint_required")
    payload["provisional_staging"] = provisional
    return payload


def _metadata(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(row.get("metadata") or {})
    effective_from = values.get("article_effective_from") or values.get("effective_date")
    effective_to = values.get("article_effective_to") or values.get("expired_date")
    return {
        "chunk_revision_id": str(row["chunk_revision_id"]),
        "document_id": int(row["document_id"]),
        "article_id": int(row["article_id"]),
        "chunk_index": int(row.get("chunk_index") or 0),
        "release_id": str(payload["release_id"]),
        "document_serving_state": str(row.get("document_serving_state") or ""),
        "law_number": str(values.get("law_number") or ""),
        "article_number": str(values.get("article_number") or ""),
        "domain_slug": str(values.get("domain_slug") or ""),
        "source_url": str(values.get("source_url") or ""),
        "effective_from": str(effective_from or ""),
        "effective_to": str(effective_to or ""),
        "content": str(row.get("content") or ""),
        "structural_path": str(row.get("structural_path") or ""),
        "passage_sha256": str(row.get("passage_sha256") or ""),
        "content_sha256": str(row.get("content_sha256") or ""),
        "token_count": int(row.get("token_count") or 0),
    }


def _keys(item: dict[str, Any]) -> Iterable[tuple[str, str]]:
    law = normalize_exact(item["law_number"])
    article = normalize_exact(item["article_number"])
    if law:
        yield "law_number", law
    if article:
        yield "article_number", article
    if law and article:
        yield "law_article", f"{law}|{article}"
    content = item["content"]
    for match in ARTICLE_RE.finditer(content):
        yield "article_number", normalize_exact(match.group(1))
        if law:
            yield "law_article", f"{law}|{normalize_exact(match.group(1))}"
    for match in PARAGRAPH_RE.finditer(content):
        yield "paragraph_number", normalize_exact(match.group(1))
    # Keep a normalized law-like token sequence when the metadata field is
    # incomplete; this is only an exact index key, never legal inference.
    for match in LAW_RE.finditer(content):
        candidate = normalize_exact(match.group(0))
        if candidate:
            yield "law_text", candidate


def _write_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE release_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE chunks (
            chunk_revision_id TEXT PRIMARY KEY,
            document_id INTEGER NOT NULL,
            article_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            release_id TEXT NOT NULL,
            document_serving_state TEXT NOT NULL CHECK (document_serving_state IN ('current_retrievable','historical_only')),
            law_number TEXT NOT NULL,
            article_number TEXT NOT NULL,
            domain_slug TEXT NOT NULL,
            source_url TEXT NOT NULL,
            effective_from TEXT,
            effective_to TEXT,
            content TEXT NOT NULL,
            structural_path TEXT NOT NULL,
            passage_sha256 TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            token_count INTEGER NOT NULL CHECK (token_count BETWEEN 1 AND 512)
        );
        CREATE INDEX idx_chunks_document ON chunks(document_id);
        CREATE INDEX idx_chunks_article ON chunks(article_id);
        CREATE INDEX idx_chunks_state_dates ON chunks(document_serving_state, effective_from, effective_to);
        CREATE TABLE exact_lookup (
            key_kind TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            chunk_revision_id TEXT NOT NULL REFERENCES chunks(chunk_revision_id),
            PRIMARY KEY (key_kind, normalized_key, chunk_revision_id)
        );
        CREATE INDEX idx_exact_lookup_key ON exact_lookup(key_kind, normalized_key);
        CREATE VIRTUAL TABLE chunk_fts USING fts5(
            chunk_revision_id UNINDEXED,
            law_number,
            article_number,
            structural_path,
            content
        );
        """
    )


def build(*, manifest_path: Path, output: Path, allow_provisional_staging: bool = False) -> dict[str, Any]:
    manifest = _load_manifest(
        manifest_path,
        allow_provisional_staging=allow_provisional_staging,
    )
    provisional = bool(manifest.get("provisional_staging"))
    if provisional and "provisional" not in str(output).casefold():
        raise RuntimeError("provisional_index_output_name_required")
    chunks = [
        row for row in manifest.get("chunks") or []
        if row.get("eligible") is True
        and row.get("serving_state") == "retrievable"
        and row.get("document_serving_state") in {"current_retrievable", "historical_only"}
    ]
    if not chunks:
        raise RuntimeError("eligible_v2_chunks_required")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    if temp.exists():
        temp.unlink()
    connection = sqlite3.connect(temp)
    try:
        _write_schema(connection)
        metadata = {
            "schema_version": "legal-retrieval-v2-exact-lexical-index-v1",
            "release_id": str(manifest["release_id"]),
            "source_snapshot_sha256": str(manifest["source_snapshot_sha256"]),
            "manifest_sha256": str(manifest.get("manifest_sha256") or ""),
            "manifest_file_sha256": file_sha256(manifest_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "filter_contract": "document_serving_state + effective interval + eligible + manifest IDs",
            "vector_collection_is_separate": "true",
            "provisional_staging": "true" if provisional else "false",
            "release_eligible": "false" if provisional else "true",
        }
        connection.executemany("INSERT INTO release_metadata(key,value) VALUES (?,?)", metadata.items())
        chunk_rows = []
        exact_rows = []
        fts_rows = []
        seen: set[str] = set()
        for row in chunks:
            item = _metadata(row, manifest)
            identifier = item["chunk_revision_id"]
            if identifier in seen:
                raise RuntimeError(f"duplicate_manifest_chunk:{identifier}")
            seen.add(identifier)
            chunk_rows.append(tuple(item[key] for key in (
                "chunk_revision_id", "document_id", "article_id", "chunk_index", "release_id",
                "document_serving_state", "law_number", "article_number", "domain_slug",
                "source_url", "effective_from", "effective_to", "content", "structural_path",
                "passage_sha256", "content_sha256", "token_count",
            )))
            exact_rows.extend((kind, key, identifier) for kind, key in set(_keys(item)))
            fts_rows.append((identifier, item["law_number"], item["article_number"], item["structural_path"], item["content"]))
        connection.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", chunk_rows
        )
        connection.executemany(
            "INSERT INTO exact_lookup(key_kind,normalized_key,chunk_revision_id) VALUES (?,?,?)",
            exact_rows,
        )
        connection.executemany(
            "INSERT INTO chunk_fts(chunk_revision_id,law_number,article_number,structural_path,content) VALUES (?,?,?,?,?)",
            fts_rows,
        )
        connection.commit()
        counts = {
            "chunk_count": len(chunk_rows),
            "exact_key_count": len(exact_rows),
            "fts_row_count": len(fts_rows),
            "current_chunk_count": sum(row[5] == "current_retrievable" for row in chunk_rows),
            "historical_chunk_count": sum(row[5] == "historical_only" for row in chunk_rows),
        }
        connection.execute("INSERT INTO release_metadata(key,value) VALUES (?,?)", ("counts", json.dumps(counts, sort_keys=True)))
        connection.commit()
    finally:
        connection.close()
    temp.replace(output)
    output_sha = file_sha256(output)
    report = {
        "schema_version": "legal-retrieval-v2-exact-lexical-index-report-v1",
        "status": "PROVISIONAL_BUILT" if provisional else "STAGING_BUILT",
        "output": str(output.resolve()),
        "output_sha256": output_sha,
        "manifest_file_sha256": file_sha256(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "release_id": manifest.get("release_id"),
        "counts": counts,
        "active_pointer_changed": False,
        "live_sql_mutated": False,
        "provisional_staging": provisional,
        "release_eligible": not provisional,
    }
    report_path = output.with_suffix(output.suffix + ".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{output_sha}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-provisional-staging", action="store_true")
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    output_path = args.output.resolve()
    try:
        report = build(
            manifest_path=manifest_path,
            output=output_path,
            allow_provisional_staging=args.allow_provisional_staging,
        )
    except Exception as exc:
        # Do not silently turn a draft manifest into a lexical index.  Emit a
        # signed blocker artifact so release evidence records why the build
        # did not happen and proves that no live SQL path was changed.
        report = {
            "schema_version": "legal-retrieval-v2-exact-lexical-index-report-v1",
            "status": "BLOCKED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": str(exc),
            "manifest_path": str(manifest_path),
            "manifest_file_sha256": file_sha256(manifest_path) if manifest_path.is_file() else None,
            "output": str(output_path),
            "active_pointer_changed": False,
            "live_sql_mutated": False,
            "provisional_staging": bool(args.allow_provisional_staging),
            "release_eligible": False,
        }
        report_path = output_path.with_suffix(output_path.suffix + ".report.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.with_suffix(report_path.suffix + ".sha256").write_text(
            f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
        )
        print(json.dumps(report, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
