"""Populate the non-destructive legal_chunk_quality sidecar."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

try:
    from scripts.backup_legal_retrieval import _database_url
except ModuleNotFoundError:  # Support direct script execution.
    from backup_legal_retrieval import _database_url


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts" / "postgres_retrieval_indexes" / "002_chunk_quality_up.sql"
QUALITY_VERSION = "legal-chunk-quality-v1"

ASSESS_SQL = """
WITH assessed AS (
    SELECT
        c.id AS chunk_id,
        a.id AS article_id,
        d.id AS document_id,
        legal_normalize_text(c.content) AS normalized_content,
        encode(digest(legal_normalize_text(c.content), 'sha256'), 'hex') AS content_hash,
        MIN(c.id) OVER (
            PARTITION BY d.id, a.id, encode(digest(legal_normalize_text(c.content), 'sha256'), 'hex')
        ) AS canonical_chunk_id,
        ROW_NUMBER() OVER (
            PARTITION BY d.id, a.id, encode(digest(legal_normalize_text(c.content), 'sha256'), 'hex')
            ORDER BY c.id
        ) AS duplicate_rank,
        CASE
            WHEN LENGTH(COALESCE(a.title, '')) > 180
              OR ARRAY_LENGTH(REGEXP_SPLIT_TO_ARRAY(COALESCE(a.title, ''), '\\s+'), 1) > 28
            THEN 'Điều ' || COALESCE(NULLIF(REGEXP_REPLACE(COALESCE(a.article_number, ''), '^Điều\\s+', '', 'i'), ''), '?')
            WHEN BTRIM(COALESCE(a.title, '')) = ''
            THEN 'Điều ' || COALESCE(NULLIF(REGEXP_REPLACE(COALESCE(a.article_number, ''), '^Điều\\s+', '', 'i'), ''), '?')
            ELSE 'Điều ' || COALESCE(NULLIF(REGEXP_REPLACE(COALESCE(a.article_number, ''), '^Điều\\s+', '', 'i'), ''), '?')
                 || '. ' || BTRIM(a.title)
        END AS cleaned_article_title,
        (BTRIM(COALESCE(c.content, '')) = '') AS empty_content,
        (BTRIM(COALESCE(d.title, '')) = '' OR BTRIM(COALESCE(d.law_number, '')) = ''
          OR BTRIM(COALESCE(d.issuing_agency, '')) = '' OR BTRIM(COALESCE(d.scope, '')) = ''
          OR BTRIM(COALESCE(d.source_url, '')) = '') AS missing_required_metadata,
        (d.effective_date IS NULL) AS unknown_effectivity,
        (COALESCE(scope_row.domain, group_row.group_slug, '') = '') AS missing_domain,
        (LENGTH(COALESCE(a.title, '')) > 180) AS noisy_title
    FROM legal_article_chunks c
    JOIN legal_articles a ON a.id = c.article_id
    JOIN legal_documents d ON d.id = a.document_id
    LEFT JOIN LATERAL (
        SELECT s.domain
        FROM legal_search_scope s
        WHERE s.document_id = d.id AND s.included = TRUE
        ORDER BY s.evaluated_at DESC NULLS LAST
        LIMIT 1
    ) scope_row ON TRUE
    LEFT JOIN LATERAL (
        SELECT g.group_slug
        FROM legal_commune_field_groups g
        WHERE g.field_id = d.field_id AND g.included = TRUE
        ORDER BY g.evaluated_at DESC NULLS LAST
        LIMIT 1
    ) group_row ON TRUE
)
INSERT INTO legal_chunk_quality (
    chunk_id, quality_version, eligible, canonical_chunk_id, content_hash,
    cleaned_article_title, quality_reasons, assessed_at
)
SELECT
    chunk_id,
    :quality_version,
    NOT (empty_content OR duplicate_rank > 1 OR missing_required_metadata OR unknown_effectivity OR missing_domain),
    CASE WHEN duplicate_rank > 1 THEN canonical_chunk_id ELSE NULL END,
    content_hash,
    cleaned_article_title,
    ARRAY_REMOVE(ARRAY[
        CASE WHEN empty_content THEN 'empty_content' END,
        CASE WHEN duplicate_rank > 1 THEN 'exact_duplicate' END,
        CASE WHEN missing_required_metadata THEN 'missing_required_metadata' END,
        CASE WHEN unknown_effectivity THEN 'unknown_effectivity' END,
        CASE WHEN missing_domain THEN 'missing_domain' END,
        CASE WHEN noisy_title THEN 'noisy_article_title' END
    ], NULL),
    NOW()
FROM assessed
ON CONFLICT (chunk_id, quality_version) DO UPDATE SET
    eligible = EXCLUDED.eligible,
    canonical_chunk_id = EXCLUDED.canonical_chunk_id,
    content_hash = EXCLUDED.content_hash,
    cleaned_article_title = EXCLUDED.cleaned_article_title,
    quality_reasons = EXCLUDED.quality_reasons,
    assessed_at = EXCLUDED.assessed_at
"""


def _split_sql(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def assess(*, apply: bool) -> dict[str, Any]:
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            before = connection.execute(
                text("SELECT COUNT(*) FROM legal_article_chunks")
            ).scalar_one()
            if apply:
                for statement in _split_sql(MIGRATION.read_text(encoding="utf-8")):
                    connection.execute(text(statement))
                connection.execute(text(ASSESS_SQL), {"quality_version": QUALITY_VERSION})
            table_exists = connection.execute(
                text("SELECT to_regclass('legal_chunk_quality') IS NOT NULL")
            ).scalar_one()
            counts = {}
            reasons: list[dict[str, Any]] = []
            if table_exists:
                rows = connection.execute(
                    text("""
                        SELECT eligible, COUNT(*) AS count
                        FROM legal_chunk_quality
                        WHERE quality_version = :quality_version
                        GROUP BY eligible
                    """),
                    {"quality_version": QUALITY_VERSION},
                ).mappings()
                counts = {str(bool(row["eligible"])).lower(): int(row["count"]) for row in rows}
                reasons = [dict(row) for row in connection.execute(
                    text("""
                        SELECT reason, COUNT(*) AS count
                        FROM legal_chunk_quality q,
                             UNNEST(q.quality_reasons) AS reason
                        WHERE q.quality_version = :quality_version
                        GROUP BY reason ORDER BY count DESC, reason
                    """),
                    {"quality_version": QUALITY_VERSION},
                ).mappings()]
            return {
                "mode": "apply" if apply else "dry-run",
                "quality_version": QUALITY_VERSION,
                "source_chunk_count": int(before),
                "sidecar_exists": bool(table_exists),
                "eligible_counts": counts,
                "quality_reasons": reasons,
                "source_rows_mutated": 0,
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = assess(apply=args.apply)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
