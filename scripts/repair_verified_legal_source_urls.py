"""Correct only pre-verified official VBPL URLs in the legal corpus.

This is a narrow provenance repair, not a legal-content import.  Each target
has an expected current value, so the script fails closed if the corpus changed
since review.  Use ``--apply`` only after checking the dry-run report.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import bindparam, create_engine, text


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "reports" / "feature006" / "verified-legal-source-url-repair.json"

VERIFIED_REPAIRS = (
    {
        "law_number": "60/2014/QH13",
        "expected_source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=46746",
        "source_url": "https://vbpl.vn/bocongan/Pages/vbpq-toanvan.aspx?ItemID=46746&Keyword=",
        "verification_source": "VBPL official page; reviewed 2026-07-30",
    },
    {
        "law_number": "02/2011/QH13",
        "expected_source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=27192",
        "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=27325&Keyword=",
        "verification_source": "VBPL official page; reviewed 2026-07-30",
    },
)


def release_database_url(values: dict[str, Any]) -> str:
    url = str(values.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is not configured")
    return url.replace("@host.docker.internal:", "@127.0.0.1:")


def plan_repairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_law = {str(row.get("law_number") or ""): row for row in rows}
    planned: list[dict[str, Any]] = []
    for rule in VERIFIED_REPAIRS:
        law_number = rule["law_number"]
        row = by_law.get(law_number)
        if not row:
            raise RuntimeError(f"verified legal document is missing: {law_number}")
        if str(row.get("status") or "").casefold() != "active":
            raise RuntimeError(f"verified legal document is not active: {law_number}")
        current_url = str(row.get("source_url") or "").strip()
        if current_url != rule["expected_source_url"]:
            raise RuntimeError(
                f"source URL changed since review for {law_number}: {current_url!r}"
            )
        planned.append(
            {
                "document_id": int(row["id"]),
                "law_number": law_number,
                "title": str(row.get("title") or ""),
                "before_source_url": current_url,
                "after_source_url": rule["source_url"],
                "verification_source": rule["verification_source"],
            }
        )
    return planned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    engine = create_engine(release_database_url(dotenv_values(ROOT / ".env")))
    law_numbers = [item["law_number"] for item in VERIFIED_REPAIRS]
    with engine.begin() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT id, law_number, title, source_url, status "
                    "FROM legal_documents WHERE law_number IN :law_numbers"
                ).bindparams(bindparam("law_numbers", expanding=True)),
                {"law_numbers": law_numbers},
            ).mappings()
        ]
        planned = plan_repairs(rows)
        if args.apply:
            for item in planned:
                connection.execute(
                    text(
                        "UPDATE legal_documents SET source_url = :source_url "
                        "WHERE id = :document_id"
                    ),
                    {"source_url": item["after_source_url"], "document_id": item["document_id"]},
                )

    report = {
        "schema_version": "feature006-verified-source-url-repair-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "APPLIED" if args.apply else "DRY_RUN",
        "repairs": planned,
        "legal_content_changed": False,
        "effective_status_changed": False,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "repair_count": len(planned)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
