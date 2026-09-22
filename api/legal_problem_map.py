"""Bounded hybrid Problem Map for Vietnamese legal questions.

The model, when enabled, may only refine retrieval wording and issue boundaries.
Authenticated role and all legal identifiers remain code-controlled.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.legal_section_grounding import (
    LegalIssue,
    classify_issue_domain,
    plan_legal_issues,
)
from api.legal_structured_answer import ensure_required_facet_issues

Priority = Literal["critical", "high", "normal"]
QueryType = Literal["exact_legal", "semantic", "metadata", "article"]


class LegalIntent(BaseModel):
    """Deterministic, public-safe intent used as a hard evidence boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain: str = "unknown"
    procedure_family: str | None = None
    action: str = "lookup"
    target_object: str = "legal_information"
    requested_facets: list[str] = Field(default_factory=list)
    legal_identifiers: list[str] = Field(default_factory=list)
    jurisdiction: str = "central_or_haiphong"
    legal_as_of: str
    confidence: float = Field(ge=0, le=1)
    ambiguity_reasons: list[str] = Field(default_factory=list)
    state: Literal["resolved", "ambiguous_needs_clarification", "unsupported"]
    router_version: str = "legal-intent-v2"


_PROCEDURE_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("permanent_residence_deletion", ("xoa dang ky thuong tru", "xoa thuong tru")),
    ("permanent_residence_registration", ("dang ky thuong tru",)),
    ("temporary_residence_extension", ("gia han tam tru",)),
    ("temporary_residence_registration", ("dang ky tam tru",)),
    ("complaint_resolution", ("khieu nai",)),
    ("denunciation_resolution", ("to cao",)),
    (
        "sanction_decision_enforcement",
        ("thi hanh quyet dinh xu phat", "cuong che thi hanh"),
    ),
    ("administrative_sanction", ("xu phat vi pham hanh chinh", "xu phat")),
    ("marriage_registration", ("dang ky ket hon", "ket hon")),
    ("birth_registration", ("dang ky khai sinh", "khai sinh")),
    ("death_registration", ("dang ky khai tu", "khai tu")),
)


def _matched_procedure_families(question: str) -> list[str]:
    folded = _fold(question)
    matched: list[str] = []
    for family, markers in _PROCEDURE_FAMILY_RULES:
        if any(marker in folded for marker in markers):
            matched.append(family)
    # A specific family subsumes its generic lexical parent.
    if "sanction_decision_enforcement" in matched:
        matched = [item for item in matched if item != "administrative_sanction"]
    residence_alternative = bool(
        re.search(
            r"dang ky thuong tru.{0,40}\b(?:hay|hoac)\b.{0,40}xoa (?:dang ky )?thuong tru",
            folded,
        )
        or re.search(
            r"xoa (?:dang ky )?thuong tru.{0,40}\b(?:hay|hoac)\b.{0,40}dang ky thuong tru",
            folded,
        )
    )
    if "permanent_residence_deletion" in matched and not residence_alternative:
        matched = [
            item for item in matched if item != "permanent_residence_registration"
        ]
    return list(dict.fromkeys(matched))


def build_legal_intent(
    question: str,
    *,
    legal_as_of: date | str | None = None,
) -> LegalIntent:
    """Normalize common commune procedures without model inference."""

    issues = plan_legal_issues(question, max_issues=6)
    facets = list(dict.fromkeys(issue.intent for issue in issues if issue.intent != "unknown"))
    families = _matched_procedure_families(question)
    ambiguity: list[str] = []
    if len(families) > 1:
        ambiguity.append("multiple_procedure_families")
    domain = classify_issue_domain(question)
    state: Literal["resolved", "ambiguous_needs_clarification", "unsupported"]
    if ambiguity:
        state = "ambiguous_needs_clarification"
    elif not families and domain == "unknown":
        state = "unsupported"
    else:
        state = "resolved"
    family = families[0] if len(families) == 1 else None
    folded = _fold(question)
    action = (
        "delete"
        if family and family.endswith("_deletion")
        else "enforce"
        if family == "sanction_decision_enforcement"
        else "register"
        if family and "registration" in family
        else "resolve"
        if family in {"complaint_resolution", "denunciation_resolution"}
        else "lookup"
    )
    target = family or "legal_information"
    identifiers = list(
        dict.fromkeys(
            re.findall(
                r"\b\d{1,4}/\d{4}/[A-ZĐÂĂÊÔƠƯa-zđâăêôơư-]+\b",
                str(question or ""),
            )
        )
    )
    as_of = legal_as_of.isoformat() if isinstance(legal_as_of, date) else str(legal_as_of or date.today().isoformat())
    return LegalIntent(
        domain=domain,
        procedure_family=family,
        action=action,
        target_object=target,
        requested_facets=facets,
        legal_identifiers=identifiers,
        jurisdiction="haiphong" if "hai phong" in folded else "central_or_haiphong",
        legal_as_of=as_of,
        confidence=0.98 if state == "resolved" and family else 0.6 if state == "resolved" else 0.0,
        ambiguity_reasons=ambiguity,
        state=state,
    )


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("đ", "d").split())


class ConfirmedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact: str = Field(min_length=1, max_length=500)
    value: str | bool | int | None = None
    source: Literal["user"] = "user"


class MissingFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fact_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)
    why_needed: str = Field(min_length=1, max_length=500)
    priority: Priority = "normal"


class ConditionalBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    branch_id: str = Field(min_length=1, max_length=64)
    condition: str = Field(min_length=1, max_length=500)
    distinguish_from: str = Field(min_length=1, max_length=500)
    missing_fact_id: str | None = Field(default=None, max_length=64)


class LegalSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query_id: str = Field(min_length=1, max_length=96)
    query_type: QueryType = "semantic"
    query: str = Field(min_length=2, max_length=2000)
    scope: Literal["central", "haiphong", "local", "all"] = "central"
    expected_article: str | None = Field(default=None, max_length=80)


class ProblemIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    issue_id: str = Field(min_length=1, max_length=96)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=800)
    priority: Priority = "normal"
    required_answer: bool = True
    intent: str = Field(default="rule", max_length=40)
    domain: str = Field(default="unknown", max_length=80)
    subject: str = Field(default="", max_length=500)
    location: str = Field(default="", max_length=300)
    facts: list[str] = Field(default_factory=list, max_length=20)
    required_fact_ids: list[str] = Field(default_factory=list, max_length=8)
    relevance_topics: list[str] = Field(default_factory=list, max_length=8)
    queries: list[LegalSearchQuery] = Field(min_length=1, max_length=4)

    @property
    def query_text(self) -> str:
        return self.queries[0].query


class LegalProblemMap(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["citizen", "officer", "admin"] = "citizen"
    legal_domain: list[str] = Field(default_factory=list, max_length=8)
    procedure_type: str | None = Field(default=None, max_length=300)
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list, max_length=24)
    missing_facts: list[MissingFact] = Field(default_factory=list, max_length=16)
    conditional_branches: list[ConditionalBranch] = Field(default_factory=list, max_length=16)
    requested_outputs: list[str] = Field(default_factory=list, max_length=16)
    legal_issues: list[ProblemIssue] = Field(min_length=1, max_length=8)
    planner_mode: Literal[
        "deterministic", "hybrid", "deterministic_fallback"
    ] = "deterministic"
    fallback_reason: str | None = Field(default=None, max_length=120)


_LAND_MARKERS = (
    "mua dat", "thua dat", "so do", "quyen su dung dat", "giay chung nhan",
    "quy hoach", "chuyen nhuong dat",
)


# Reviewed source routes are code-owned metadata, never model output. They are
# intentionally limited to exact official identities already present in the
# release corpus. The semantic query remains as a second route, so these hints
# improve recall without becoming an answer or bypassing validity/hierarchy
# gates.
_REVIEWED_PROCEDURE_QUERY_ROUTES: dict[
    tuple[str, str], tuple[str, ...]
] = {
    (
        "cap giay phep xay dung nha o rieng le",
        "condition",
    ): (
        "Nghị định 217/2026/NĐ-CP Điều 50 điều kiện cấp giấy phép xây dựng mới nhà ở riêng lẻ",
    ),
    (
        "cap giay phep xay dung nha o rieng le",
        "documents",
    ): (
        "Nghị định 217/2026/NĐ-CP Điều 60 hồ sơ cấp giấy phép xây dựng nhà ở riêng lẻ",
    ),
    (
        "cap giay phep xay dung nha o rieng le",
        "authority",
    ): (
        "Nghị định 217/2026/NĐ-CP Điều 53 thẩm quyền cấp giấy phép xây dựng nhà ở riêng lẻ",
    ),
    (
        "cap giay phep xay dung nha o rieng le",
        "deadline",
    ): (
        "Nghị định 217/2026/NĐ-CP Điều 54 thời gian 07 ngày làm việc cấp giấy phép xây dựng nhà ở riêng lẻ",
    ),
    (
        "cap giay phep xay dung nha o rieng le",
        "form",
    ): (
        "Nghị định 217/2026/NĐ-CP Điều 60 Mẫu số 01 Phụ lục II đơn đề nghị cấp giấy phép xây dựng mới",
    ),
}


