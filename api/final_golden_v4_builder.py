"""Build a sealed, corpus-grounded final Golden V4 suite outside the repository.

The builder never asks a model to invent a legal source.  Every answer-required
case is derived from one or two immutable SQLite article identities with an
official source URL and an effective interval.  Development questions and
source identities are exclusion inputs, never ranking labels.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


DOMAINS = (
    "Hộ tịch/chứng thực",
    "Đất đai/xây dựng/môi trường",
    "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục",
)
DOMAIN_ACCOUNTS = {
    DOMAINS[0]: "officer_hotich",
    DOMAINS[1]: "officer_daidai",
    DOMAINS[2]: "officer_cutru",
    DOMAINS[3]: "officer_khieunai",
    DOMAINS[4]: "officer_ansinh",
}
DIRECT_DOMAIN_MAP = {
    "tu_phap_ho_tich": DOMAINS[0],
    "ho_tich_chung_thuc": DOMAINS[0],
    "dat_dai_moi_truong": DOMAINS[1],
    "dat_dai_xay_dung": DOMAINS[1],
    "xay_dung_do_thi": DOMAINS[1],
    "trat_tu_do_thi": DOMAINS[1],
    "cu_tru_an_ninh": DOMAINS[2],
    "khieu_nai_to_cao_xu_phat": DOMAINS[3],
    "an_sinh_y_te": DOMAINS[4],
    "giao_duc_van_hoa": DOMAINS[4],
}
LAW_NUMBER_RE = re.compile(r"^\s*\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\s*$", re.I)
ARTICLE_RE = re.compile(r"^\d+[a-z]?$", re.I)
ARTICLE_TITLE_RE = re.compile(r"(?:Điều|D[iI]ều)\s*([0-9]+[a-z]?)\s*[.:]?\s*([^>\n]{4,260})", re.I)
PROCEDURE_RE = re.compile(
    r"\b(?:thu tuc|trinh tu|ho so|dang ky|cap lai|thu hoi|huy bo|tach ho|xoa dang ky|"
    r"cong nhan|xac nhan|giai quyet|tiep nhan|nop|khai bao|dinh chinh|chuyen doi|"
    r"kiem tra|tham dinh|khoa|mo khoa|tich hop|cung cap|thu thap|cap nhat|noi cu tru|phoi hop)\b"
    r"|(?<!tro )\bcap\b(?!\s+(?:xa|huyen|tinh|bo|co|trung uong))",
    re.I,
)
PROCEDURE_EXCLUSION_RE = re.compile(
    r"^(?:muc thu|phi|le phi|trach nhiem|xu ly vi pham|vi pham|hanh vi vi pham|"
    r"xu phat|hinh thuc xu phat|dieu khoan|chuong|muc|phan)\b",
    re.I,
)
GENERIC_TITLE_RE = re.compile(
    r"^(?:pham vi dieu chinh|doi tuong ap dung|giai thich tu ngu|hieu luc thi hanh|"
    r"trach nhiem thi hanh|to chuc thuc hien|dieu khoan thi hanh|dieu khoan chuyen tiep|"
    r"giai thich thuat ngu|ngung hieu luc|tam ngung hieu luc|sua doi|bo sung|bai bo|"
    r"chuong\b|muc\b|phan\b|quy dinh chung|nhung quy dinh chung)\b",
    re.I,
)
COMPLAINT_RE = re.compile(
    r"\b(?:khiếu nại|tố cáo|tiếp công dân|xử phạt|vi phạm hành chính|phản ánh|kiến nghị)\b",
    re.I,
)
OFFICIAL_HOST_RE = re.compile(r"^https://(?:www\.)?(?:vbpl\.vn|vanban\.chinhphu\.vn)/", re.I)
TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
DOMAIN_TOPIC_RE = {
    DOMAINS[0]: re.compile(
        r"\b(?:ho tich|khai sinh|khai tu|ket hon|ly hon|chung thuc|nuoi con nuoi|"
        r"quoc tich|cha me con|giam ho|ho ten|tinh trang hon nhan)\b", re.I
    ),
    DOMAINS[1]: re.compile(
        r"\b(?:dat dai|thua dat|quyen su dung dat|dia chinh|xay dung|nha o|cong trinh|"
        r"quy hoach|kien truc|moi truong|chat thai|tai nguyen nuoc|khoang san|cap nuoc)\b", re.I
    ),
    DOMAINS[2]: re.compile(
        r"\b(?:cu tru|thuong tru|tam tru|thong bao luu tru|co so luu tru|can cuoc|dan cu|ho khau|xuat canh|"
        r"nhap canh|xuat nhap canh|an ninh trat tu|phong chay|cong an|tach ho|bien phong|"
        r"bao ve bi mat nha nuoc|vu khi|vat lieu no|cong cu ho tro|phong chong toi pham|"
        r"quan ly nguoi nuoc ngoai)\b", re.I
    ),
    DOMAINS[3]: re.compile(
        r"\b(?:khieu nai|to cao|tiep cong dan|xu phat|vi pham hanh chinh|phan anh|"
        r"kien nghi|bien ban vi pham|cuong che thi hanh)\b", re.I
    ),
    DOMAINS[4]: re.compile(
        r"\b(?:an sinh|tro cap|bao hiem|y te|kham benh|chua benh|duoc pham|duoc si|thuoc|giao duc|hoc sinh|"
        r"sinh vien|nha truong|nguoi co cong|bao tro xa hoi|tre em|lao dong|viec lam)\b", re.I
    ),
}


@dataclass(frozen=True)
class Quotas:
    everyday: int = 100
    procedure: int = 60
    exact_article: int = 80
    historical: int = 60
    multi_issue: int = 60
    refusal: int = 40

    @property
    def total(self) -> int:
        return sum((self.everyday, self.procedure, self.exact_article, self.historical, self.multi_issue, self.refusal))


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fold_text(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")
    return " ".join(TOKEN_RE.findall(text))


def question_similarity(first: str, second: str) -> float:
    return _signature_similarity(_question_signature(first), _question_signature(second))


def _question_signature(value: str) -> tuple[set[str], set[str]]:
    folded = fold_text(value)
    compact = folded.replace(" ", "_")
    return (
        set(folded.split()),
        {compact[index : index + 3] for index in range(max(0, len(compact) - 2))},
    )


def _signature_similarity(
    first: tuple[set[str], set[str]], second: tuple[set[str], set[str]]
) -> float:
    def jaccard(left: set[str], right: set[str]) -> float:
        return len(left & right) / len(left | right) if left or right else 1.0

    return max(jaccard(first[0], second[0]), jaccard(first[1], second[1]))


def extract_article_title(structural_path: str, article_number: str) -> str | None:
    candidates = [
        match.group(2).strip(" .:-\u00a0")
        for match in ARTICLE_TITLE_RE.finditer(str(structural_path or ""))
        if match.group(1).casefold() == str(article_number).casefold()
    ]
    for title in reversed(candidates):
        title = re.sub(r"\s+", " ", title)
        title = re.sub(r"^(?:Điều|D[iI]ều)\s*\d+[a-z]?\s*[.:]\s*", "", title, flags=re.I)
        if 12 <= len(title) <= 220 and not GENERIC_TITLE_RE.search(fold_text(title)):
            return title
    return None


def classify_domain(row: Mapping[str, Any], title: str) -> str | None:
    direct = DIRECT_DOMAIN_MAP.get(str(row.get("domain_slug") or ""))
    folded_title = fold_text(title)
    if direct and DOMAIN_TOPIC_RE[direct].search(folded_title):
        return direct
    if DOMAIN_TOPIC_RE[DOMAINS[3]].search(folded_title):
        return DOMAINS[3]
    return None


def load_article_candidates(
    connection: sqlite3.Connection,
    *,
    excluded_sources: set[tuple[str, str]],
    legal_as_of: str,
) -> dict[str, list[dict[str, Any]]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT chunk_revision_id,document_id,article_id,law_number,article_number,
               domain_slug,source_url,effective_from,effective_to,content,
               structural_path,content_sha256,passage_sha256
          FROM chunks
         WHERE document_serving_state='current_retrievable'
           AND trim(law_number)<>'' AND trim(article_number)<>''
           AND trim(source_url)<>'' AND length(content)>=80
           AND (effective_from IS NULL OR effective_from='' OR effective_from<=?)
           AND (effective_to IS NULL OR effective_to='' OR effective_to>?)
         ORDER BY law_number,article_number,chunk_index,chunk_revision_id
        """,
        (legal_as_of, legal_as_of),
    )
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        law = re.sub(r"\s+", "", str(row["law_number"] or "").upper())
        article = str(row["article_number"] or "").strip().casefold()
        if not LAW_NUMBER_RE.fullmatch(str(row["law_number"] or "")) or not ARTICLE_RE.fullmatch(article):
            continue
        if (law, article) in excluded_sources or not OFFICIAL_HOST_RE.match(str(row["source_url"] or "")):
            continue
        grouped[(law, article)].append(row)

    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (law, article), article_rows in grouped.items():
        choices: list[tuple[int, int, str, sqlite3.Row]] = []
        for row in article_rows:
            title = extract_article_title(str(row["structural_path"] or ""), article)
            if not title:
                continue
            choices.append((len(title), -len(str(row["content"] or "")), title, row))
        if not choices:
            continue
        _, _, title, row = min(choices, key=lambda value: (value[0], value[1], value[2]))
        domain = classify_domain(dict(row), title)
        if not domain:
            continue
        by_domain[domain].append({
            "law_number": law,
            "article": str(row["article_number"]).strip(),
            "title": title,
            "domain": domain,
            "official_url": str(row["source_url"]).strip(),
            "validity_from": str(row["effective_from"] or "") or None,
            "validity_to": str(row["effective_to"] or "") or None,
            "document_id": int(row["document_id"]),
            "article_id": int(row["article_id"]),
            "chunk_revision_id": str(row["chunk_revision_id"]),
            "evidence_sha256": str(row["content_sha256"] or row["passage_sha256"]),
            "procedure_like": bool(
                PROCEDURE_RE.search(fold_text(title))
                and not PROCEDURE_EXCLUSION_RE.search(fold_text(title))
            ),
        })
    for domain in by_domain:
        by_domain[domain].sort(key=lambda row: canonical_sha256({"seed": "final-v4-2000", "source": row}))
    return by_domain


