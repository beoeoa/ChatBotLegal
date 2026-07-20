"""Build an auditable, non-destructive Hai Phong commune legal scope."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from legal_search_server import EXPIRED_DOCUMENT_OVERRIDES, retriever


REPORT_PATH = Path("notebook_data/haiphong-legal-scope-report.json")

# Framework laws that must remain searchable even when their metadata does not
# explicitly mention commune-level work.
CORE_LAWS = {
    "15/2012/QH13",  # Administrative violations
    "35/2013/QH13",  # Grassroots mediation
    "60/2014/QH13",  # Civil status
    "23/2015/NĐ-CP",  # Authentication
    "123/2015/NĐ-CP",  # Civil status guidance
    "91/2015/QH13",  # Civil Code
    "02/2016/QH14",  # Belief and religion
    "11/2017/QH14",  # Legal aid
    "17/2017/QH14",  # Irrigation
    "31/2024/QH15",  # Land
    "27/2023/QH15",  # Housing
    "50/2014/QH13",  # Construction
    "72/2020/QH14",  # Environmental protection
    "68/2020/QH14",  # Residence
    "55/2021/TT-BCA",  # Residence guidance
    "45/2019/QH14",  # Labor
    "41/2024/QH15",  # Social insurance
    "43/2019/QH14",  # Education
    "15/2023/QH15",  # Medical examination and treatment
    "105/2016/QH13",  # Pharmacy
    "25/2008/QH12",  # Health insurance
    "83/2015/QH13",  # State budget
    "38/2019/QH14",  # Tax administration
    "59/2020/QH14",  # Enterprise
    "17/2023/QH15",  # Cooperatives
    "61/2018/NĐ-CP",  # Single-window mechanism
    "72/2025/QH15",  # Local government organization
    "150/2025/NĐ-CP",  # Commune professional bodies
    "370/2025/NĐ-CP",  # Amendments to commune professional bodies
    "26/2023/QH15",  # Identification (Luật Căn cước)
    "10/2022/QH15",  # Grassroots democracy (Luật Thực hiện dân chủ ở cơ sở)
    "78/2015/QH13",  # Military service (Luật Nghĩa vụ quân sự)
    "02/2011/QH13",  # Complaints (Luật Khiếu nại)
    "25/2018/QH14",  # Denunciations (Luật Tố cáo)
}

COMMUNE_TERMS = {
    "ubnd cap xa",
    "uy ban nhan dan cap xa",
    "ubnd xa",
    "ubnd phuong",
    "hdnd cap xa",
    "hoi dong nhan dan cap xa",
    "cong an cap xa",
    "cong an xa",
    "cong an phuong",
    "cap xa",
    "xa phuong",
    "phuong xa",
    "bo phan mot cua",
    "trung tam phuc vu hanh chinh cong",
    "chinh quyen dia phuong",
}

DOMAIN_PATTERNS = {
    "tu_phap_ho_tich": {
        "ho tich",
        "khai sinh",
        "khai tu",
        "ket hon",
        "chung thuc",
        "hoa giai o co so",
        "nuoi con nuoi",
        "tro giup phap ly",
        "pho bien giao duc phap luat",
    },
    "dat_dai_moi_truong": {
        "dat dai",
        "moi truong",
        "tai nguyen nuoc",
        "do dac va ban do",
        "phong chong thien tai",
        "thuy loi",
        "rac thai",
        "boi thuong",
        "giai phong mat bang",
        "tai dinh cu",
        "thu hoi dat",
        "gia dat",
        "bang gia dat",
        "don gia dat",
    },
    "xay_dung_do_thi": {
        "quan ly hoat dung xay dung",
        "giay phep xay dung",
        "trat tu xay dung",
        "nha o",
        "quy hoach",
        "ha tang ky thuat",
        "duong bo",
    },
    "cu_tru_an_ninh": {
        "dang ky quan ly cu tru",
        "quan ly cu tru",
        "an ninh va trat tu",
        "phong chay chua chay",
        "nghia vu quan su",
        "dan quan tu ve",
    },
    "noi_vu_hanh_chinh": {
        "chinh quyen dia phuong",
        "cai cach hanh chinh",
        "kiem soat thu tuc hanh chinh",
        "cong chuc",
        "vien chuc",
        "van thu",
        "luu tru",
        "tiep cong dan",
        "khieu nai",
        "to cao",
    },
    "an_sinh_y_te": {
        "bao tro xa hoi",
        "nguoi co cong",
        "giam ngheo",
        "bao hiem y te",
        "bao hiem xa hoi",
        "an toan thuc pham",
        "bao ve tre em",
        "dan so",
        "y te du phong",
    },
    "giao_duc_van_hoa": {
        "giao duc mam non",
        "giao duc tieu hoc",
        "giao duc trung hoc",
        "van hoa co so",
        "gia dinh",
        "the duc the thao",
        "quang cao",
        "thong tin co so",
    },
    "kinh_te_tai_chinh": {
        "ngan sach cap xa",
        "ngan sach xa",
        "ngan sach phuong",
        "ho kinh doanh",
        "hop tac xa",
        "phi va le phi",
        "quan ly tai san cong",
        "le phi",
        "thu phi",
        "nop phi",
        "muc thu",
        "phi tham quan",
    },
}

LOCAL_AGENCY_TERMS = {
    "ubnd tinh",
    "uy ban nhan dan tinh",
    "ubnd thanh pho",
    "uy ban nhan dan thanh pho",
    "hdnd tinh",
    "hoi dong nhan dan tinh",
    "hdnd thanh pho",
    "hoi dong nhan dan thanh pho",
}

RELATION_TERMS = {
    "sua doi",
    "bo sung",
    "thay the",
    "huong dan",
    "quy dinh chi tiet",
}


def normalize(value: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or "").casefold())
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_text)).strip()


def contains_phrase(blob: str, phrases: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(phrase)}\b", blob) for phrase in phrases)


def domain_for(blob: str) -> str | None:
    for domain, patterns in DOMAIN_PATTERNS.items():
        if contains_phrase(blob, patterns):
            return domain
    return None


def is_hai_phong(row: dict) -> bool:
    blob = normalize(
        " ".join(
            str(row.get(key) or "")
            for key in ("scope", "issuing_agency", "title", "applicability_info")
        )
    )
    return "hai phong" in blob


def is_other_local(row: dict) -> bool:
    if is_hai_phong(row):
        return False
    scope = normalize(row.get("scope"))
    agency = normalize(row.get("issuing_agency"))
    if scope in {"toan quoc", "trung uong", "ca nuoc", ""}:
        return False
    return contains_phrase(agency, LOCAL_AGENCY_TERMS) or any(
        marker in scope
        for marker in ("tinh ", "thanh pho ", "tp ", "quan ", "huyen ")
    )


def validity_reason(row: dict, as_of: date) -> str | None:
    law_number = str(row.get("law_number") or "").strip()
    if law_number in EXPIRED_DOCUMENT_OVERRIDES:
        return "known_superseded"
    if str(row.get("status") or "").casefold() != "active":
        return "expired_status"
    effective = row.get("effective_date")
    expired = row.get("expired_date")
    if effective and effective > as_of:
        return "not_yet_effective"
    if expired and expired <= as_of:
        return "expired_by_date"
    return None


def initial_decision(row: dict, as_of: date) -> tuple[bool, str, str | None]:
    invalid = validity_reason(row, as_of)
    if invalid:
        return False, invalid, None
    if is_other_local(row):
        return False, "other_province", None

    blob = normalize(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "title",
                "law_number",
                "document_type",
                "issuing_agency",
                "scope",
                "sector",
                "applicability_info",
                "field_name",
            )
        )
    )
    law_number = str(row.get("law_number") or "").strip()
    domain = domain_for(blob)
    commune_specific = contains_phrase(blob, COMMUNE_TERMS)

    if law_number in CORE_LAWS:
        return True, "current_core_law", domain
    if is_hai_phong(row) and commune_specific:
        return True, "hai_phong_commune_specific", domain
    if is_hai_phong(row) and domain:
        return True, "hai_phong_relevant_domain", domain
    if commune_specific and domain:
        return True, "central_commune_specific", domain
    if domain and normalize(row.get("document_type")) in {
        "luat",
        "bo luat",
        "phap lenh",
        "nghi dinh",
        "thong tu",
        "nghi quyet",
    }:
        return True, "central_relevant_framework", domain
    return False, "out_of_commune_scope", domain


def main() -> None:
    as_of = date.today()
    with retriever._engine.begin() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT d.id, d.title, d.law_number, d.document_type,
                           d.issuing_agency, d.scope, d.sector, d.status,
                           d.effective_date, d.expired_date,
                           d.applicability_info, f.name AS field_name
                    FROM legal_documents d
                    LEFT JOIN legal_fields f ON f.id = d.field_id
                    ORDER BY d.id
                    """
                )
            ).mappings()
        ]
        decisions = {
            int(row["id"]): initial_decision(row, as_of) for row in rows
        }

        relationships = connection.execute(
            text(
                """
                SELECT source_document_id, target_document_id, relationship_type
                FROM legal_document_relationships
                """
            )
        ).mappings()
        broken_relationships = 0
        for relation in relationships:
            relation_type = normalize(relation["relationship_type"])
            if not any(term in relation_type for term in RELATION_TERMS):
                continue
            if (
                relation["source_document_id"] is None
                or relation["target_document_id"] is None
            ):
                broken_relationships += 1
                continue
            source_id = int(relation["source_document_id"])
            target_id = int(relation["target_document_id"])
            source = decisions.get(source_id)
            target = decisions.get(target_id)
            if not source or not target:
                continue
            if source[0] and target[1] == "out_of_commune_scope":
                decisions[target_id] = (
                    True,
                    "related_current_instrument",
                    target[2] or source[2],
                )
            elif target[0] and source[1] == "out_of_commune_scope":
                decisions[source_id] = (
                    True,
                    "related_current_instrument",
                    source[2] or target[2],
                )

        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS legal_search_scope (
                    document_id INTEGER PRIMARY KEY
                        REFERENCES legal_documents(id) ON DELETE CASCADE,
                    included BOOLEAN NOT NULL,
                    reason VARCHAR(80) NOT NULL,
                    domain VARCHAR(80),
                    evaluated_as_of DATE NOT NULL,
                    evaluated_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(text("TRUNCATE legal_search_scope"))
        now = datetime.now()
        payload = [
            {
                "document_id": document_id,
                "included": decision[0],
                "reason": decision[1],
                "domain": decision[2],
                "evaluated_as_of": as_of,
                "evaluated_at": now,
            }
            for document_id, decision in decisions.items()
        ]
        connection.execute(
            text(
                """
                INSERT INTO legal_search_scope (
                    document_id, included, reason, domain,
                    evaluated_as_of, evaluated_at
                ) VALUES (
                    :document_id, :included, :reason, :domain,
                    :evaluated_as_of, :evaluated_at
                )
                """
            ),
            payload,
        )
        connection.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_legal_search_scope_included
                ON legal_search_scope (included, document_id)
                """
            )
        )

        counts = Counter(decision[1] for decision in decisions.values())
        included = sum(1 for decision in decisions.values() if decision[0])
        chunk_counts = dict(
            connection.execute(
                text(
                    """
                    SELECT s.included, count(c.id) AS chunks
                    FROM legal_search_scope s
                    JOIN legal_articles a ON a.document_id = s.document_id
                    JOIN legal_article_chunks c ON c.article_id = a.id
                    GROUP BY s.included
                    """
                )
            ).fetchall()
        )
        samples = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT d.id, d.law_number, d.title, d.scope,
                           s.included, s.reason, s.domain
                    FROM legal_search_scope s
                    JOIN legal_documents d ON d.id = s.document_id
                    WHERE s.reason IN (
                        'out_of_commune_scope', 'other_province',
                        'known_superseded', 'expired_by_date'
                    )
                    ORDER BY d.id
                    LIMIT 100
                    """
                )
            ).mappings()
        ]

    report = {
        "generated_at": now.isoformat(),
        "as_of": as_of.isoformat(),
        "documents_total": len(rows),
        "documents_included": included,
        "documents_excluded": len(rows) - included,
        "chunks_included": int(chunk_counts.get(True, 0)),
        "chunks_excluded": int(chunk_counts.get(False, 0)),
        "reason_counts": dict(counts.most_common()),
        "broken_relevant_relationships": broken_relationships,
        "samples": samples,
    }
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