def _reviewed_route_queries(issue: LegalIssue) -> tuple[str, ...]:
    return _REVIEWED_PROCEDURE_QUERY_ROUTES.get(
        (_fold(issue.subject), str(issue.intent or "unknown")),
        (),
    )


def is_complex_legal_question(
    question: str, *, required_sections: Sequence[str] | None = None
) -> bool:
    folded = _fold(question)
    signals = sum(
        1
        for marker in (
            "giay viet tay", "nguoi ban da mat", "khong hop tac", "tranh chap",
            "quy hoach", "thu hoi", "ho so", "nop o dau", "thoi han",
            "nghia vu tai chinh", "le phi",
        )
        if marker in folded
    )
    return (
        signals >= 3
        or len(set(required_sections or ())) >= 3
        or len(re.findall(r"[;,\n]", question or "")) >= 2
    )


def _queries(issue_id: str, exact: str, semantic: str) -> list[LegalSearchQuery]:
    return [
        LegalSearchQuery(
            query_id=f"{issue_id}-q1",
            query_type="exact_legal",
            query=exact,
        ),
        LegalSearchQuery(
            query_id=f"{issue_id}-q2",
            query_type="semantic",
            query=semantic,
        ),
    ]


def _land_problem_map(
    question: str,
    *,
    role: Literal["citizen", "officer", "admin"],
    required_sections: Sequence[str],
) -> LegalProblemMap:
    folded = _fold(question)
    year_match = re.search(r"\b(?:19|20)\d{2}\b", question)
    year = year_match.group(0) if year_match else "mốc thời gian người dùng nêu"
    location = "Hải Phòng" if "hai phong" in folded else ""
    subject = "cá nhân nhận chuyển quyền sử dụng đất"
    facts = [
        ConfirmedFact(fact="Mua đất bằng giấy viết tay", value=True),
        ConfirmedFact(fact="Thời điểm giao dịch", value=year),
    ]
    if "chua co so do" in folded or "chua co giay chung nhan" in folded:
        facts.append(ConfirmedFact(fact="Chưa có Giấy chứng nhận", value=True))
    if "nguoi ban da mat" in folded:
        facts.append(ConfirmedFact(fact="Người bán đã mất", value=True))
    if "khong hop tac" in folded:
        facts.append(ConfirmedFact(fact="Người thừa kế không hợp tác", value=True))
    if "quy hoach" in folded:
        facts.append(ConfirmedFact(fact="Thửa đất có phần nằm trong quy hoạch", value=True))

    definitions = [
        (
            "Giá trị giao dịch giấy viết tay",
            f"Xác định cơ chế xử lý việc nhận chuyển quyền bằng giấy viết tay năm {year}",
            "critical",
            "rule",
            "nhận chuyển quyền sử dụng đất trước ngày 01 tháng 7 năm 2014 "
            "chưa thực hiện thủ tục chuyển quyền",
            f"mua đất giấy viết tay năm {year} chưa sang tên",
            ["handwritten_paper"],
        ),
        (
            "Điều kiện cấp Giấy chứng nhận lần đầu",
            "Xác định các điều kiện cần đáp ứng để được xem xét cấp lần đầu",
            "critical",
            "condition",
            "điều kiện cấp Giấy chứng nhận quyền sử dụng đất lần đầu cho cá nhân",
            "đất chưa có sổ đỏ điều kiện cấp Giấy chứng nhận lần đầu",
            ["first_registration_condition"],
        ),
        (
            "Xử lý người chuyển quyền đã chết",
            "Xác định thủ tục và chứng cứ khi người chuyển quyền đã chết",
            "critical",
            "procedure",
            "người chuyển quyền sử dụng đất đã chết thủ tục đăng ký cấp Giấy chứng nhận",
            "người bán đất đã mất người thừa kế không ký lại giấy tờ",
            ["deceased_transferor"],
        ),
        (
            "Không hợp tác và tình trạng tranh chấp",
            "Phân biệt việc không ký lại giấy tờ với tranh chấp đất đai chính thức",
            "critical",
            "dispute",
            "tranh chấp đất đai điều kiện cấp Giấy chứng nhận lần đầu",
            "người thừa kế không hợp tác có phải tranh chấp đất đai",
            ["noncooperation"],
        ),
        (
            "Quy hoạch và quyết định thu hồi",
            "Phân biệt đất trong quy hoạch với đất đã có quyết định thu hồi",
            "critical",
            "condition",
            "đất nằm trong quy hoạch điều kiện cấp Giấy chứng nhận quyết định thu hồi",
            "một phần thửa đất trong quy hoạch chưa có quyết định thu hồi",
            ["planning"],
        ),
        (
            "Chứng cứ, hồ sơ và nơi nộp",
            "Xác định chứng cứ cần chuẩn bị, thành phần hồ sơ và cơ quan tiếp nhận",
            "high",
            "documents",
            "hồ sơ chứng cứ nơi nộp cấp Giấy chứng nhận quyền sử dụng đất lần đầu",
            "giấy tờ chứng minh mua đất giấy tay sử dụng ổn định nơi nộp hồ sơ",
            ["official_form", "documents"],
        ),
        (
            "Thời hạn giải quyết",
            "Xác định thời hạn của thủ tục cấp Giấy chứng nhận lần đầu",
            "high",
            "deadline",
            "thời hạn giải quyết cấp Giấy chứng nhận quyền sử dụng đất lần đầu",
            "thủ tục cấp sổ đỏ lần đầu bao nhiêu ngày",
            ["deadline"],
        ),
        (
            "Nghĩa vụ tài chính",
            "Xác định các khoản tài chính và căn cứ tính áp dụng cho trường hợp cụ thể",
            "high",
            "fee",
            "nghĩa vụ tài chính cấp Giấy chứng nhận quyền sử dụng đất lần đầu",
            "mua đất giấy tay cấp sổ đỏ tiền sử dụng đất lệ phí chi phí",
            ["fee"],
        ),
    ]
    required_facts_by_index = {
        1: ["fact-signatures"],
        4: ["fact-dispute"],
        5: ["fact-recovery"],
    }
    issues = [
        ProblemIssue(
            issue_id=f"issue-{index}",
            title=title,
            description=description,
            priority=priority,
            intent=intent,
            domain="land",
            subject=subject,
            location=location,
            facts=[item.fact for item in facts],
            required_fact_ids=required_facts_by_index.get(index, []),
            relevance_topics=topics,
            queries=_queries(f"issue-{index}", exact, semantic),
        )
        for index, (title, description, priority, intent, exact, semantic, topics) in enumerate(
            definitions, start=1
        )
    ]
    return LegalProblemMap(
        role=role,
        legal_domain=["đất đai"],
        procedure_type="cấp Giấy chứng nhận lần đầu",
        confirmed_facts=facts,
        missing_facts=[
            MissingFact(
                fact_id="fact-signatures",
                question="Giấy mua bán có chữ ký của bên bán và bên mua không?",
                why_needed="Không được đồng nhất giấy viết tay với giấy có đủ chữ ký.",
                priority="critical",
            ),
            MissingFact(
                fact_id="fact-dispute",
                question="Người thừa kế đã nộp đơn tranh chấp hay mới chỉ từ chối ký lại giấy tờ?",
                why_needed="Không hợp tác không mặc nhiên là tranh chấp chính thức.",
                priority="critical",
            ),
            MissingFact(
                fact_id="fact-recovery",
                question="Thửa đất đã có thông báo hoặc quyết định thu hồi đất chưa?",
                why_needed="Có quy hoạch không mặc nhiên đồng nghĩa đã có quyết định thu hồi.",
                priority="critical",
            ),
        ],
        conditional_branches=[
            ConditionalBranch(
                branch_id="branch-dispute",
                condition="Người thừa kế chỉ không hợp tác hoặc từ chối ký lại giấy tờ",
                distinguish_from="Người thừa kế đã phản đối quyền sử dụng đất hoặc có tranh chấp được cơ quan có thẩm quyền tiếp nhận",
                missing_fact_id="fact-dispute",
            ),
            ConditionalBranch(
                branch_id="branch-planning",
                condition="Thửa đất nằm trong quy hoạch nhưng chưa xác định có quyết định thu hồi",
                distinguish_from="Thửa đất đã có thông báo hoặc quyết định thu hồi",
                missing_fact_id="fact-recovery",
            ),
        ],
        requested_outputs=list(dict.fromkeys(str(item) for item in required_sections)),
        legal_issues=issues,
    )


