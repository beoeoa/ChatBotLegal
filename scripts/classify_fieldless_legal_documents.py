"""Classify active fieldless legal documents into the seven public-scope buckets.

This command is deliberately read-only with respect to PostgreSQL.  It writes a
versioned manifest and a review CSV; activating a classification in retrieval is
a separate, gated operation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CLASSIFIER_VERSION = "2026-08-14.1"
SCHEMA_VERSION = "legal-document-domain-classification-v1"
DEFAULT_JSON = Path("output/legal-document-domain-classification-v1.json")
DEFAULT_CSV = Path("output/legal-document-domain-classification-v1-review.csv")

FIVE_MAIN_DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
)
TAXONOMY = FIVE_MAIN_DOMAINS + ("hanh_chinh_cong", "ngoai_pham_vi")

# A specialist match wins a tie against the cross-cutting administrative bucket.
DOMAIN_PRIORITY = (
    "khieu_nai_to_cao_xu_phat",
    "ho_tich_chung_thuc",
    "cu_tru_an_ninh",
    "an_sinh_y_te_giao_duc",
    "dat_dai_xay_dung",
    "hanh_chinh_cong",
)

LEGACY_DOMAIN_MAP = {
    "tu_phap_ho_tich": "ho_tich_chung_thuc",
    "ho_tich_chung_thuc": "ho_tich_chung_thuc",
    "dat_dai_moi_truong": "dat_dai_xay_dung",
    "xay_dung_do_thi": "dat_dai_xay_dung",
    "dat_dai_xay_dung": "dat_dai_xay_dung",
    "an_sinh_y_te": "an_sinh_y_te_giao_duc",
    "giao_duc_van_hoa": "an_sinh_y_te_giao_duc",
    "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
    "cu_tru_an_ninh": "cu_tru_an_ninh",
    "trat_tu_do_thi": "khieu_nai_to_cao_xu_phat",
    "khieu_nai_to_cao_xu_phat": "khieu_nai_to_cao_xu_phat",
    "noi_vu_hanh_chinh": "hanh_chinh_cong",
    "hanh_chinh_cong": "hanh_chinh_cong",
}


def _rules(*items: tuple[str, int]) -> tuple[tuple[str, int], ...]:
    return items


# Weights apply to the title. Article text is capped and contributes only a
# small corroborating score, preventing a generic document from being routed by
# one incidental mention buried in the body.
TITLE_RULES: dict[str, tuple[tuple[str, int], ...]] = {
    "ho_tich_chung_thuc": _rules(
        ("ho tich", 16),
        ("khai sinh", 16),
        ("khai tu", 16),
        ("dang ky ket hon", 16),
        ("tinh trang hon nhan", 15),
        ("chung thuc", 15),
        ("ban sao tu so goc", 14),
        ("nuoi con nuoi", 14),
        ("nhan cha me con", 14),
        ("cai chinh", 11),
        ("giam ho", 11),
        ("ly lich tu phap", 11),
        ("quoc tich", 9),
        ("hoa giai o co so", 13),
    ),
    "dat_dai_xay_dung": _rules(
        ("dat dai", 16),
        ("quyen su dung dat", 16),
        ("giay chung nhan quyen su dung dat", 18),
        ("dia chinh", 14),
        ("thu hoi dat", 14),
        ("boi thuong ho tro tai dinh cu", 15),
        ("giai phong mat bang", 13),
        ("quy hoach su dung dat", 14),
        ("cap phep xay dung", 15),
        ("giay phep xay dung", 15),
        ("trat tu xay dung", 14),
        ("cong trinh xay dung", 11),
        ("quan ly xay dung", 11),
        ("nha o", 10),
        ("quy hoach do thi", 12),
        ("bao ve moi truong", 12),
        ("o nhiem moi truong", 12),
        ("phong chay chua chay rung", 16),
        ("nuoc thai", 10),
        ("chat thai", 10),
        ("rac thai", 10),
        ("khoang san", 9),
        ("thuy loi", 8),
        ("phong chong thien tai", 9),
    ),
    "an_sinh_y_te_giao_duc": _rules(
        ("an sinh xa hoi", 15),
        ("bao tro xa hoi", 15),
        ("tro cap xa hoi", 14),
        ("nguoi co cong", 15),
        ("thuong binh", 13),
        ("liet si", 13),
        ("giam ngheo", 13),
        ("ho ngheo", 13),
        ("bao hiem xa hoi", 13),
        ("bao hiem y te", 14),
        ("nguoi cao tuoi", 12),
        ("nguoi khuyet tat", 13),
        ("bao ve cham soc tre em", 13),
        ("tre em", 9),
        ("y te", 10),
        ("kham benh", 12),
        ("chua benh", 12),
        ("dich benh", 11),
        ("an toan thuc pham", 13),
        ("giao duc", 11),
        ("mam non", 11),
        ("tieu hoc", 10),
        ("trung hoc", 10),
        ("truong hoc", 9),
        ("hoc phi", 11),
        ("lao dong", 8),
        ("viec lam", 10),
        ("binh dang gioi", 10),
        ("dan so", 8),
    ),
    "cu_tru_an_ninh": _rules(
        ("cu tru", 16),
        ("thuong tru", 15),
        ("tam tru", 15),
        ("tam vang", 13),
        ("ho khau", 14),
        ("can cuoc", 16),
        ("dinh danh dien tu", 15),
        ("co so du lieu quoc gia ve dan cu", 15),
        ("cong an xa", 13),
        ("an ninh trat tu", 13),
        ("phong chay chua chay", 13),
        ("phong chay", 11),
        ("ma tuy", 11),
        ("nghia vu quan su", 13),
        ("dan quan tu ve", 13),
        ("quoc phong", 8),
        ("xuat canh nhap canh", 11),
        ("xuat nhap canh", 11),
    ),
    "khieu_nai_to_cao_xu_phat": _rules(
        ("khieu nai", 17),
        ("to cao", 17),
        ("tiep cong dan", 16),
        ("xu phat vi pham hanh chinh", 18),
        ("vi pham hanh chinh", 15),
        ("xu ly vi pham hanh chinh", 17),
        ("tham quyen xu phat", 16),
        ("bien ban vi pham", 13),
        ("phong chong tham nhung", 14),
        ("kien nghi phan anh", 13),
        ("giai quyet kien nghi", 10),
        ("thanh tra", 10),
        ("xu phat", 13),
    ),
    "hanh_chinh_cong": _rules(
        ("thu tuc hanh chinh", 16),
        ("kiem soat thu tuc hanh chinh", 17),
        ("dich vu cong", 14),
        ("mot cua", 14),
        ("cai cach hanh chinh", 15),
        ("chinh quyen dia phuong", 14),
        ("uy ban nhan dan cap xa", 15),
        ("hoi dong nhan dan cap xa", 14),
        ("to chuc bo may", 13),
        ("co quan chuyen mon", 11),
        ("dia gioi hanh chinh", 14),
        ("sap xep don vi hanh chinh", 15),
        ("can bo cong chuc", 12),
        ("cong chuc", 10),
        ("vien chuc", 9),
        ("bien che", 10),
        ("van thu luu tru", 12),
        ("tai san cong", 10),
        ("ngan sach xa", 13),
        ("ngan sach cap xa", 13),
        ("phi va le phi", 11),
        ("bau cu", 10),
        ("thuc hien dan chu o co so", 13),
    ),
}

# These phrases may corroborate a title/legacy-domain decision but cannot make a
# document public-scope by themselves.
BODY_RULES = {
    domain: tuple((phrase, max(1, weight // 6)) for phrase, weight in rules)
    for domain, rules in TITLE_RULES.items()
}

OTHER_LOCALITIES = (
    "ha noi", "ho chi minh", "da nang", "can tho", "an giang", "bac ninh",
    "ca mau", "cao bang", "dak lak", "dien bien", "dong nai", "dong thap",
    "gia lai", "ha tinh", "hung yen", "khanh hoa", "lai chau", "lam dong",
    "lang son", "lao cai", "nghe an", "ninh binh", "phu tho", "quang ngai",
    "quang ninh", "quang tri", "son la", "tay ninh", "thanh hoa",
    "thai nguyen", "tuyen quang", "vinh long", "bac giang", "bac kan",
    "bac lieu", "ba ria vung tau", "ben tre", "binh dinh", "binh duong",
    "binh phuoc", "binh thuan", "dak nong", "ha giang", "ha nam",
    "hai duong", "hau giang", "hoa binh", "kien giang", "kon tum",
    "long an", "nam dinh", "ninh thuan", "phu yen", "quang binh",
    "quang nam", "soc trang", "thai binh", "thua thien hue", "tien giang",
    "tra vinh", "vinh phuc", "nghia binh", "minh hai", "ha son binh",
    "gia lai kon tum", "song be", "cuu long", "hoang lien son",
    "ha long", "dong ha", "cam lo", "da lat", "buon ma thuot",
    "viet tri", "vinh yen", "thu dau mot", "bien hoa", "rach gia",
    "my tho", "long xuyen", "phan thiet", "phan rang", "quy nhon",
    "pleiku", "kon tum", "vinh", "thanh hoa", "nam dinh", "thai binh",
)

OUTSIDE_TITLE_PHRASES = (
    "hai quan", "hang hai", "hang khong", "chung khoan", "ngan hang",
    "dau khi", "ngoai giao", "lanh su", "xuat nhap khau", "thue quan",
    "quan ly thi truong", "doanh nghiep nha nuoc", "hop tac xa thuy san",
    "nghe ca", "ngan hang nha nuoc", "bao chi xuat ban",
)

HARD_OUTSIDE_TITLE_PHRASES = (
    "dich vu chung thuc chu ky so",
    "chung thuc chu ky so",
    "chung thuc thong diep du lieu",
    "chung thuc dien tu",
)

PHRASE_SUFFIX_BLOCKS = {
    # “dịch vụ công nghiệp” is not the public-service concept.
    "dich vu cong": ("nghiep", "ich"),
}

# When a title contains one of these unambiguous phrases, it resolves a score
# tie. Ordering still ensures a specialist domain wins over hanh_chinh_cong.
EXPLICIT_PRIORITY_PHRASES: dict[str, tuple[str, ...]] = {
    "khieu_nai_to_cao_xu_phat": (
        "xu phat vi pham hanh chinh",
        "xu ly vi pham hanh chinh",
        "vi pham hanh chinh",
        "khieu nai",
        "to cao",
        "tiep cong dan",
    ),
    "ho_tich_chung_thuc": (
        "ho tich",
        "khai sinh",
        "khai tu",
        "dang ky ket hon",
        "chung thuc",
        "nuoi con nuoi",
    ),
    "cu_tru_an_ninh": (
        "cu tru",
        "thuong tru",
        "tam tru",
        "can cuoc",
        "dinh danh dien tu",
    ),
    "an_sinh_y_te_giao_duc": (
        "an sinh xa hoi",
        "bao tro xa hoi",
        "tro cap xa hoi",
        "nguoi co cong",
        "bao hiem y te",
        "bao hiem xa hoi",
        "giao duc",
    ),
    "dat_dai_xay_dung": (
        "dat dai",
        "quyen su dung dat",
        "dia chinh",
        "thu hoi dat",
        "cap phep xay dung",
        "giay phep xay dung",
        "bao ve moi truong",
        "phong chay chua chay rung",
    ),
    "hanh_chinh_cong": (
        "thu tuc hanh chinh",
        "dich vu cong",
        "mot cua",
        "cai cach hanh chinh",
    ),
}


@dataclass(frozen=True)
class SourceDocument:
    document_id: int
    title: str
    law_number: str | None = None
    document_type: str | None = None
    issuing_agency: str | None = None
    scope: str | None = None
    sector: str | None = None
    applicability_info: str | None = None
    source_url: str | None = None
    previous_included: bool | None = None
    previous_domain: str | None = None
    article_excerpt: str = ""


@dataclass(frozen=True)
class Classification:
    document_id: int
    title: str
    law_number: str | None
    source_url: str | None
    domain: str
    confidence: str
    score: int
    margin: int
    reason: str
    matched_terms: tuple[str, ...]
    previous_included: bool | None
    previous_domain: str | None


def normalize(value: Any) -> str:
    text = str(value or "").casefold().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def _has_phrase(blob: str, phrase: str) -> bool:
    if not re.search(rf"(?:^| )({re.escape(phrase)})(?: |$)", blob):
        return False
    blocked_suffixes = PHRASE_SUFFIX_BLOCKS.get(phrase, ())
    return not any(
        re.search(rf"(?:^| ){re.escape(phrase)} {re.escape(suffix)}(?: |$)", blob)
        for suffix in blocked_suffixes
    )


def _locality_exclusion(title: str) -> str | None:
    if "hai phong" in title:
        return None
    for locality in OTHER_LOCALITIES:
        if _has_phrase(title, locality) and any(
            marker in title
            for marker in ("thuoc tinh", "tinh ", "thanh pho ", "thi xa ")
        ):
            return locality
    return None


def _rule_scores(blob: str, rules: dict[str, Iterable[tuple[str, int]]]):
    scores = {domain: 0 for domain in DOMAIN_PRIORITY}
    matches: dict[str, list[str]] = {domain: [] for domain in DOMAIN_PRIORITY}
    for domain, domain_rules in rules.items():
        for phrase, weight in domain_rules:
            if _has_phrase(blob, phrase):
                scores[domain] += weight
                matches[domain].append(phrase)
    return scores, matches


def classify_document(document: SourceDocument) -> Classification:
    title = normalize(document.title)
    metadata = normalize(
        " ".join(
            str(value or "")
            for value in (
                document.document_type,
                document.issuing_agency,
                document.scope,
                document.sector,
                document.applicability_info,
            )
        )
    )
    article = normalize(document.article_excerpt[:16000])
    title_scores, title_matches = _rule_scores(title, TITLE_RULES)
    body_scores, body_matches = _rule_scores(article, BODY_RULES)
    scores = dict(title_scores)
    evidence: dict[str, list[str]] = {
        domain: [f"title:{term}" for term in title_matches[domain]]
        for domain in DOMAIN_PRIORITY
    }

    # Article content confirms a title or a reviewed legacy domain. It never
    # creates a public-scope decision on its own.
    previous_canonical = LEGACY_DOMAIN_MAP.get(
        normalize(document.previous_domain).replace(" ", "_")
    )
    for domain in DOMAIN_PRIORITY:
        if title_scores[domain] or (
            document.previous_included and previous_canonical == domain
        ):
            body_bonus = min(body_scores[domain], 5)
            scores[domain] += body_bonus
            if body_bonus:
                evidence[domain].extend(
                    f"article:{term}" for term in body_matches[domain][:4]
                )

    if document.previous_included and previous_canonical:
        prior = 9 if previous_canonical in FIVE_MAIN_DOMAINS else 7
        scores[previous_canonical] += prior
        evidence[previous_canonical].append(
            f"reviewed_scope:{document.previous_domain}"
        )

    # Agency/metadata is weak corroboration only.
    metadata_scores, metadata_matches = _rule_scores(metadata, BODY_RULES)
    for domain in DOMAIN_PRIORITY:
        if scores[domain] > 0:
            bonus = min(metadata_scores[domain], 2)
            scores[domain] += bonus
            if bonus:
                evidence[domain].extend(
                    f"metadata:{term}" for term in metadata_matches[domain][:2]
                )

    locality = _locality_exclusion(title)
    hard_outside_signal = next(
        (
            phrase
            for phrase in HARD_OUTSIDE_TITLE_PHRASES
            if _has_phrase(title, phrase)
        ),
        None,
    )
    outside_signal = next(
        (phrase for phrase in OUTSIDE_TITLE_PHRASES if _has_phrase(title, phrase)),
        None,
    )
    ranked = sorted(
        DOMAIN_PRIORITY,
        key=lambda domain: (-scores[domain], DOMAIN_PRIORITY.index(domain)),
    )
    winner = ranked[0]
    best = scores[winner]
    second = scores[ranked[1]]
    margin = best - second
    has_title_evidence = bool(title_matches[winner])
    has_reviewed_prior = bool(
        document.previous_included and previous_canonical == winner
    )
    explicit_domain = next(
        (
            domain
            for domain in DOMAIN_PRIORITY
            if any(
                _has_phrase(title, phrase)
                for phrase in EXPLICIT_PRIORITY_PHRASES[domain]
            )
        ),
        None,
    )

    if locality:
        domain = "ngoai_pham_vi"
        reason = f"other_locality:{locality}"
    elif hard_outside_signal:
        domain = "ngoai_pham_vi"
        reason = f"outside_sector:{hard_outside_signal}"
    elif outside_signal and best < 15 and not has_reviewed_prior:
        domain = "ngoai_pham_vi"
        reason = f"outside_sector:{outside_signal}"
    elif explicit_domain and scores[explicit_domain] >= 10:
        domain = explicit_domain
        reason = (
            "explicit_specialist_title"
            if explicit_domain in FIVE_MAIN_DOMAINS
            else "explicit_cross_cutting_title"
        )
    elif best >= 10 and margin >= 3 and (has_title_evidence or has_reviewed_prior):
        domain = winner
        reason = "specialist_evidence" if winner in FIVE_MAIN_DOMAINS else "cross_cutting_administration"
    else:
        domain = "ngoai_pham_vi"
        if best == 0:
            reason = "no_domain_evidence"
        elif margin < 3:
            reason = "ambiguous_domain_evidence"
        else:
            reason = "insufficient_domain_evidence"

    if domain == "ngoai_pham_vi":
        confidence = (
            "high"
            if locality or hard_outside_signal or outside_signal or best == 0
            else "review"
        )
        matched = tuple(evidence[winner][:8])
        reported_score = best
        reported_margin = margin
    else:
        reported_score = scores[domain]
        reported_margin = reported_score - max(
            scores[other] for other in DOMAIN_PRIORITY if other != domain
        )
        confidence = (
            "high"
            if reported_score >= 18 and reported_margin >= 7
            else "medium"
        )
        matched = tuple(evidence[domain][:8])

    return Classification(
        document_id=document.document_id,
        title=document.title,
        law_number=document.law_number,
        source_url=document.source_url,
        domain=domain,
        confidence=confidence,
        score=reported_score,
        margin=reported_margin,
        reason=reason,
        matched_terms=matched,
        previous_included=document.previous_included,
        previous_domain=document.previous_domain,
    )


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def database_url() -> str:
    values = _dotenv_values(Path(".env"))
    configured = (
        os.getenv("LEGAL_DATABASE_URL")
        or os.getenv("LEGAL_RELEASE_DATABASE_URL")
        or values.get("LEGAL_RELEASE_DATABASE_URL")
        or values.get("DATABASE_URL")
    )
    if not configured:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is required")
    return configured.replace("host.docker.internal", "127.0.0.1")


def load_documents() -> list[SourceDocument]:
    import psycopg2

    url = database_url().replace("postgresql+psycopg2://", "postgresql://", 1)
    connection = psycopg2.connect(url, connect_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    d.id,
                    d.title,
                    d.law_number,
                    d.document_type,
                    d.issuing_agency,
                    d.scope,
                    d.sector,
                    d.applicability_info,
                    d.source_url,
                    s.included,
                    s.domain,
                    COALESCE(article_excerpt.content, '')
                FROM legal_documents d
                LEFT JOIN legal_search_scope s ON s.document_id = d.id
                LEFT JOIN LATERAL (
                    SELECT string_agg(
                        left(COALESCE(a.title, '') || ' ' || COALESCE(a.content, ''), 3200),
                        ' ' ORDER BY a.id
                    ) AS content
                    FROM (
                        SELECT id, title, content
                        FROM legal_articles
                        WHERE document_id = d.id
                        ORDER BY id
                        LIMIT 5
                    ) a
                ) article_excerpt ON TRUE
                WHERE d.status = 'active' AND d.field_id IS NULL
                ORDER BY d.id
                """
            )
            return [SourceDocument(*row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _source_snapshot(documents: Iterable[SourceDocument]) -> str:
    digest = hashlib.sha256()
    for document in documents:
        payload = {
            "document_id": document.document_id,
            "title": document.title,
            "law_number": document.law_number,
            "source_url": document.source_url,
            "previous_included": document.previous_included,
            "previous_domain": document.previous_domain,
        }
        digest.update(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def write_outputs(
    documents: list[SourceDocument],
    classifications: list[Classification],
    json_path: Path,
    csv_path: Path,
) -> dict[str, Any]:
    counts = {domain: 0 for domain in TAXONOMY}
    confidence_counts: dict[str, int] = {}
    for item in classifications:
        counts[item.domain] += 1
        confidence_counts[item.confidence] = (
            confidence_counts.get(item.confidence, 0) + 1
        )
    ids = [item.document_id for item in classifications]
    invariants = {
        "expected_total": 6141,
        "classified_total": len(classifications),
        "unique_document_ids": len(set(ids)),
        "duplicate_document_ids": len(ids) - len(set(ids)),
        "unclassified_documents": len(documents) - len(classifications),
        "unknown_domains": sorted(
            {item.domain for item in classifications if item.domain not in TAXONOMY}
        ),
    }
    invariants["passed"] = (
        invariants["classified_total"] == invariants["expected_total"]
        and invariants["unique_document_ids"] == invariants["expected_total"]
        and invariants["duplicate_document_ids"] == 0
        and invariants["unclassified_documents"] == 0
        and not invariants["unknown_domains"]
        and sum(counts.values()) == invariants["expected_total"]
    )
    if not invariants["passed"]:
        raise RuntimeError(f"classification invariants failed: {invariants}")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "table": "legal_documents",
            "filter": "status='active' AND field_id IS NULL",
            "snapshot_sha256": _source_snapshot(documents),
        },
        "activation": {
            "status": "proposed",
            "retrieval_mutated": False,
            "field_id_mutated": False,
        },
        "taxonomy": list(TAXONOMY),
        "summary": {
            "counts": counts,
            "confidence_counts": confidence_counts,
            "invariants": invariants,
        },
        "documents": [asdict(item) for item in classifications],
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "document_id", "title", "law_number", "domain", "confidence",
                "score", "margin", "reason", "matched_terms",
                "previous_included", "previous_domain", "source_url",
            ),
        )
        writer.writeheader()
        for item in classifications:
            row = asdict(item)
            row["matched_terms"] = " | ".join(item.matched_terms)
            writer.writerow(row)
    return manifest


