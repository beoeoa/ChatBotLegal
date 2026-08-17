"""Build 1,000 independent, review-ready legal Golden cases from PostgreSQL.

The script is read-only with respect to the legal corpus. It does not call an
LLM and does not infer legal facts: required claims are exact spans copied from
article content. Human approval is still required before the dataset is frozen.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "golden-1000-complete"
AS_OF = date(2026, 8, 11)
OFFICIAL_PREFIXES = ("https://vbpl.vn/", "https://vanban.chinhphu.vn/")

DOMAIN_SOURCES = {
    "Hộ tịch/chứng thực": ["60/2014/QH13", "43/2025/NQ-HĐND"],
    "Đất đai/xây dựng": ["31/2024/QH15", "62/2020/QH14"],
    "Cư trú/an ninh": ["68/2020/QH14", "26/2023/QH15", "55/2021/TT-BCA", "66/2023/TT-BCA"],
    "Khiếu nại/tố cáo/xử phạt": ["02/2011/QH13", "88/2025/QH15"],
    "An sinh/y tế/giáo dục": ["188/2025/NĐ-CP", "176/2025/NĐ-CP", "73/2025/QH15"],
}

EXPIRED_GUARDS = {
    "Hộ tịch/chứng thực": ("15/2015/TT-BTP", "Đã hết hiệu lực và được thay bởi Thông tư 04/2020/TT-BTP.", "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=94691", "2020-07-16"),
    "Đất đai/xây dựng": ("45/2013/QH13", "Luật Đất đai 2013 đã hết hiệu lực khi Luật Đất đai 2024 có hiệu lực.", "https://vbpl.vn/TW/Pages/vbpq-lichsu.aspx?ItemID=32833", "2025-01-01"),
    "Cư trú/an ninh": ("31/2014/NĐ-CP", "Nghị định 31/2014/NĐ-CP đã hết hiệu lực toàn bộ; không dùng thay cho quy định cư trú hiện hành.", "https://vbpl.vn/bocongan/Pages/vbpq-toanvan.aspx?ItemID=34848", "2021-07-01"),
    "Khiếu nại/tố cáo/xử phạt": ("44/2002/PL-UBTVQH10", "Pháp lệnh Xử lý vi phạm hành chính 2002 đã được Luật Xử lý vi phạm hành chính thay thế.", "https://vbpl.vn/TW/Pages/vbpq-lichsu.aspx?ItemID=22291", "2013-07-01"),
    "An sinh/y tế/giáo dục": ("41/2024/QH15", "Luật Bảo hiểm xã hội 2024 hết hiệu lực toàn bộ từ 01/01/2026; không dùng cho trả lời hiện hành.", "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=175027", "2026-01-01"),
}

OFFICIAL_CURRENT_SOURCES = {
    "02/2011/QH13": ("Còn hiệu lực", "2012-07-01", "https://vbpl.vn/bocongthuong/Pages/vbpq-toanvan.aspx?ItemID=27325"),
    "188/2025/NĐ-CP": ("Còn hiệu lực", "2025-08-15", "https://vbpl.vn/bokehoachvadautu/Pages/vbpq-toanvan.aspx?ItemID=179711&Keyword="),
    "26/2023/QH15": ("Còn hiệu lực", "2024-07-01", "https://vbpl.vn/vienkiemsatnhandantoicao/Pages/vbpq-toanvan.aspx?ItemID=165958&Keyword="),
    "31/2024/QH15": ("Còn hiệu lực", "2025-01-01", "https://vbpl.vn/TW/Pages/ivbpq-thuoctinh.aspx?ItemID=177815&Keyword="),
    "60/2014/QH13": ("Còn hiệu lực", "2016-01-01", "https://vbpl.vn/botuphap/Pages/vbpq-toanvan.aspx?ItemID=46746&dvid=41"),
    "62/2020/QH14": ("Còn hiệu lực", "2021-01-01", "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=144268"),
    "68/2020/QH14": ("Còn hiệu lực", "2021-07-01", "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=146648&dvid=13"),
    "55/2021/TT-BCA": ("Còn hiệu lực", "2021-07-01", "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=148326&Keyword="),
    "66/2023/TT-BCA": ("Còn hiệu lực", "2024-01-01", "https://vbpl.vn/bocongan/Pages/vbpq-vanbanlienquan.aspx?ItemID=163388"),
    "88/2025/QH15": ("Còn hiệu lực", "2025-07-01", "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=178737&Keyword="),
    "176/2025/NĐ-CP": ("Còn hiệu lực", "2025-07-01", "https://vbpl.vn/TW/Pages/vbpq-thuoctinh.aspx?ItemID=184035"),
    "73/2025/QH15": ("Còn hiệu lực", "2026-01-01", "https://vbpl.vn/caobang/Pages/vbpq-toanvan.aspx?ItemID=179262"),
    "43/2025/NQ-HĐND": ("Còn hiệu lực", "2026-01-01", "https://vbpl.vn/haiphong/Pages/vbpq-thuoctinh.aspx?ItemID=184698"),
}

OFFICIAL_SCHEDULED_EXPIRY = {
    "43/2025/NQ-HĐND": "2030-12-31",
}

CATEGORY_COUNTS = [
    ("procedure", 120),
    ("exact_article", 30),
    ("multi_issue", 20),
    ("validity", 20),
    ("insufficient_evidence", 10),
]


@dataclass(frozen=True)
class Article:
    document_id: int
    article_id: int
    law_number: str
    document_title: str
    document_status: str
    effective_date: str | None
    expired_date: str | None
    source_url: str
    article_number: str
    article_title: str
    content: str


@dataclass(frozen=True)
class Unit:
    article: Article
    text: str
    start: int
    end: int
    order: int


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


def compact(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFKC", compact(value)).casefold()
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def valid_article_number(value: object) -> bool:
    return bool(re.fullmatch(r"\d+[a-zA-ZđĐ]?", compact(value)))


def load_articles() -> dict[str, list[Article]]:
    all_laws = sorted({law for laws in DOMAIN_SOURCES.values() for law in laws})
    conn = psycopg2.connect(db_url(), connect_timeout=10)
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, a.id, d.law_number, d.title, d.status,
               d.effective_date, d.expired_date, d.source_url,
               a.article_number, a.title, a.content,
               a.status, a.effective_from, a.effective_to
        FROM legal_documents d
        JOIN legal_articles a ON a.document_id=d.id
        WHERE d.law_number = ANY(%s)
          AND COALESCE(a.content, '') <> ''
        ORDER BY d.id, a.id
    """, (all_laws,))
    rows = cur.fetchall()
    conn.close()
    duplicates = Counter((row[0], compact(row[8])) for row in rows)
    by_law: dict[str, list[Article]] = defaultdict(list)
    for row in rows:
        (document_id, article_id, law_number, document_title, doc_status,
         effective_date, expired_date, source_url, article_number,
         article_title, content, article_status, article_from,
         article_to) = row
        article_number = compact(article_number)
        if not valid_article_number(article_number):
            continue
        if duplicates[(document_id, article_number)] != 1:
            continue
        if not str(source_url or "").startswith(OFFICIAL_PREFIXES):
            continue
        if effective_date and effective_date > AS_OF:
            continue
        if expired_date and expired_date <= AS_OF:
            continue
        if article_from and article_from > AS_OF:
            continue
        if article_to and article_to <= AS_OF:
            continue
        if normalized(article_status) in {"expired", "inactive", "repealed", "het hieu luc"}:
            continue
        by_law[law_number].append(Article(
            document_id=document_id,
            article_id=article_id,
            law_number=law_number,
            document_title=compact(document_title),
            document_status=compact(doc_status),
            effective_date=effective_date.isoformat() if effective_date else None,
            expired_date=expired_date.isoformat() if expired_date else None,
            source_url=str(source_url),
            article_number=article_number,
            article_title=compact(article_title),
            content=str(content),
        ))
    return by_law


