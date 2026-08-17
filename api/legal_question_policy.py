"""Deterministic question classification and answer contracts.

This module deliberately does not use an LLM.  The classifier only controls
the response shape; legal conclusions still come from the filtered retrieval
sources.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


QUESTION_TYPES = (
    "procedure",
    "form_request",
    "legal_explanation",
    "land_construction",
    "complaint_sanction",
    "residence_security",
    "social_support",
    "out_of_scope",
)

_FOLD_RE = re.compile(r"\s+")

_OFFICER_HEADINGS = [
    "## 1. Kết luận chuyên môn",
    "## 2. Căn cứ pháp lý & thẩm quyền",
    "## 3. Quy trình xử lý đề xuất",
    "## 4. Hồ sơ / biểu mẫu",
    "## 5. Điểm cần xác minh trước khi trả lời dân",
]
_CITIZEN_PROCEDURE_HEADINGS = [
    "## Kết luận ngắn",
    "## Bạn cần làm gì ngay",
    "## Nơi nộp / cơ quan giải quyết",
    "## Hồ sơ bắt buộc",
    "## Giấy tờ chỉ cần trong trường hợp đặc biệt",
    "## Thời hạn, lệ phí hoặc mức phạt",
    "## Các bước thực hiện",
    "## Biểu mẫu và căn cứ pháp lý",
]
_CITIZEN_EXPLANATION_HEADINGS = [
    "## Kết luận",
    "## Điều kiện áp dụng",
    "## Căn cứ pháp lý",
    "## Việc cần xác minh",
]

# A procedure profile is not permission to fill every possible procedure
# field.  The classifier supplies the user's requested sections and this map
# keeps the prompt/validator contract aligned with that intent.
_CITIZEN_PROCEDURE_HEADING_BY_SECTION = {
    "conclusion": ["## Kết luận ngắn"],
    "submission_place": ["## Nơi nộp / cơ quan giải quyết"],
    "documents": ["## Hồ sơ bắt buộc"],
    "processing_time": ["## Thời hạn, lệ phí hoặc mức phạt"],
    "fee": ["## Thời hạn, lệ phí hoặc mức phạt"],
    "penalty": ["## Thời hạn, lệ phí hoặc mức phạt"],
    "steps": ["## Bạn cần làm gì ngay", "## Các bước thực hiện"],
    "verification_duties": ["## Nội dung cơ quan cần kiểm tra, xác minh"],
    "recorded_content": ["## Nội dung được ghi nhận"],
    "official_forms": ["## Biểu mẫu và căn cứ pháp lý"],
    "legal_basis_links": ["## Biểu mẫu và căn cứ pháp lý"],
}


def _citizen_procedure_headings(
    required_sections: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Build only headings requested by the deterministic question policy."""
    headings: list[str] = []
    for section in required_sections or ("conclusion",):
        for heading in _CITIZEN_PROCEDURE_HEADING_BY_SECTION.get(str(section), []):
            if heading not in headings:
                headings.append(heading)
    return headings or ["## Kết luận ngắn"]
_PROCEDURAL_SECTIONS = {
    "submission_place",
    "documents",
    "processing_time",
    "fee",
    "steps",
    "official_forms",
}

_HEADING_ALIASES = {
    "ket luan ngan noi nop": "## Kết luận ngắn",
    "ket luan ngan": "## Kết luận ngắn",
    "giay to can chuan bi": "## Hồ sơ bắt buộc",
    "huong dan thuc hien": "## Các bước thực hiện",
    "noi nop co quan giai quyet": "## Nơi nộp / cơ quan giải quyết",
}


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "").casefold()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    # Vietnamese đ/Đ is not decomposed by Unicode NFD. Normalize it explicitly
    # so the classifier uses the same accent-insensitive vocabulary as search.
    text = text.replace("đ", "d")
    return _FOLD_RE.sub(" ", text).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_fold(phrase) in text for phrase in phrases)


