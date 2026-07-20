"""Group legal fields by commune/ward task domains.

The script is intentionally non-destructive: it keeps ``legal_fields`` intact and
stores the commune-oriented grouping in ``legal_commune_field_groups``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text


REPORT_PATH = Path("notebook_data/commune-domain-groups-report.json")
OLD_ENV_PATH = Path(
    os.getenv("LEGAL_OLD_ENV_PATH", r"J:\ChatBot\legal-chatbot\backend\.env")
)

GROUPS = {
    "ho_tich_chung_thuc": {
        "name": "Hộ tịch - chứng thực",
        "terms": {
            "hanh chinh tu phap",
            "ho tich",
            "khai sinh",
            "khai tu",
            "ket hon",
            "chung thuc",
            "quoc tich",
            "nuoi con nuoi",
            "hoa giai o co so",
            "tro giup phap ly",
            "pho bien giao duc phap luat",
            "ly lich tu phap",
            "tu phap",
        },
    },
    "dat_dai_xay_dung": {
        "name": "Đất đai - xây dựng",
        "terms": {
            "dat dai",
            "dia chinh",
            "do dac",
            "ban do",
            "tai nguyen",
            "moi truong",
            "nha o",
            "xay dung",
            "do thi",
            "quy hoach",
            "kien truc",
            "ha tang ky thuat",
            "khoang san",
            "thuy loi",
            "phong chong thien tai",
            "bien doi khi hau",
        },
    },
    "cu_tru_an_ninh": {
        "name": "Cư trú - an ninh trật tự",
        "terms": {
            "cu tru",
            "cong an",
            "an ninh",
            "trat tu",
            "an toan xa hoi",
            "phong chay",
            "chua chay",
            "pccc",
            "ma tuy",
            "quoc phong",
            "nghia vu quan su",
            "dan quan",
            "bao ve bi mat",
        },
    },
    "khieu_nai_to_cao_xu_phat": {
        "name": "Khiếu nại - tố cáo - xử phạt",
        "terms": {
            "khieu nai",
            "to cao",
            "tiep cong dan",
            "thanh tra",
            "xu phat",
            "vi pham hanh chinh",
            "thi hanh an",
            "phong chong tham nhung",
            "kien nghi",
            "phan anh",
        },
    },
    "an_sinh_y_te_giao_duc": {
        "name": "An sinh xã hội - y tế - giáo dục",
        "terms": {
            "an sinh",
            "bao tro",
            "nguoi co cong",
            "giam ngheo",
            "bao hiem",
            "lao dong",
            "viec lam",
            "tre em",
            "dan so",
            "y te",
            "kham",
            "chua benh",
            "du phong",
            "an toan thuc pham",
            "giao duc",
            "mam non",
            "tieu hoc",
            "trung hoc",
            "van hoa",
            "the thao",
            "du lich",
            "gia dinh",
        },
    },
    "hanh_chinh_cong": {
        "name": "Bộ máy - thủ tục hành chính",
        "terms": {
            "thu tuc hanh chinh",
            "kiem soat thu tuc",
            "bo may",
            "chinh quyen dia phuong",
            "phan cap",
            "phan quyen",
            "dia gioi",
            "can bo",
            "cong chuc",
            "vien chuc",
            "noi vu",
            "to chuc",
            "bien che",
            "van thu",
            "luu tru",
            "ngan sach",
            "tai san cong",
            "phi va le phi",
            "le phi",
            "mot cua",
        },
    },
}

GROUP_PRIORITY = [
    "ho_tich_chung_thuc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
    "dat_dai_xay_dung",
    "hanh_chinh_cong",
]

BROAD_TERMS = {
    "bao hiem",
    "cong an",
    "du lich",
    "giao duc",
    "lao dong",
    "le phi",
    "moi truong",
    "ngan sach",
    "noi vu",
    "quoc phong",
    "tai nguyen",
    "the thao",
    "tu phap",
    "van hoa",
    "xay dung",
    "y te",
}


def database_url() -> str:
    configured = os.getenv("LEGAL_DATABASE_URL", "").strip()
    if configured:
        return configured
    old_settings = dotenv_values(OLD_ENV_PATH)
    old_url = str(old_settings.get("DATABASE_URL") or "").strip()
    if old_url:
        return old_url
    return "postgresql+psycopg2://postgres:postgres@localhost:5432/legal_chatbot"


def normalize(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    ascii_text = ascii_text.replace("đ", "d").replace("Đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def matched_terms(blob: str, terms: set[str]) -> list[str]:
    return sorted(
        term for term in terms if re.search(rf"\b{re.escape(term)}\b", blob)
    )


def classify_field(field: dict) -> tuple[bool, str | None, str | None, list[str]]:
    name_blob = normalize(field.get("name", ""))
    description_blob = normalize(field.get("description", ""))
    scored_matches: list[tuple[int, int, str, str, list[str]]] = []
    for priority, slug in enumerate(GROUP_PRIORITY):
        config = GROUPS[slug]
        name_matches = matched_terms(name_blob, config["terms"])
        description_matches = [
            term
            for term in matched_terms(description_blob, config["terms"])
            if term not in BROAD_TERMS
        ]
        matches = sorted(set(name_matches + description_matches))
        if matches:
            score = len(name_matches) * 4 + len(description_matches)
            scored_matches.append((score, -priority, slug, config["name"], matches))
    if scored_matches:
        _, _, slug, group_name, matches = max(scored_matches)
        return True, slug, group_name, matches
    return False, None, None, []


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    engine = create_engine(database_url())
    now = datetime.now()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_commune_field_groups (
                    field_id INTEGER PRIMARY KEY
                        REFERENCES legal_fields(id) ON DELETE CASCADE,
                    group_slug VARCHAR(64),
                    group_name TEXT,
                    included BOOLEAN NOT NULL,
                    reason VARCHAR(128) NOT NULL,
                    matched_terms TEXT,
                    evaluated_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(text("TRUNCATE legal_commune_field_groups"))

        fields = [
            dict(row)
            for row in connection.execute(
                text("SELECT id, name, description FROM legal_fields ORDER BY id")
            ).mappings()
        ]
        payload = []
        for field in fields:
            included, slug, group_name, matches = classify_field(field)
            payload.append(
                {
                    "field_id": field["id"],
                    "group_slug": slug,
                    "group_name": group_name,
                    "included": included,
                    "reason": "matched_commune_domain"
                    if included
                    else "outside_commune_task_domains",
                    "matched_terms": ", ".join(matches),
                    "evaluated_at": now,
                }
            )

        connection.execute(
            text(
                """
                INSERT INTO legal_commune_field_groups (
                    field_id, group_slug, group_name, included, reason,
                    matched_terms, evaluated_at
                ) VALUES (
                    :field_id, :group_slug, :group_name, :included, :reason,
                    :matched_terms, :evaluated_at
                )
                """
            ),
            payload,
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_legal_commune_field_groups_included
                ON legal_commune_field_groups (included, group_slug)
                """
            )
        )

        group_stats = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT
                        g.group_slug,
                        g.group_name,
                        count(DISTINCT f.id) AS field_count,
                        count(DISTINCT d.id) AS document_count,
                        count(DISTINCT CASE WHEN s.included THEN d.id END)
                            AS scoped_document_count,
                        count(c.id) AS chunk_count
                    FROM legal_commune_field_groups g
                    JOIN legal_fields f ON f.id = g.field_id
                    LEFT JOIN legal_documents d ON d.field_id = f.id
                    LEFT JOIN legal_search_scope s ON s.document_id = d.id
                    LEFT JOIN legal_articles a ON a.document_id = d.id
                    LEFT JOIN legal_article_chunks c ON c.article_id = a.id
                    WHERE g.included = TRUE
                    GROUP BY g.group_slug, g.group_name
                    ORDER BY scoped_document_count DESC, document_count DESC
                    """
                )
            ).mappings()
        ]
        excluded_field_count = connection.execute(
            text(
                """
                SELECT count(*)
                FROM legal_commune_field_groups
                WHERE included = FALSE
                """
            )
        ).scalar_one()
        samples = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT
                        g.group_slug,
                        g.group_name,
                        f.id AS field_id,
                        f.name AS field_name,
                        g.matched_terms,
                        count(DISTINCT d.id) AS document_count,
                        count(DISTINCT CASE WHEN s.included THEN d.id END)
                            AS scoped_document_count
                    FROM legal_commune_field_groups g
                    JOIN legal_fields f ON f.id = g.field_id
                    LEFT JOIN legal_documents d ON d.field_id = f.id
                    LEFT JOIN legal_search_scope s ON s.document_id = d.id
                    WHERE g.included = TRUE
                    GROUP BY
                        g.group_slug, g.group_name, f.id, f.name,
                        g.matched_terms
                    ORDER BY scoped_document_count DESC, document_count DESC
                    LIMIT 80
                    """
                )
            ).mappings()
        ]

    report = {
        "generated_at": now.isoformat(),
        "field_count": len(fields),
        "included_field_count": len(fields) - int(excluded_field_count),
        "excluded_field_count": int(excluded_field_count),
        "groups": group_stats,
        "top_matching_fields": samples,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
