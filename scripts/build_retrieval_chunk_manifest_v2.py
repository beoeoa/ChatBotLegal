#!/usr/bin/env python3
"""Build an additive V2 chunk manifest from PostgreSQL article source text.

This command is a staging artifact builder only. It reads PostgreSQL in a
read-only transaction, never updates V1 articles/chunks, and intentionally
emits ``approved=false`` until legal metadata review is complete. The output
can therefore be inspected and benchmark-prepared, but the shadow vector
builder will refuse it until a reviewer attests the manifest.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import unicodedata
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_structural_chunking import (
    V2_MAX_TOKENS,
    V2_TOKEN_OVERLAP,
    split_parent_children_v2,
)
from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.backup_legal_retrieval import _database_url
from scripts.feature005_db1_snapshot import safe_database_target


DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_TOKENIZER = Path(
    r"D:\legal-chatbot-data\sentence_transformers\models--darklethelong--vnlegal-lal\snapshots\de759324ef931a2475ae8db97137b6a6cbb98aa0"
)
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-draft-passage-v4.json"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _load_metadata_overlay(inventory: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Return a separately attested metadata overlay, if one is present.

    The normal 12,236-document inventory has no overlay.  An overlay is only
    accepted when it was produced by the metadata-attestation tool; it is
    additive and is never written back to PostgreSQL.
    """

    header = inventory.get("metadata_overlay")
    if header is None:
        return {}
    if not isinstance(header, dict) or header.get("schema_version") != "legal-retrieval-metadata-overlay-v1":
        raise RuntimeError("metadata_overlay_schema_required")
    if not str(header.get("attestation_sha256") or ""):
        raise RuntimeError("metadata_overlay_attestation_required")
    if header.get("source_inventory_unchanged") is not True:
        raise RuntimeError("metadata_overlay_source_mutation_contract_failed")
    rows: dict[int, dict[str, Any]] = {}
    for raw in inventory.get("documents") or []:
        if raw.get("metadata_attestation_sha256") != header.get("attestation_sha256"):
            continue
        document_id = int(raw["document_id"])
        if document_id in rows:
            raise RuntimeError(f"metadata_overlay_duplicate_document:{document_id}")
        rows[document_id] = dict(raw)
    if not rows:
        raise RuntimeError("metadata_overlay_rows_required")
    return rows


def _sha256_directory(path: Path) -> str:
    rows = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            rows.append({
                "path": item.relative_to(path).as_posix(),
                "sha256": file_sha256(item),
                "size": item.stat().st_size,
            })
    return canonical_sha256(rows)


def _load_tokenizer(path: Path) -> Any:
    if not path.is_dir():
        raise RuntimeError(f"tokenizer_path_missing:{path}")
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(path, local_files_only=True)
    except Exception as exc:
        raise RuntimeError("v2_tokenizer_unreadable") from exc


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10] or None


def _article_parent(row: dict[str, Any]) -> dict[str, Any]:
    number = str(row.get("article_number") or "").strip()
    title = str(row.get("article_title") or "").strip()
    heading = f"Điều {number}" if number else "Điều không xác định"
    if title and title.casefold() != heading.casefold():
        heading = f"{heading}. {title}"
    return {
        "article_number": number or "unknown",
        "title": heading,
        "content": str(row.get("article_content") or ""),
        "parent_kind": "article",
        "source_start_offset": 0,
    }