def semantic_units(article: Article) -> list[Unit]:
    content = article.content
    boundaries = {0, len(content)}
    for match in re.finditer(r"\n\s*\n+|(?<=[.;!?])\s+(?=(?:\d+\.|[a-zđ]\)|[A-ZÀ-Ỹ]))", content):
        boundaries.add(match.start())
        boundaries.add(match.end())
    points = sorted(boundaries)
    raw: list[tuple[int, int]] = []
    for left, right in zip(points, points[1:]):
        while left < right and content[left].isspace():
            left += 1
        while right > left and content[right - 1].isspace():
            right -= 1
        if 70 <= right - left <= 900:
            raw.append((left, right))
        elif right - left > 900:
            cursor = left
            while cursor < right:
                stop = min(cursor + 650, right)
                if stop < right:
                    split_at = max(content.rfind(";", cursor + 160, stop), content.rfind(".", cursor + 160, stop))
                    if split_at > cursor:
                        stop = split_at + 1
                start = cursor
                while start < stop and content[start].isspace():
                    start += 1
                while stop > start and content[stop - 1].isspace():
                    stop -= 1
                if stop - start >= 70:
                    raw.append((start, stop))
                cursor = max(stop, cursor + 1)
    seen = set()
    units = []
    accepted_token_sets: list[set[str]] = []
    for index, (start, end) in enumerate(raw, 1):
        text = content[start:end]
        signature = normalized(text)
        if len(signature.split()) < 12 or signature in seen:
            continue
        tokens = set(signature.split())
        if any(
            len(tokens & previous) / len(tokens | previous) >= 0.85
            for previous in accepted_token_sets
            if tokens | previous
        ):
            continue
        seen.add(signature)
        accepted_token_sets.append(tokens)
        units.append(Unit(article, text, start, end, index))
    return units