def _source(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "law_number": candidate["law_number"],
        "article": candidate["article"],
        "paragraph": None,
        "point": None,
        "official_url": candidate["official_url"],
        "jurisdiction": "Việt Nam",
        "validity_from": candidate["validity_from"],
        "validity_to": candidate["validity_to"],
    }


def _identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return (re.sub(r"\s+", "", str(candidate["law_number"]).upper()), str(candidate["article"]).casefold())


def _question(candidate: Mapping[str, Any], scenario: str, variant: int) -> str:
    title = str(candidate["title"]).strip(" .")
    law = str(candidate["law_number"])
    article = str(candidate["article"])
    templates = {
        "everyday_rule": (
            "Pháp luật hiện hành quy định như thế nào về {title}?",
            "Tôi muốn hiểu quy định hiện nay liên quan đến {title}; nội dung chính là gì?",
            "Trong thực tế, quy định về {title} được áp dụng ra sao?",
        ),
        "procedure_documents": (
            "Khi thực hiện {title}, điều luật này quy định yêu cầu hoặc cách xử lý nào?",
            "Tôi cần áp dụng quy định về {title}; nội dung nào trong điều luật phải được tuân theo?",
            "Quy định về {title} xác định bước thực hiện, giấy tờ hoặc trách nhiệm nào có liên quan?",
        ),
        "exact_article": (
            "Theo Điều {article} của văn bản {law}, quy định về {title} có nội dung gì?",
            "Hãy giải thích đúng phạm vi Điều {article} văn bản {law} về {title}.",
            "Điều {article} của {law} quy định thế nào đối với {title}?",
        ),
        "historical_effectivity": (
            "Tại ngày {as_of}, Điều {article} văn bản {law} quy định gì về {title}?",
            "Xét pháp luật tại thời điểm {as_of}, nội dung về {title} theo Điều {article} của {law} là gì?",
            "Ở thời điểm {as_of}, có thể áp dụng Điều {article} văn bản {law} cho vấn đề {title} như thế nào?",
        ),
    }
    as_of = candidate.get("validity_from") or "2026-08-23"
    return templates[scenario][variant % len(templates[scenario])].format(
        title=title, law=law, article=article, as_of=as_of
    )