def build_deterministic_problem_map(
    question: str,
    *,
    role: str,
    required_sections: Sequence[str] | None = None,
    location: str | None = None,
    seed_issues: Sequence[LegalIssue] | None = None,
    preserve_seed_issues: bool = False,
) -> LegalProblemMap:
    safe_role: Literal["citizen", "officer", "admin"] = (
        role if role in {"citizen", "officer", "admin"} else "citizen"
    )  # type: ignore[assignment]
    sections = [str(item) for item in required_sections or ()]
    folded = _fold(question)
    if (
        not (preserve_seed_issues and seed_issues)
        and
        any(marker in folded for marker in _LAND_MARKERS)
        and is_complex_legal_question(question, required_sections=sections)
        and any(marker in folded for marker in ("giay viet tay", "nguoi ban da mat", "quy hoach"))
    ):
        return _land_problem_map(question, role=safe_role, required_sections=sections)

    planned = list(seed_issues or ())
    if not planned:
        planned = ensure_required_facet_issues(
            question=question,
            issues=plan_legal_issues(
                question,
                max_issues=8,
                context={"location": location or ""},
            ),
            required_sections=sections,
            max_issues=8,
        )
    issues: list[ProblemIssue] = []
    total_queries = 0
    for index, item in enumerate(planned[:8], start=1):
        remaining_issues = len(planned[:8]) - index
        allowed = min(4, 16 - total_queries - remaining_issues)
        reviewed_routes = list(_reviewed_route_queries(item))
        semantic_query = " ".join(
            part
            for part in (
                item.retrieval_subject,
                item.title,
                *item.retrieval_facts,
                item.text,
            )
            if str(part or "").strip()
        ).strip()
        if not semantic_query or _fold(semantic_query) == _fold(item.query_text):
            semantic_query = f"{item.query_text}; {item.title}"
        query_values = list(
            dict.fromkeys(
                [*reviewed_routes, item.query_text, semantic_query]
            )
        )[: max(1, allowed)]
        queries = [
            LegalSearchQuery(
                query_id=f"issue-{index}-q{query_index}",
                query_type=(
                    "exact_legal"
                    if query_index <= max(1, len(reviewed_routes))
                    else "semantic"
                ),
                query=value,
            )
            for query_index, value in enumerate(query_values, start=1)
        ]
        total_queries += len(queries)
        issues.append(
            ProblemIssue(
                issue_id=f"issue-{index}",
                title=item.title,
                description=item.text or item.query_text,
                priority=(
                    "critical"
                    if item.intent in {"rule", "condition", "dispute", "recording"}
                    else "high"
                ),
                intent=item.intent,
                domain=item.domain,
                subject=item.subject,
                location=item.location,
                facts=list(item.facts),
                queries=queries,
            )
        )
    return LegalProblemMap(
        role=safe_role,
        legal_domain=sorted(
            {item.domain for item in issues if item.domain not in {"", "unknown"}}
        ),
        procedure_type=next(
            (item.subject for item in planned if str(item.subject or "").strip()),
            None,
        ),
        confirmed_facts=[
            ConfirmedFact(fact=fact, value=True)
            for fact in dict.fromkeys(
                fact
                for item in planned
                for fact in item.facts
                if str(fact or "").strip()
            )
        ],
        requested_outputs=sections,
        legal_issues=issues,
    )