def full_article_units(article: Article) -> list[Unit]:
    """Partition the whole article into ordered, exact spans without omissions."""
    content = article.content
    units: list[Unit] = []
    cursor = 0
    order = 1
    while cursor < len(content):
        while cursor < len(content) and content[cursor].isspace():
            cursor += 1
        if cursor >= len(content):
            break
        stop = min(cursor + 900, len(content))
        if stop < len(content):
            candidates = [
                content.rfind("\n\n", cursor + 200, stop),
                content.rfind(";", cursor + 200, stop),
                content.rfind(".", cursor + 200, stop),
            ]
            boundary = max(candidates)
            if boundary > cursor:
                stop = boundary + (2 if content[boundary:boundary + 2] == "\n\n" else 1)
        while stop > cursor and content[stop - 1].isspace():
            stop -= 1
        if stop <= cursor:
            stop = min(cursor + 900, len(content))
        units.append(Unit(article, content[cursor:stop], cursor, stop, order))
        order += 1
        cursor = stop
    return units


def topic(article: Article, unit: Unit | None = None) -> str:
    title = re.sub(r"^Điều\s+\w+[.:\-]?\s*", "", article.article_title, flags=re.I).strip()
    if len(title) >= 18:
        return title[:180]
    if unit:
        text = re.sub(r"^(?:\d+\.|[a-zđ]\))\s*", "", compact(unit.text), flags=re.I)
        words = text.split()
        if len(words) >= 8:
            return " ".join(words[:18]).rstrip(";,.:")
    return article.document_title[:180]


def facet(text: str) -> str:
    n = normalized(text)
    for name, words in (
        ("deadline", ("thoi han", "ngay lam viec", "trong vong")),
        ("fee_or_amount", ("muc", "phi", "le phi", "ty le", "phan tram")),
        ("authority", ("tham quyen", "uy ban nhan dan", "co quan co tham quyen")),
        ("documents", ("ho so", "giay to", "tai lieu", "to khai")),
        ("conditions", ("dieu kien", "truong hop", "khi co")),
        ("rights", ("co quyen", "duoc huong", "duoc cap", "duoc yeu cau")),
        ("obligations", ("co trach nhiem", "phai", "nghia vu")),
    ):
        if any(word in n for word in words):
            return name
    return "substantive_rule"


def cue(unit: Unit) -> str:
    text = re.sub(r"^(?:\d+\.|[a-zđ]\))\s*", "", compact(unit.text), flags=re.I)
    words = text.split()
    if len(words) <= 14:
        return text[:150]
    return " ".join(words[:9] + words[-5:])[:150]


