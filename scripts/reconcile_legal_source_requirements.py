#!/usr/bin/env python3
"""Reconcile official legal-source requirements against the current corpus.

The default run is read-only and writes only privacy-safe report artifacts.
Optional discovery uses exact instrument numbers against the existing official
VBPL adapter.  Optional enqueue/process creates unapproved source-gap
candidates only; it never imports, indexes, embeds, approves or switches a
collection.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_source_completion import (
    build_legal_source_gap_job,
    build_requirements,
    classify_requirement,
    extract_legal_basis,
    legal_basis_context_reason,
)
from api.official_source_diagnostics import classify_official_source_failure
from api.source_gap_jobs import (
    DEFAULT_CANDIDATE_DIR,
    DEFAULT_STORE_PATH,
    enqueue_source_gap_job,
    process_source_gap_job,
)
from scripts.feature005_db1_snapshot import database_url, safe_database_target
from scripts.resolve_three_tier_form_sources import (
    DEFAULT_CACHE_DIR,
    fetch_official_document,
)


DEFAULT_PROCEDURES = (
    ROOT / "notebook_data" / "forms" / "canonical_procedure_sources_v1.json"
)
DEFAULT_EXPECTED = (
    ROOT
    / "reports"
    / "feature005"
    / "step2-data-gap-20260724"
    / "expected-sources.jsonl"
)
DEFAULT_OUTPUT_ROOT = ROOT / "reports" / "feature005"
ACTIVE_COLLECTION = "legal_chunks_lechan_primary_v20260723"

DOCUMENT_SQL = """
SELECT d.id, d.title, d.law_number, d.document_type, d.issuing_agency,
       d.scope, d.sector, d.status, d.effective_date, d.expired_date,
       d.source_url, f.name AS field_name,
       count(c.id) FILTER (
           WHERE btrim(coalesce(c.content, '')) <> ''
             AND coalesce(a.status, 'active') <> 'expired'
             AND (a.effective_from IS NULL OR a.effective_from <= :as_of)
             AND (a.effective_to IS NULL OR a.effective_to > :as_of)
       ) AS valid_chunk_count
FROM legal_documents d
LEFT JOIN legal_fields f ON f.id = d.field_id
LEFT JOIN legal_articles a ON a.document_id = d.id
LEFT JOIN legal_article_chunks c ON c.article_id = a.id
GROUP BY d.id, d.title, d.law_number, d.document_type, d.issuing_agency,
         d.scope, d.sector, d.status, d.effective_date, d.expired_date,
         d.source_url, f.name
ORDER BY d.id
"""

COUNT_SQL = """
SELECT
  (SELECT count(*) FROM legal_documents) AS document_count,
  (SELECT count(*) FROM legal_articles) AS article_count,
  (SELECT count(*) FROM legal_article_chunks) AS chunk_count