_LAW_NUMBER_RE = re.compile(r"\b\d{1,4}/\d{4}/[A-ZÀ-ỸĐ0-9.-]+(?:-[A-ZÀ-ỸĐ0-9.-]+)*\b", re.IGNORECASE)
_ARTICLE_RE = re.compile(r"\bĐiều\s+\d+[a-z]?\b", re.IGNORECASE)


def _contains_untrusted_legal_identifier(raw: str, question: str) -> bool:
    trusted = _fold(question)
    identifiers = _LAW_NUMBER_RE.findall(raw) + _ARTICLE_RE.findall(raw)
    return any(_fold(identifier) not in trusted for identifier in identifiers)


def _planner_prompt(question: str, base: LegalProblemMap) -> str:
    return (
        "Bạn chỉ phân rã câu hỏi, không trả lời pháp luật và không thêm số hiệu "
        "văn bản hoặc Điều/Khoản. Trả JSON object có legal_issues, mỗi issue có "
        "issue_id,title,description,priority,required_answer,intent,domain,subject,"
        "location,facts,queries. Tối đa 8 issue, 4 query/issue.\n"
        f"Câu hỏi: {question}\n"
        "Bản đồ xác định làm mốc, không được bỏ yêu cầu rõ ràng:\n"
        + json.dumps(base.model_dump(), ensure_ascii=False, separators=(",", ":"))
    )


