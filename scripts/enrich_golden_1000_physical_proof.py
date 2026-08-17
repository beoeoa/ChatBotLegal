# -*- coding: utf-8 -*-
"""Attach read-only PostgreSQL article evidence to the Golden candidate set.

The database is queried only; no legal record, chunk, vector, or production
collection is changed.  A case remains needs_legal_review when any expected
source cannot be matched to an article-level quote and official URL.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-luna"
AS_OF = date(2026, 8, 11)


def norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", "", value)


def article_numbers(value: str | None) -> list[str]:
    if value is None:
        return []
    raw = str(value).replace("–", "-").replace(";", ",")
    result: list[str] = []
    for part in raw.split(","):
        m = re.search(r"\d+", part)
        if m:
            result.append(m.group(0))
    return result


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LEGAL_RELEASE_DATABASE_URL="):
            return line.split("=", 1)[1].replace("postgresql+psycopg2://", "postgresql://").replace("host.docker.internal", "127.0.0.1")
    raise RuntimeError("LEGAL_RELEASE_DATABASE_URL missing")


def main() -> None:
    source_path = OUT / "golden-1000-luna.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    conn = psycopg2.connect(db_url(), connect_timeout=10)
    cur = conn.cursor()
    cur.execute("select id,title,law_number,status,effective_date,expired_date,source_url from legal_documents")
    documents = cur.fetchall()
    by_law: dict[str, list[dict]] = {}
    for row in documents:
        item = {"id": row[0], "title": row[1], "law_number": row[2], "status": row[3], "effective_date": row[4], "expired_date": row[5], "source_url": row[6]}
        by_law.setdefault(norm(row[2]), []).append(item)
    cur.execute("select id,document_id,article_number,title,content,effective_from,effective_to,status from legal_articles")
    articles_by_doc: dict[int, dict[str, dict]] = {}
    for row in cur.fetchall():
        item = {"id": row[0], "document_id": row[1], "article_number": row[2], "title": row[3], "content": row[4] or "", "effective_from": row[5], "effective_to": row[6], "status": row[7]}
        articles_by_doc.setdefault(row[1], {})[norm(re.sub(r"\D.*$", "", str(row[2] or "")) or row[2])] = item

    matched_sources = 0
    proof_sources = 0
    missing_sources = 0
    cases_with_proof = 0
    unresolved_cases = 0
    status_counts = Counter()
    for case in payload["cases"]:
        case_all_proof = True
        for source in case.get("expected_sources", []):
            law_key = norm(source.get("law_number"))
            candidates = by_law.get(law_key, [])
            if not candidates:
                # A few historical rows contain a Unicode variant; fall back
                # to a digit-and-year prefix match without guessing the law.
                digits = re.match(r"\d+/\d{4}", str(source.get("law_number") or ""))
                if digits:
                    candidates = [d for key, rows in by_law.items() if key.startswith(norm(digits.group(0))) for d in rows]
            if not candidates:
                case_all_proof = False
                missing_sources += 1
                continue
            candidates.sort(key=lambda d: (d["status"] == "active", d["expired_date"] is None, d["id"]), reverse=True)
            doc = candidates[0]
            matched_sources += 1
            source["db_document_id"] = doc["id"]
            source["db_status"] = doc["status"]
            source["db_effective_date"] = doc["effective_date"].isoformat() if doc["effective_date"] else None
            source["db_expired_date"] = doc["expired_date"].isoformat() if doc["expired_date"] else None
            source["db_title"] = doc["title"]
            source["proof"] = source.get("proof") if isinstance(source.get("proof"), dict) else {}
            source["proof"]["official_url"] = source["proof"].get("official_url") or doc["source_url"] or ""
            numbers = article_numbers(source.get("article"))
            article_map = articles_by_doc.get(doc["id"], {})
            chosen = [article_map.get(norm(n)) for n in numbers]
            chosen = [a for a in chosen if a and a.get("content")]
            if numbers and len(chosen) == len(numbers):
                chunks = []
                for a in chosen:
                    # Keep the quote byte-for-byte equal to the normalized
                    # article content.  The article number/title are already
                    # stored as metadata; prepending them would invalidate
                    # char_start/char_end as a physical position in content.
                    chunks.append(a["content"])
                quote = "\n\n".join(chunks)
                source["proof"].update({
                    "quote": quote,
                    "page_number": None,
                    "char_start": 0,
                    "char_end": len(quote),
                    "bounding_box": None,
                    "checked_at": AS_OF.isoformat(),
                    "provenance": "postgresql:legal_articles.content (read-only)",
                })
                source["physical_proof_status"] = "ready_for_human_review"
                proof_sources += 1
            else:
                source["physical_proof_status"] = "missing_article_or_content"
                case_all_proof = False
        if case_all_proof and case.get("expected_sources"):
            cases_with_proof += 1
            if case.get("review_status") == "needs_legal_review":
                case["review_status"] = "pending_human_review"
        else:
            unresolved_cases += 1
            case["review_status"] = "needs_legal_review"
        for source in case.get("expected_sources", []):
            status_counts[source.get("physical_proof_status", "missing") or "missing"] += 1
        case["unresolved_reason"] = None if case_all_proof and case.get("expected_sources") else "At least one expected source lacks a matched legal_articles article/quote or official URL; human legal review required."
        case["risk_tags"] = sorted(set(case.get("risk_tags", []) + (["physical_proof_ready"] if case_all_proof and case.get("expected_sources") else ["physical_proof_missing"])))
    conn.close()
    payload["summary"]["physical_proof_count"] = cases_with_proof
    payload["summary"]["legal_review_count"] = unresolved_cases
    payload["summary"]["physical_proof_source_count"] = proof_sources
    payload["summary"]["db_source_match_count"] = matched_sources
    payload["summary"]["db_source_missing_count"] = missing_sources
    payload["summary"]["proof_status_counts"] = dict(status_counts)
    payload["status"] = "pending_human_legal_review"
    source_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUT / "physical-proof-db-audit.json").write_text(json.dumps({"schema_version": "golden-1000-luna-db-proof-v1", "as_of": AS_OF.isoformat(), "read_only": True, "summary": payload["summary"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cases": len(payload["cases"]), "cases_with_physical_proof": cases_with_proof, "legal_review": unresolved_cases, "source_proof": proof_sources, "source_missing": missing_sources, "status_counts": dict(status_counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
