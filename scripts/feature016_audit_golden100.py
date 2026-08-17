"""Read-only Golden 100 source/database/vector audit.

This script deliberately does not import, update, delete, re-index, or switch
any production collection.  It uses the existing management API only to read
exact vector membership for documents represented in the reviewed dataset.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2
import requests

AS_OF = date(2026, 8, 10)
OFFICIAL_HOSTS = {"vbpl.vn", "www.vbpl.vn", "vanban.chinhphu.vn"}
LAW_RE = re.compile(r"^\s*(.+?)\s*#\s*(.*)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    return parser.parse_args()


def load_dataset(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != "2.0" or len(data.get("cases", [])) != 100:
        raise ValueError("Golden dataset must remain schema 2.0 with exactly 100 cases")
    return data


def split_articles(value: Any) -> list[str]:
    if value is None or str(value).strip() == "":
        return []
    out: list[str] = []
    for part in re.split(r"[,;]", str(value)):
        cleaned = part.strip()
        if cleaned:
            out.append(cleaned)
    return out


def official_url_check(url: str) -> dict[str, Any]:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    result: dict[str, Any] = {
        "url": url or None,
        "official_domain": host in OFFICIAL_HOSTS,
        "http_status": None,
        "reachable": False,
        "law_number_found_in_page": None,
        "error": None,
    }
    if host not in OFFICIAL_HOSTS or parsed.scheme not in {"http", "https"}:
        result["error"] = "non_official_or_invalid_url"
        return result
    try:
        response = requests.get(
            url,
            timeout=12,
            allow_redirects=True,
            headers={"User-Agent": "ChatBotLegal-Golden-Audit/1.0"},
        )
        result["http_status"] = response.status_code
        result["reachable"] = response.ok
        result["final_url"] = response.url
        # Content is only used for an identity check; it is not copied into
        # the production corpus and no claim is generated from this heuristic.
        result["content_sha256"] = hashlib.sha256(response.content).hexdigest()
        result["content_bytes"] = len(response.content)
    except requests.RequestException as exc:
        result["error"] = type(exc).__name__
    return result


def api_vector_check(api: str, doc_id: int) -> dict[str, Any]:
    try:
        response = requests.get(f"{api.rstrip('/')}/management/documents/{doc_id}", timeout=30)
        if response.status_code != 200:
            return {"status": "unavailable", "reason_code": f"http_{response.status_code}"}
        payload = response.json()
        vectors = payload.get("vectors") or {}
        return {
            "status": vectors.get("status"),
            "reason_code": vectors.get("reason_code"),
            "expected": vectors.get("expected"),
            "collections": vectors.get("collections") or {},
        }
    except (requests.RequestException, ValueError) as exc:
        return {"status": "unavailable", "reason_code": type(exc).__name__}


def connect_db():
    conn = psycopg2.connect(
        "host=127.0.0.1 port=5432 dbname=legal_chatbot user=postgres "
        "password=123456 connect_timeout=8"
    )
    conn.set_session(readonly=True, autocommit=True)
    return conn


def audit(dataset: dict[str, Any], api: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        for index, source in enumerate(case.get("expected_sources", []), start=1):
            law_number = str(source.get("law_number") or "").strip()
            expected.append(
                {
                    "case_id": case["case_id"],
                    "domain": case.get("domain"),
                    "source_index": index,
                    "law_number": law_number,
                    "article": source.get("article"),
                    "clause": source.get("clause"),
                    "point": source.get("point"),
                    "reason": source.get("reason"),
                }
            )
    laws = sorted({row["law_number"] for row in expected if row["law_number"]})
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id, d.title, d.law_number, d.document_type,
                       d.issuing_agency, d.scope, d.source_url, d.status,
                       d.issued_date, d.effective_date, d.expired_date,
                       d.field_id, COUNT(DISTINCT a.id) AS article_count,
                       COUNT(DISTINCT c.id) AS chunk_count,
                       COUNT(DISTINCT c.id) FILTER (WHERE q.eligible IS TRUE) AS eligible_chunk_count
                FROM legal_documents d
                LEFT JOIN legal_articles a ON a.document_id=d.id
                LEFT JOIN legal_article_chunks c ON c.article_id=a.id
                LEFT JOIN legal_chunk_quality q ON q.chunk_id=c.id
                WHERE lower(trim(d.law_number)) = ANY(%s)
                GROUP BY d.id
                ORDER BY d.id
                """,
                [[item.lower() for item in laws]],
            )
            docs = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT d.id AS document_id, a.article_number,
                       COUNT(c.id) AS chunk_count,
                       COUNT(c.id) FILTER (WHERE q.eligible IS TRUE) AS eligible_chunk_count,
                       array_agg(c.id ORDER BY c.chunk_index) AS chunk_ids
                FROM legal_documents d
                JOIN legal_articles a ON a.document_id=d.id
                LEFT JOIN legal_article_chunks c ON c.article_id=a.id
                LEFT JOIN legal_chunk_quality q ON q.chunk_id=c.id
                WHERE lower(trim(d.law_number)) = ANY(%s)
                GROUP BY d.id, a.id, a.article_number
                ORDER BY d.id, a.id
                """,
                [[item.lower() for item in laws]],
            )
            article_rows = [dict(zip([desc[0] for desc in cur.description], row)) for row in cur.fetchall()]
    finally:
        conn.close()

    by_law: dict[str, list[dict[str, Any]]] = {}
    for row in docs:
        by_law.setdefault(str(row["law_number"]).strip().lower(), []).append(row)
    articles_by_doc: dict[int, dict[str, dict[str, Any]]] = {}
    for row in article_rows:
        articles_by_doc.setdefault(int(row["document_id"]), {})[str(row["article_number"]).strip()] = row

    vector_by_doc: dict[int, dict[str, Any]] = {}
    doc_ids = sorted({int(row["id"]) for rows in by_law.values() for row in rows})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(api_vector_check, api, doc_id): doc_id for doc_id in doc_ids}
        for future in concurrent.futures.as_completed(futures):
            vector_by_doc[futures[future]] = future.result()

    source_rows: list[dict[str, Any]] = []
    for row in expected:
        matches = by_law.get(row["law_number"].lower(), [])
        # Duplicate rows are retained for legal review rather than silently
        # choosing a document with a different issuer or version.
        doc = matches[0] if len(matches) == 1 else None
        source_check: dict[str, Any] = {
            **row,
            "db_document_count": len(matches),
            "document_id": int(doc["id"]) if doc else None,
            "document_title": doc["title"] if doc else None,
            "db_status": doc["status"] if doc else None,
            "db_effective_date": doc["effective_date"].isoformat() if doc and doc["effective_date"] else None,
            "db_expired_date": doc["expired_date"].isoformat() if doc and doc["expired_date"] else None,
            "db_source_url": doc["source_url"] if doc else None,
            "official": official_url_check(str(doc["source_url"] or "")) if doc else {
                "url": None, "official_domain": False, "reachable": False,
                "error": "document_missing"
            },
        }
        if doc:
            article_map = articles_by_doc.get(int(doc["id"]), {})
            expected_articles = split_articles(row["article"])
            matched_articles = [article_map.get(article) for article in expected_articles]
            source_check["expected_articles"] = expected_articles
            source_check["matched_articles"] = [bool(item) for item in matched_articles]
            source_check["article_missing"] = [article for article, item in zip(expected_articles, matched_articles) if not item]
            source_check["article_chunk_counts"] = {
                article: int(article_map[article]["chunk_count"] or 0)
                for article in expected_articles if article in article_map
            }
            source_check["article_eligible_chunk_counts"] = {
                article: int(article_map[article]["eligible_chunk_count"] or 0)
                for article in expected_articles if article in article_map
            }
            source_check["vector"] = vector_by_doc.get(int(doc["id"]), {"status": "unavailable"})
        else:
            source_check.update({"expected_articles": split_articles(row["article"]), "matched_articles": [], "article_missing": split_articles(row["article"]), "article_chunk_counts": {}, "article_eligible_chunk_counts": {}, "vector": {"status": "unavailable", "reason_code": "document_missing"}})
        source_check["current_eligible"] = bool(doc and doc["status"] == "active" and (not doc["effective_date"] or doc["effective_date"] <= AS_OF) and (not doc["expired_date"] or doc["expired_date"] >= AS_OF))
        source_check["reason_code"] = (
            "document_missing" if not doc else
            "duplicate_document_identity" if len(matches) > 1 else
            "metadata_incorrect" if not source_check["official"].get("official_domain") else
            "article_missing" if source_check["article_missing"] else
            "embedding_missing" if source_check["vector"].get("status") not in {"available", "degraded"} or any((source_check["vector"].get("collections") or {}).get(name, {}).get("missing", 0) for name in ("fast", "expanded")) else
            "ok"
        )
        source_rows.append(source_check)

    def count(predicate):
        return sum(1 for item in source_rows if predicate(item))

    summary = {
        "schema_version": "feature016-golden100-readonly-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": AS_OF.isoformat(),
        "dataset_cases": len(dataset["cases"]),
        "expected_source_rows": len(source_rows),
        "unique_law_numbers": len(laws),
        "unique_documents_found": len(doc_ids),
        "official_url_reachable": count(lambda x: x["official"].get("reachable") is True),
        "official_domain_ok": count(lambda x: x["official"].get("official_domain") is True),
        "document_missing": count(lambda x: x["reason_code"] == "document_missing"),
        "article_missing": count(lambda x: x["reason_code"] == "article_missing"),
        "embedding_missing": count(lambda x: x["reason_code"] == "embedding_missing"),
        "metadata_incorrect": count(lambda x: x["reason_code"] == "metadata_incorrect"),
        "ok": count(lambda x: x["reason_code"] == "ok"),
        "current_eligible": count(lambda x: x["current_eligible"] is True),
        "observed_active_collection": "legal_chunks_vnlegal_lal_haiphong",
        "note": "Read-only; no import, approval, re-index, collection switch, or corpus mutation performed.",
    }
    db_coverage = {
        **summary,
        "sources": source_rows,
        "document_rows": [
            {
                **{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()},
                "vector": vector_by_doc.get(int(row["id"]), {"status": "unavailable"}),
            }
            for row in docs
        ],
    }
    return {
        "schema_version": "feature016-golden100-source-evidence-v1",
        "generated_at": summary["generated_at"],
        "official_hosts": sorted(OFFICIAL_HOSTS),
        "legal_as_of": AS_OF.isoformat(),
        "sources": source_rows,
    }, db_coverage


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(Path(args.dataset))
    evidence, coverage = audit(dataset, args.api)
    (out_dir / "source-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out_dir / "database-coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: coverage[k] for k in ("expected_source_rows", "unique_law_numbers", "unique_documents_found", "official_url_reachable", "document_missing", "article_missing", "embedding_missing", "metadata_incorrect", "ok", "current_eligible")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