def answer_contract(
    role: str,
    question_type: str | None,
    required_sections: list[str] | tuple[str, ...] | None,
) -> dict[str, Any]:
    """Return the single presentation contract shared by every Ask path.

    The contract contains presentation rules only.  It never supplies legal
    facts and therefore can safely be used by prompts, local fallback and
    deterministic validation.
    """
    normalized_role = role if role in {"citizen", "officer", "admin"} else "citizen"
    sections = [str(item) for item in (required_sections or [])]
    procedural = bool(
        question_type in {"procedure", "form_request"}
        or _PROCEDURAL_SECTIONS.intersection(sections)
    )

    if normalized_role == "officer":
        profile = "officer"
        headings = list(_OFFICER_HEADINGS)
        style = "Văn phong hành chính, chuyên môn; đi thẳng vào kết luận."
    elif normalized_role == "admin":
        profile = "admin"
        headings = []
        style = (
            "Văn phong kỹ thuật, ngắn gọn; ưu tiên trạng thái dữ liệu, "
            "độ tin cậy, giới hạn và hành động quản trị."
        )
    elif procedural:
        profile = "citizen_procedure"
        headings = _citizen_procedure_headings(sections)
        style = "Câu ngắn, từ phổ thông; chỉ tập trung vào các mục người dùng đã hỏi."
    else:
        profile = "citizen_explanation"
        headings = list(_CITIZEN_EXPLANATION_HEADINGS)
        style = "Giải thích ngắn gọn bằng từ phổ thông, không ép thêm thủ tục."

    return {
        "role": normalized_role,
        "profile": profile,
        "question_type": question_type or "unknown",
        "required_sections": sections,
        "headings": headings,
        "style": style,
        "procedural": procedural,
    }


def render_answer_contract(contract: dict[str, Any]) -> str:
    """Render a model-facing contract from the same deterministic object."""
    lines = [str(contract.get("style") or "Trả lời ngắn gọn, rõ ràng.")]
    requested = [str(item) for item in (contract.get("required_sections") or [])]
    if requested:
        lines.append(
            "Chỉ trả lời các mục đã được yêu cầu: "
            + ", ".join(requested)
            + ". Không tự mở rộng sang thẩm quyền, thời hạn, lệ phí, mức phạt "
            "hoặc giấy tờ khác nếu câu hỏi không hỏi các mục đó."
        )
    headings = [str(item) for item in (contract.get("headings") or [])]
    if headings:
        lines.append("Dùng đúng thứ tự heading sau; ẩn mục không áp dụng:")
        lines.extend(f"- `{heading}`" for heading in headings)
    else:
        lines.append("Không bắt buộc cấu trúc heading cố định.")
    lines.extend(
        [
            "Không thêm nội dung chỉ để lấp đủ đề mục.",
            (
                'Mục thiếu căn cứ phải ghi "Chưa xác minh được nội dung này '
                'từ nguồn hiện có".'
            ),
        ]
    )
    return "\n".join(lines)


def missing_contract_headings(answer: str, contract: dict[str, Any]) -> list[str]:
    """Report missing headings without modifying or filling answer content."""
    folded = _fold(answer)
    return [
        heading
        for heading in contract.get("headings") or []
        if _fold(re.sub(r"^#+\s*", "", str(heading))) not in folded
    ]


