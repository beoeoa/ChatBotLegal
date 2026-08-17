#!/usr/bin/env python3
"""Build immutable baseline/candidate corpus manifests without mutating stores."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_corpus_candidate import (
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    canonical_sha256,
    normalized_law_number,
    prepare_documents,
    required_source_blockers,
    select_candidate,
)


DEFAULT_CHROMA_PATH = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_ACTIVE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
DEFAULT_CANDIDATE_COLLECTION = "legal_chunks_candidate_3000_v1"
DEFAULT_CLASSIFICATION = ROOT / "output" / "legal-document-domain-classification-v1.json"
DEFAULT_REMEDIATION = ROOT / "reports" / "corpus-thinning" / "candidate-remediation-v2.json"
DEFAULT_GOLDENS = (
    ROOT
    / "outputs"
    / "019fe6cd-c481-70c0-8c58-3f69816592fc"
    / "golden-294-live"
    / "golden-1000-residence-remap-proposal.json",
)


def _database_url() -> str:
    direct = os.getenv("LEGAL_DATABASE_URL", "").strip()
    if direct:
        return direct
    values = dotenv_values(ROOT / ".env")
    configured = str(values.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if configured:
        return configured.replace("host.docker.internal", "127.0.0.1")
    return "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/legal_chatbot"


def _safe_database_target(url: str) -> dict[str, Any]:
    parsed = make_url(url)
    return {
        "driver": parsed.drivername,
        "host": parsed.host,
        "port": parsed.port,
        "database": parsed.database,
        "username": parsed.username,
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _classification(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        return {}, {"path": str(path), "present": False}
    payload = _json(path)
    rows = {
        int(item["document_id"]): dict(item)
        for item in payload.get("documents") or []
        if item.get("document_id") not in (None, "")
    }
    return rows, {
        "path": str(path.resolve()),
        "present": True,
        "sha256": _file_sha256(path),
        "schema_version": payload.get("schema_version"),
        "activation_status": (payload.get("activation") or {}).get("status"),
    }


def _remediation(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load reviewed candidate-only scope/source remediation decisions."""

    payload = _json(path)
    if payload.get("schema_version") != "legal-corpus-candidate-remediation-v2":
        raise RuntimeError(f"unsupported_remediation_schema:{path}")
    if payload.get("approval_status") != "approved_for_candidate_build":
        raise RuntimeError(f"remediation_not_approved:{path}")
    rows: dict[int, dict[str, Any]] = {}
    for item in [
        *(payload.get("active_scope_overrides") or []),
        *(payload.get("staging_supplements") or []),
    ]:
        document_id = int(item["document_id"])
        if document_id in rows:
            raise RuntimeError(f"duplicate_remediation_document:{document_id}")
        rows[document_id] = dict(item)
    return rows, {
        "path": str(path.resolve()),
        "sha256": _file_sha256(path),
        "legal_as_of": payload.get("legal_as_of"),
        "scope_override_count": len(payload.get("active_scope_overrides") or []),
        "staging_supplement_count": len(payload.get("staging_supplements") or []),
        "retained_expired_count": len(payload.get("expired_retained") or []),
        "metadata_restored_count": int(
            (payload.get("metadata_remediation") or {}).get("restored_count") or 0
        ),
        "metadata_quarantined_count": int(
            (payload.get("metadata_remediation") or {}).get("quarantined_count") or 0
        ),
    }


def _golden_evidence(paths: list[Path]) -> tuple[set[str], dict[str, set[str]], list[dict[str, Any]]]:
    required: set[str] = set()
    mappings: dict[str, set[str]] = defaultdict(set)
    inputs: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            inputs.append({"path": str(path), "present": False})
            continue
        payload = _json(path)
        cases = payload.get("cases") or payload.get("records") or []
        for case in cases:
            if not isinstance(case, Mapping):
                continue
            case_id = str(case.get("case_id") or case.get("id") or "").strip()
            for source in case.get("expected_sources") or []:
                law_number = str(source.get("law_number") or "").strip()
                if not law_number:
                    continue
                required.add(law_number)
                if case_id:
                    mappings[normalized_law_number(law_number)].add(case_id)
        inputs.append(
            {
                "path": str(path.resolve()),
                "present": True,
                "sha256": _file_sha256(path),
                "schema_version": payload.get("schema_version"),
                "status": payload.get("status") or payload.get("review_status"),
                "case_count": len(cases),
            }
        )
    return required, mappings, inputs


