#!/usr/bin/env python3
"""Assess V2 chunk quality from PostgreSQL without changing V1 tables.

The assessment is intentionally conservative.  A tokenizer is required to
make a 512-token claim; when it is unavailable the report is still useful for
empty/duplicate/orphan counts but the quality gate remains blocked.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.backup_legal_retrieval import _database_url
from scripts.feature005_db1_snapshot import safe_database_target


DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "quality-policy-v2-assessment.json"
DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation.json"


def _load_inventory(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("inventory_report_object_required")
    if int(value.get("source_snapshot_document_count") or 0) != 12_236:
        raise ValueError("inventory_report_not_12236")
    return value


def _load_tokenizer(path: str | None) -> Any | None:
    if not path:
        return None
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(path, local_files_only=True)
    except Exception:
        return None


def _token_count(tokenizer: Any, value: str) -> int:
    try:
        return len(tokenizer.encode(value, add_special_tokens=False))
    except TypeError:
        return len(tokenizer.encode(value))


def assess(*, legal_as_of: date, inventory_path: Path, tokenizer_path: str | None) -> dict[str, Any]:
    inventory = _load_inventory(inventory_path)
    tokenizer = _load_tokenizer(tokenizer_path)
    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    document_ids: set[int] = set()
    document_chunk_counts: defaultdict[int, int] = defaultdict(int)
    empty_chunks: list[int] = []
    duplicate_chunks: list[int] = []
    oversized_chunks: list[dict[str, int]] = []
    seen_content: defaultdict[int, dict[str, int]] = defaultdict(dict)
    chunk_rows = 0
    orphan_chunks = 0
    try:
        with engine.connect() as connection:
            tx = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                document_ids.update(
                    int(value)
                    for value in connection.execute(
                        text("SELECT id FROM legal_documents")
                    ).scalars()
                )
                query = text(
                    """
                    SELECT c.id AS chunk_id, c.content, a.document_id
                    FROM legal_article_chunks c
                    JOIN legal_articles a ON a.id = c.article_id
                    ORDER BY c.id
                    """
                )
                for row in connection.execute(query).mappings():
                    chunk_rows += 1
                    chunk_id = int(row["chunk_id"])
                    document_id = int(row["document_id"])
                    if document_id not in document_ids:
                        orphan_chunks += 1
                        continue
                    document_chunk_counts[document_id] += 1
                    content = str(row.get("content") or "").strip()
                    if not content:
                        empty_chunks.append(chunk_id)
                        continue
                    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    prior = seen_content[document_id].get(content_sha)
                    if prior is not None:
                        duplicate_chunks.append(chunk_id)
                    else:
                        seen_content[document_id][content_sha] = chunk_id
                    if tokenizer is not None:
                        count = _token_count(tokenizer, content)
                        if count > 512:
                            oversized_chunks.append({"chunk_id": chunk_id, "token_count": count})
            finally:
                tx.rollback()
    finally:
        engine.dispose()

    inventory_documents = inventory.get("documents") or []
    inventory_document_ids = {
        int(item["document_id"])
        for item in inventory_documents
        if item.get("document_id") is not None
    }
    database_document_ids = set(document_ids)
    inventory_ids_not_in_database = sorted(inventory_document_ids - database_document_ids)
    database_ids_not_in_inventory = sorted(database_document_ids - inventory_document_ids)
    provisional_state = {
        int(item["document_id"]): str(item.get("serving_state") or "quarantined")
        for item in inventory_documents
        if item.get("document_id") is not None
    }
    current_or_history = {
        document_id
        for document_id, state in provisional_state.items()
        if state in {"current_retrievable", "historical_only"}
    }
    no_chunk_docs = sorted(current_or_history - set(document_chunk_counts))
    eligible_chunks = chunk_rows - len(empty_chunks) - len(duplicate_chunks) - orphan_chunks
    token_gate = tokenizer is not None and not oversized_chunks
    report = {
        "schema_version": "legal-quality-policy-v2-assessment",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of.isoformat(),
        "database_target": safe_database_target(url),
        "inventory_report": {
            "path": str(inventory_path.resolve()),
            "sha256": file_sha256(inventory_path),
            "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        },
        "quality_policy": {
            "version": "legal-chunk-quality-v2",
            "max_tokens": 512,
            "overlap_tokens": 64,
            "empty_or_duplicate_chunks_embedded": False,
            "tokenizer_path": tokenizer_path,
            "token_budget_checked": tokenizer is not None,
        },
        "counts": {
            "inventory_documents": len(document_ids),
            "inventory_expected": 12_236,
            "inventory_report_document_ids": len(inventory_document_ids),
            "inventory_ids_not_in_database": len(inventory_ids_not_in_database),
            "database_ids_not_in_inventory": len(database_ids_not_in_inventory),
            "chunk_rows": chunk_rows,
            "empty_chunks": len(empty_chunks),
            "duplicate_internal_chunks": len(duplicate_chunks),
            "orphan_chunks": orphan_chunks,
            "current_or_historical_documents_without_chunks": len(no_chunk_docs),
            "eligible_chunks_provisional": max(0, eligible_chunks),
            "oversized_chunks": len(oversized_chunks),
        },
        "examples": {
            "inventory_ids_not_in_database": inventory_ids_not_in_database[:100],
            "database_ids_not_in_inventory": database_ids_not_in_inventory[:100],
            "empty_chunk_ids": empty_chunks[:100],
            "duplicate_chunk_ids": duplicate_chunks[:100],
            "oversized_chunks": oversized_chunks[:100],
            "documents_without_chunks": no_chunk_docs[:100],
        },
        "gates": {
            "inventory_exact_12236": len(document_ids) == 12_236,
            "inventory_id_set_matches_database": (
                len(inventory_document_ids) == 12_236
                and not inventory_ids_not_in_database
                and not database_ids_not_in_inventory
            ),
            "no_orphan_chunks": orphan_chunks == 0,
            "token_budget_checked": tokenizer is not None,
            "no_oversized_chunks": tokenizer is not None and not oversized_chunks,
            "retrievable_documents_have_chunks": not no_chunk_docs,
            "no_empty_eligible_chunks": True,
        },
        "gate_passed": (
            len(document_ids) == 12_236
            and len(inventory_document_ids) == 12_236
            and not inventory_ids_not_in_database
            and not database_ids_not_in_inventory
            and orphan_chunks == 0
            and token_gate
            and not no_chunk_docs
        ),
        "mutation": {
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--tokenizer", default=None)
    parser.add_argument("--legal-as-of", type=date.fromisoformat, default=date(2026, 8, 16))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = assess(
        legal_as_of=args.legal_as_of,
        inventory_path=args.inventory.resolve(),
        tokenizer_path=args.tokenizer,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PASS" if report["gate_passed"] else "NEEDS_REVIEW",
        "counts": report["counts"],
        "report_sha256": report["report_sha256"],
        "output": str(output),
    }, ensure_ascii=False))
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