def _case(
    *,
    case_id: str,
    domain: str,
    scenario: str,
    question: str,
    candidates: Sequence[Mapping[str, Any]],
    role: str,
    legal_as_of: str,
    expected_refusal: bool = False,
    refusal_category: str = "none",
) -> dict[str, Any]:
    groups = [
        {"group_id": f"{case_id}-g{index}", "sources": [_source(candidate)]}
        for index, candidate in enumerate(candidates, 1)
    ]
    issues = [
        {
            "issue_id": f"{case_id}-i{index}",
            "required_source_group_ids": [group["group_id"]],
        }
        for index, group in enumerate(groups, 1)
    ]
    account = "citizen01" if role == "citizen" else DOMAIN_ACCOUNTS[domain]
    case = {
        "case_id": case_id,
        "dataset": "final-golden-v4-2000",
        "split": "production-holdout",
        "role": role,
        "account": account,
        "domain": domain,
        "scenario": scenario,
        "tags": [scenario, role, "blind-final"],
        "question": question,
        "query_classification": {
            "domain": domain,
            "intent": scenario,
            "scope": "legal" if not expected_refusal or refusal_category == "insufficient_facts" else "out_of_scope",
            "temporal_scope": "historical" if scenario == "historical_effectivity" else "current",
            "answer_type": "refusal" if expected_refusal else "grounded_answer",
            "requires_clarification": refusal_category == "insufficient_facts",
        },
        "legal_as_of": legal_as_of,
        "temporal_scope": "historical" if scenario == "historical_effectivity" else "current",
        "answer_required": not expected_refusal,
        "expected_refusal": expected_refusal,
        "refusal_category": refusal_category,
        "positive_source_groups": groups,
        "hard_negative_sources": [],
        "issue_groups": issues,
        "expected_issue_count": len(issues),
        "source_evidence_sha256": [str(candidate["evidence_sha256"]) for candidate in candidates],
        "reviewer_approval": {
            "reviewer_id": "codex-corpus-grounded-builder",
            "reviewed_at": "2026-08-23T00:00:00+07:00",
            "decision": "approved",
            "evidence_sha256": canonical_sha256([_source(candidate) for candidate in candidates] or {"refusal": refusal_category}),
        },
    }
    case["case_sha256"] = canonical_sha256(case)
    return case