def _fingerprints(*, tokenizer_path: Path, release_id: str) -> dict[str, str]:
    baseline_path = ROOT / "reports" / "m1-freeze" / "baseline_manifest.json"
    model_fingerprint = ""
    if baseline_path.is_file():
        baseline = _load_json(baseline_path)
        model_fingerprint = str(
            ((baseline.get("collection") or {}).get("model_artifact_fingerprint")) or ""
        )
    if not model_fingerprint:
        raise RuntimeError("model_artifact_fingerprint_missing_from_baseline_attestation")
    return {
        "model_artifact_fingerprint": model_fingerprint,
        "tokenizer_fingerprint": _sha256_directory(tokenizer_path),
        "embedding_recipe_fingerprint": canonical_sha256({
            "model": "VNLegal-LAL",
            "query_instruction": "Instruct: Given a Vietnamese legal question, retrieve relevant legal passages that answer the question\\nQuery: ",
            "pooling": "last_token",
            "normalize": "l2",
            "dtype": "float32_persisted",
            "dimension": 1024,
        }),
        "passage_recipe_fingerprint": canonical_sha256({
            "header": ["document_title", "law_number", "article_heading", "structural_path", "content"],
            "unicode": "NFC",
            "newline": "LF",
            "truncate": False,
        }),
        "splitter_fingerprint": canonical_sha256({
            "algorithm": "legal_structural_chunking_v2",
            "max_tokens": V2_MAX_TOKENS,
            "overlap_tokens": V2_TOKEN_OVERLAP,
            "release_id": release_id,
        }),
        "dependency_lock_fingerprint": canonical_sha256({
            "python": sys.version.split()[0],
            "script": "build_retrieval_chunk_manifest_v2",
        }),
    }


