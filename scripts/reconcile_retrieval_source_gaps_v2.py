#!/usr/bin/env python3
"""Reconcile Golden source gaps against the complete local inventory.

This is an evidence inventory only.  It checks PostgreSQL, legacy manifests
and available Chroma metadata, but it does not decide that a source is
official/current, edit a Golden case, import a document, or mutate vectors.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

from sqlalchemy import bindparam, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.backup_legal_retrieval import _database_url


DEFAULT_GAPS = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-legacy.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-gap-reconciliation-full-inventory-v4.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_MANIFESTS = (
    ROOT / "reports" / "m1-freeze" / "baseline_manifest.json",
    ROOT / "reports" / "m1-freeze" / "candidate_manifest.json",
    ROOT / "release-data" / "legal" / "serving_manifests" / "legal-baseline-7245-m2-v1.json",
)


def normalize_law(value: Any) -> str:
    text_value = unicodedata.normalize("NFC", str(value or "")).upper().replace("Đ", "D")
    text_value = re.sub(r"[^A-Z0-9]+", " ", text_value)
    aliases = {"QUYET DINH": "QD", "NGHI DINH": "ND", "THONG TU": "TT", "CHI THI": "CT"}
    for source, target in aliases.items():
        text_value = text_value.replace(source, target)
    return " ".join(text_value.split())


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _manifest_evidence(paths: tuple[Path, ...], law_numbers: set[str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = defaultdict(lambda: {"manifest_paths": [], "document_ids": [], "chunk_counts": []})
    for path in paths:
        if not path.is_file():
            continue
        payload = _load(path)
        for row in payload.get("documents") or []:
            law_key = normalize_law(row.get("law_number"))
            if law_key not in law_numbers:
                continue
            item = evidence[law_key]
            item["manifest_paths"].append(str(path.resolve()))
            item["document_ids"].append(int(row["document_id"]))
            item["chunk_counts"].append(int(row.get("chunk_count") or len(row.get("expected_chunk_ids") or [])))
    return evidence


def _record_chroma_rows(
    evidence: dict[str, dict[str, Any]],
    *,
    path: Path,
    collection: Any,
    ids: list[Any],
    metadatas: list[Any],
) -> None:
    for identifier, metadata in zip(ids, metadatas):
        values = metadata or {}
        law_key = normalize_law(values.get("law_number"))
        if not law_key:
            continue
        item = evidence[law_key]
        item["collections"].append({"path": str(path.resolve()), "name": collection.name})
        item["chunk_ids"].append(str(identifier))
        article = str(values.get("article_number") or "")
        if article:
            item["articles"].append(article)


def _chroma_evidence(
    paths: tuple[Path, ...],
    law_numbers: set[str],
    raw_law_numbers: set[str],
    *,
    full_scan: bool = False,
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = defaultdict(lambda: {"collections": [], "chunk_ids": [], "articles": []})
    try:
        import chromadb
    except Exception:
        return evidence
    for path in paths:
        if not path.is_dir():
            continue
        try:
            client = chromadb.PersistentClient(path=str(path))
            collections = client.list_collections()
        except Exception:
            continue
        for collection in collections:
            try:
                if full_scan:
                    total = int(collection.count())
                    for offset in range(0, total, 5000):
                        page = collection.get(include=["metadatas"], limit=5000, offset=offset)
                        _record_chroma_rows(
                            evidence,
                            path=path,
                            collection=collection,
                            ids=list(page.get("ids") or []),
                            metadatas=list(page.get("metadatas") or []),
                        )
                else:
                    # Chroma's metadata index can answer exact law-number
                    # filters without loading the full collection.  Query each
                    # source spelling separately because older stores do not
                    # guarantee one normalized metadata representation.
                    for raw_law in sorted(raw_law_numbers):
                        page = collection.get(
                            where={"law_number": raw_law},
                            include=["metadatas"],
                        )
                        _record_chroma_rows(
                            evidence,
                            path=path,
                            collection=collection,
                            ids=list(page.get("ids") or []),
                            metadatas=list(page.get("metadatas") or []),
                        )
            except Exception:
                continue
    for item in evidence.values():
        item["collections"] = sorted({json.dumps(row, sort_keys=True) for row in item["collections"]})
        item["collections"] = [json.loads(row) for row in item["collections"]]
        item["chunk_ids"] = sorted(set(item["chunk_ids"]))
        item["articles"] = sorted(set(item["articles"]))
    return evidence


def _database_evidence(law_numbers: set[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = defaultdict(lambda: {"documents": [], "articles": []})
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            documents = [dict(row) for row in connection.execute(text(
                "SELECT id, law_number, title, status, source_url, effective_date, expired_date, scope, sector "
                "FROM legal_documents ORDER BY id"
            )).mappings()]
            matched_ids: list[int] = []
            for row in documents:
                law_key = normalize_law(row.get("law_number"))
                if law_key not in law_numbers:
                    continue
                matched_ids.append(int(row["id"]))
                output[law_key]["documents"].append({
                    key: (value.isoformat() if hasattr(value, "isoformat") else value)
                    for key, value in row.items()
                })
            if matched_ids:
                rows = connection.execute(text(
                    "SELECT a.id AS article_id, a.document_id, a.article_number, a.status, "
                    "COUNT(DISTINCT c.id) AS chunk_count "
                    "FROM legal_articles a LEFT JOIN legal_article_chunks c ON c.article_id=a.id "
                    "WHERE a.document_id IN :document_ids GROUP BY a.id, a.document_id, a.article_number, a.status"
                ).bindparams(bindparam("document_ids", expanding=True)), {"document_ids": matched_ids}).mappings()
                law_by_doc = {int(row["id"]): normalize_law(row.get("law_number")) for row in documents}
                for row in rows:
                    item = dict(row)
                    item["chunk_count"] = int(item.get("chunk_count") or 0)
                    output[law_by_doc[int(item["document_id"])]]["articles"].append(item)
    finally:
        engine.dispose()
    return output


def reconcile(*, gaps_path: Path, manifest_paths: tuple[Path, ...], chroma_paths: tuple[Path, ...], output: Path, full_chroma: bool = False) -> dict[str, Any]:
    gaps = _load(gaps_path)
    references = list(gaps.get("missing_references") or [])
    law_numbers = {normalize_law(row.get("law_number")) for row in references}
    raw_law_numbers = {str(row.get("law_number") or "").strip() for row in references if str(row.get("law_number") or "").strip()}
    database = _database_evidence(law_numbers)
    manifests = _manifest_evidence(manifest_paths, law_numbers)
    chroma = _chroma_evidence(chroma_paths, law_numbers, raw_law_numbers, full_scan=full_chroma)
    records: list[dict[str, Any]] = []
    for reference in references:
        law_key = normalize_law(reference.get("law_number"))
        db = database.get(law_key, {"documents": [], "articles": []})
        manifest = manifests.get(law_key, {"manifest_paths": [], "document_ids": [], "chunk_counts": []})
        vector = chroma.get(law_key, {"collections": [], "chunk_ids": [], "articles": []})
        article_text = str(reference.get("article") or "").strip()
        article_numbers = {item.strip().casefold() for item in re.split(r"[,;/]+", article_text) if item.strip()}
        db_article_numbers = {
            str(item.get("article_number") or "").strip().casefold()
            for item in db["articles"]
            if int(item.get("chunk_count") or 0) > 0
        }
        matched_article = not article_numbers or bool(article_numbers & db_article_numbers)
        if db["documents"] and matched_article:
            classification = "available_in_full_database"
        elif db["documents"]:
            classification = "metadata_or_article_gap"
        elif manifest["manifest_paths"] or vector["collections"]:
            classification = "legacy_or_vector_only"
        else:
            classification = "not_found_in_local_inventory"
        records.append({
            **reference,
            "normalized_law_number": law_key,
            "classification": classification,
            "database_evidence": db,
            "legacy_manifest_evidence": manifest,
            "chroma_metadata_evidence": vector,
            "article_match_in_database": matched_article,
            "legal_decision_required": True,
            "database_mutated": False,
            "vectors_mutated": False,
        })
    summary = {
        "reference_count": len(records),
        "law_count": len(law_numbers),
        "classifications": {
            key: sum(row["classification"] == key for row in records)
            for key in sorted({row["classification"] for row in records})
        },
        "local_database_documents_for_gaps": sum(bool(row["database_evidence"]["documents"]) for row in records),
        "local_database_article_resolutions": sum(row["article_match_in_database"] for row in records),
        "requires_legal_review": True,
    }
    report = {
        "schema_version": "legal-retrieval-source-gap-reconciliation-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_gap_manifest": {"path": str(gaps_path.resolve()), "sha256": file_sha256(gaps_path)},
        "source_inventory_scope": 12_236,
        "chroma_reconciliation_mode": "full_metadata_scan" if full_chroma else "targeted_law_number_metadata_filters",
        "summary": summary,
        "references": records,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--manifest", type=Path, action="append", dest="manifests")
    parser.add_argument("--chroma-path", type=Path, action="append", dest="chroma_paths")
    parser.add_argument("--skip-chroma", action="store_true", help="Do not scan large Chroma stores; use database/manifests only.")
    parser.add_argument("--full-chroma", action="store_true", help="Use a complete Chroma metadata scan instead of targeted law-number filters.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    manifests = tuple((args.manifests or [str(path) for path in DEFAULT_MANIFESTS]))
    chroma_paths = tuple() if args.skip_chroma else tuple(args.chroma_paths or [DEFAULT_CHROMA])
    report = reconcile(
        gaps_path=args.gaps.resolve(),
        manifest_paths=tuple(Path(item).resolve() for item in manifests),
        chroma_paths=tuple(Path(item).resolve() for item in chroma_paths),
        output=args.output.resolve(),
        full_chroma=args.full_chroma,
    )
    print(json.dumps({"status": "READ_ONLY_RECONCILED", "summary": report["summary"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