def _merge_model_map(
    raw: str, base: LegalProblemMap, question: str
) -> LegalProblemMap:
    if _contains_untrusted_legal_identifier(raw, question):
        raise ValueError("planner_untrusted_legal_identifier")
    try:
        payload = json.loads(raw)
        model_issues = [ProblemIssue.model_validate(item) for item in payload["legal_issues"]]
    except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("planner_invalid_json") from exc
    if not model_issues:
        raise ValueError("planner_invalid_json")

    selected: list[ProblemIssue] = []
    query_budget = 16
    for index, issue in enumerate(model_issues[:8], start=1):
        allowed = min(4, query_budget - max(0, len(model_issues[:8]) - index))
        if allowed <= 0:
            break
        queries = issue.queries[:allowed]
        query_budget -= len(queries)
        base_issue = base.legal_issues[index - 1]
        selected.append(
            issue.model_copy(
                update={
                    "issue_id": f"issue-{index}",
                    # Missing facts and legal branches are deterministic user
                    # facts. A planner model may refine wording, but it cannot
                    # remove or invent those bindings.
                    "required_fact_ids": list(base_issue.required_fact_ids),
                    "relevance_topics": list(base_issue.relevance_topics),
                    "queries": [
                        query.model_copy(update={"query_id": f"issue-{index}-q{q_index}"})
                        for q_index, query in enumerate(queries, start=1)
                    ],
                }
            )
        )
    # A model may refine wording but cannot silently remove an explicit
    # deterministic issue. Prefer the deterministic map whenever coverage drops.
    if len(selected) < len(base.legal_issues):
        raise ValueError("planner_dropped_required_issue")
    return base.model_copy(
        update={"legal_issues": selected, "planner_mode": "hybrid", "fallback_reason": None}
    )


async def build_hybrid_problem_map(
    question: str,
    *,
    role: str,
    required_sections: Sequence[str] | None = None,
    location: str | None = None,
    invoke_model: Callable[[str], Awaitable[str]] | None = None,
    timeout_seconds: float = 4.0,
    seed_issues: Sequence[LegalIssue] | None = None,
    preserve_seed_issues: bool = False,
) -> LegalProblemMap:
    base = build_deterministic_problem_map(
        question,
        role=role,
        required_sections=required_sections,
        location=location,
        seed_issues=seed_issues,
        preserve_seed_issues=preserve_seed_issues,
    )
    if invoke_model is None or not is_complex_legal_question(
        question, required_sections=required_sections
    ):
        return base
    try:
        raw = await asyncio.wait_for(
            invoke_model(_planner_prompt(question, base)),
            timeout=max(0.001, float(timeout_seconds)),
        )
        return _merge_model_map(str(raw or ""), base, question)
    except asyncio.TimeoutError:
        return base.model_copy(
            update={
                "planner_mode": "deterministic_fallback",
                "fallback_reason": "planner_timeout",
            }
        )
    except ValueError as exc:
        return base.model_copy(
            update={
                "planner_mode": "deterministic_fallback",
                "fallback_reason": str(exc),
            }
        )
    except Exception:
        return base.model_copy(
            update={
                "planner_mode": "deterministic_fallback",
                "fallback_reason": "planner_unavailable",
            }
        )


def problem_map_to_legal_issues(
    problem_map: LegalProblemMap, *, request_id: str
) -> list[LegalIssue]:
    combined_question = " ".join(
        issue.query_text for issue in problem_map.legal_issues
    )
    normalized_intent = build_legal_intent(combined_question)
    return [
        LegalIssue(
            request_id=request_id,
            issue_id=issue.issue_id,
            title=issue.title,
            query_text=issue.query_text,
            text=issue.description or issue.query_text,
            intent=issue.intent,  # type: ignore[arg-type]
            domain=issue.domain or classify_issue_domain(issue.query_text),
            split_confidence="high",
            priority=issue.priority,
            subject=issue.subject,
            location=issue.location,
            facts=tuple(issue.facts),
            relevance_topics=tuple(issue.relevance_topics),
            procedure_family=normalized_intent.procedure_family,
        )
        for issue in problem_map.legal_issues
    ]