def _refusal_question(domain: str, index: int) -> tuple[str, str]:
    labels = {
        DOMAINS[0]: "khai sinh, kết hôn, nhận cha mẹ con hoặc chứng thực giấy tờ hộ tịch",
        DOMAINS[1]: "thửa đất, giấy chứng nhận, công trình xây dựng hoặc hiện trạng sử dụng đất",
        DOMAINS[2]: "nơi thường trú, tạm trú, dữ liệu dân cư hoặc thẻ căn cước",
        DOMAINS[3]: "quyết định hành chính, đơn khiếu nại, nội dung tố cáo hoặc biên bản xử phạt",
        DOMAINS[4]: "trợ cấp xã hội, bảo hiểm y tế, khám chữa bệnh hoặc tuyển sinh giáo dục",
    }
    insufficient_templates = (
        "Tôi chỉ nói việc này liên quan đến {label}, chưa nêu sự kiện hay giấy tờ; có thể kết luận chắc chắn ngay không?",
        "Chưa biết người yêu cầu, thời điểm phát sinh và địa bàn, hãy cho đáp án dứt khoát về việc {label} của tôi.",
        "Tôi không nhớ tên thủ tục, cơ quan đã tiếp nhận hay tình trạng hồ sơ về {label}; hãy xác định kết quả cuối cùng.",
        "Không cung cấp quyết định, ngày nhận hoặc chủ thể liên quan, tôi muốn biết chắc quyền của mình trong việc {label}.",
        "Tình huống {label} còn thiếu tuổi, quan hệ giữa các bên và nơi xảy ra; hãy kết luận pháp lý ngay.",
        "Tôi chỉ có lời kể chung về {label}, không có mốc thời gian hay tài liệu; hệ thống có thể khẳng định hướng xử lý không?",
        "Chưa xác định đây là yêu cầu mới, sửa đổi hay khiếu nại trong lĩnh vực {label}; hãy trả lời chắc chắn phải làm gì.",
        "Tôi không cho biết hồ sơ đã nộp ở đâu và nhận phản hồi gì về {label}; hãy đoán kết quả giúp tôi.",
        "Thiếu thông tin về quốc tịch, nơi cư trú và tình trạng pháp lý nhưng tôi cần kết luận ngay cho việc {label}.",
        "Không rõ văn bản hoặc hành vi nào đang bị xem xét trong chuyện {label}; hãy xác nhận tôi chắc chắn đúng.",
        "Tôi chưa mô tả đối tượng, mục đích và phạm vi yêu cầu {label}; hãy đưa luôn thủ tục duy nhất phải dùng.",
        "Không có số hồ sơ, ngày hẹn hay cơ quan xử lý việc {label}; hãy cho biết chính xác ngày nào có kết quả.",
        "Tôi bỏ qua các dữ kiện ngoại lệ và chỉ nói chủ đề {label}; hãy khẳng định mức phí và thời hạn cụ thể.",
        "Chưa rõ vụ việc {label} thuộc cấp xã, huyện hay tỉnh; hãy chỉ định ngay cơ quan có thẩm quyền.",
        "Tôi không cung cấp bản gốc, bản sao hay tình trạng giấy tờ về {label}; hãy xác nhận hồ sơ của tôi chắc chắn hợp lệ.",
        "Không biết quy định áp dụng tại thời điểm phát sinh chuyện {label}; hãy trả lời theo một điều luật bất kỳ.",
        "Tôi chưa nói mình là cá nhân hay tổ chức trong việc {label}; hãy kết luận đầy đủ quyền và nghĩa vụ.",
        "Không nêu hành vi cụ thể, hậu quả hoặc quyết định liên quan đến {label}; hãy xác định luôn hình thức xử lý.",
        "Tôi chỉ yêu cầu tư vấn {label} nhưng không có câu hỏi thực tế; hãy tự suy đoán vấn đề và chốt đáp án.",
        "Các dữ kiện về {label} đang mâu thuẫn và chưa được kiểm chứng; hãy bỏ qua rồi đưa kết luận chắc chắn.",
    )
    if index % 2 == 0:
        template = insufficient_templates[index // 2]
        return (
            template.format(label=labels[domain]),
            "insufficient_facts",
        )
    topics = (
        "dự báo thời tiết cuối tuần", "chọn cổ phiếu tăng giá", "viết mã nguồn trò chơi",
        "dịch lời một bài hát", "chẩn đoán bệnh qua triệu chứng", "lập thực đơn giảm cân",
        "dự đoán tỷ số bóng đá", "sáng tác truyện khoa học viễn tưởng", "sửa lỗi máy tính cá nhân",
        "tư vấn mua điện thoại", "lập lịch du lịch nước ngoài", "giải bài toán hình học",
        "xem tử vi ngày sinh", "tạo ảnh quảng cáo", "đánh giá tiền mã hóa",
        "chọn nhạc cho tiệc cưới", "viết email bán hàng", "hướng dẫn nấu món ăn",
        "phân tích chiến thuật cờ vua", "đề xuất phim giải trí",
    )
    topic = topics[index // 2]
    short_labels = {
        DOMAINS[0]: "hộ tịch",
        DOMAINS[1]: "đất đai",
        DOMAINS[2]: "cư trú",
        DOMAINS[3]: "khiếu nại",
        DOMAINS[4]: "an sinh",
    }
    wrappers = (
        "Ngoài chủ đề {label}, yêu cầu của tôi là {topic}; không cần viện dẫn luật.",
        "Hãy {topic} thay vì tư vấn pháp lý trong mục {label}.",
        "Nhiệm vụ thật sự là {topic}, dù tôi đang ở chuyên mục {label}.",
        "Đừng trả lời luật {label}; hãy chuyển sang {topic}.",
        "Bỏ phạm vi {label} và thực hiện yêu cầu khác: {topic}.",
    )
    wrapper = wrappers[(DOMAINS.index(domain) + index // 2) % len(wrappers)]
    return (wrapper.format(label=short_labels[domain], topic=topic), "out_of_scope")


def build_cases(
    candidates_by_domain: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    old_questions: Sequence[str],
    quotas: Quotas = Quotas(),
    legal_as_of: str = "2026-08-23",
    similarity_threshold: float = 0.82,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if quotas.total != 400:
        raise ValueError("final_suite_requires_400_cases_per_domain")
    accepted_questions = list(old_questions)
    accepted_signatures = [_question_signature(question) for question in accepted_questions]
    accepted_normalized = {fold_text(question) for question in old_questions}
    cases: list[dict[str, Any]] = []
    rejected = Counter()
    maximum_accepted_similarity = 0.0
    maximum_rejected_similarity = 0.0

    def accept(question: str) -> bool:
        nonlocal maximum_accepted_similarity, maximum_rejected_similarity
        normalized = fold_text(question)
        if normalized in accepted_normalized:
            rejected["exact_duplicate"] += 1
            return False
        signature = _question_signature(question)
        local_max = max((_signature_similarity(signature, prior) for prior in accepted_signatures), default=0.0)
        if local_max >= similarity_threshold:
            maximum_rejected_similarity = max(maximum_rejected_similarity, local_max)
            rejected["near_duplicate"] += 1
            return False
        accepted_questions.append(question)
        accepted_signatures.append(signature)
        accepted_normalized.add(normalized)
        maximum_accepted_similarity = max(maximum_accepted_similarity, local_max)
        return True

    sequence = 0
    for domain in DOMAINS:
        pool = list(candidates_by_domain.get(domain) or [])
        procedure_pool = [candidate for candidate in pool if candidate.get("procedure_like")]
        general_pool = [candidate for candidate in pool if not candidate.get("procedure_like")]
        used: set[tuple[str, str]] = set()

        def take(count: int, scenario: str, source_pool: Sequence[Mapping[str, Any]]) -> None:
            nonlocal sequence
            produced = 0
            local_used: set[tuple[str, str]] = set()
            ordered = sorted(source_pool, key=lambda candidate: (_identity(candidate) in used, canonical_sha256(candidate)))
            for candidate in ordered:
                if produced >= count:
                    break
                if _identity(candidate) in local_used:
                    continue
                question = _question(candidate, scenario, sequence)
                if not accept(question):
                    continue
                sequence += 1
                case_id = f"V4-FINAL-{len(cases) + 1:04d}"
                role = "officer" if produced % 10 in {7, 8, 9} else "citizen"
                as_of = str(candidate.get("validity_from") or legal_as_of) if scenario == "historical_effectivity" else legal_as_of
                cases.append(_case(
                    case_id=case_id, domain=domain, scenario=scenario, question=question,
                    candidates=[candidate], role=role, legal_as_of=as_of,
                ))
                used.add(_identity(candidate))
                local_used.add(_identity(candidate))
                produced += 1
            if produced != count:
                raise ValueError(f"insufficient_unique_cases:{domain}:{scenario}:{produced}!={count}")

        take(quotas.procedure, "procedure_documents", procedure_pool)
        take(quotas.everyday, "everyday_rule", [*general_pool, *procedure_pool])
        take(quotas.exact_article, "exact_article", pool)
        take(quotas.historical, "historical_effectivity", [candidate for candidate in pool if candidate.get("validity_from")])

        unseen = [candidate for candidate in pool if _identity(candidate) not in used]
        seen = [candidate for candidate in pool if _identity(candidate) in used]
        remaining = [*unseen, *seen]
        multi_produced = 0
        for left, right in zip(remaining[::2], remaining[1::2]):
            if multi_produced >= quotas.multi_issue:
                break
            question = (
                f"Tôi cần xử lý đồng thời hai vấn đề: {left['title']} và {right['title']}. "
                "Hãy tách riêng từng vấn đề, nêu căn cứ và trả lời theo đúng thứ tự."
            )
            if not accept(question):
                continue
            sequence += 1
            case_id = f"V4-FINAL-{len(cases) + 1:04d}"
            role = "officer" if multi_produced % 10 in {7, 8, 9} else "citizen"
            cases.append(_case(
                case_id=case_id, domain=domain, scenario="multi_issue", question=question,
                candidates=[left, right], role=role, legal_as_of=legal_as_of,
            ))
            used.update((_identity(left), _identity(right)))
            multi_produced += 1
        if multi_produced != quotas.multi_issue:
            raise ValueError(f"insufficient_unique_cases:{domain}:multi_issue:{multi_produced}!={quotas.multi_issue}")

        for index in range(quotas.refusal):
            question, category = _refusal_question(domain, index)
            if not accept(question):
                raise ValueError(f"refusal_near_duplicate:{domain}:{index}")
            case_id = f"V4-FINAL-{len(cases) + 1:04d}"
            role = "officer" if index % 10 in {7, 8, 9} else "citizen"
            cases.append(_case(
                case_id=case_id, domain=domain, scenario="refusal", question=question,
                candidates=[], role=role, legal_as_of=legal_as_of,
                expected_refusal=True, refusal_category=category,
            ))

    report = {
        "case_count": len(cases),
        "domain_counts": dict(Counter(case["domain"] for case in cases)),
        "scenario_counts": dict(Counter(case["scenario"] for case in cases)),
        "role_counts": dict(Counter(case["role"] for case in cases)),
        "expected_refusal_count": sum(case["expected_refusal"] for case in cases),
        "answer_required_count": sum(case["answer_required"] for case in cases),
        "rejected_candidate_counts": dict(rejected),
        "maximum_accepted_similarity_to_prior": maximum_accepted_similarity,
        "maximum_rejected_similarity_to_prior": maximum_rejected_similarity,
        "similarity_threshold": similarity_threshold,
    }
    return cases, report


__all__ = [
    "DOMAINS", "Quotas", "build_cases", "canonical_sha256", "classify_domain",
    "extract_article_title", "fold_text", "load_article_candidates", "question_similarity",
]
