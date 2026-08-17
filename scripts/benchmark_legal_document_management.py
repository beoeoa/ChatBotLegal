"""Benchmark metadata-only management pagination on an isolated temp table.

The script never reads or writes legal corpus rows. PostgreSQL creates the table in
the current session and drops it automatically when the transaction rolls back.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import quantiles
from time import perf_counter

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def _database_url() -> str:
    values = dotenv_values(ROOT / ".env") if (ROOT / ".env").is_file() else {}
    configured = str(
        os.getenv("LEGAL_DATABASE_URL")
        or values.get("LEGAL_RELEASE_DATABASE_URL")
        or "postgresql+psycopg2://postgres:123456@127.0.0.1:5432/legal_chatbot"
    )
    return configured.replace("host.docker.internal", "127.0.0.1")


def run(*, documents: int, iterations: int) -> dict[str, object]:
    engine = create_engine(_database_url(), connect_args={"connect_timeout": 5})
    timings: list[float] = []
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    """
                    CREATE TEMP TABLE benchmark_legal_management (
                        id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        law_number VARCHAR(120),
                        issuing_agency TEXT,
                        document_type VARCHAR(120),
                        status VARCHAR(40),
                        source_url TEXT,
                        effective_date DATE,
                        expired_date DATE,
                        article_count INTEGER,
                        chunk_count INTEGER
                    ) ON COMMIT DROP
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO benchmark_legal_management (
                        id, title, law_number, issuing_agency, document_type,
                        status, source_url, effective_date, expired_date,
                        article_count, chunk_count
                    )
                    SELECT
                        value,
                        'Văn bản pháp luật ' || value,
                        value || '/2026/QĐ-TEST',
                        CASE WHEN value % 3 = 0 THEN 'UBND thành phố Hải Phòng' ELSE 'Cơ quan trung ương' END,
                        CASE WHEN value % 2 = 0 THEN 'Quyết định' ELSE 'Nghị định' END,
                        CASE WHEN value % 10 = 0 THEN 'archived' ELSE 'active' END,
                        CASE WHEN value % 25 = 0 THEN NULL ELSE 'https://example.gov.vn/' || value END,
                        DATE '2025-01-01' + (value % 500),
                        CASE WHEN value % 20 = 0 THEN DATE '2026-01-01' ELSE NULL END,
                        value % 80,
                        value % 240
                    FROM generate_series(1, :documents) value
                    """
                ),
                {"documents": documents},
            )
            connection.execute(
                text(
                    "CREATE INDEX benchmark_legal_management_filter_idx "
                    "ON benchmark_legal_management (status, effective_date DESC, id DESC)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX benchmark_legal_management_number_idx "
                    "ON benchmark_legal_management (law_number)"
                )
            )
            connection.execute(text("ANALYZE benchmark_legal_management"))
            filtered_statement = text(
                """
                SELECT id, title, law_number, issuing_agency, document_type,
                       status, source_url, effective_date, expired_date,
                       article_count, chunk_count
                FROM benchmark_legal_management
                WHERE status = 'active'
                  AND effective_date <= DATE '2026-08-09'
                  AND (expired_date IS NULL OR expired_date > DATE '2026-08-09')
                ORDER BY effective_date DESC NULLS LAST, id DESC
                LIMIT 30 OFFSET :offset
                """
            )
            exact_statement = text(
                """
                SELECT id, title, law_number, issuing_agency, document_type,
                       status, source_url, effective_date, expired_date,
                       article_count, chunk_count
                FROM benchmark_legal_management
                WHERE law_number = :law_number
                ORDER BY effective_date DESC NULLS LAST, id DESC
                LIMIT 30
                """
            )
            connection.execute(filtered_statement, {"offset": 0}).all()
            connection.execute(exact_statement, {"law_number": "42001/2026/QĐ-TEST"}).all()
            for index in range(iterations):
                started = perf_counter()
                if index % 2:
                    rows = connection.execute(
                        exact_statement,
                        {"law_number": f"{(index * 7919) % documents + 1}/2026/QĐ-TEST"},
                    ).all()
                else:
                    rows = connection.execute(
                        filtered_statement,
                        {"offset": (index * 30) % max(documents - 30, 1)},
                    ).all()
                if len(rows) > 30:
                    raise AssertionError("metadata page exceeded the bounded limit")
                timings.append((perf_counter() - started) * 1000)
        finally:
            transaction.rollback()
    ordered = sorted(timings)
    p95 = quantiles(ordered, n=100, method="inclusive")[94] if len(ordered) > 1 else ordered[0]
    return {
        "documents": documents,
        "iterations": iterations,
        "p50_ms": round(ordered[len(ordered) // 2], 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(max(ordered), 2),
        "target_p95_ms": 1000,
        "passed": p95 < 1000,
        "storage": "PostgreSQL temporary table; transaction rolled back",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=100_000)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    report = run(
        documents=max(1_000, min(args.documents, 1_000_000)),
        iterations=max(2, min(args.iterations, 200)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
