"""Read-only inspection of proposed core sources and their lifecycle relations."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
LAW_NUMBERS = [
    "753/VBHN-BTP", "280/2025/NĐ-CP", "07/2025/NĐ-CP", "43/2025/NQ-HĐND",
    "104/2025/NĐ-CP", "18/2026/NĐ-CP", "175/2024/NĐ-CP",
    "50/2014/QH13", "62/2020/QH14", "15/2021/NĐ-CP",
    "88/2025/QH15", "176/2025/NĐ-CP", "73/2025/QH15",
    "15/2015/TT-BTP", "45/2013/QH13", "81/2006/QH11", "59/2014/QH13",
    "31/2014/NĐ-CP", "44/2002/PL-UBTVQH10", "41/2024/QH15", "96/2014/TT-BQP",
    "60/2014/QH13", "04/2020/TT-BTP", "23/2015/NĐ-CP",
    "31/2024/QH15", "101/2024/NĐ-CP", "102/2024/NĐ-CP", "88/2024/NĐ-CP", "123/2024/NĐ-CP", "27/2023/QH15", "46/2024/QH15",
    "68/2020/QH14", "62/2021/NĐ-CP", "55/2021/TT-BCA", "66/2023/TT-BCA", "26/2023/QH15", "70/2024/NĐ-CP",
    "02/2011/QH13", "124/2020/NĐ-CP", "25/2018/QH14", "31/2019/NĐ-CP", "15/2012/QH13", "118/2021/NĐ-CP", "93/2015/QH13",
    "51/2024/QH15", "188/2025/NĐ-CP", "74/2025/QH15", "374/2025/NĐ-CP", "20/2021/NĐ-CP", "76/2024/NĐ-CP", "102/2016/QH13", "135/2020/NĐ-CP", "238/2025/NĐ-CP", "07/2021/NĐ-CP", "28/2020/TT-BGDĐT", "32/2020/TT-BGDĐT",
]


def db_url() -> str:
    line = next(
        row for row in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if row.startswith("LEGAL_RELEASE_DATABASE_URL=")
    )
    return (
        line.split("=", 1)[1]
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("host.docker.internal", "127.0.0.1")
    )


def main() -> None:
    conn = psycopg2.connect(db_url(), connect_timeout=10)
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.law_number, d.title, d.status, d.effective_date,
               d.expired_date, d.source_url, COUNT(a.id)
        FROM legal_documents d
        LEFT JOIN legal_articles a ON a.document_id=d.id AND COALESCE(a.content, '') <> ''
        WHERE d.law_number = ANY(%s)
        GROUP BY d.id
        ORDER BY d.law_number, d.id DESC
    """, (LAW_NUMBERS,))
    docs = cur.fetchall()
    ids = [row[0] for row in docs]
    cur.execute("""
        SELECT r.relationship_type,
               s.id, s.law_number, s.title,
               t.id, t.law_number, t.title
        FROM legal_document_relationships r
        LEFT JOIN legal_documents s ON s.id=r.source_document_id
        LEFT JOIN legal_documents t ON t.id=r.target_document_id
        WHERE r.source_document_id = ANY(%s) OR r.target_document_id = ANY(%s)
        ORDER BY r.id
    """, (ids, ids))
    relations = cur.fetchall()
    conn.close()
    result = {
        "documents": docs,
        "relations": relations,
        "missing": sorted(set(LAW_NUMBERS) - {row[1] for row in docs}),
    }
    output = ROOT / "outputs" / "golden-1000-complete" / "core-source-inspection.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "documents": len(docs),
        "relations": len(relations),
        "missing": result["missing"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