def question_for(unit: Unit, index: int) -> str:
    art = unit.article
    subject = topic(art, unit)
    f = facet(unit.text)
    prompts = {
        "deadline": "Tôi cần tính đúng thời hạn và mốc xử lý",
        "fee_or_amount": "Tôi cần biết mức, tỷ lệ hoặc khoản phải áp dụng",
        "authority": "Tôi chưa rõ cơ quan nào có thẩm quyền giải quyết",
        "documents": "Tôi đang chuẩn bị hồ sơ và giấy tờ cần thiết",
        "conditions": "Tôi cần kiểm tra mình có thuộc trường hợp hoặc đủ điều kiện hay không",
        "rights": "Tôi muốn biết quyền hoặc chế độ mình được hưởng",
        "obligations": "Tôi muốn biết nghĩa vụ và trách nhiệm phải thực hiện",
        "substantive_rule": "Tôi cần hiểu quy định áp dụng cho trường hợp của mình",
    }
    endings = [
        "Xin nêu đúng nội dung có căn cứ và không suy rộng.",
        "Xin chỉ rõ căn cứ đang có hiệu lực tại ngày 11/08/2026.",
        "Xin giải thích ngắn gọn để người dân có thể thực hiện đúng.",
        "Nếu căn cứ không đủ, xin nói rõ phần nào chưa thể kết luận.",
    ]
    return (
        f"{prompts[f]} về “{subject}”. Theo Điều {art.article_number} "
        f"{art.law_number}, quy định cụ thể liên quan là gì? Phần tôi cần đối chiếu có dấu hiệu “{cue(unit)}”. "
        f"Đây là phần tham chiếu thứ {unit.order} trong điều. {endings[index % len(endings)]}"
    )


def source_object(article: Article, reason: str) -> dict:
    return {
        "law_number": article.law_number,
        "document_title": article.document_title,
        "article": article.article_number,
        "clause": None,
        "point": None,
        "reason": reason,
        "proof": {
            "official_url": article.source_url,
            "quote": article.content,
            "page_number": None,
            "char_start": 0,
            "char_end": len(article.content),
            "bounding_box": None,
            "checked_at": AS_OF.isoformat(),
        },
    }


def claim_object(unit: Unit, claim_id: str, order: int) -> dict:
    return {
        "claim_id": claim_id,
        "facet": facet(unit.text),
        "text": unit.text,
        "order": order,
        "critical": True,
    }


def split_for(domain_position: int) -> str:
    if domain_position < 120:
        return "development"
    if domain_position < 160:
        return "validation"
    return "held_out"


def round_robin_by_law(items: list, laws: list[str], law_getter) -> list:
    groups = {law: [item for item in items if law_getter(item) == law] for law in laws}
    result = []
    index = 0
    while any(index < len(groups[law]) for law in laws):
        for law in laws:
            if index < len(groups[law]):
                result.append(groups[law][index])
        index += 1
    return result


def case_base(case_id: str, domain: str, procedure_family: str, category: str,
              question: str, expected_sources: list[dict], claims: list[dict],
              forbidden: list[dict] | None = None, refusal: bool = False,
              unresolved_reason: str | None = None) -> dict:
    return {
        "case_id": case_id,
        "schema_version": "2.0",
        "domain": domain,
        "procedure_family": procedure_family,
        "legal_as_of": AS_OF.isoformat(),
        "questions": {"citizen": question, "officer": None},
        "expected_sources": expected_sources,
        "forbidden_sources": forbidden or [],
        "required_claims": claims,
        "expected_answer_mode": "explicit_fallback" if refusal else "grounded_answer",
        "expected_refusal": refusal,
        "risk_tags": [
            f"scenario_category_{category}",
            "independent_golden_v3",
            "requires_human_legal_approval",
        ],
        "evaluation_split": "",
        "review_status": "pending_human_review",
        "unresolved_reason": unresolved_reason,
    }


