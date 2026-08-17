"""Build DB-4 primary/support Chroma collections by copying existing vectors.

The builder is intentionally independent of the active collection pointer. It
uses the verified DB-2 chunk ledger as the eligibility contract and copies
embeddings, documents and metadata from an existing Chroma source collection.
No embedding model is called and no PostgreSQL imported row is changed.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import chromadb
from dotenv import dotenv_values
from sqlalchemy import bindparam, create_engine, text


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB2 = ROOT / "reports" / "feature005" / "db2-shadow-20260723"
DEFAULT_DB3 = ROOT / "reports" / "feature005" / "db3-coverage-20260723"
DEFAULT_STEP2 = ROOT / "reports" / "feature005" / "step2-data-gap-20260724"
DEFAULT_CHROMA = Path(r"J:\legal-chatbot-data\chroma_store")
DEFAULT_SOURCE = "legal_chunks_vnlegal_lal"
DEFAULT_PRIMARY = "legal_chunks_lechan_primary_v20260724_r2"
DEFAULT_SUPPORT = "legal_chunks_lechan_support_v20260724_r2"
BATCH_SIZE = 512


def _present(value: Any) -> Any:
    return [] if value is None else value


def _sha256_ids(ids: set[str]) -> str:
    digest = hashlib.sha256()
    for identifier in sorted(ids):
        digest.update(identifier.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_ledger(
    db2_dir: Path,
) -> tuple[
    dict[str, set[str]],
    dict[str, dict[str, Any]],
    dict[int, str],
]:
    tiers: dict[str, set[str]] = {"primary": set(), "support": set()}
    chunks: dict[str, dict[str, Any]] = {}
    document_domains: dict[int, str] = {}
    with (db2_dir / "documents.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if not row.get("eligible") or row.get("tier") not in tiers:
                continue
            failed_gates = sorted(
                str(name)
                for name, passed in (row.get("hard_gate") or {}).items()
                if not passed
            )
            domain = str(row.get("domain_code") or "").strip()
            if failed_gates or not domain:
                raise ValueError(
                    "eligible document failed hard gate: "
                    f"document={row.get('document_id')} failed={failed_gates or ['domain']}"
                )
            document_domains[int(row["document_id"])] = domain
    with (db2_dir / "chunks.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            identifier = str(row["chunk_id"])
            chunks[identifier] = row
            if row.get("eligible") and row.get("tier") in tiers:
                if row.get("canonical_chunk_id") not in (None, row["chunk_id"]):
                    raise ValueError(f"eligible canonical duplicate: {identifier}")
                if int(row["document_id"]) not in document_domains:
                    raise ValueError(
                        f"eligible chunk has no eligible document: {identifier}"
                    )
                tiers[str(row["tier"])].add(identifier)
    return tiers, chunks, document_domains


def _source_ids(source: Any) -> set[str]:
    identifiers: set[str] = set()
    for offset in range(0, int(source.count()), 10_000):
        identifiers.update(
            str(identifier)
            for identifier in (
                source.get(limit=10_000, offset=offset, include=[]).get("ids") or []
            )
        )
    return identifiers


def _safe_metadata(metadata: dict[str, Any], *, tier: str, scope_version: str) -> dict[str, Any]:
    result = {
        str(key): value
        for key, value in (metadata or {}).items()
        if value is not None and value != ""
    }
    result["serving_tier"] = tier
    result["scope_version"] = scope_version
    result["db4_source"] = "copied_existing_vector"
    return result


def _database_url() -> str:
    configured = str(dotenv_values(ROOT / ".env").get("LEGAL_RELEASE_DATABASE_URL") or "")
    if configured:
        return configured.replace("@host.docker.internal:", "@127.0.0.1:")
    raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is required for DB-4 hydration")


def _hydrate_database_rows(
    *,
    chunk_ids: set[str],
    scope_version: str,
) -> dict[str, dict[str, Any]]:
    """Hydrate canonical documents/metadata for source vectors with sparse metadata."""

    if not chunk_ids:
        return {}
    engine = create_engine(_database_url(), pool_pre_ping=True)
    rows: dict[str, dict[str, Any]] = {}
    ordered = sorted(int(identifier) for identifier in chunk_ids)
    statement = text(
        """
        SELECT
            c.id AS chunk_id,
            c.chunk_index,
            c.heading AS chunk_heading,
            c.content,
            a.id AS article_id,
            a.article_number,
            a.title AS article_title,
            a.status AS article_status,
            a.effective_from AS article_effective_from,
            a.effective_to AS article_effective_to,
            d.id AS document_id,
            d.title AS document_title,
            d.law_number,
            d.document_type,
            d.issuing_agency,
            d.scope,
            d.sector,
            d.status AS document_status,
            d.issued_date,
            d.effective_date,
            d.expired_date,
            d.source_url,
            d.field_id,
            f.name AS field_name,
            scope_row.domain AS domain_slug
        FROM legal_article_chunks c
        JOIN legal_articles a ON a.id = c.article_id
        JOIN legal_documents d ON d.id = a.document_id
        LEFT JOIN legal_fields f ON f.id = d.field_id
        LEFT JOIN LATERAL (
            SELECT s.domain
            FROM legal_search_scope s
            WHERE s.document_id = d.id AND s.included = TRUE
            ORDER BY s.evaluated_at DESC NULLS LAST
            LIMIT 1
        ) scope_row ON TRUE
        WHERE c.id IN :chunk_ids
        """
    ).bindparams(bindparam("chunk_ids", expanding=True))
    with engine.connect() as connection:
        for offset in range(0, len(ordered), 1000):
            batch = ordered[offset : offset + 1000]
            for row in connection.execute(statement, {"chunk_ids": batch}).mappings():
                item = dict(row)
                item["scope_version"] = scope_version
                rows[str(item["chunk_id"])] = item
    engine.dispose()
    return rows


def _load_step2_incremental_scope(
    step2_dir: Path,
) -> tuple[dict[int, str], dict[str, Any]]:
    manifest = json.loads((step2_dir / "manifest.json").read_text(encoding="utf-8"))
    completion = json.loads(
        (step2_dir / "completion.json").read_text(encoding="utf-8")
    )
    if completion.get("status") != "PASS":
        raise RuntimeError("Step 2 data-gap completion is not PASS")
    if completion.get("corpus_deleted") or completion.get("full_corpus_reembedded"):
        raise RuntimeError("Step 2 artifact violates the non-destructive contract")
    documents: dict[int, str] = {}
    for outcome in manifest.get("import_outcomes") or []:
        document_id = int(outcome["document_id"])
        scope = outcome.get("scope_after") or {}
        if not scope.get("included"):
            raise ValueError(f"Step 2 document is not included: {document_id}")
        domain = str(scope.get("domain") or "").strip()
        if not domain or domain.casefold() == "unknown":
            raise ValueError(f"Step 2 document has no approved domain: {document_id}")
        documents[document_id] = domain
    if not documents:
        raise RuntimeError("Step 2 contains no approved runtime documents")
    return documents, manifest


def _eligible_incremental_chunks(
    *,
    document_domains: dict[int, str],
    legal_as_of: str,
    scope_version: str,
) -> tuple[set[str], dict[str, dict[str, Any]]]:
    """Return eligible Step-2 chunks without mutating imported rows."""

    engine = create_engine(_database_url(), pool_pre_ping=True)
    statement = text(
        """
        SELECT c.id AS chunk_id
        FROM legal_article_chunks c
        JOIN legal_articles a ON a.id = c.article_id
        JOIN legal_documents d ON d.id = a.document_id
        LEFT JOIN LATERAL (
            SELECT q.eligible, q.canonical_chunk_id, q.quality_reasons
            FROM legal_chunk_quality q
            WHERE q.chunk_id = c.id
            ORDER BY q.assessed_at DESC
            LIMIT 1
        ) quality ON TRUE
        WHERE d.id IN :document_ids
          AND btrim(coalesce(c.content, '')) <> ''
          AND coalesce(a.status, 'active') <> 'expired'
          AND (a.effective_from IS NULL OR a.effective_from <= :as_of)
          AND (a.effective_to IS NULL OR a.effective_to > :as_of)
          AND (
              quality.eligible IS NULL
              OR (
                  quality.eligible = TRUE
                  AND quality.canonical_chunk_id IS NULL
                  AND NOT coalesce(quality.quality_reasons, ARRAY[]::text[])
                      && ARRAY['empty_content', 'exact_duplicate',
                               'noisy_article_title']
              )
          )
        ORDER BY c.id
        """
    ).bindparams(bindparam("document_ids", expanding=True))
    with engine.connect() as connection:
        identifiers = {
            str(row[0])
            for row in connection.execute(
                statement,
                {
                    "document_ids": sorted(document_domains),
                    "as_of": date.fromisoformat(legal_as_of),
                },
            )
        }
    engine.dispose()
    rows = _hydrate_database_rows(
        chunk_ids=identifiers,
        scope_version=scope_version,
    )
    for identifier, row in rows.items():
        document_id = int(row["document_id"])
        if document_id not in document_domains:
            raise ValueError(f"unexpected Step 2 document for chunk {identifier}")
        _validate_hydrated_eligibility(
            row,
            legal_as_of=legal_as_of,
            manifest_domain=document_domains[document_id],
        )
    if set(rows) != identifiers:
        raise RuntimeError(
            "Step 2 hydration mismatch: "
            f"expected={len(identifiers)} actual={len(rows)}"
        )
    return identifiers, rows


def _passage(row: dict[str, Any]) -> str:
    return "\n".join(
        str(row.get(key) or "").strip()
        for key in (
            "document_title",
            "law_number",
            "document_type",
            "issuing_agency",
            "scope",
            "sector",
            "field_name",
            "article_title",
            "chunk_heading",
            "content",
        )
        if str(row.get(key) or "").strip()
    )


def _hydrated_metadata(
    row: dict[str, Any],
    *,
    tier: str,
    scope_version: str,
    manifest_domain: str | None = None,
) -> dict[str, Any]:
    values = {
        "chunk_id": int(row["chunk_id"]),
        "article_id": int(row["article_id"]),
        "chunk_index": int(row.get("chunk_index") or 0),
        "document_id": int(row["document_id"]),
        "document_title": row.get("document_title"),
        "law_number": row.get("law_number"),
        "document_type": row.get("document_type"),
        "issuing_agency": row.get("issuing_agency"),
        "scope": row.get("scope"),
        "sector": row.get("sector"),
        "document_status": row.get("document_status"),
        "article_status": row.get("article_status"),
        "field_id": row.get("field_id"),
        "field_name": row.get("field_name"),
        "article_number": row.get("article_number"),
        "article_title": row.get("article_title"),
        "source_url": row.get("source_url"),
        "effective_date": (
            row["effective_date"].isoformat()
            if row.get("effective_date")
            else None
        ),
        "expired_date": (
            row["expired_date"].isoformat()
            if row.get("expired_date")
            else None
        ),
        "domain_slug": manifest_domain or row.get("domain_slug"),
        "serving_tier": tier,
        "scope_version": scope_version,
        "db4_source": "copied_existing_vector_hydrated_metadata",
    }
    return {
        str(key): value
        for key, value in values.items()
        if value is not None and value != ""
    }


def _official_source_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    return bool(
        parsed.scheme.casefold() == "https"
        and host
        and (
            host in {"vbpl.vn", "www.vbpl.vn", "chinhphu.vn"}
            or host.endswith(".chinhphu.vn")
            or host.endswith(".gov.vn")
        )
    )


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _validate_hydrated_eligibility(
    row: dict[str, Any],
    *,
    legal_as_of: str,
    manifest_domain: str | None = None,
) -> None:
    """Fail closed when a copied vector no longer satisfies serving hard gates."""

    chunk_id = row.get("chunk_id")
    if not _official_source_url(row.get("source_url")):
        raise ValueError(
            f"unapproved source provenance for serving chunk {chunk_id}"
        )
    active = {"active", "effective", "current", "con_hieu_luc"}
    document_status = str(row.get("document_status") or "").strip().casefold()
    article_status = str(row.get("article_status") or "active").strip().casefold()
    if document_status not in active or article_status not in active:
        raise ValueError(f"inactive serving chunk {chunk_id}")
    domain = str(manifest_domain or row.get("domain_slug") or "").strip()
    if not domain or domain.casefold() == "unknown":
        raise ValueError(f"missing serving domain for chunk {chunk_id}")
    if not str(row.get("content") or "").strip() and "content" in row:
        raise ValueError(f"empty serving chunk {chunk_id}")
    as_of = date.fromisoformat(legal_as_of)
    effective = _as_date(row.get("effective_date"))
    expired = _as_date(row.get("expired_date"))
    if effective and effective > as_of:
        raise ValueError(f"not-yet-effective serving chunk {chunk_id}")
    if expired and expired < as_of:
        raise ValueError(f"expired serving chunk {chunk_id}")


def _copy_tier(
    *,
    source: Any,
    target: Any,
    identifiers: set[str],
    chunks: dict[str, dict[str, Any]],
    tier: str,
    scope_version: str,
    hydrated_rows: dict[str, dict[str, Any]],
    document_domains: dict[int, str],
    resume: bool,
) -> dict[str, Any]:
    expected_ids = {f"chunk-{identifier}" for identifier in identifiers}
    source_missing: set[str] = set()
    copied = 0
    started = perf_counter()
    ordered = sorted(expected_ids)
    for offset in range(0, len(ordered), BATCH_SIZE):
        batch_ids = ordered[offset : offset + BATCH_SIZE]
        fetched = source.get(
            ids=batch_ids,
            include=["embeddings", "documents", "metadatas"],
        )
        actual_ids = [str(identifier) for identifier in fetched.get("ids") or []]
        source_missing.update(set(batch_ids) - set(actual_ids))
        if not actual_ids:
            continue
        if resume:
            existing = set(
                str(identifier)
                for identifier in (target.get(ids=actual_ids, include=[]).get("ids") or [])
            )
        else:
            existing = set()
        pending_positions = [
            index for index, identifier in enumerate(actual_ids) if identifier not in existing
        ]
        if not pending_positions:
            continue
        target.upsert(
            ids=[actual_ids[index] for index in pending_positions],
            embeddings=[
                fetched["embeddings"][index] for index in pending_positions
            ],
            documents=[
                _passage(hydrated_rows[str(actual_ids[index][6:])])
                for index in pending_positions
            ],
            metadatas=[
                _hydrated_metadata(
                    hydrated_rows[str(actual_ids[index][6:])],
                    tier=tier,
                    scope_version=scope_version,
                    manifest_domain=document_domains[
                        int(hydrated_rows[str(actual_ids[index][6:])]["document_id"])
                    ],
                )
                for index in pending_positions
            ],
        )
        copied += len(pending_positions)
    if source_missing:
        raise RuntimeError(
            json.dumps(
                {
                    "tier": tier,
                    "missing_source_vectors": sorted(source_missing)[:20],
                    "missing_count": len(source_missing),
                }
            )
        )
    return {
        "expected_vectors": len(expected_ids),
        "copied_this_run": copied,
        "actual_vectors": int(target.count()),
        "id_sha256": _sha256_ids(
            {
                str(identifier)
                for identifier in (target.get(limit=target.count(), include=[]).get("ids") or [])
            }
        ),
        "elapsed_seconds": round(perf_counter() - started, 2),
    }


def _validate_collection(
    *,
    collection: Any,
    expected_ids: set[str],
    chunks: dict[str, dict[str, Any]],
    tier: str,
) -> dict[str, Any]:
    actual_ids = {
        str(identifier)
        for identifier in (collection.get(limit=collection.count(), include=[]).get("ids") or [])
    }
    rows = collection.get(
        limit=collection.count(),
        include=["documents", "metadatas", "embeddings"],
    )
    ids = [str(identifier) for identifier in rows.get("ids") or []]
    metadatas = rows.get("metadatas") or []
    documents = rows.get("documents") or []
    embeddings = _present(rows.get("embeddings"))
    metadata_by_id = dict(zip(ids, metadatas))
    expected_chroma_ids = {f"chunk-{identifier}" for identifier in expected_ids}
    stale = actual_ids - expected_chroma_ids
    missing = expected_chroma_ids - actual_ids
    empty = [
        identifier
        for identifier, document in zip(ids, documents)
        if not str(document or "").strip()
    ]
    bad_metadata = [
        identifier
        for identifier, metadata in metadata_by_id.items()
        if str((metadata or {}).get("serving_tier") or "") != tier
        or not str((metadata or {}).get("chunk_id") or "").strip()
    ]
    orphan = [
        identifier
        for identifier, metadata in metadata_by_id.items()
        if str((metadata or {}).get("chunk_id") or "")
        not in chunks
    ]
    no_embedding = [
        identifier
        for identifier, embedding in zip(ids, embeddings)
        if embedding is None or len(embedding) == 0
    ]
    duplicate = len(ids) != len(set(ids))
    document_ids = {
        str((metadata or {}).get("document_id"))
        for metadata in metadatas
        if (metadata or {}).get("document_id") is not None
    }
    domain_counts = Counter(
        str((metadata or {}).get("domain_slug") or "unknown")
        for metadata in metadatas
    )
    unknown_domain_count = int(domain_counts.get("unknown", 0))
    return {
        "expected_vectors": len(expected_chroma_ids),
        "actual_vectors": len(actual_ids),
        "id_sha256": _sha256_ids(actual_ids),
        "expected_id_sha256": _sha256_ids(expected_chroma_ids),
        "missing_count": len(missing),
        "stale_count": len(stale),
        "empty_count": len(empty),
        "orphan_count": len(orphan),
        "duplicate_count": int(duplicate),
        "canonical_duplicate_count": 0,
        "missing_embedding_count": len(no_embedding),
        "metadata_linkage_count": len(document_ids),
        "domain_counts": dict(sorted(domain_counts.items())),
        "unknown_domain_count": unknown_domain_count,
        "valid": not any(
            (
                missing,
                stale,
                empty,
                orphan,
                duplicate,
                no_embedding,
                bad_metadata,
                unknown_domain_count,
            )
        ),
    }


def _ann_hydration_probe(collection: Any) -> dict[str, Any]:
    rows = collection.get(limit=collection.count(), include=["embeddings", "metadatas"])
    by_domain: dict[str, tuple[list[float], str]] = {}
    for identifier, embedding, metadata in zip(
        rows.get("ids") or [],
        _present(rows.get("embeddings")),
        rows.get("metadatas") or [],
    ):
        domain = str((metadata or {}).get("domain_slug") or "unknown")
        if (
            domain not in by_domain
            and embedding is not None
            and len(embedding) > 0
        ):
            by_domain[domain] = (embedding, str(identifier))
    failures: list[str] = []
    probed = 0
    for domain, (embedding, expected_id) in sorted(by_domain.items()):
        result = collection.query(
            query_embeddings=[embedding],
            n_results=1,
            include=["metadatas"],
        )
        returned = str((result.get("ids") or [[]])[0][0]) if result.get("ids") else ""
        if not returned:
            failures.append(domain)
            continue
        hydrated = collection.get(ids=[returned], include=["metadatas", "documents"])
        if not (hydrated.get("metadatas") and hydrated.get("documents")):
            failures.append(domain)
        probed += 1
    return {
        "domains_probed": probed,
        "domain_count": len(by_domain),
        "failures": failures,
        "valid": not failures and probed == len(by_domain),
    }


def build(
    *,
    db2_dir: Path,
    db3_dir: Path,
    step2_dir: Path,
    chroma_path: Path,
    source_name: str,
    primary_name: str,
    support_name: str,
    apply: bool,
    resume: bool,
) -> dict[str, Any]:
    db2_manifest = json.loads((db2_dir / "manifest.json").read_text(encoding="utf-8"))
    db3_verification = json.loads(
        (db3_dir / "verification-approved.json").read_text(encoding="utf-8")
    )
    if db2_manifest.get("status") != "verified":
        raise RuntimeError("DB-2 manifest is not verified")
    if db3_verification.get("status") != "approved":
        raise RuntimeError("DB-3 legal review is not approved")
    tiers, chunks, document_domains = _load_ledger(db2_dir)
    step2_document_domains, step2_manifest = _load_step2_incremental_scope(
        step2_dir
    )
    scope_digest = hashlib.sha256()
    scope_digest.update(_file_sha256(db2_dir / "manifest.json").encode("ascii"))
    scope_digest.update(_file_sha256(step2_dir / "manifest.json").encode("ascii"))
    scope_version = scope_digest.hexdigest()
    legal_as_of = str(
        step2_manifest.get("legal_as_of")
        or db2_manifest.get("legal_as_of")
        or ""
    )
    incremental_ids, incremental_rows = _eligible_incremental_chunks(
        document_domains=step2_document_domains,
        legal_as_of=legal_as_of,
        scope_version=scope_version,
    )
    # Step 2 sources are reviewed direct sources and therefore belong in the
    # primary collection. Existing DB-2 IDs de-duplicate naturally.
    tiers["primary"].update(incremental_ids)
    for identifier in incremental_ids:
        row = incremental_rows[identifier]
        chunks.setdefault(
            identifier,
            {
                "chunk_id": int(identifier),
                "document_id": int(row["document_id"]),
                "tier": "primary",
                "eligible": True,
                "canonical_chunk_id": None,
                "source": "feature005_step2_incremental",
            },
        )
    document_domains.update(step2_document_domains)
    client = chromadb.PersistentClient(path=str(chroma_path))
    names = {item.name for item in client.list_collections()}
    if source_name not in names:
        raise RuntimeError(f"source collection missing: {source_name}")
    active_pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = (
        active_pointer_path.read_text(encoding="utf-8").strip()
        if active_pointer_path.exists()
        else None
    )
    source = client.get_collection(source_name)
    source_ids_before = _source_ids(source)
    hydrated_rows: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "schema_version": "feature005-db4-serving-v2",
        "status": "planned" if not apply else "building",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "copy_existing_embeddings",
        "source_collection": source_name,
        "primary_collection": primary_name,
        "support_collection": support_name,
        "scope_manifest_sha256": scope_version,
        "db2_manifest_sha256": _file_sha256(db2_dir / "manifest.json"),
        "step2_manifest_sha256": _file_sha256(step2_dir / "manifest.json"),
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_before,
        "source_count_before": int(source.count()),
        "source_id_sha256_before": _sha256_ids(source_ids_before),
        "ledger_counts": {tier: len(ids) for tier, ids in tiers.items()},
        "embedding_calls": 0,
        "ingestion_incremental": True,
        "incremental_document_count": len(step2_document_domains),
        "incremental_chunk_count": len(incremental_ids),
    }
    if not apply:
        report["status"] = "dry_run"
        report["source_coverage"] = {
            tier: len({f"chunk-{identifier}" for identifier in ids} & source_ids_before)
            for tier, ids in tiers.items()
        }
        return report

    all_eligible_ids = tiers["primary"] | tiers["support"]
    hydrated_rows = _hydrate_database_rows(
        chunk_ids=all_eligible_ids,
        scope_version=scope_version,
    )
    if set(hydrated_rows) != all_eligible_ids:
        raise RuntimeError(
            f"database hydration mismatch: expected={len(all_eligible_ids)} "
            f"actual={len(hydrated_rows)}"
        )
    for identifier, row in hydrated_rows.items():
        _validate_hydrated_eligibility(
            row,
            legal_as_of=legal_as_of,
            manifest_domain=document_domains[int(row["document_id"])],
        )
    report["hydrated_documents"] = len(hydrated_rows)

    targets = {}
    for name, tier in ((primary_name, "primary"), (support_name, "support")):
        if name in names:
            if not resume:
                raise RuntimeError(
                    f"immutable target already exists: {name}; choose a new version "
                    "or use --resume after verifying its manifest"
                )
            target = client.get_collection(name)
            metadata = target.metadata or {}
            if str(metadata.get("db4_scope_manifest_sha256") or "") != scope_version:
                raise RuntimeError(
                    f"existing target {name} belongs to another manifest; refusing overwrite"
                )
            if metadata.get("db4_documents_hydrated") != "postgresql":
                raise RuntimeError(
                    f"existing target {name} is not a verified DB-4 collection"
                )
            current_ids = _source_ids(target)
            expected_ids = {
                f"chunk-{identifier}" for identifier in tiers[tier]
            }
            unexpected = current_ids - expected_ids
            if unexpected:
                raise RuntimeError(
                    f"immutable target {name} contains {len(unexpected)} "
                    "unexpected vector(s)"
                )
        else:
            target = None
        if target is None:
            target = client.create_collection(
                name,
                metadata={
                    "db4_version": "20260724-r2",
                    "db4_scope_manifest_sha256": scope_version,
                    "db4_source_collection": source_name,
                    "db4_documents_hydrated": "postgresql",
                    "serving_tier": tier,
                },
            )
        targets[tier] = target

    for tier in ("primary", "support"):
        _copy_tier(
            source=source,
            target=targets[tier],
            identifiers=tiers[tier],
            chunks=chunks,
            tier=tier,
            scope_version=scope_version,
            hydrated_rows=hydrated_rows,
            document_domains=document_domains,
            resume=resume,
        )

    report["collections"] = {
        tier: _validate_collection(
            collection=targets[tier],
            expected_ids=tiers[tier],
            chunks=chunks,
            tier=tier,
        )
        for tier in ("primary", "support")
    }
    report["ann_hydration"] = {
        tier: _ann_hydration_probe(targets[tier])
        for tier in ("primary", "support")
    }
    source_ids_after = _source_ids(source)
    pointer_after = (
        active_pointer_path.read_text(encoding="utf-8").strip()
        if active_pointer_path.exists()
        else None
    )
    report["source_count_after"] = int(source.count())
    report["source_id_sha256_after"] = _sha256_ids(source_ids_after)
    report["source_unchanged"] = (
        source_ids_before == source_ids_after
        and report["source_count_before"] == report["source_count_after"]
    )
    report["active_pointer_unchanged"] = pointer_before == pointer_after
    report["status"] = (
        "verified"
        if report["source_unchanged"]
        and report["active_pointer_unchanged"]
        and all(item["valid"] for item in report["collections"].values())
        and all(item["valid"] for item in report["ann_hydration"].values())
        else "verification_failed"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db2-dir", type=Path, default=DEFAULT_DB2)
    parser.add_argument("--db3-dir", type=Path, default=DEFAULT_DB3)
    parser.add_argument("--step2-dir", type=Path, default=DEFAULT_STEP2)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--primary", default=DEFAULT_PRIMARY)
    parser.add_argument("--support", default=DEFAULT_SUPPORT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = build(
        db2_dir=args.db2_dir.resolve(),
        db3_dir=args.db3_dir.resolve(),
        step2_dir=args.step2_dir.resolve(),
        chroma_path=args.chroma_path.resolve(),
        source_name=args.source,
        primary_name=args.primary,
        support_name=args.support,
        apply=args.apply,
        resume=args.resume,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # Windows service shells may use cp1252. Keep stdout ASCII-safe while the
    # persisted report remains UTF-8 and human-readable.
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] in {"dry_run", "verified"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
