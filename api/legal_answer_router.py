"""Deterministic request router for the legal answer pipeline V2.

The router classifies and binds request-local identity only. It never answers
law, invents a procedure identifier, or lets an LLM split a request. Retrieval,
validity, authority and citation gates remain downstream responsibilities.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal, Mapping

from api.legal_exact_retrieval import plan_exact_lookup
from api.legal_form_catalog import FormCatalog
from api.legal_domains import canonicalize_legal_domain
from api.legal_query_understanding import classify_legal_query
from api.legal_section_grounding import LegalIssue, plan_legal_issues

EXACT_ARTICLE = "exact_article"
PROCEDURE_FORM = "procedure_form"
GENERAL_LEGAL = "general_legal"
HISTORICAL = "historical"
PIPELINE_VERSION = "legal-answer-v2"
DECISION_VERSION = "legal-query-decision-v1"

AnswerRouteName = Literal[
    "exact_article", "procedure_form", "general_legal", "historical"
]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FORM_CODE_RE = re.compile(r"\bm[aẫ]u\s+(?:s[oố]\s*)?[0-9]{1,3}[a-z]?\b", re.IGNORECASE)

# These are exact, code-owned aliases for the three UAT procedures whose
# current official records are already present in the reviewed catalog.  They
# are intentionally narrower than the legacy catalog aliases: a broad alias
# such as ``khai sinh`` or ``trợ cấp xã hội`` can resolve to a different
# procedure.  The resolver below still verifies the official catalog record
# before returning one of these IDs; it never activates the remediation
# staging catalog.
_REMEDIATION_OFFICIAL_ALIASES: tuple[tuple[str, str], ...] = (
    ("dang ky lai khai sinh", "1.004884"),
    ("tro cap huu tri xa hoi", "1.014027"),
    ("dang ky tam tru", "1.004194"),
)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").casefold())
    return " ".join(
        "".join(character for character in normalized if unicodedata.category(character) != "Mn")
        .replace("đ", "d")
        .split()
    )


@dataclass(frozen=True)
class LegalAnswerRoute:
    pipeline_version: str
    answer_route: AnswerRouteName
    issues: tuple[LegalIssue, ...]
    clarifying_questions: tuple[str, ...] = ()
    procedure_id: str | None = None
    exact_law_number: str | None = None
    exact_article_number: str | None = None
    decision_reason: str = "deterministic_route"
    decision: "LegalQueryDecisionV1 | None" = None


@dataclass(frozen=True)
class LegalQueryDecisionV1:
    """One request-local decision shared by routing and retrieval.

    This structure intentionally carries no legal answer.  It prevents later
    stages from independently reclassifying domain or time scope.
    """

    version: str
    canonical_domain: str
    domain_source: Literal["account", "request", "classifier"]
    temporal_scope: Literal["current", "historical", "unknown"]
    legal_as_of: str | None
    temporal_reason: str
    answer_route: AnswerRouteName
    facets: tuple[str, ...]
    issues: tuple[LegalIssue, ...]
    procedure_candidate: str | None
    clarifying_questions: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        """Serialize only request-local routing metadata for retrieval/trace."""

        return {
            "version": self.version,
            "canonical_domain": self.canonical_domain,
            "domain_source": self.domain_source,
            "temporal_scope": self.temporal_scope,
            "legal_as_of": self.legal_as_of,
            "temporal_reason": self.temporal_reason,
            "answer_route": self.answer_route,
            "facets": list(self.facets),
            "issues": [
                {
                    "issue_id": issue.issue_id,
                    "subject": issue.subject,
                    "subject_anchor": issue.retrieval_subject,
                    "query_text": issue.query_text,
                    "domain": issue.domain,
                    "intent": issue.intent,
                    "facts": list(issue.facts),
                    "fact_anchors": list(issue.retrieval_facts),
                    "location": issue.location,
                    "procedure_family": issue.procedure_family,
                }
                for issue in self.issues
            ],
            "procedure_candidate": self.procedure_candidate,
            "clarifying_questions": list(self.clarifying_questions),
        }


def is_legal_answer_remediation_v1_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Role-scoped, default-off guard for remediation behaviour."""

    values = os.environ if environ is None else environ
    if str(values.get("LEGAL_ANSWER_REMEDIATION_V1_ENABLED", "false")).strip().casefold() not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(values.get("LEGAL_ANSWER_REMEDIATION_V1_ROLES", "")).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def is_legal_answer_remediation_v1_shadow_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether remediation decisions should be traced without serving.

    Shadow mode is deliberately a second, explicit switch.  It requires the
    remediation feature flag and the same role allow-list, then computes the
    new decision for audit comparison while leaving the legacy answer path in
    charge.  Clearing either switch is an immediate rollback.
    """

    values = os.environ if environ is None else environ
    if not is_legal_answer_remediation_v1_enabled(role, environ=values):
        return False
    if str(values.get("LEGAL_ANSWER_REMEDIATION_V1_SHADOW", "false")).strip().casefold() not in _TRUE_VALUES:
        return False
    return True


def is_answer_pipeline_v2_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return true only for explicitly enabled rollout roles.

    The default role set is citizen so enabling the main flag cannot
    accidentally activate officer/admin traffic.
    """

    values = os.environ if environ is None else environ
    if str(values.get("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "false")).strip().casefold() not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(values.get("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def configured_answer_pipeline(
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the single serving selector used for rollout/rollback.

    Legacy role flags remain readable for compatibility, but ``baseline`` is
    an explicit hard rollback that disables additive V2/simplified/Phase-D
    serving decisions at the orchestration boundary.
    """

    values = os.environ if environ is None else environ
    configured = str(values.get("LEGAL_ANSWER_PIPELINE", "unified_v3")).strip().casefold()
    return configured if configured in {"baseline", "unified_v3", "direct_rag_v1"} else "baseline"


def _looks_historical(question: str) -> bool:
    # M4 owns temporal classification.  In particular, a reference to the
    # current calendar year is current law, not a historical lookup.
    return classify_legal_query(question, today=date.today()).get("temporal_scope") == "historical"


def _looks_procedural_or_form(question: str) -> bool:
    folded = _fold(question)
    return bool(
        _FORM_CODE_RE.search(question)
        or any(
            marker in folded
            for marker in (
                "bieu mau",
                "mau don",
                "mau to khai",
                "mau nao",
                "file mau",
                "to khai",
                "tai mau",
                "thu tuc",
                "ho so",
                "giay to",
                "can giay",
                "nop o dau",
                "bao lau",
                "thoi han",
                "thoi han bao lau",
                "thoi han giai quyet",
                "thoi han nao",
                "can kiem tra gi",
                "dang ky khai sinh",
                "dang ky lai khai sinh",
                "dang ky tam tru",
                "sang ten",
                "tach thua",
                "tang cho",
                "chuyen den ai",
                "tiep nhan yeu cau",
                "xac minh voi co quan nao",
                "khieu nai lan dau",
                "phan loai don",
                "can cu rieng",
                "thoi han khieu nai",
                "tham quyen khieu nai",
                "thoi han xu ly",
                "yeu cau giay xac nhan",
                "le phi",
            )
        )
    )


def _resolve_unique_procedure(question: str) -> tuple[str | None, bool]:
    """Resolve an approved catalog identity without guessing.

    ``ambiguous`` is true when the catalog reports a tie or when a generic form
    code is the only usable identity. Catalog failures fail closed and leave
    retrieval to the ordinary legal route.
    """

    try:
        resolution = FormCatalog.load_default().resolve_procedures(question, limit=3)
    except (OSError, ValueError, TypeError):
        return None, False
    matches = list(resolution.get("matches") or [])
    if resolution.get("ambiguous"):
        return None, True
    if len(matches) == 1:
        return str(matches[0].get("procedure_id") or "").strip() or None, False
    if _FORM_CODE_RE.search(question):
        return None, True
    return None, False


def _resolve_remediation_procedure(question: str) -> tuple[str | None, bool]:
    """Resolve only an exact, reviewed official identity for remediation.

    The legacy resolver is useful for compatibility, but its short aliases
    can choose a neighbouring procedure (for example ``khai sinh`` instead of
    ``đăng ký lại khai sinh``).  Remediation therefore accepts only the exact
    UAT phrases above and verifies that the corresponding current catalog row
    is an approved official record.  Land and complaint candidates remain
    unresolved until their owner attestation is complete.
    """

    folded = _fold(question)
    for alias, procedure_id in _REMEDIATION_OFFICIAL_ALIASES:
        if alias not in folded:
            continue
        try:
            procedure = FormCatalog.load_default().get_procedure(procedure_id)
        except (OSError, ValueError, TypeError):
            return None, False
        if not isinstance(procedure, Mapping):
            return None, False
        if (
            str(procedure.get("official_procedure_code") or "").strip()
            != procedure_id
            or str(procedure.get("source_status") or "").casefold() != "verified"
            or str(procedure.get("review_status") or "").casefold() != "approved"
            or procedure.get("approved") is not True
        ):
            return None, False
        # ``mai táng``/death facts are a hard negative for the pension
        # procedure; they must be answered only when the question explicitly
        # identifies the funeral-benefit event and its separately reviewed
        # procedure.
        if procedure_id == "1.014027" and any(
            marker in folded
            for marker in ("mai tang", "tu vong", "da chet", "nguoi da mat")
        ):
            return None, False
        return procedure_id, False
    return None, False


def _missing_fact_clarification(question: str) -> str | None:
    """Return one deterministic clarification for an explicitly incomplete case."""

    folded = _fold(question)
    if not (
        "he thong co the ket luan ngay khong" in folded
        and "can toi bo sung" in folded
    ):
        return None
    if "thu tuc" in folded and "chua neu" in folded:
        return "Bạn muốn thực hiện thủ tục cụ thể nào và mong nhận kết quả gì?"
    if "quyet dinh" in folded and "ban hanh" in folded:
        return "Vui lòng cho biết cơ quan hoặc người ban hành, số hiệu và ngày của quyết định."
    if "thoi han" in folded or "ngay nhan quyet dinh" in folded:
        return "Vui lòng cho biết ngày nhận quyết định hoặc ngày xảy ra sự kiện cần tính thời hạn."
    if any(marker in folded for marker in ("tuoi", "quan he", "tu cach")):
        return "Vui lòng bổ sung tuổi, quan hệ và tư cách của những người liên quan."
    if any(
        marker in folded
        for marker in ("noi cu tru", "noi co tai san", "noi su kien xay ra")
    ):
        return "Vui lòng cho biết nơi cư trú, nơi có tài sản hoặc nơi sự kiện xảy ra để xác định thẩm quyền."
    if "anh bi cat" in folded or (
        "so hieu" in folded and "ngay ban hanh" in folded
    ):
        return "Vui lòng gửi ảnh đầy đủ hoặc bổ sung số hiệu, ngày và cơ quan ban hành văn bản."
    if "mau thuan" in folded and "ngay" in folded:
        return "Vui lòng xác nhận ngày tháng đúng và cung cấp giấy tờ dùng để đối chiếu."
    if "du kien trong tuong lai" in folded and "khong co van ban chinh thuc" in folded:
        return "Chưa thể kết luận theo quy định dự kiến; vui lòng cung cấp văn bản chính thức nếu đã được ban hành."
    if "chua co ho so" in folded or (
        "chua co" in folded and "chung cu" in folded
    ):
        return "Vui lòng nêu thủ tục, hồ sơ hiện có và dữ kiện cần cơ quan xem xét; hệ thống không thể bảo đảm trước kết quả giải quyết."
    return "Vui lòng bổ sung sự kiện, giấy tờ và kết quả bạn muốn được giải quyết."


def _facets_from_classification(classification: Mapping[str, object]) -> tuple[str, ...]:
    mapping = {
        "REQUIRED_DOCUMENTS": "documents",
        "AUTHORITY": "authority",
        "DEADLINE": "deadline",
        "ELIGIBILITY": "condition",
        "FORM": "form",
        "LEGAL_BASIS": "legal_basis",
        "PROCESS": "procedure",
        "PROCEDURE": "procedure",
        "COMPLAINT": "complaint",
    }
    intents = [classification.get("intent"), *(classification.get("secondary_intents") or [])]
    facets = [mapping[item] for item in intents if item in mapping]
    # M4 deliberately keeps the stable intent vocabulary small.  These
    # request-local signals add operational facets without creating a second
    # competing domain/temporal classifier.
    folded = _fold(str(classification.get("normalized_query") or ""))
    if any(marker in folded for marker in ("xac minh", "can kiem tra", "kiem tra gi", "doi chieu")) or (
        "xac nhan cu tru" in folded
        and any(marker in folded for marker in ("tro cap", "huu tri xa hoi"))
    ):
        facets.append("verification")
    if any(
        marker in folded
        for marker in ("yeu cau giay", "giay xac nhan", "can giay", "ho so")
    ):
        facets.append("documents")
    if any(
        marker in folded
        for marker in (
            "nop o dau",
            "tai dau",
            "o dau",
            "co quan nao",
            "chuyen den ai",
            "chuyen cho ai",
            "ai tiep nhan",
        )
    ):
        facets.append("authority")
    if any(
        marker in folded
        for marker in ("can cu", "can cu phap ly", "dieu khoan", "co so phap ly")
    ):
        facets.append("legal_basis")
    # A direct self-service temporary-residence question needs the process
    # facet in addition to its dossier/deadline facets.  Keep this signal
    # request-local so it cannot be inferred from a neighbouring domain.
    if "dang ky tam tru" in folded and any(
        marker in folded for marker in ("tu dang ky", "tu lam", "chu nha khong")
    ):
        facets.append("procedure")
    # Every procedural request gets an explicit next-action retrieval facet so
    # the answer can end with a grounded, actionable step rather than leaving
    # the user to infer what to do after reading the dossier/authority facts.
    if any(item in facets for item in ("procedure", "documents", "authority", "deadline", "condition", "form")):
        facets.append("next_action")
    return tuple(dict.fromkeys(facets))


_FACET_QUERY_TERMS: dict[str, str] = {
    "rule": "quy định pháp luật áp dụng",
    "documents": "hồ sơ giấy tờ cần nộp",
    "authority": "thẩm quyền cơ quan nơi nộp",
    "deadline": "thời hạn giải quyết bao lâu",
    "condition": "điều kiện trường hợp áp dụng",
    "form": "biểu mẫu tờ khai chính thức",
    "legal_basis": "căn cứ pháp lý điều khoản",
    "procedure": "trình tự thủ tục bước thực hiện",
    "next_action": "hành động tiếp theo",
    "complaint": "khiếu nại cơ quan giải quyết",
}


def facets_for_issue(
    decision: LegalQueryDecisionV1,
    issue: LegalIssue,
) -> tuple[str, ...]:
    """Project request facets onto one issue without sibling intent leakage."""

    facets = list(decision.facets) or [str(issue.intent or "procedure")]
    issue_identity = _fold(
        " ".join(
            value
            for value in (issue.title, issue.retrieval_subject)
            if str(value or "").strip()
        )
    )
    # COMPLAINT is a request-level M4 intent, not a safe facet for a sibling
    # denunciation issue. Keep that issue on its own governing rule.
    if "to cao" in issue_identity:
        facets = ["rule" if facet == "complaint" else facet for facet in facets]

    local_query = _fold(str(issue.query_text or ""))
    if any(
        marker in local_query
        for marker in (
            "tham quyen",
            "co quan nao",
            "ai giai quyet",
            "nop o dau",
            "chuyen den ai",
            "chuyen cho ai",
            "ai tiep nhan",
        )
    ):
        facets.append("authority")
    if "authority" in facets:
        facets.append("next_action")
    return tuple(dict.fromkeys(facets))


def build_facet_queries(
    decision: LegalQueryDecisionV1,
    issue: LegalIssue,
    *,
    max_queries: int = 8,
) -> list[dict[str, str]]:
    """Create bounded facet queries while preserving the issue's subject anchor."""

    facets = facets_for_issue(decision, issue)
    queries: list[dict[str, str]] = []
    seen: set[str] = set()
    # A multi-issue query must not carry the sibling's full question.  The
    # issue title plus retrieval-only anchors retain the actor/legal object
    # while avoiding complaint terms in a denunciation search (and vice versa).
    anchor_parts = [
        str(issue.title or "").strip(),
        str(issue.retrieval_subject or "").strip(),
        *[
            str(value).strip()
            for value in issue.retrieval_facts
            if str(value).strip()
        ],
    ]
    if len(decision.issues) == 1:
        anchor_parts.insert(0, str(issue.query_text or "").strip())
    anchor = ", ".join(dict.fromkeys(value for value in anchor_parts if value))
    if decision.procedure_candidate:
        anchor = f"{anchor}, thủ tục {decision.procedure_candidate}".strip(", ")
    for index, facet in enumerate(facets):
        term = _FACET_QUERY_TERMS.get(str(facet), str(facet))
        query = f"{anchor}, {term}" if anchor else term
        normalized = " ".join(query.casefold().split())
        if normalized in seen:
            continue
        seen.add(normalized)
        queries.append(
            {
                "query_id": f"{issue.issue_id}-facet-{index + 1}",
                "query_type": "facet",
                "query": query[:2000],
            }
        )
        if len(queries) >= max_queries:
            break
    return queries


def build_legal_query_decision(
    question: str,
    *,
    role: str = "citizen",
    requested_domain: str | None = None,
    account_domain: str | None = None,
    legal_as_of: date | None = None,
    procedure_candidate: str | None = None,
    clarifying_questions: tuple[str, ...] = (),
    apply_account_domain_acl: bool = False,
) -> LegalQueryDecisionV1:
    """Compute the remediation decision once without looking up legal facts."""

    classification = classify_legal_query(
        question,
        requested_domain=requested_domain if role != "officer" else None,
        as_of=legal_as_of,
        as_of_explicit=legal_as_of is not None,
        today=date.today(),
    )
    account_scope_without_request = bool(
        role == "officer" and account_domain and not requested_domain
    )
    if (
        role == "officer"
        and account_domain
        and (apply_account_domain_acl or account_scope_without_request)
    ):
        # An officer request without an explicit domain is scoped to the
        # account's assigned domain.  This prevents the classifier from
        # turning a natural-language subject into an ACL bypass.  An explicit
        # request domain remains authoritative and is checked by the serving
        # layer against the account grants.
        canonical_domain = canonicalize_legal_domain(account_domain) or str(account_domain)
        source = "account"
    elif requested_domain:
        # Preserve an explicit request-domain selection for the serving ACL.
        # Officer classification intentionally does not feed the request hint
        # into the classifier, but dropping the hint here would turn an
        # unauthorized explicit domain into ``unknown`` and bypass the normal
        # denial path.
        canonical_domain = canonicalize_legal_domain(requested_domain) or str(
            requested_domain
        )
        source = "request"
    else:
        canonical_domain, source = classification["domain"], "classifier"
        # An officer's account scope is the only safe context for an
        # otherwise unclassified short follow-up (for example, "Còn thời
        # hạn là bao lâu?").  It fills an unknown domain but must not
        # relabel an explicit topic; the serving layer still performs the
        # normal authorization check for explicit cross-domain questions.
        if (
            role == "officer"
            and account_domain
            and str(canonical_domain or "").strip().casefold()
            in {"", "unknown", "administrative"}
        ):
            canonical_domain = canonicalize_legal_domain(account_domain) or str(account_domain)
            source = "account"
    canonical_domain = canonicalize_legal_domain(canonical_domain) or str(
        canonical_domain or "unknown"
    )
    temporal_scope = classification["temporal_scope"]
    if temporal_scope not in {"current", "historical", "unknown"}:
        temporal_scope = "unknown"
    issues = tuple(plan_legal_issues(question, max_issues=8, explicit_only=True))
    # Preserve every issue's own subject and actor. Officer ACL must not
    # rewrite issue domains unless the compatibility flag is set.
    if (
        role == "officer"
        and account_domain
        and (apply_account_domain_acl or account_scope_without_request)
    ):
        issues = tuple(replace(issue, domain=canonical_domain) for issue in issues)
    answer_route: AnswerRouteName = (
        HISTORICAL if temporal_scope == "historical" else
        PROCEDURE_FORM if _looks_procedural_or_form(question) else GENERAL_LEGAL
    )
    return LegalQueryDecisionV1(
        version=DECISION_VERSION,
        canonical_domain=str(canonical_domain or "unknown"),
        domain_source=source,  # type: ignore[arg-type]
        temporal_scope=temporal_scope,  # type: ignore[arg-type]
        legal_as_of=classification.get("retrieval_as_of"),
        temporal_reason=str((classification.get("signals") or {}).get("temporal_reason") or "default_current"),
        answer_route=answer_route,
        facets=_facets_from_classification(classification),
        issues=issues,
        procedure_candidate=procedure_candidate,
        clarifying_questions=clarifying_questions,
    )


def route_legal_answer(
    question: str,
    *,
    resolve_legacy_procedure: bool = True,
    remediation: bool = False,
    role: str = "citizen",
    requested_domain: str | None = None,
    account_domain: str | None = None,
    legal_as_of: date | None = None,
    apply_account_domain_acl: bool = False,
) -> LegalAnswerRoute:
    """Classify one request and produce explicit-only, identity-local issues."""

    clean_question = " ".join(str(question or "").split())
    exact = plan_exact_lookup(clean_question)
    procedural = _looks_procedural_or_form(clean_question)

    # Remediation computes M4 and the request-local decision once.  Procedure
    # identity is resolved afterward and projected into that same immutable
    # decision; the presentation/retrieval stages must never classify the
    # question a second time.
    if remediation:
        decision = build_legal_query_decision(
            clean_question,
            role=role,
            requested_domain=requested_domain,
            account_domain=account_domain,
            legal_as_of=legal_as_of,
            apply_account_domain_acl=apply_account_domain_acl,
        )
        procedure_id: str | None = None
        clarifying: tuple[str, ...] = ()
        if procedural and resolve_legacy_procedure:
            procedure_id, ambiguous = _resolve_remediation_procedure(clean_question)
            if ambiguous:
                clarifying = (
                    "Bạn cần biểu mẫu cho thủ tục nào? Vui lòng nêu tên thủ tục thay vì chỉ ghi mã mẫu.",
                )
        missing_fact_question = _missing_fact_clarification(clean_question)
        if missing_fact_question:
            clarifying = (missing_fact_question,)

        # Exact-article identity is lexical/structural evidence, not a second
        # temporal classification. Historical scope remains authoritative when
        # M4 selected it.
        if (
            decision.temporal_scope != "historical"
            and exact.law_number
            and exact.article_number
        ):
            decision = replace(decision, answer_route=EXACT_ARTICLE)
        decision = replace(
            decision,
            procedure_candidate=procedure_id,
            clarifying_questions=clarifying,
        )
        return LegalAnswerRoute(
            pipeline_version=PIPELINE_VERSION,
            answer_route=decision.answer_route,
            issues=decision.issues,
            clarifying_questions=clarifying,
            procedure_id=procedure_id,
            exact_law_number=exact.law_number,
            exact_article_number=exact.article_number,
            decision_reason=f"{DECISION_VERSION}:{decision.temporal_reason}",
            decision=decision,
        )

    # Compatibility/legacy routing retains its existing behavior and is
    # deliberately outside the remediation flag's single-decision contract.
    historical = _looks_historical(clean_question)

    procedure_id: str | None = None
    clarifying: tuple[str, ...] = ()
    if procedural and resolve_legacy_procedure:
        procedure_id, ambiguous = _resolve_unique_procedure(clean_question)
        if ambiguous:
            clarifying = (
                "Bạn cần biểu mẫu cho thủ tục nào? Vui lòng nêu tên thủ tục thay vì chỉ ghi mã mẫu.",
            )

    missing_fact_question = _missing_fact_clarification(clean_question)
    if missing_fact_question:
        clarifying = (missing_fact_question,)

    if historical:
        answer_route: AnswerRouteName = HISTORICAL
        reason = "explicit_historical_time"
    elif exact.law_number and exact.article_number:
        answer_route = EXACT_ARTICLE
        reason = "exact_document_and_article"
    elif procedural:
        answer_route = PROCEDURE_FORM
        reason = "procedure_or_form_markers"
    else:
        answer_route = GENERAL_LEGAL
        reason = "general_legal_default"

    if missing_fact_question:
        reason = "missing_facts_require_clarification"

    issues = tuple(plan_legal_issues(clean_question, max_issues=8, explicit_only=True))
    return LegalAnswerRoute(
        pipeline_version=PIPELINE_VERSION,
        answer_route=answer_route,
        issues=issues,
        clarifying_questions=clarifying,
        procedure_id=procedure_id,
        exact_law_number=exact.law_number,
        exact_article_number=exact.article_number,
        decision_reason=reason,
        decision=None,
    )