def normalize_answer_markdown(answer: str) -> str:
    """Repair presentation mechanically without generating legal content."""
    normalized_lines: list[str] = []
    for raw_line in str(answer or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        bold_heading = re.fullmatch(r"\*\*\s*(.+?)\s*\*\*", line)
        if bold_heading:
            title = bold_heading.group(1).strip()
            canonical = _HEADING_ALIASES.get(_fold(title), f"## {title}")
            line = canonical
        elif re.match(r"^#{1,6}\s*", line):
            title = re.sub(r"^#{1,6}\s*", "", line).strip().strip("*").strip()
            line = _HEADING_ALIASES.get(_fold(title), f"## {title}")
        elif re.match(r"^[-*+]\s+", line):
            line = "- " + re.sub(r"^[-*+]\s+", "", line).strip()
        line = re.sub(r"[ \t]+", " ", line).rstrip()
        if not line:
            if normalized_lines and normalized_lines[-1] != "":
                normalized_lines.append("")
            continue
        normalized_lines.append(line)

    while normalized_lines and normalized_lines[-1] == "":
        normalized_lines.pop()
    return "\n".join(normalized_lines).strip()


def classify_question(question: str, detected_domain: str | None = None) -> dict[str, Any]:
    """Classify a question without inventing legal meaning.

    Domain-specific types take precedence over generic procedure/explanation
    labels so the answer engine can choose the right checklist.
    """
    # Source-oriented questions can quote a legal provision in a
    # parenthetical, e.g. “phần 8: Khiếu nại...”, while the citizen is asking
    # about residence rights. Do not let those quoted source words change the
    # response contract into a complaint workflow. The quoted text remains in
    # the retrieval query; this only controls deterministic routing facets.
    classification_text = re.sub(r"\([^()]*\)", " ", str(question or ""))
    text = _fold(classification_text)
    form_request = _has_any(text, (
        "bieu mau", "mau don", "to khai", "tai mau", "mau de nghi",
        "mau dang ky", "download mau", "mau giay", "mau nao", "bieu mau nao",
        "cho toi mau", "can mau", "xin mau",
    ))
    asks_specific_provision = bool(re.search(r"\bdieu\s+\d+", text))
    direct_content_request = not asks_specific_provision and _has_any(text, (
        "noi dung gom", "gom nhung thong tin", "gom thong tin gi",
        "nhung noi dung chinh", "noi dung chinh nao", "noi dung gi",
        "ghi nhung noi dung", "noi dung can ghi", "noi dung ghi nhan",
        "ghi nhan ra sao", "thong tin nao", "thong tin gi",
        "the hien cac thong tin", "bao gom nhung noi dung",
        "duoc ghi", "se ghi", "ghi theo", "ghi ra sao", "ghi the nao",
    ))
    explicit_procedure_request = _has_any(text, (
        "thu tuc", "ho so", "nop o dau", "noi nop", "thoi han",
        "le phi", "lam the nao", "can chuan bi",
        "thuc hien the nao", "quy trinh", "xac minh the nao",
        "cach xac minh", "lap bien ban", "ngan chan", "buoc khac phuc",
        "trinh tu", "xu ly the nao", "xu ly ra sao", "tiep nhan ho so",
    ))
    generic_action_request = _has_any(text, ("dang ky", "xin cap", "de nghi cap"))
    procedure = explicit_procedure_request or generic_action_request
    verification_request = _has_any(text, (
        "trach nhiem xac minh", "trach nhiem kiem tra", "phai xac minh",
        "noi dung xac minh", "kiem tra xac minh", "co quan xac minh",
    ))
    authority_request = _has_any(text, (
        "co quan nao", "ai giai quyet", "tham quyen", "lien he co quan",
        "cap nao giai quyet", "don vi nao", "nop cho ai",
    ))
    land = _has_any(text, (
        "dat dai", "so do", "giay chung nhan quyen su dung dat", "xay dung",
        "giay phep xay dung", "chuyen nhuong dat", "tach thua", "thua dat",
        "ranh gioi dat", "quy hoach", "khu dat", "dat xen ket",
        "thu hoi dat", "dien tich dat",
    ))
    complaint = _has_any(text, (
        "khieu nai", "to cao", "xu phat", "phat hanh chinh", "bien ban",
        "quyet dinh xu phat", "khieu kien", "giai trinh",
    ))
    residence = _has_any(text, (
        "cu tru", "thuong tru", "tam tru", "luu tru", "can cuoc", "an ninh",
        "xac nhan cu tru", "cu tru dien tu",
    ))
    social = _has_any(text, (
        "bao tro xa hoi", "tro cap", "ho ngheo", "bao hiem y te", "y te",
        "giao duc", "nguoi co cong", "khuyet tat", "tre em",
    ))
    explanation = _has_any(text, (
        "la gi", "quy dinh the nao", "co duoc khong", "dieu kien",
        "quyen va nghia vu", "phan biet", "giai thich", "theo dieu nao",
    ))

    if form_request:
        question_type = "form_request"
    elif land:
        question_type = "land_construction"
    elif complaint:
        question_type = "complaint_sanction"
    elif residence:
        question_type = "residence_security"
    elif social:
        question_type = "social_support"
    elif direct_content_request and not explicit_procedure_request:
        question_type = "legal_explanation"
    elif procedure:
        question_type = "procedure"
    elif explanation:
        question_type = "legal_explanation"
    elif detected_domain:
        question_type = "legal_explanation"
    else:
        question_type = "out_of_scope"

    # An authority-only question is a targeted request, not a broad legal
    # explanation. Treating it as an explanation used to invent condition and
    # exception facets that the user never asked for, depressing coverage even
    # when the authority evidence was fully grounded.
    procedural_intent = bool(
        form_request
        or authority_request
        or explicit_procedure_request
        or (generic_action_request and not direct_content_request)
    )
    if procedural_intent:
        # Ask only for sections the citizen actually requested.  A broad
        # procedure template made a documents-only question look incomplete
        # merely because it did not also discuss fees, deadlines and forms.
        sections = ["conclusion"]
        if _has_any(text, (
            "ho so", "giay to", "tai lieu", "can chuan bi", "can mang",
            "mang gi", "can gi",
        )):
            sections.append("documents")
        if _has_any(text, (
            "nop o dau", "noi nop", "nop tai dau", "co quan nao",
            "bo phan nao", "tham quyen nao", "tham quyen",
            "ai giai quyet", "nop cho ai", "co quan xac minh", "cap xa", "cap phuong",
        )):
            sections.append("submission_place")
        if _has_any(text, (
            "thoi han", "bao lau", "may ngay", "khi nao co ket qua",
            "ngay lam viec",
        )):
            sections.append("processing_time")
        if _has_any(text, (
            "le phi", "muc phi", "phi bao nhieu", "mien phi",
            "thue phi", "phi ra sao", "chi phi",
        )):
            sections.append("fee")
        step_request = _has_any(text, (
            "lam the nao", "thuc hien the nao", "quy trinh",
            "cac buoc", "dang ky nhu the nao", "xin cap nhu the nao",
            "xac minh the nao", "cach xac minh", "lap bien ban",
            "ngan chan", "buoc khac phuc", "trinh tu", "trinh tu nao",
            "xu ly theo trinh tu", "xu ly the nao", "xu ly ra sao",
        ))
        # "Thủ tục có mã X cần biểu mẫu nào?" identifies a procedure; it does
        # not ask for the procedure steps. Requiring steps here made an exact
        # form lookup look incomplete even when the approved form was returned.
        if _has_any(text, ("thu tuc",)) and not form_request:
            step_request = True
        if step_request:
            sections.append("steps")
        if verification_request:
            sections.append("verification_duties")
        if direct_content_request:
            sections.append("recorded_content")
        if _has_any(text, (
            "dieu kien", "truong hop nao", "hop phap",
            "duoc phep khi nao", "khi nao duoc", "dien tich toi thieu",
            "ty le dat", "ty le toi da", "muc toi da",
        )):
            sections.append("conditions_or_rights")
        if form_request:
            sections.append("official_forms")
        if len(sections) == 1 and generic_action_request:
            # A vague "đăng ký/xin cấp" request still benefits from a concise
            # action path, without inventing demands for every procedure field.
            sections.append("steps")
        # Citations remain mandatory for legal conclusions, but do not force a
        # separate legal-basis/form section when the user only asks for a
        # checklist. Add it only when the question explicitly requests sources
        # or legal grounds.
        if _has_any(text, (
            "can cu", "dieu luat", "theo dieu", "van ban", "nguon",
            "trich dan", "co so phap ly", "quy dinh nao",
        )):
            sections.append("legal_basis_links")
    elif direct_content_request:
        sections = ["recorded_content"]
        if _has_any(text, (
            "can cu", "dieu luat", "theo dieu", "van ban", "nguon",
            "trich dan", "co so phap ly", "quy dinh nao",
        )):
            sections.append("legal_basis_links")
    elif question_type in {"complaint_sanction"}:
        sections = ["conclusion", "authority", "rights_or_explanation", "documents", "deadline", "legal_basis_links"]
    else:
        # A direct request to explain one provision needs the governing rule,
        # not synthetic condition and exception sections. Add those facets only
        # when the user explicitly asks for them; otherwise a perfectly
        # retrievable article can be rendered as two unrelated evidence gaps.
        sections = ["applicable_rule"]
        if _has_any(text, (
            "dieu kien", "truong hop nao", "khi nao", "quyen va nghia vu",
            "duoc phep khi nao", "ap dung trong truong hop",
        )):
            sections.append("conditions_or_rights")
        if _has_any(text, (
            "ngoai le", "khong ap dung", "tru truong hop", "loai tru",
        )):
            sections.append("exceptions")
        if _has_any(text, (
            "can cu", "dieu luat", "theo dieu", "van ban", "nguon",
            "trich dan", "co so phap ly", "quy dinh nao",
        )):
            sections.append("legal_basis_links")

    return {
        "question_type": question_type,
        "detected_domain": detected_domain,
        "required_sections": sections,
        "requests_form": form_request,
        "is_procedural": procedural_intent,
        "confidence": "rule_based",
    }


def missing_answer_sections(
    answer: str,
    question_type: str,
    procedural: bool | None = None,
    required_sections: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    """Return only observable omissions; never fill them with guessed law."""
    text = _fold(answer)
    if procedural is None:
        procedural = question_type in {"procedure", "form_request"}
    if not procedural:
        return []
    checks = {
        "conclusion": ("ket luan", "duoc", "khong duoc", "co the", "chua du"),
        "submission_place": ("noi nop", "ubnd", "co quan", "bo phan tiep nhan"),
        "documents": ("ho so", "giay to", "tai lieu"),
        "processing_time": ("thoi han", "ngay lam viec", "giai quyet"),
        "fee": ("le phi", "phi", "mien phi"),
        "steps": ("buoc", "thuc hien", "nop ho so"),
        "official_forms": ("bieu mau", "to khai", "mau don", "chua co"),
        "legal_basis_links": ("can cu", "dieu ", "luat ", "nghi dinh", "http", "nguon"),
    }
    requested = set(required_sections) if required_sections is not None else set(checks)
    return [
        name
        for name, needles in checks.items()
        if name in requested and not any(needle in text for needle in needles)
    ]