def refusal_questions(domain: str) -> list[tuple[str, str]]:
    labels = {
        "Hộ tịch/chứng thực": "hộ tịch hoặc chứng thực",
        "Đất đai/xây dựng": "đất đai hoặc xây dựng",
        "Cư trú/an ninh": "cư trú hoặc an ninh",
        "Khiếu nại/tố cáo/xử phạt": "khiếu nại, tố cáo hoặc xử phạt",
        "An sinh/y tế/giáo dục": "an sinh, y tế hoặc giáo dục",
    }
    label = labels[domain]
    missing = [
        ("missing_procedure", "Tôi chỉ nói cần làm thủ tục nhưng chưa nêu thủ tục cụ thể, sự kiện hay kết quả mong muốn."),
        ("missing_authority", "Tôi muốn phản ánh quyết định nhưng chưa cho biết cơ quan hoặc người đã ban hành quyết định."),
        ("missing_date", "Tôi hỏi còn thời hạn hay không nhưng chưa cung cấp ngày nhận quyết định hoặc ngày phát sinh sự kiện."),
        ("missing_person", "Tôi hỏi mình có thuộc diện được giải quyết không nhưng chưa nêu tuổi, quan hệ và tư cách của người liên quan."),
        ("missing_location", "Tôi chưa cho biết nơi cư trú, nơi có tài sản hoặc nơi sự kiện xảy ra để xác định thẩm quyền."),
        ("missing_document", "Tôi chỉ có ảnh bị cắt mất số hiệu, ngày ban hành và cơ quan ban hành của văn bản cần kiểm tra."),
        ("conflicting_facts", "Thông tin tôi cung cấp mâu thuẫn về ngày tháng và chưa xác định dữ kiện nào là đúng."),
        ("hypothetical_future", "Tôi hỏi về một quy định dự kiến trong tương lai nhưng không có văn bản chính thức đã ban hành."),
        ("personal_prediction", "Tôi yêu cầu khẳng định chắc chắn cơ quan sẽ chấp thuận hồ sơ dù chưa có hồ sơ và chứng cứ."),
        ("outside_legal_scope", "Tôi yêu cầu tư vấn lựa chọn mang tính cá nhân, không phải câu hỏi có thể kết luận từ quy định pháp luật."),
    ]
    return [
        (
            reason,
            f"Tôi cần hỏi về {label}, nhưng {body.lower()} Hệ thống có thể kết luận ngay không; nếu không, cần tôi bổ sung chính xác thông tin gì?",
        )
        for reason, body in missing
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    by_law = load_articles()
    missing_laws = sorted({law for laws in DOMAIN_SOURCES.values() for law in laws if not by_law.get(law)})
    if missing_laws:
        raise RuntimeError(f"Core laws missing usable articles: {missing_laws}")

    cases: list[dict] = []
    claim_evidence: list[dict] = []
    source_inventory = []
    global_case_number = 1
    used_unit_keys: set[tuple[int, int, int, int]] = set()

    for domain, laws in DOMAIN_SOURCES.items():
        articles = [article for law in laws for article in by_law[law]]
        articles.sort(key=lambda a: (a.law_number, int(re.match(r"\d+", a.article_number).group()), a.article_id))
        article_units = {a.article_id: semantic_units(a) for a in articles}
        articles = [a for a in articles if article_units[a.article_id]]

        exact_candidates = [a for a in articles if 100 <= len(a.content) <= 16000 and len(full_article_units(a)) <= 20]
        exact_articles = round_robin_by_law(exact_candidates, laws, lambda article: article.law_number)[:30]
        if len(exact_articles) < 30:
            raise RuntimeError(f"{domain}: only {len(exact_articles)} exact-article candidates")
        # A granular claim case and a full-article case exercise different
        # behaviours, so they may share an article while retaining different
        # complete source+claim signatures.
        single_unit_exact_ids = {
            article.article_id for article in exact_articles
            if len(full_article_units(article)) == 1
        }
        pool = [
            unit for article in articles if article.article_id not in single_unit_exact_ids
            for unit in article_units[article.article_id]
        ]
        pool.sort(key=lambda u: (u.article.law_number, u.article.article_id, u.start))
        pool = round_robin_by_law(pool, laws, lambda unit: unit.article.law_number)
        if len(pool) < 180:
            raise RuntimeError(f"{domain}: only {len(pool)} independent semantic units")

        domain_cases: list[dict] = []
        cursor = 0
        # 120 practical/procedural questions.
        for local_index in range(120):
            unit = pool[cursor]
            cursor += 1
            used_unit_keys.add((unit.article.document_id, unit.article.article_id, unit.start, unit.end))
            case_id = f"golden-{global_case_number:04d}"
            case = case_base(
                case_id, domain, topic(unit.article, unit), "procedure",
                question_for(unit, local_index),
                [source_object(unit.article, f"Điều {unit.article.article_number} {unit.article.law_number} - nguồn hiện hành tại ngày chốt.")],
                [claim_object(unit, "claim-01", 1)],
            )
            domain_cases.append(case)
            claim_evidence.append({
                "case_id": case_id, "claim_id": "claim-01",
                "db_document_id": unit.article.document_id,
                "db_article_id": unit.article.article_id,
                "char_start": unit.start, "char_end": unit.end,
                "quote": unit.text,
            })
            global_case_number += 1

        # 30 full-article retrieval questions.
        for article in exact_articles:
            units = full_article_units(article)
            case_id = f"golden-{global_case_number:04d}"
            question = (
                f"Xin cho biết đầy đủ Điều {article.article_number} của {article.law_number} "
                f"về “{topic(article)}” gồm những nội dung nào. Hãy giữ đúng thứ tự khoản, điểm, "
                "báo rõ nếu thiếu phần nào và chỉ dùng văn bản hiện hành."
            )
            claims = [claim_object(unit, f"claim-{i:02d}", i) for i, unit in enumerate(units, 1)]
            case = case_base(
                case_id, domain, topic(article), "exact_article", question,
                [source_object(article, f"Toàn bộ Điều {article.article_number} {article.law_number} theo đúng thứ tự cấu trúc.")],
                claims,
            )
            domain_cases.append(case)
            for i, unit in enumerate(units, 1):
                used_unit_keys.add((article.document_id, article.article_id, unit.start, unit.end))
                claim_evidence.append({
                    "case_id": case_id, "claim_id": f"claim-{i:02d}",
                    "db_document_id": article.document_id,
                    "db_article_id": article.article_id,
                    "char_start": unit.start, "char_end": unit.end,
                    "quote": unit.text,
                })
            global_case_number += 1

        # 20 multi-issue questions, each with two unused semantic units.
        for local_index in range(20):
            left = pool[cursor]
            partner_index = next(
                index for index in range(cursor + 1, len(pool))
                if pool[index].article.article_id != left.article.article_id
            )
            pool[cursor + 1], pool[partner_index] = pool[partner_index], pool[cursor + 1]
            right = pool[cursor + 1]
            cursor += 2
            case_id = f"golden-{global_case_number:04d}"
            question = (
                f"Tôi đồng thời cần xử lý hai vấn đề: “{topic(left.article, left)}” "
                f"(phần {left.order}: {cue(left)}) và “{topic(right.article, right)}” "
                f"(phần {right.order}: {cue(right)}). Xin tách riêng từng vấn đề, nêu quy định tương ứng "
                "và sắp xếp câu trả lời theo đúng thứ tự này."
            )
            case = case_base(
                case_id, domain, f"{topic(left.article, left)} + {topic(right.article, right)}",
                "multi_issue", question,
                [
                    source_object(left.article, f"Vấn đề 1 - Điều {left.article.article_number} {left.article.law_number}."),
                    source_object(right.article, f"Vấn đề 2 - Điều {right.article.article_number} {right.article.law_number}."),
                ],
                [claim_object(left, "claim-01", 1), claim_object(right, "claim-02", 2)],
            )
            domain_cases.append(case)
            for claim_id, unit in (("claim-01", left), ("claim-02", right)):
                used_unit_keys.add((unit.article.document_id, unit.article.article_id, unit.start, unit.end))
                claim_evidence.append({
                    "case_id": case_id, "claim_id": claim_id,
                    "db_document_id": unit.article.document_id,
                    "db_article_id": unit.article.article_id,
                    "char_start": unit.start, "char_end": unit.end,
                    "quote": unit.text,
                })
            global_case_number += 1

        # 20 current-vs-expired validity gates.
        old_law, old_reason, old_url, old_expired_date = EXPIRED_GUARDS[domain]
        for local_index in range(20):
            unit = pool[cursor]
            cursor += 1
            case_id = f"golden-{global_case_number:04d}"
            question = (
                f"Tôi thấy một hướng dẫn cũ viện dẫn {old_law}. Tại ngày {AS_OF.strftime('%d/%m/%Y')}, "
                f"văn bản đó còn hiệu lực để trả lời hiện hành không; Điều {unit.article.article_number} "
                f"{unit.article.law_number} hiện quy định gì về “{topic(unit.article, unit)}”? "
                f"Dấu hiệu nội dung cần đối chiếu ở phần {unit.order} là “{cue(unit)}”."
            )
            case = case_base(
                case_id, domain, topic(unit.article, unit), "validity", question,
                [source_object(unit.article, f"Nguồn hiện hành đối chiếu văn bản cũ - Điều {unit.article.article_number}.")],
                [claim_object(unit, "claim-01", 1)],
                forbidden=[{"law_number": old_law, "reason": old_reason}],
            )
            domain_cases.append(case)
            used_unit_keys.add((unit.article.document_id, unit.article.article_id, unit.start, unit.end))
            claim_evidence.append({
                "case_id": case_id, "claim_id": "claim-01",
                "db_document_id": unit.article.document_id,
                "db_article_id": unit.article.article_id,
                "char_start": unit.start, "char_end": unit.end,
                "quote": unit.text,
            })
            global_case_number += 1

        # 10 genuinely under-specified/out-of-scope questions.
        for reason, question in refusal_questions(domain):
            case_id = f"golden-{global_case_number:04d}"
            case = case_base(
                case_id, domain, f"Dự phòng - {reason}", "insufficient_evidence",
                question, [], [], refusal=True,
                unresolved_reason=f"Không được kết luận pháp lý vì {reason}; phải yêu cầu người dùng bổ sung dữ kiện hoặc giới hạn lại câu hỏi.",
            )
            domain_cases.append(case)
            global_case_number += 1

        if len(domain_cases) != 200:
            raise AssertionError((domain, len(domain_cases)))
        for position, case in enumerate(domain_cases):
            case["evaluation_split"] = split_for(position)
        cases.extend(domain_cases)

        for article in articles:
            if any(article.law_number == law for law in laws):
                source_inventory.append({
                    "domain": domain,
                    "db_document_id": article.document_id,
                    "db_article_id": article.article_id,
                    "law_number": article.law_number,
                    "document_title": article.document_title,
                    "article": article.article_number,
                    "article_title": article.article_title,
                    "status": article.document_status,
                    "effective_date": article.effective_date,
                    "expired_date": article.expired_date,
                    "official_url": article.source_url,
                    "used": any(e["db_article_id"] == article.article_id for e in claim_evidence),
                })

    if len(cases) != 1000:
        raise AssertionError(len(cases))
    payload = {
        "schema_version": "golden-1000-independent-v3",
        "dataset_kind": "citizen_legal_golden_review_packet",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": AS_OF.isoformat(),
        "status": "ready_for_machine_validation",
        "usage_notice": "Máy đã đối chiếu nguồn/quote; chuyên gia pháp lý vẫn phải duyệt trước khi đóng băng làm ground truth.",
        "summary": {
            "case_count": len(cases),
            "domain_counts": dict(Counter(case["domain"] for case in cases)),
            "split_counts": dict(Counter(case["evaluation_split"] for case in cases)),
            "category_counts": dict(Counter(tag.removeprefix("scenario_category_") for case in cases for tag in case["risk_tags"] if tag.startswith("scenario_category_"))),
            "claim_count": sum(len(case["required_claims"]) for case in cases),
            "source_count": sum(len(case["expected_sources"]) for case in cases),
            "refusal_count": sum(case["expected_refusal"] for case in cases),
        },
        "cases": cases,
    }
    dataset_path = OUT / "golden-1000-complete.json"
    dataset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "claim-evidence-map.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "claims": claim_evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "source-inventory.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "sources": source_inventory}, ensure_ascii=False, indent=2), encoding="utf-8")
    validity_guards = [
        {
            "domain": domain,
            "law_number": values[0],
            "reason": values[1],
            "official_url": values[2],
            "expired_date": values[3],
            "checked_at": AS_OF.isoformat(),
        }
        for domain, values in EXPIRED_GUARDS.items()
    ]
    (OUT / "validity-guards.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "guards": validity_guards}, ensure_ascii=False, indent=2), encoding="utf-8")
    current_source_checks = [
        {
            "law_number": law_number,
            "status": values[0],
            "effective_date": values[1],
            "expired_date": OFFICIAL_SCHEDULED_EXPIRY.get(law_number),
            "official_url": values[2],
            "checked_at": AS_OF.isoformat(),
        }
        for law_number, values in OFFICIAL_CURRENT_SOURCES.items()
    ]
    (OUT / "official-current-sources.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "sources": current_source_checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": "golden-1000-independent-manifest-v1",
        "dataset": dataset_path.name,
        "sha256": digest,
        "legal_as_of": AS_OF.isoformat(),
        "production_mutation": False,
        "generation": "deterministic_db_spans_no_llm",
        "human_approval_required": True,
        "used_semantic_unit_count": len(used_unit_keys),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(dataset_path), "summary": payload["summary"], "sha256": digest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