def _postgres_rows(
    *,
    legal_as_of: date,
    classification: Mapping[int, Mapping[str, Any]],
    document_ids: list[int] | None = None,
    serving_only: bool = True,
    statuses: tuple[str, ...] = ("active",),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    id_filter = "AND d.id = ANY(:document_ids)" if document_ids else ""
    scope_filter = "AND s.included = TRUE" if serving_only else ""
    statement = text(
        f"""
        WITH chunk_stats AS (
            SELECT
                d.id AS document_id,
                COUNT(DISTINCT a.id) AS article_count,
                COUNT(c.id) AS chunk_count,
                COUNT(c.id) FILTER (
                    WHERE LENGTH(BTRIM(COALESCE(c.content, ''))) > 0
                ) AS nonempty_chunk_count,
                COUNT(c.id) FILTER (
                    WHERE LENGTH(BTRIM(COALESCE(c.content, ''))) = 0
                ) AS empty_chunk_count,
                COUNT(c.id) FILTER (
                    WHERE COALESCE(q.eligible, TRUE) = TRUE
                ) AS eligible_chunk_count,
                COALESCE(ARRAY_AGG(c.id ORDER BY c.id)
                    FILTER (WHERE c.id IS NOT NULL), ARRAY[]::INTEGER[]) AS chunk_ids,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(tham quyen|uy ban nhan dan|chu tich uy ban)%'
                ) AS facet_authority,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(ho so|giay to|to khai)%'
                ) AS facet_documents,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(thu tuc|trinh tu|tiep nhan|giai quyet)%'
                ) AS facet_procedure,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(thoi han|thoi gian giai quyet)%'
                ) AS facet_deadline,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(le phi|muc phi|thu phi)%'
                ) AS facet_fee,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(bieu mau|mau so|to khai)%'
                ) AS facet_form,
                BOOL_OR(
                    legal_normalize_text(COALESCE(c.content, '') || ' ' || COALESCE(c.heading, '') || ' ' || COALESCE(a.title, ''))
                    SIMILAR TO '%(hieu luc|sua doi|bo sung|thay the)%'
                ) AS facet_effectivity
            FROM legal_documents d
            LEFT JOIN legal_articles a ON a.document_id = d.id
            LEFT JOIN legal_article_chunks c ON c.article_id = a.id
            LEFT JOIN legal_chunk_quality q
              ON q.chunk_id = c.id
             AND q.quality_version = 'legal-chunk-quality-v1'
            WHERE d.status = ANY(:statuses)
              {id_filter}
            GROUP BY d.id
        ),
        relation_stats AS (
            SELECT document_id, COUNT(*) AS relationship_count
            FROM (
                SELECT source_document_id AS document_id
                FROM legal_document_relationships
                WHERE source_document_id IS NOT NULL
                UNION ALL
                SELECT target_document_id AS document_id
                FROM legal_document_relationships
                WHERE target_document_id IS NOT NULL
            ) r
            GROUP BY document_id
        )
        SELECT
            d.id AS document_id,
            d.title,
            d.law_number,
            d.issued_date,
            d.effective_date,
            d.expired_date,
            d.status,
            d.source_url,
            d.document_type,
            d.issuing_agency,
            d.scope,
            d.sector,
            d.field_id,
            s.reason AS scope_reason,
            COALESCE(s.domain, g.group_slug) AS domain,
            cs.chunk_count,
            cs.article_count,
            cs.nonempty_chunk_count,
            cs.empty_chunk_count,
            cs.eligible_chunk_count,
            cs.chunk_ids,
            COALESCE(rs.relationship_count, 0) AS relationship_count,
            cs.facet_authority,
            cs.facet_documents,
            cs.facet_procedure,
            cs.facet_deadline,
            cs.facet_fee,
            cs.facet_form,
            cs.facet_effectivity
        FROM legal_documents d
        JOIN legal_search_scope s
          ON s.document_id = d.id
         {scope_filter}
        JOIN chunk_stats cs ON cs.document_id = d.id
        LEFT JOIN relation_stats rs ON rs.document_id = d.id
        LEFT JOIN LATERAL (
            SELECT group_slug
            FROM legal_commune_field_groups
            WHERE field_id = d.field_id AND included = TRUE
            ORDER BY evaluated_at DESC NULLS LAST
            LIMIT 1
        ) g ON TRUE
        WHERE d.status = ANY(:statuses)
          {id_filter}
        ORDER BY d.id
        """
    )
    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION READ ONLY"))
            parameters: dict[str, Any] = {"statuses": list(statuses)}
            if document_ids:
                parameters["document_ids"] = sorted(set(int(item) for item in document_ids))
            rows = [
                dict(row)
                for row in connection.execute(statement, parameters).mappings()
            ]
            transaction.rollback()
    finally:
        engine.dispose()

    for row in rows:
        tags = [
            name.removeprefix("facet_")
            for name in (
                "facet_authority",
                "facet_documents",
                "facet_procedure",
                "facet_deadline",
                "facet_fee",
                "facet_form",
                "facet_effectivity",
            )
            if row.pop(name, False)
        ]
        row["facet_tags"] = tags
        proposal = classification.get(int(row["document_id"]))
        if proposal:
            row["classification_proposal"] = {
                "domain": proposal.get("domain"),
                "confidence": proposal.get("confidence"),
                "reason": proposal.get("reason"),
            }
        if not str(row.get("domain") or "").strip() and proposal:
            proposed_domain = str(proposal.get("domain") or "").strip()
            confidence = str(proposal.get("confidence") or "").strip()
            if proposed_domain == "ngoai_pham_vi":
                row["scope_reason"] = (
                    "classification_pending_review"
                    if confidence == "review"
                    else "out_of_commune_scope"
                )
            else:
                row["domain"] = proposed_domain
    inventory_projection = [
        {
            "document_id": row["document_id"],
            "law_number": row.get("law_number"),
            "status": row.get("status"),
            "scope_reason": row.get("scope_reason"),
            "domain": row.get("domain"),
            "chunk_ids": row.get("chunk_ids") or [],
        }
        for row in rows
    ]
    return rows, {
        "database_target": _safe_database_target(url),
        "read_only_transaction": True,
        "document_count": len(rows),
        "article_count": sum(int(row.get("article_count") or 0) for row in rows),
        "chunk_count": sum(len(row.get("chunk_ids") or []) for row in rows),
        "empty_chunk_count": sum(int(row.get("empty_chunk_count") or 0) for row in rows),
        "inventory_sha256": canonical_sha256(inventory_projection),
    }


def _historical_source_inventory(required_law_numbers: set[str]) -> dict[str, list[dict[str, Any]]]:
    required = {normalized_law_number(item) for item in required_law_numbers}
    if not required:
        return {}
    url = _database_url()
    engine = create_engine(url, future=True, pool_pre_ping=True)
    statement = text(
        """
        SELECT id AS document_id, law_number, title, status,
               issued_date, effective_date, expired_date, source_url
        FROM legal_documents
        ORDER BY id
        """
    )
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION READ ONLY"))
            rows = [dict(row) for row in connection.execute(statement).mappings()]
            transaction.rollback()
    finally:
        engine.dispose()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = normalized_law_number(row.get("law_number"))
        if normalized not in required:
            continue
        result[normalized].append(
            {
                key: value
                for key, value in row.items()
                if key != "source_url"
            }
            | {"source_url_present": bool(str(row.get("source_url") or "").strip())}
        )
    return dict(result)


def _collection_inventory(
    *, chroma_path: Path, collection_name: str
) -> dict[str, Any]:
    database = chroma_path / "chroma.sqlite3"
    if not database.is_file():
        raise RuntimeError(f"chroma_sqlite_missing:{database}")
    before = {"size": database.stat().st_size, "mtime_ns": database.stat().st_mtime_ns}
    uri = f"file:{database.resolve().as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        row = connection.execute(
            "SELECT id FROM collections WHERE name = ?", (collection_name,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"collection_missing:{collection_name}")
        collection_id = str(row[0])
        metadata = {}
        for key, string, integer, floating, boolean in connection.execute(
            "SELECT key, str_value, int_value, float_value, bool_value "
            "FROM collection_metadata WHERE collection_id = ? ORDER BY key",
            (collection_id,),
        ):
            metadata[str(key)] = next(
                (value for value in (string, integer, floating, boolean) if value is not None),
                None,
            )
        query = """
            SELECT e.embedding_id,
                   COALESCE(c.int_value, c.string_value),
                   COALESCE(d.int_value, d.string_value)
            FROM segments s
            JOIN embeddings e ON e.segment_id = s.id
            LEFT JOIN embedding_metadata c ON c.id = e.id AND c.key = 'chunk_id'
            LEFT JOIN embedding_metadata d ON d.id = e.id AND d.key = 'document_id'
            WHERE s.collection = ? AND s.scope = 'METADATA'
            ORDER BY e.id
        """
        vector_ids: list[str] = []
        chunk_ids: list[int] = []
        document_ids: set[int] = set()
        for vector_id, chunk_id, document_id in connection.execute(query, (collection_id,)):
            raw_vector = str(vector_id)
            vector_ids.append(raw_vector)
            raw_chunk = chunk_id
            if raw_chunk in (None, "") and raw_vector.startswith("chunk-"):
                raw_chunk = raw_vector[6:]
            try:
                chunk_ids.append(int(raw_chunk))
            except (TypeError, ValueError):
                continue
            try:
                document_ids.add(int(document_id))
            except (TypeError, ValueError):
                pass
    finally:
        connection.close()
    after = {"size": database.stat().st_size, "mtime_ns": database.stat().st_mtime_ns}
    return {
        "collection_name": collection_name,
        "vector_count": len(vector_ids),
        "vector_ids_sha256": canonical_sha256(vector_ids),
        "chunk_ids": sorted(chunk_ids),
        "chunk_ids_sha256": canonical_sha256(sorted(chunk_ids)),
        "document_metadata_count": len(document_ids),
        "embedding_fingerprint": str(
            metadata.get("embedding_fingerprint")
            or metadata.get("model_fingerprint")
            or ""
        ),
        "splitter_fingerprint": str(metadata.get("splitter_fingerprint") or ""),
        "pipeline_fingerprint": str(
            metadata.get("pipeline_fingerprint")
            or metadata.get("pipeline_version")
            or ""
        ),
        "distance_metric": str(metadata.get("hnsw:space") or ""),
        "metadata": metadata,
        "sqlite_signature_before": before,
        "sqlite_signature_after": after,
        "store_unchanged": before == after,
        "read_only": True,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


def _public_document(row: Mapping[str, Any], *, include_chunks: bool) -> dict[str, Any]:
    result = {
        "document_id": row.get("document_id"),
        "law_number": row.get("law_number"),
        "title": row.get("title"),
        "stored_status": row.get("stored_status") or row.get("status"),
        "status": row.get("status"),
        "domain": row.get("canonical_domain"),
        "scope_reason": row.get("scope_reason"),
        "decision_reason": row.get("decision_reason"),
        "reason_kept": row.get("reason_kept") or [],
        "score_components": row.get("score_components"),
        "required_procedure_mappings": row.get("required_procedure_mappings") or [],
        "metadata_repair_fields": row.get("metadata_repair_fields") or [],
        "foundation_document": bool(row.get("foundation_document")),
        "golden_required": bool(row.get("golden_required")),
        "chunk_count": len(row.get("chunk_ids") or []),
        "article_count": int(row.get("article_count") or 0),
    }
    if include_chunks:
        result["expected_chunk_ids"] = list(row.get("chunk_ids") or [])
    return result


def run(
    *,
    legal_as_of: date,
    target_count: int,
    output_dir: Path,
    chroma_path: Path,
    active_collection: str,
    candidate_collection: str,
    classification_path: Path,
    remediation_path: Path,
    golden_paths: list[Path],
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    classification, classification_input = _classification(classification_path)
    remediation_rows, remediation_input = _remediation(remediation_path)
    required_laws, procedure_mappings, golden_inputs = _golden_evidence(golden_paths)
    baseline_rows, database_inventory = _postgres_rows(
        legal_as_of=legal_as_of, classification=classification
    )
    supplemental_rows: list[dict[str, Any]] = []
    if remediation_rows:
        supplemental_rows, _ = _postgres_rows(
            legal_as_of=legal_as_of,
            classification=classification,
            document_ids=sorted(remediation_rows),
            serving_only=False,
            statuses=("active", "staging"),
        )
    supplemental_by_id = {
        int(row["document_id"]): row for row in supplemental_rows
    }
    missing_remediation_rows = sorted(set(remediation_rows) - set(supplemental_by_id))
    if missing_remediation_rows:
        raise RuntimeError(
            f"remediation_documents_missing_from_database:{missing_remediation_rows}"
        )
    pool_by_id = {int(row["document_id"]): dict(row) for row in baseline_rows}
    for document_id, decision in remediation_rows.items():
        row = dict(supplemental_by_id[document_id])
        row["stored_status"] = row.get("status")
        row["domain"] = str(decision.get("domain") or "").strip()
        row["scope_reason"] = str(decision.get("reason") or "candidate_scope_override")
        row["candidate_remediation_kind"] = str(decision.get("action") or "")
        if str(decision.get("action") or "").strip() == "candidate_staging_include":
            # Staging is intentionally not runtime-active.  For this isolated
            # benchmark pool, official validity is independently verified by
            # the remediation report, so classify it as a candidate-only
            # current row while retaining the stored status above.
            row["status"] = "active"
        pool_by_id[document_id] = row
    rows = [pool_by_id[key] for key in sorted(pool_by_id)]
    for row in rows:
        row["required_procedure_mappings"] = sorted(
            procedure_mappings.get(normalized_law_number(row.get("law_number")), set())
        )
    prepared = prepare_documents(
        rows,
        legal_as_of=legal_as_of,
        required_law_numbers=required_laws,
    )
    kept, excluded, coverage = select_candidate(
        prepared,
        target_count=target_count,
        coverage_per_cell=3,
        max_domain_share=0.45,
    )
    vector_inventory = _collection_inventory(
        chroma_path=chroma_path, collection_name=active_collection
    )
    database_chunk_ids = sorted(
        int(chunk_id)
        for row in baseline_rows
        for chunk_id in (row.get("chunk_ids") or [])
    )
    vector_chunk_ids = vector_inventory.pop("chunk_ids")
    vector_missing = sorted(set(database_chunk_ids) - set(vector_chunk_ids))
    vector_orphan = sorted(set(vector_chunk_ids) - set(database_chunk_ids))
    vector_integrity = {
        "expected_chunk_count": len(database_chunk_ids),
        "observed_vector_count": vector_inventory["vector_count"],
        "missing_chunk_count": len(vector_missing),
        "orphan_chunk_count": len(vector_orphan),
        "missing_chunk_ids_sample": vector_missing[:100],
        "orphan_chunk_ids_sample": vector_orphan[:100],
        "exact_chunk_set_match": not vector_missing and not vector_orphan,
    }

    baseline_projection = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "legal_as_of": legal_as_of.isoformat(),
        "collection": {
            key: value
            for key, value in vector_inventory.items()
            if key != "metadata"
        },
        "database": database_inventory,
        "vector_integrity": vector_integrity,
        "document_ids": [row["document_id"] for row in baseline_rows],
        "expected_chunk_ids": database_chunk_ids,
        "documents": [
            {
                "document_id": row["document_id"],
                "law_number": row.get("law_number"),
                "stored_status": row.get("status"),
                "domain": row.get("domain"),
                "scope_reason": row.get("scope_reason"),
                "expected_chunk_ids": list(row.get("chunk_ids") or []),
            }
            for row in baseline_rows
        ],
        "retrieval_configuration": {
            "active_collection": active_collection,
            "candidate_count": os.getenv("LEGAL_CANDIDATE_COUNT", "runtime_default"),
            "lexical_candidate_count": os.getenv(
                "LEGAL_LEXICAL_CANDIDATE_COUNT", "runtime_default"
            ),
            "ranking_strategy": "unchanged_runtime_configuration",
            "model_embedding_chunking_reranker_prompt_validator": "frozen_for_comparison",
        },
        "inputs": {"classification": classification_input, "golden": golden_inputs},
        "read_only": True,
        "corpus_mutated": False,
        "vectors_mutated": False,
        "active_pointer_changed": False,
    }
    baseline_fingerprint = canonical_sha256(baseline_projection)
    baseline = {
        **baseline_projection,
        "generated_at": generated_at,
        "manifest_sha256": baseline_fingerprint,
    }

    blockers = required_source_blockers(prepared, required_laws)
    historical_sources = _historical_source_inventory(required_laws)
    for blocker in blockers:
        matches = historical_sources.get(blocker["normalized_law_number"], [])
        blocker["database_matches_all_statuses"] = matches
        active_matches = [item for item in matches if item.get("status") == "active"]
        historical_matches = [item for item in matches if item.get("status") != "active"]
        if active_matches:
            blocker["remediation_class"] = "active_scope_or_metadata_conflict"
        elif historical_matches:
            blocker["remediation_class"] = "historical_only_verify_current_replacement"
        else:
            blocker["remediation_class"] = "missing_from_database"
    selected_domain_counts = coverage["summary"]["selected_domain_counts"]
    coverage_balanced = (
        all(int(selected_domain_counts.get(domain) or 0) > 0 for domain in (
            "ho_tich_chung_thuc",
            "dat_dai_xay_dung",
            "an_sinh_y_te_giao_duc",
            "cu_tru_an_ninh",
            "khieu_nai_to_cao_xu_phat",
            "hanh_chinh_cong",
        ))
        and max(selected_domain_counts.values(), default=0) <= math.ceil(target_count * 0.45)
        and all(
            int(cell["selected_documents"])
            >= min(3, int(cell["eligible_documents"]))
            for cell in coverage["matrix"]
        )
    )
    prebuild_blockers: list[str] = []
    if not vector_integrity["exact_chunk_set_match"]:
        prebuild_blockers.append("baseline_vector_chunk_set_mismatch")
    if blockers:
        prebuild_blockers.append("required_golden_source_blockers")
    if not coverage_balanced:
        prebuild_blockers.append("candidate_coverage_not_balanced")
    gate_blockers = [
        "candidate_collection_not_built",
        "candidate_collection_retrieval_isolation_not_exercised",
        "golden_baseline_candidate_comparison_not_run",
        "rollback_rehearsal_not_run",
        "activation_approval_not_granted",
    ]
    gate_blockers.extend(prebuild_blockers)

    candidate_documents = [
        _public_document(row, include_chunks=True) for row in kept
    ]
    candidate_document_ids = {int(row["document_id"]) for row in kept}
    expected_candidate_chunk_count = sum(
        len(row.get("chunk_ids") or []) for row in kept
    )
    candidate_chunk_ids = {
        int(chunk_id)
        for row in kept
        for chunk_id in (row.get("chunk_ids") or [])
    }
    baseline_document_ids = {int(row["document_id"]) for row in baseline_rows}
    pool_document_ids = {int(row["document_id"]) for row in rows}
    baseline_chunk_id_set = set(database_chunk_ids)
    candidate_projection = {
        "schema_version": SCHEMA_VERSION,
        "legal_as_of": legal_as_of.isoformat(),
        "status": (
            "ready_for_candidate_build" if not prebuild_blockers else "proposed_no_go"
        ),
        "baseline_manifest_sha256": baseline_fingerprint,
        "baseline_collection": active_collection,
        "candidate_collection": candidate_collection,
        "target_document_count": target_count,
        "selected_document_count": len(kept),
        "expected_vector_count": sum(len(row.get("chunk_ids") or []) for row in kept),
        "expected_article_count": sum(int(row.get("article_count") or 0) for row in kept),
        "selection_configuration": {
            "coverage_per_cell": 3,
            "max_domain_share": 0.45,
            "model_embedding_chunking_reranker_prompt_validator": "unchanged",
        },
        "selection_summary": coverage["summary"],
        "prebuild_gate": {
            "allowed": not prebuild_blockers,
            "coverage_balanced": coverage_balanced,
            "blockers": prebuild_blockers,
        },
        "coverage_matrix": coverage["matrix"],
        "manifest_integrity": {
            "candidate_documents_are_pool_subset": candidate_document_ids
            <= pool_document_ids,
            "candidate_documents_outside_baseline": len(
                candidate_document_ids - baseline_document_ids
            ),
            "candidate_chunks_outside_baseline": len(
                candidate_chunk_ids - baseline_chunk_id_set
            ),
            "unique_document_count": len(candidate_document_ids),
            "unique_chunk_count": len(candidate_chunk_ids),
            "duplicate_document_count": len(kept) - len(candidate_document_ids),
            "duplicate_chunk_count": expected_candidate_chunk_count
            - len(candidate_chunk_ids),
        },
        "retrieval_isolation_contract": {
            "benchmark_only": True,
            "vector_chunk_filter": "same_manifest_exact_chunk_ids",
            "lexical_sql_filter": "same_manifest_document_ids_before_limit",
            "hydration_filter": "same_manifest_document_ids_and_chunk_ids",
            "activation_pointer_unchanged": True,
        },
        "inputs": {
            "classification": classification_input,
            "golden": golden_inputs,
            "remediation": remediation_input,
        },
        "documents": candidate_documents,
        "required_source_blockers": blockers,
        "gate_blockers": sorted(set(gate_blockers)),
        "activation": {
            "allowed": False,
            "active_pointer_changed": False,
            "reason_code": "golden_and_integrity_gates_pending",
        },
        "read_only": True,
        "corpus_mutated": False,
        "vectors_mutated": False,
    }
    candidate = {
        **candidate_projection,
        "generated_at": generated_at,
        "manifest_sha256": canonical_sha256(candidate_projection),
    }

    baseline_path = output_dir / "legal-serving-baseline-7245-v1.json"
    candidate_path = output_dir / f"legal-serving-candidate-{target_count}-v1.json"
    kept_path = output_dir / f"kept-{target_count}-v1.jsonl"
    excluded_path = output_dir / f"excluded-from-{target_count}-v1.jsonl"
    repair_path = output_dir / "metadata-repairable-v1.jsonl"
    blocker_path = output_dir / "required-source-blockers-v1.json"
    scope_conflict_path = output_dir / "scope-conflicts-v1.jsonl"
    coverage_path = output_dir / f"coverage-matrix-{target_count}-v1.json"
    _atomic_json(baseline_path, baseline)
    _atomic_json(candidate_path, candidate)
    _jsonl(kept_path, [_public_document(row, include_chunks=False) for row in kept])
    _jsonl(excluded_path, [_public_document(row, include_chunks=False) for row in excluded])
    _jsonl(
        repair_path,
        [
            _public_document(row, include_chunks=False)
            for row in prepared
            if row.get("status") == "metadata_repairable"
        ],
    )
    _atomic_json(
        blocker_path,
        {
            "schema_version": "legal-corpus-required-source-blockers-v1",
            "generated_at": generated_at,
            "legal_as_of": legal_as_of.isoformat(),
            "count": len(blockers),
            "blockers": blockers,
        },
    )
    _jsonl(
        scope_conflict_path,
        [
            _public_document(row, include_chunks=False)
            | {"classification_proposal": row.get("classification_proposal")}
            for row in prepared
            if row.get("status") == "out_of_scope"
            and (row.get("golden_required") or row.get("canonical_domain"))
        ],
    )
    _atomic_json(
        coverage_path,
        {
            "schema_version": "legal-corpus-coverage-matrix-v1",
            "generated_at": generated_at,
            "legal_as_of": legal_as_of.isoformat(),
            **coverage,
        },
    )
    summary = {
        "status": candidate["status"],
        "baseline_documents": len(baseline_rows),
        "baseline_chunks": len(database_chunk_ids),
        "baseline_vectors": vector_inventory["vector_count"],
        "candidate_documents": len(kept),
        "candidate_expected_vectors": candidate["expected_vector_count"],
        "hard_filter_status_counts": coverage["summary"]["status_counts"],
        "required_source_blockers": len(blockers),
        "gate_blockers": candidate["gate_blockers"],
        "baseline_manifest": str(baseline_path.resolve()),
        "candidate_manifest": str(candidate_path.resolve()),
        "kept_ledger": str(kept_path.resolve()),
        "excluded_ledger": str(excluded_path.resolve()),
        "metadata_repair_ledger": str(repair_path.resolve()),
        "required_source_blockers_report": str(blocker_path.resolve()),
        "scope_conflicts_ledger": str(scope_conflict_path.resolve()),
        "coverage_report": str(coverage_path.resolve()),
        "stores_mutated": False,
        "active_pointer_changed": False,
    }
    _atomic_json(output_dir / "build-summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="store_true", required=True)
    parser.add_argument("--legal-as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--target-count", type=int, default=3000)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "reports" / "corpus-thinning"
    )
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--active-collection", default=DEFAULT_ACTIVE_COLLECTION)
    parser.add_argument("--candidate-collection", default=DEFAULT_CANDIDATE_COLLECTION)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--remediation", type=Path, default=DEFAULT_REMEDIATION)
    parser.add_argument("--golden", type=Path, action="append")
    args = parser.parse_args(argv)
    summary = run(
        legal_as_of=args.legal_as_of,
        target_count=args.target_count,
        output_dir=args.output_dir.resolve(),
        chroma_path=args.chroma_path.resolve(),
        active_collection=args.active_collection,
        candidate_collection=args.candidate_collection,
        classification_path=args.classification.resolve(),
        remediation_path=args.remediation.resolve(),
        golden_paths=[path.resolve() for path in (args.golden or DEFAULT_GOLDENS)],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] in {"go", "ready_for_candidate_build"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
