"""Read-only source inventory audit for the independent Golden 1000 build."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-complete"
AS_OF = date(2026, 8, 11)
OFFICIAL_PREFIXES = ("https://vbpl.vn/", "https://vanban.chinhphu.vn/")

DOMAIN_RULES = {
    "Hộ tịch/chứng thực": (
        "hộ tịch", "khai sinh", "khai tử", "kết hôn", "nuôi con nuôi",
        "chứng thực", "quốc tịch", "giám hộ", "nhận cha mẹ con",
    ),
    "Đất đai/xây dựng": (
        "đất đai", "quyền sử dụng đất", "thửa đất", "địa chính", "nhà ở",
        "xây dựng", "giấy phép xây dựng", "quy hoạch đô thị", "bất động sản",
    ),
    "Cư trú/an ninh": (
        "cư trú", "thường trú", "tạm trú", "lưu trú", "căn cước", "an ninh",
        "phòng cháy", "trật tự", "công an", "xuất cảnh", "nhập cảnh",
    ),
    "Khiếu nại/tố cáo/xử phạt": (
        "khiếu nại", "tố cáo", "xử phạt", "vi phạm hành chính", "tiếp công dân",
        "thanh tra", "cưỡng chế", "khởi kiện hành chính",
    ),
    "An sinh/y tế/giáo dục": (
        "bảo hiểm xã hội", "bảo hiểm y tế", "trợ cấp", "bảo trợ", "người có công",
        "y tế", "khám bệnh", "chữa bệnh", "giáo dục", "học phí", "trẻ em",
        "người cao tuổi", "người khuyết tật", "việc làm", "hộ nghèo",
    ),
}


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LEGAL_RELEASE_DATABASE_URL="):
            return (
                line.split("=", 1)[1]
                .replace("postgresql+psycopg2://", "postgresql://")
                .replace("host.docker.internal", "127.0.0.1")
            )
    raise RuntimeError("LEGAL_RELEASE_DATABASE_URL missing")


def norm(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def is_current(status: object, effective: object, expired: object) -> bool:
    status_text = norm(status)
    if "het hieu luc toan bo" in status_text or status_text in {"expired", "inactive", "repealed"}:
        return False
    if effective and effective > AS_OF:
        return False
    if expired and expired <= AS_OF:
        return False
    return True


def main() -> None:
    conn = psycopg2.connect(db_url(), connect_timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM legal_articles WHERE COALESCE(content, '') <> ''")
    total_articles = cur.fetchone()[0]
    cur.execute("""
        SELECT id, title, law_number, status, effective_date, expired_date, source_url
        FROM legal_documents
        WHERE (source_url LIKE 'https://vbpl.vn/%%'
               OR source_url LIKE 'https://vanban.chinhphu.vn/%%')
          AND (effective_date IS NULL OR effective_date <= %s)
          AND (expired_date IS NULL OR expired_date > %s)
          AND LOWER(COALESCE(status, '')) NOT IN ('expired', 'inactive', 'repealed')
        ORDER BY id
    """, (AS_OF, AS_OF))
    documents = cur.fetchall()
    document_domains: dict[int, list[str]] = {}
    document_rows = {}
    for document in documents:
        doc_id, title, law_number, status, effective, expired, source_url = document
        title_norm = norm(title)
        domains = [
            domain for domain, keywords in DOMAIN_RULES.items()
            if any(norm(keyword) in title_norm for keyword in keywords)
        ]
        if domains:
            document_domains[doc_id] = domains
            document_rows[doc_id] = document
    rows = []
    doc_ids = sorted(document_domains)
    if doc_ids:
        cur.execute("""
            SELECT id, document_id, article_number, title, LEFT(content, 1800),
                   status, effective_from, effective_to
            FROM legal_articles
            WHERE document_id = ANY(%s) AND COALESCE(content, '') <> ''
            ORDER BY document_id, id
        """, (doc_ids,))
        rows = cur.fetchall()
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema='public' AND table_name LIKE 'legal%'
        ORDER BY table_name
    """)
    tables = [row[0] for row in cur.fetchall()]
    relation_columns = []
    relationship_summary = {"type_counts": {}, "samples": []}
    if "legal_document_relationships" in tables:
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='legal_document_relationships'
            ORDER BY ordinal_position
        """)
        relation_columns = cur.fetchall()
        cur.execute("""
            SELECT relationship_type, COUNT(*)
            FROM legal_document_relationships
            GROUP BY relationship_type
            ORDER BY COUNT(*) DESC
        """)
        relationship_summary["type_counts"] = dict(cur.fetchall())
        cur.execute("""
            SELECT r.relationship_type,
                   s.id, s.law_number, s.title, s.status, s.expired_date,
                   t.id, t.law_number, t.title, t.status, t.expired_date
            FROM legal_document_relationships r
            LEFT JOIN legal_documents s ON s.id = r.source_document_id
            LEFT JOIN legal_documents t ON t.id = r.target_document_id
            ORDER BY r.id DESC
            LIMIT 120
        """)
        relationship_summary["samples"] = cur.fetchall()
    conn.close()

    counts = Counter({"articles_total": total_articles})
    by_domain: dict[str, set[tuple[int, int]]] = defaultdict(set)
    by_domain_document_counts: dict[str, Counter[int]] = defaultdict(Counter)
    samples: dict[str, list[dict]] = defaultdict(list)
    status_counts = Counter()
    for row in rows:
        (article_id, doc_id, article_number, article_title, content,
         article_status, article_from, article_to) = row
        (doc_id, title, law_number, status, effective, expired,
         source_url) = document_rows[doc_id]
        official = str(source_url or "").startswith(OFFICIAL_PREFIXES)
        current = is_current(status, effective, expired) and is_current(
            article_status, article_from, article_to
        )
        counts["articles_official"] += int(official)
        counts["articles_current"] += int(current)
        counts["articles_official_current"] += int(official and current)
        status_counts[str(status or "missing")] += 1
        for domain in document_domains[doc_id]:
            if official and current:
                by_domain[domain].add((doc_id, article_id))
                by_domain_document_counts[domain][doc_id] += 1
                if len(samples[domain]) < 8:
                    samples[domain].append({
                        "document_id": doc_id,
                        "article_id": article_id,
                        "law_number": law_number,
                        "article": article_number,
                        "title": article_title or title,
                    })

    document_candidates = {}
    for domain, counts_by_doc in by_domain_document_counts.items():
        ranked = []
        for doc_id, article_count in counts_by_doc.items():
            (_, title, law_number, status, effective, expired,
             source_url) = document_rows[doc_id]
            year_match = re.search(r"(?:19|20)\d{2}", str(law_number or ""))
            ranked.append({
                "document_id": doc_id,
                "law_number": law_number,
                "title": title,
                "status": status,
                "effective_date": effective,
                "expired_date": expired,
                "article_count": article_count,
                "source_url": source_url,
                "law_year": int(year_match.group(0)) if year_match else 0,
            })
        ranked.sort(key=lambda item: (item["law_year"], item["article_count"], item["document_id"]), reverse=True)
        document_candidates[domain] = ranked[:60]

    result = {
        "schema_version": "golden-1000-source-inventory-audit-v1",
        "as_of": AS_OF.isoformat(),
        "read_only": True,
        "tables": tables,
        "relationship_columns": relation_columns,
        "relationship_summary": relationship_summary,
        "counts": dict(counts),
        "document_status_article_counts": dict(status_counts),
        "domain_candidate_article_counts": {k: len(v) for k, v in by_domain.items()},
        "document_candidates": document_candidates,
        "samples": samples,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    output_path = OUT / "source-inventory-audit.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output_path),
        "counts": result["counts"],
        "domain_candidate_article_counts": result["domain_candidate_article_counts"],
        "relationship_type_counts": relationship_summary["type_counts"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