def _write_chunk_line(handle: Any, row: dict[str, Any]) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def build(*, inventory_path: Path, tokenizer_path: Path, output: Path, release_id: str, document_state: str) -> dict[str, Any]:
    inventory = _load_json(inventory_path)
    if int(inventory.get("source_snapshot_document_count") or 0) != 12_236:
        raise RuntimeError("inventory_must_contain_12236_documents")
    inventory_documents = inventory.get("documents") or []
    metadata_overlay = _load_metadata_overlay(inventory)
    state_by_document = {
        int(row["document_id"]): str(
            (metadata_overlay.get(int(row["document_id"])) or row).get("serving_state") or "quarantined"
        )
        for row in inventory_documents
    }
    allowed_states = {
        "all": {"current_retrievable", "historical_only"},
        "current_retrievable": {"current_retrievable"},
        "historical_only": {"historical_only"},
    }
    if document_state not in allowed_states:
        raise ValueError("invalid_document_state")
    tokenizer = _load_tokenizer(tokenizer_path)
    fingerprints = _fingerprints(tokenizer_path=tokenizer_path, release_id=release_id)
    temp_chunks = output.with_suffix(output.suffix + ".chunks.ndjson")
    temp_chunks.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter[str] = Counter()
    identity_rows: list[dict[str, Any]] = []
    seen_content: defaultdict[int, set[str]] = defaultdict(set)
    included_documents: set[int] = set()
    included_articles: set[int] = set()
    by_state: Counter[str] = Counter()
    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with temp_chunks.open("w", encoding="utf-8") as chunk_handle:
            with engine.connect() as connection:
                tx = connection.begin()
                try:
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    query = text(
                        """
                        SELECT d.id AS document_id, d.title AS document_title,
                               d.law_number, d.status AS document_status,
                               d.source_url, d.effective_date, d.expired_date,
                               d.scope, d.sector,
                               a.id AS article_id, a.article_number,
                               a.title AS article_title, a.content AS article_content,
                               a.effective_from AS article_effective_from,
                               a.effective_to AS article_effective_to,
                               a.status AS article_status,
                               scope_row.domain AS domain_slug
                        FROM legal_documents d
                        JOIN legal_articles a ON a.document_id = d.id
                        LEFT JOIN LATERAL (
                            SELECT s.domain
                            FROM legal_search_scope s
                            WHERE s.document_id = d.id AND s.included = TRUE
                            ORDER BY s.evaluated_at DESC NULLS LAST
                            LIMIT 1
                        ) scope_row ON TRUE
                        ORDER BY d.id, a.id
                        """
                    )
                    result = connection.execution_options(stream_results=True).execute(query)
                    for raw in result.mappings():
                        row = dict(raw)
                        document_id = int(row["document_id"])
                        overlay_row = metadata_overlay.get(document_id)
                        if overlay_row:
                            # Only fields explicitly represented by the
                            # attested overlay are allowed to affect the
                            # staging passage/metadata.  The database row
                            # remains read-only and is never rewritten.
                            for source_key, target_key in (
                                ("law_number", "law_number"),
                                ("status_observed", "document_status"),
                                ("source_url", "source_url"),
                                ("effective_date", "effective_date"),
                                ("expired_date", "expired_date"),
                            ):
                                if source_key in overlay_row:
                                    row[target_key] = overlay_row.get(source_key)
                        state = state_by_document.get(document_id, "quarantined")
                        if state not in allowed_states[document_state]:
                            stats[f"document_skipped_{state}"] += 1
                            continue
                        included_documents.add(document_id)
                        by_state[state] += 1
                        article_id = int(row["article_id"])
                        parent = _article_parent(row)
                        source_content = unicodedata.normalize(
                            "NFC", str(parent.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                        )
                        if not source_content:
                            stats["empty_article_source"] += 1
                            continue
                        passage_prefix = "\n".join(
                            value for value in (
                                str(row.get("document_title") or "").strip(),
                                str(row.get("law_number") or "").strip(),
                            ) if value
                        )
                        parent["content"] = source_content
                        children = split_parent_children_v2(
                            parent,
                            tokenizer=tokenizer,
                            release_id=release_id,
                            passage_prefix=passage_prefix,
                        )
                        for child in children:
                            content = str(child.get("content") or "").strip()
                            token_count = int(child.get("token_count") or 0)
                            if not content:
                                stats["empty_chunk"] += 1
                                continue
                            if token_count <= 0 or token_count > V2_MAX_TOKENS:
                                stats["oversized_or_invalid_chunk"] += 1
                                continue
                            content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                            embedding_text = str(child.get("passage_text") or content)
                            embedding_text_sha = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()
                            if content_sha in seen_content[document_id]:
                                stats["duplicate_internal_chunk"] += 1
                                continue
                            seen_content[document_id].add(content_sha)
                            chunk_index = len(identity_rows)
                            chunk_id = f"{release_id}-{document_id}-{article_id}-{chunk_index:08d}"
                            structural_path = str(child.get("heading") or parent["title"])
                            passage_sha = str(child.get("passage_sha256") or "")
                            chunk = {
                                "chunk_revision_id": chunk_id,
                                "release_id": release_id,
                                "document_id": document_id,
                                "article_id": article_id,
                                "chunk_index": chunk_index,
                                "structural_path": structural_path,
                                "child_kind": child.get("child_kind") or "fallback",
                                "heading": child.get("heading") or parent["title"],
                                "content": content,
                                "content_sha256": content_sha,
                                "embedding_text": embedding_text,
                                "embedding_text_sha256": embedding_text_sha,
                                "source_content_sha256": hashlib.sha256(source_content.encode("utf-8")).hexdigest(),
                                "passage_sha256": passage_sha,
                                "source_start_offset": child.get("source_start_offset"),
                                "source_end_offset": child.get("source_end_offset"),
                                "token_count": token_count,
                                "quality_policy_version": "legal-chunk-quality-v2",
                                "quality_assessed": True,
                                "eligible": True,
                                "serving_state": "retrievable",
                                "document_serving_state": state,
                                "quality_reasons": list(child.get("quality_reasons") or []),
                                "metadata": {
                                    "document_title": row.get("document_title"),
                                    "law_number": row.get("law_number"),
                                    "document_status": row.get("document_status"),
                                    "article_number": row.get("article_number"),
                                    "article_title": row.get("article_title"),
                                    "article_status": row.get("article_status"),
                                    "domain_slug": row.get("domain_slug"),
                                    "source_url": row.get("source_url"),
                                    "effective_date": _normalize_date(row.get("effective_date")),
                                    "expired_date": _normalize_date(row.get("expired_date")),
                                    "article_effective_from": _normalize_date(row.get("article_effective_from")),
                                    "article_effective_to": _normalize_date(row.get("article_effective_to")),
                                    "serving_state": state,
                                    "metadata_attestation_sha256": (overlay_row or {}).get("metadata_attestation_sha256"),
                                    "metadata_evidence_url": (overlay_row or {}).get("metadata_evidence_url"),
                                    "metadata_evidence_sha256": (overlay_row or {}).get("metadata_evidence_sha256"),
                                    "metadata_reviewed_by": (overlay_row or {}).get("metadata_reviewed_by"),
                                    "metadata_reviewed_at": (overlay_row or {}).get("metadata_reviewed_at"),
                                },
                            }
                            included_articles.add(article_id)
                            _write_chunk_line(chunk_handle, chunk)
                            identity_rows.append({
                                "chunk_revision_id": chunk_id,
                                "document_id": document_id,
                                "article_id": article_id,
                                "content_sha256": content_sha,
                                "embedding_text_sha256": embedding_text_sha,
                                "passage_sha256": passage_sha,
                                "token_count": token_count,
                            })
                            stats["eligible_chunk_count"] += 1
                finally:
                    tx.rollback()
    finally:
        engine.dispose()

    counts = {
        "inventory_document_count": 12_236,
        "current_retrievable_document_count": sum(
            state == "current_retrievable" for state in state_by_document.values()
        ),
        "historical_only_document_count": sum(
            state == "historical_only" for state in state_by_document.values()
        ),
        "quarantined_document_count": sum(
            state == "quarantined" for state in state_by_document.values()
        ),
        "future_effective_document_count": sum(
            state == "future_effective" for state in state_by_document.values()
        ),
        "included_document_count": len(included_documents),
        "included_article_count": len(included_articles),
        "chunk_count": int(stats["eligible_chunk_count"]),
        "vector_count": int(stats["eligible_chunk_count"]),
    }
    manifest_projection = {
        "release_id": release_id,
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "metadata_overlay_attestation_sha256": (
            (inventory.get("metadata_overlay") or {}).get("attestation_sha256")
            if metadata_overlay else None
        ),
        "document_state_filter": document_state,
        "counts": counts,
        "chunks": identity_rows,
        "fingerprints": fingerprints,
    }
    manifest_sha256 = canonical_sha256(manifest_projection)
    header = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "release_id": release_id,
        "dataset_version": "retrieval-release-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": date(2026, 8, 16).isoformat(),
        "database_target": safe_database_target(url),
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "inventory_report_sha256": file_sha256(inventory_path),
        "metadata_overlay_attestation_sha256": (
            (inventory.get("metadata_overlay") or {}).get("attestation_sha256")
            if metadata_overlay else None
        ),
        "document_state_filter": document_state,
        "approved": False,
        "legal_review_attestation": False,
        "approval_blocker": "official_source_and_metadata_review_required",
        **fingerprints,
        **counts,
        "quality_policy_version": "legal-chunk-quality-v2",
        "manifest_sha256": manifest_sha256,
        "stats": dict(sorted(stats.items())),
        "chunk_identity_sha256": canonical_sha256(identity_rows),
    }
    temp_output = output.with_suffix(output.suffix + ".tmp")
    with temp_output.open("w", encoding="utf-8") as handle:
        header_json = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
        # The header has no ``chunks`` member; stream that member without
        # holding all passage content in memory.
        handle.write(header_json[:-1])
        handle.write(',"chunks":[')
        first = True
        with temp_chunks.open("r", encoding="utf-8") as chunk_handle:
            for line in chunk_handle:
                if not first:
                    handle.write(",")
                handle.write(line.rstrip("\n"))
                first = False
        handle.write("]}")
    temp_output.replace(output)
    temp_chunks.unlink(missing_ok=True)
    output_sha256 = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{output_sha256}  {output.name}\n", encoding="ascii"
    )
    report = {
        "status": "DRAFT_BLOCKED",
        "release_id": release_id,
        "output": str(output),
        "output_sha256": output_sha256,
        "manifest_sha256": manifest_sha256,
        "counts": counts,
        "stats": dict(sorted(stats.items())),
        "approved": False,
        "active_pointer_changed": False,
        "database_mutated": False,
    }
    report_path = output.with_name(output.stem + ".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--release-id", default="legal-retrieval-v2-20260816")
    parser.add_argument(
        "--document-state",
        choices=("all", "current_retrievable", "historical_only"),
        default="all",
    )
    args = parser.parse_args(argv)
    report = build(
        inventory_path=args.inventory.resolve(),
        tokenizer_path=args.tokenizer.resolve(),
        output=args.output.resolve(),
        release_id=args.release_id,
        document_state=args.document_state,
    )
    print(json.dumps(report, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