def self_test() -> None:
    cases = (
        ("Đăng ký khai sinh cho trẻ em", "ho_tich_chung_thuc"),
        ("Cấp giấy chứng nhận quyền sử dụng đất", "dat_dai_xay_dung"),
        ("Trợ cấp xã hội đối với người cao tuổi", "an_sinh_y_te_giao_duc"),
        ("Đăng ký thường trú và cấp căn cước", "cu_tru_an_ninh"),
        (
            "Quy định xử phạt vi phạm hành chính trong lĩnh vực xây dựng",
            "khieu_nai_to_cao_xu_phat",
        ),
        ("Cơ chế một cửa trong giải quyết thủ tục hành chính", "hanh_chinh_cong"),
        ("Quản lý hoạt động hàng không dân dụng", "ngoai_pham_vi"),
        ("Quy chuẩn dịch vụ chứng thực thông điệp dữ liệu", "ngoai_pham_vi"),
        ("Quy định phòng cháy chữa cháy rừng", "dat_dai_xay_dung"),
        ("Điều lệ hợp tác xã sản xuất dịch vụ công nghiệp", "ngoai_pham_vi"),
        (
            "Điều lệ tạm thời về bảo hiểm xã hội đối với công nhân viên chức",
            "an_sinh_y_te_giao_duc",
        ),
        (
            "Điều chỉnh địa giới hành chính một số xã thuộc tỉnh Tây Ninh",
            "ngoai_pham_vi",
        ),
    )
    for index, (title, expected) in enumerate(cases, 1):
        actual = classify_document(SourceDocument(index, title)).domain
        if actual != expected:
            raise AssertionError(f"{title!r}: expected {expected}, got {actual}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        print("self-test: PASS")
        return
    documents = load_documents()
    classifications = [classify_document(document) for document in documents]
    manifest = write_outputs(documents, classifications, args.json, args.csv)
    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    print(f"manifest: {args.json.resolve()}")
    print(f"review_csv: {args.csv.resolve()}")


if __name__ == "__main__":
    main()