"""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _feature_flag() -> bool:
    configured = os.getenv("LEGAL_SECTION_GROUNDING_ENABLED")
    if configured is None:
        configured = str(
            dotenv_values(ROOT / ".env").get("LEGAL_SECTION_GROUNDING_ENABLED")
            or "false"
        )
    return configured.strip().casefold() in {"1", "true", "yes", "on"}


def select_corpus_database_url(
    *,
    environment: Mapping[str, str] | None = None,
    settings: Mapping[str, Any] | None = None,
) -> str:
    """Select the runtime corpus DB without reusing isolated test credentials."""

    environment = environment if environment is not None else os.environ
    settings = settings if settings is not None else dotenv_values(ROOT / ".env")
    explicit = str(environment.get("LEGAL_CORPUS_DATABASE_URL") or "").strip()
    if explicit:
        return explicit.replace("@host.docker.internal:", "@127.0.0.1:")
    release = str(settings.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if release:
        return release.replace("@host.docker.internal:", "@127.0.0.1:")
    # This fallback is deliberately last: developer/test processes commonly
    # set LEGAL_DATABASE_URL to an isolated staging database.
    return database_url()


def _active_collection() -> str:
    return os.getenv("LEGAL_ACTIVE_COLLECTION", "").strip() or ACTIVE_COLLECTION


def _invalid_basis_counts(
    procedures: Sequence[Mapping[str, Any]],
    *,
    legal_as_of: date,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for procedure in procedures:
        if str(procedure.get("status") or "") != "OFFICIAL_PROCEDURE_MATCHED":
            continue
        for value in procedure.get("legal_basis") or []:
            number, name, reason = extract_legal_basis(
                value,
                legal_as_of=legal_as_of,
            )
            if reason is None and number:
                reason = legal_basis_context_reason(
                    law_number=number,
                    name=name,
                    expected_jurisdiction=str(
                        procedure.get("jurisdiction") or "Hải Phòng"
                    ),
                )
            if reason:
                counts[reason] += 1
    return dict(sorted(counts.items()))


def _corpus_state(legal_as_of: str) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    url = select_corpus_database_url()
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text("SET TRANSACTION READ ONLY"))
            table_exists = connection.execute(
                text("SELECT to_regclass('public.legal_documents')")
            ).scalar_one_or_none()
            if table_exists is None:
                raise RuntimeError("CORPUS_TABLES_NOT_FOUND_IN_SELECTED_DATABASE")
            rows = [
                dict(row)
                for row in connection.execute(
                    text(DOCUMENT_SQL),
                    {"as_of": date.fromisoformat(legal_as_of)},
                ).mappings()
            ]
            counts = {
                key: int(value or 0)
                for key, value in connection.execute(text(COUNT_SQL)).mappings().one().items()
            }
            transaction.rollback()
    finally:
        engine.dispose()
    return rows, counts, safe_database_target(url)


def execute(
    *,
    procedure_path: Path,
    expected_paths: Sequence[Path],
    output_dir: Path,
    legal_as_of: str,
    discover_missing: bool,
    enqueue: bool,
    process: bool,
    workers: int,
    timeout: float,
) -> dict[str, Any]:
    del workers  # Discovery is deliberately sequential and cache-first.
    as_of = date.fromisoformat(legal_as_of)
    procedure_payload = _read_json(procedure_path)
    procedures = [
        dict(item)
        for item in procedure_payload.get("procedures") or []
        if isinstance(item, Mapping)
    ]
    expected_rows = [
        row
        for path in expected_paths
        for row in _read_jsonl(path)
    ]
    requirements = build_requirements(
        procedures=procedures,
        expected_source_rows=expected_rows,
        legal_as_of=legal_as_of,
    )
    corpus_rows, corpus_counts, database_target = _corpus_state(legal_as_of)
    classifications = [
        {
            **classification,
            "origin_count": len(requirement.get("origin_ids") or []),
            "domains": requirement.get("domains") or [],
            "jurisdictions": requirement.get("jurisdictions") or [],
        }
        for requirement in requirements
        for classification in [classify_requirement(requirement, corpus_rows)]
    ]

    discovery_records: list[dict[str, Any]] = []
    job_records: list[dict[str, Any]] = []
    if discover_missing:
        requirement_by_id = {
            item["requirement_id"]: item for item in requirements
        }
        for classification in classifications:
            if classification["classification"] != "MISSING_LEGAL_SOURCE":
                continue
            requirement = requirement_by_id[classification["requirement_id"]]
            try:
                discovery = fetch_official_document(
                    requirement["law_number"],
                    timeout=timeout,
                    cache_dir=DEFAULT_CACHE_DIR,
                    refresh=False,
                )
                discovery_record = {
                    "requirement_id": requirement["requirement_id"],
                    "law_number": requirement["law_number"],
                    "status": str(discovery.get("status") or ""),
                    "reason_code": str(discovery.get("reason_code") or "UNCLASSIFIED"),
                    "source_domain": "vbpl.vn",
                }
                discovery_records.append(discovery_record)
                if discovery.get("status") != "found":
                    continue
                try:
                    job = build_legal_source_gap_job(requirement, discovery)
                except ValueError as exc:
                    discovery_record["candidate_reason_code"] = str(exc)
                    continue
                if enqueue:
                    enqueue_source_gap_job(job, store_path=DEFAULT_STORE_PATH)
                if process:
                    processed = process_source_gap_job(
                        job,
                        candidate_dir=DEFAULT_CANDIDATE_DIR,
                        checked_on=as_of,
                    )
                    if enqueue:
                        enqueue_source_gap_job(processed, store_path=DEFAULT_STORE_PATH)
                    job = processed
                job_records.append(
                    {
                        "job_id": job["job_id"],
                        "requirement_id": requirement["requirement_id"],
                        "law_number": requirement["law_number"],
                        "status": job["status"],
                        "reason_code": job["reason_code"],
                        "approved": False,
                        "runtime_eligible": False,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - structured external diagnosis
                discovery_records.append(
                    {
                        "requirement_id": requirement["requirement_id"],
                        "law_number": requirement["law_number"],
                        "status": "blocked_external",
                        "reason_code": classify_official_source_failure(exc),
                        "source_domain": "vbpl.vn",
                    }
                )

    output_dir.mkdir(parents=True, exist_ok=True)
    requirement_path = output_dir / "requirements.jsonl"
    classification_path = output_dir / "classifications.jsonl"
    discovery_path = output_dir / "source-attempts.jsonl"
    jobs_path = output_dir / "candidate-jobs.jsonl"
    _write_jsonl(requirement_path, requirements)
    _write_jsonl(classification_path, classifications)
    _write_jsonl(discovery_path, discovery_records)
    _write_jsonl(jobs_path, job_records)

    classification_counts = dict(
        sorted(Counter(item["classification"] for item in classifications).items())
    )
    discovery_counts = dict(
        sorted(Counter(item["reason_code"] for item in discovery_records).items())
    )
    status = (
        "BLOCKED_RELEASE"
        if classification_counts.get("MISSING_LEGAL_SOURCE", 0)
        or classification_counts.get("METADATA_INCOMPLETE", 0)
        or classification_counts.get("AMBIGUOUS_CORPUS_MATCH", 0)
        or classification_counts.get("EXPIRED_OR_SUPERSEDED", 0)
        else "DATA_RECONCILIATION_COMPLETE"
    )
    report = {
        "schema_version": "legal-source-completion-v1",
        "run_id": output_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of,
        "status": status,
        "candidate_only": True,
        "automated_approval": False,
        "corpus_mutated": False,
        "full_corpus_reembedded": False,
        "active_collection": _active_collection(),
        "feature_flag_enabled": _feature_flag(),
        "database_target": database_target,
        "counts": {
            **corpus_counts,
            "procedure_count": len(procedures),
            "official_procedure_count": sum(
                item.get("status") == "OFFICIAL_PROCEDURE_MATCHED"
                for item in procedures
            ),
            "legal_basis_occurrence_count": sum(
                len(item.get("legal_basis") or [])
                for item in procedures
                if item.get("status") == "OFFICIAL_PROCEDURE_MATCHED"
            ),
            "canonical_requirement_count": len(requirements),
            "expected_source_row_count": len(expected_rows),
            "source_attempt_count": len(discovery_records),
            "candidate_job_count": len(job_records),
        },
        "classification_counts": classification_counts,
        "invalid_basis_reason_counts": _invalid_basis_counts(
            procedures,
            legal_as_of=as_of,
        ),
        "discovery_reason_counts": discovery_counts,
        "artifacts": {
            "requirements": {
                "path": str(requirement_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(requirement_path),
            },
            "classifications": {
                "path": str(classification_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(classification_path),
            },
            "source_attempts": {
                "path": str(discovery_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(discovery_path),
            },
            "candidate_jobs": {
                "path": str(jobs_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": _sha256(jobs_path),
            },
        },
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }
    _write_json(output_dir / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--procedures", type=Path, default=DEFAULT_PROCEDURES)
    parser.add_argument("--expected-sources", type=Path, action="append")
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--discover-missing", action="store_true")
    parser.add_argument("--enqueue", action="store_true")
    parser.add_argument("--process", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.process and not args.enqueue:
        parser.error("--process requires --enqueue")
    expected_paths = args.expected_sources or [DEFAULT_EXPECTED]
    output_dir = args.output_dir or (
        DEFAULT_OUTPUT_ROOT
        / f"legal-source-completion-{datetime.now():%Y%m%d-%H%M%S}"
    )
    report = execute(
        procedure_path=args.procedures.resolve(),
        expected_paths=[path.resolve() for path in expected_paths],
        output_dir=output_dir.resolve(),
        legal_as_of=args.legal_as_of,
        discover_missing=args.discover_missing,
        enqueue=args.enqueue,
        process=args.process,
        workers=max(1, args.workers),
        timeout=max(1.0, args.timeout),
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "run_id": report["run_id"],
                "counts": report["counts"],
                "classification_counts": report["classification_counts"],
                "invalid_basis_reason_counts": report["invalid_basis_reason_counts"],
                "discovery_reason_counts": report["discovery_reason_counts"],
                "feature_flag_enabled": report["feature_flag_enabled"],
                "active_collection": report["active_collection"],
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["status"] == "DATA_RECONCILIATION_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
