"""Deterministic conversation orchestration for the owner-scoped Ask chat.

This module deliberately has no database, retrieval, or model dependencies.
It decides how a turn should be handled, builds a token-bounded conversation
packet, and projects backend-owned document references.  Legal evidence remains
the only legal source of truth; prior assistant prose is conversational context
only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Sequence

from api.legal_domains import CANONICAL_DOMAIN_ALIASES, canonicalize_legal_domain
from open_notebook.utils.token_utils import token_count

TURN_DECISION_VERSION = "conversation-turn-decision-v1"
TURN_DECISION_V2_VERSION = "conversation-intent-decision-v2"
CONTEXT_PACKET_VERSION = "conversation-context-packet-v1"
ORCHESTRATOR_PIPELINE_VERSION = "conversational-orchestrator-v1"
ORCHESTRATOR_V2_PIPELINE_VERSION = "conversational-orchestrator-v2"

ConversationRoute = Literal[
    "chat_meta",
    "document_followup",
    "legal_query",
    "out_of_scope",
]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_ROLES = {"citizen", "officer"}
_DEFAULT_INPUT_BUDGET = 16_000
_DEFAULT_EVIDENCE_RESERVE = 6_000
_DEFAULT_OUTPUT_RESERVE = 4_096
_RECENT_MESSAGE_LIMIT = 12  # six complete user/assistant turns
_RELEVANT_OLDER_LIMIT = 4
_ROUTER_ALLOWED_FACETS = {
    "rule",
    "condition",
    "authority",
    "documents",
    "procedure",
    "verification",
    "recording",
    "deadline",
    "fee",
    "dispute",
    "form",
    "next_action",
    "unknown",
}
_ROUTER_ALLOWED_DOMAINS = set(CANONICAL_DOMAIN_ALIASES) | {"unknown"}

# The small router is deliberately a bounded query-planning component. It
# must not be asked to produce a full legal problem map in a single call.
ROUTER_MAX_ISSUES = 3
ROUTER_MAX_FACETS_PER_ISSUE = 5
ROUTER_MAX_ANCHORS_PER_ISSUE = 5

_META_PATTERNS = (
    r"^(?:xin\s+)?ch[aà]o(?:\s+(?:b[aạ]n|anh|chị|bot|legal\s+bot))?[.!?]*$",
    r"^(?:c[aả]m\s+[ơo]n|thanks|thank\s+you)(?:\s+\S+){0,4}[.!?]*$",
    r"^(?:b[aạ]n\s+)?l[aà]\s+(?:ai|chatbot|trợ\s+l[ýy])(?:\s+à)?[.!?]*$",
    r"^(?:b[aạ]n\s+)?c[oó]\s+ph[aả]i\s+(?:l[aà]\s+)?(?:chatbot|luật\s+sư)(?:\s+kh[oô]ng|\s+à)?[.!?]*$",
    r"^(?:t[oô]i|m[iì]nh)\s+vừa\s+hỏi\s+(?:b[aạ]n\s+)?(?:c[aâ]u\s+)?g[iì][.!?]*$",
    r"^(?:c[aâ]u\s+)?(?:hỏi|y[eê]u\s+cầu)\s+(?:trước|vừa\s+rồi)\s+(?:l[aà]\s+)?g[iì][.!?]*$",
    r"^(?:b[aạ]n\s+)?c[oó]\s+thể\s+l[aà]m\s+g[iì][.!?]*$",
    r"^(?:hello|hi|hey|alo|help)(?:\s+(?:bạn|ban|bot))?[.!?]*$",
    r"^chào\s+(?:bot|buổi\s+sáng|legal\s+bot)[.!?]*$",
    r"^(?:good\s+morning|hướng\s+dẫn\s+sử\s+dụng|trợ\s+giúp)[.!?]*$",
)

_DOCUMENT_MARKERS = (
    "văn bản trên",
    "văn bản này",
    "văn bản đó",
    "văn bản vừa nêu",
    "văn bản vừa dẫn",
    "nguồn trên",
    "nguồn này",
    "nguồn đó",
    "nguồn vừa dùng",
    "nguồn gần nhất",
    "điều trên",
    "điều vừa nêu",
    "điều luật trên",
    "điều luật vừa dẫn",
    "điều luật vừa nêu",
    "khoản trên",
    "quy định vừa nêu",
    "tài liệu trên",
    "tài liệu này",
)

_DOCUMENT_DEICTIC_RE = re.compile(
    r"\b(?:van ban|nguon|dieu(?: luat)?|khoan|quy dinh|tai lieu)"
    r"(?:\s+[a-z0-9]+){0,4}\s+"
    r"(?:tren|nay|do|gan nhat|vua\s+(?:duoc\s+)?"
    r"(?:neu|dan|dung|nhac\s+den|de\s+cap))\b"
)

# Folded identifiers such as 31/2024/qh15 or 123/2015/nd-cp. A numbered
# instrument is a complete legal lookup even without a request verb.
_VIETNAMESE_LAW_NUMBER_RE = re.compile(
    r"(?:"
    r"\b\d{1,4}/\d{4}/(?:qh\d+|nd-cp|tt-[a-z0-9-]+|qd-[a-z0-9-]+)\b"
    r"|"
    r"\b(?:qh\d+|nd(?:-cp)?|tt(?:-[a-z0-9]+)?|qd(?:-[a-z0-9]+)?)\s+\d{1,4}/\d{4}\b"
    r"|"
    r"\b\d{1,4}/\d{4}\s+(?:qh\d+|nd(?:-cp)?)\b"
    r")"
)


def _is_history_recall_request(question: str) -> bool:
    """Recognize requests about what happened in this conversation.

    Topic words such as ``đất đai`` or ``an sinh`` must not turn a recall
    request into a new legal search.  The test is based on recall/history
    language, not on a list of legal cases.
    """

    folded = _fold(_compact(question))
    history_reference = any(
        marker in folded
        for marker in (
            "cau ngay truoc",
            "cau hoi truoc",
            "yeu cau truoc",
            "luot truoc",
            "vua hoi",
            "vua roi",
            "dang hoi",
            "dang noi den",
            "da hoi",
            "da neu",
            "doan vua roi",
            "gan nhat",
            "toan bo phien",
            "trong phien",
        )
    )
    recall_action = any(
        marker in folded
        for marker in (
            "khong tra cuu",
            "chi nhac lai",
            "nhac lai",
            "tom tat",
            "liet ke",
            "toi da hoi nhung",
            "toi da neu nhung",
            "goi y cau hoi tiep theo",
        )
    )
    explicit_no_search = any(
        marker in folded
        for marker in (
            "khong tra cuu",
            "chi nhac lai",
            "chi tom tat",
        )
    )
    explicit_session_summary = bool(
        folded.startswith(("tom tat ", "liet ke ", "de xuat ", "goi y "))
        and any(
            marker in folded
            for marker in (
                "viec can kiem tra",
                "noi dung da hoi",
                "chu de da hoi",
                "linh vuc da hoi",
                "cau hoi tiep theo",
            )
        )
        and re.search(
            r"\b(?:dieu|khoan|diem|luat|nghi dinh|thong tu|quyet dinh)\s+\d+",
            folded,
        )
        is None
    )
    return explicit_no_search or explicit_session_summary or history_reference and (
        recall_action
        or folded.rstrip(".!? ").endswith("gi")
        or re.search(
            r"\b(?:nhom|chu de|van de|vuong mac|viec|noi dung|linh vuc)\b.*\bnao\b",
            folded,
        )
        is not None
    )


def _is_operational_legal_followup(question: str) -> bool:
    """Return True for a facet follow-up, rather than a source follow-up."""

    folded = _fold(_compact(question))
    if any(
        marker in folded
        for marker in (
            "van ban",
            "nguon",
            "dieu luat",
            "quy dinh vua neu",
            "tai lieu",
        )
    ):
        return False
    return bool(
        re.search(
            r"\b(?:thoi han|ho so|giay to|bieu mau|mau nao|nop o dau|"
            r"co quan|tham quyen|le phi|dieu kien|thu tuc|buoc tiep theo|"
            r"muc huong|duoc huong|duoc cap the|bao hiem y te|bhyt|chi tra|tro cap)\b",
            folded,
        )
        and re.search(
            r"\b(?:do|nay|tren|vua roi|hai|cac|nhung)\b",
            folded,
        )
    )


def _explicit_conversation_route(question: str) -> ConversationRoute | None:
    """Return only explicit conversational intent that must beat an LLM guess.

    This is deliberately generic: it recognizes a user's direct instruction to
    recall the conversation without retrieval and deictic references to a
    previously cited document. It does not classify legal domains or facts.
    """

    folded = _fold(_compact(question))
    if not folded:
        return None
    if _is_history_recall_request(question):
        return "chat_meta"
    if any(
        re.fullmatch(pattern, _compact(question), flags=re.IGNORECASE)
        for pattern in _META_PATTERNS
    ):
        return "chat_meta"
    # Numbered instruments are complete legal lookups. They must beat the
    # gratitude heuristic, out-of-scope markers, and deictic document follow-up.
    if _is_standalone_legal_identifier_query(question):
        return "legal_query"
    folded_clean = folded.rstrip(".!? ")
    if (
        "cam on" in folded_clean
        and len(folded_clean) <= 48
        and not _topic_domains(question)
        and not _is_standalone_legal_identifier_query(question)
        and re.search(
            r"\b(?:dieu|khoan|luat|nghi dinh|thong tu|thu tuc|ho so)\b",
            folded_clean,
        )
        is None
    ):
        return "chat_meta"
    if (
        len(folded_clean) <= 80
        and not _topic_domains(question)
        and not _is_standalone_legal_identifier_query(question)
        and (
            re.search(
                r"\b(?:ban khoe|ban hieu tieng|ban ho tro nhung linh vuc|"
                r"cach dung chatbot|huong dan su dung|lam sao de hoi|"
                r"gioi thieu ve ban|chinh sach bao mat|"
                r"phien ban he thong|xoa lich su|doi giao dien)\b",
                folded_clean,
            )
            is not None
            or folded_clean in {"help", "tro giup", "huong dan su dung"}
        )
    ):
        return "chat_meta"
    if (
        any(_fold(marker) in folded for marker in _OUT_OF_SCOPE_MARKERS)
        or re.search(r"\b(?:viet|lam|sang tac)\b.*\bbai tho\b", folded)
        or re.search(
            r"\b(?:hom nay|ngay mai|du bao)\b.*\b(?:mua|thoi tiet|nhiet do)\b",
            folded,
        )
        or re.search(r"\bke(?:\s+lai)?\s+chuyen\s+(?:cuoi|vui)\b", folded)
        or re.search(r"\bchuyen cuoi\b", folded)
    ):
        return "out_of_scope"
    if _is_operational_legal_followup(question):
        return "legal_query"
    if any(_fold(marker) in folded for marker in _DOCUMENT_MARKERS):
        return "document_followup"
    if _DOCUMENT_DEICTIC_RE.search(folded) is not None:
        return "document_followup"
    return None


def _has_vietnamese_law_number(question: str) -> bool:
    """True when the turn names a QH / NĐ-CP / TT / QĐ instrument number."""

    folded = _fold(_compact(question))
    return bool(folded and _VIETNAMESE_LAW_NUMBER_RE.search(folded))


def _is_standalone_legal_identifier_query(question: str) -> bool:
    """Recognize a complete request anchored by a legal/form identifier.

    Codes such as CT01 identify the object being asked about and must not be
    interpreted as an ambiguous continuation merely because the previous
    turn discussed another legal domain.

    A numbered instrument is itself a complete lookup. Holdout phrasing such
    as ``quy định những gì`` folds to ``quy dinh nhung gi`` and would miss
    ``quy dinh gi``; ``Giải thích 123/2015/NĐ-CP`` has no request verb at all.
    """

    folded = _fold(_compact(question))
    if not folded:
        return False
    has_law_number = bool(_VIETNAMESE_LAW_NUMBER_RE.search(folded))
    # A numbered instrument is already a complete retrieval identity.
    # Never let conversation history, deictic follow-up, or an LLM router
    # turn that independent lookup into document_followup / chat_meta.
    if has_law_number:
        return True
    has_article = bool(re.search(r"\b(?:dieu|khoan)\s+\d+[a-z]?\b", folded))
    has_identifier = bool(
        re.search(r"\bct\s*\d{1,3}\b", folded) or has_article
    )
    has_request = bool(
        re.search(
            r"\b(?:dung de|giai thich|noi dung|tom tat|pham vi|dieu khoan|"
            r"la gi|nhung gi|quy dinh(?:\s+nhung)?\s+gi|"
            r"thu tuc|ho so|giay to|nop o dau|"
            r"co quan|tham quyen|thoi han|le phi|bieu mau|mau|dieu kien)\b",
            folded,
        )
    )
    return has_identifier and has_request


def _is_short_active_document_followup(
    question: str,
    active_document: Mapping[str, Any] | None,
) -> bool:
    """Short facet turns inherit the pinned document instead of a new lookup."""

    if not isinstance(active_document, Mapping):
        return False
    has_pin = any(
        str(active_document.get(key) or "").strip()
        for key in ("document_id", "id", "law_number", "title")
    )
    if not has_pin:
        return False
    if _is_standalone_legal_identifier_query(question):
        return False
    # Facet words (công chứng, lệ phí, giấy tờ) must not block follow-up
    # when a document is already pinned. Numbered instruments still win
    # via the standalone check above.
    current = _compact(question)
    return bool(current) and len(current) <= 80


def is_explicit_history_recall_request(question: str) -> bool:
    """Return whether the user explicitly asks to recall chat without retrieval."""

    return _is_history_recall_request(question)


@dataclass(frozen=True)
class DeterministicFollowupRewriteV1:
    original_query: str
    standalone_query: str
    rewrite_applied: bool
    reason_code: str
    inherited_turn_ids: tuple[str, ...]
    checksum: str


def rewrite_legal_followup_deterministic(
    question: str,
    history_messages: Sequence[Mapping[str, Any]] | None,
) -> DeterministicFollowupRewriteV1:
    """Add the preceding user question only for genuinely anaphoric follow-ups.

    A complete question is returned byte-for-byte (after whitespace compaction).
    The rewrite never predicts a domain, actor or procedure and never reads
    assistant prose.  Its sole job is to resolve phrases such as ``còn hồ sơ?``
    before full-corpus retrieval.
    """

    current = _compact(question)
    folded = _fold(current).rstrip(".!? ")
    explicit_topics = _topic_domains(current)
    legal_identifiers = re.search(
        r"\b(?:dieu|khoan|diem|luat|nghi dinh|thong tu|quyet dinh)\b",
        folded,
    )
    followup_signal = bool(
        re.search(
            r"^(?:con|the con|vay|the|truong hop (?:do|nay)|cai (?:do|nay)|"
            r"viec (?:do|nay)|no|ho so|hs|thoi han|tg|le phi|phi|bieu mau|"
            r"mau nao|nop o dau|ai giai quyet|co quan nao)\b",
            folded,
        )
        or re.search(
            r"\b(?:do|nay|tren|vua neu|vua roi|nhu vay|truong hop ay)\b",
            folded,
        )
    )
    # Explicit subjects/documents make the current question self-contained.
    if not current or explicit_topics or legal_identifiers or not followup_signal:
        payload = {
            "original": current,
            "standalone": current,
            "reason": "already_standalone",
            "turn_ids": [],
        }
        return DeterministicFollowupRewriteV1(
            original_query=current,
            standalone_query=current,
            rewrite_applied=False,
            reason_code="already_standalone",
            inherited_turn_ids=(),
            checksum=_checksum(payload),
        )

    previous: Mapping[str, Any] | None = None
    for message in reversed(tuple(history_messages or ())):
        if _message_role(message) != "user":
            continue
        content = _compact(message.get("content"))
        if not content or content == current:
            continue
        if _explicit_conversation_route(content) == "chat_meta":
            continue
        previous = message
        break
    previous_text = _compact((previous or {}).get("content"))
    # A vague follow-up after a multi-domain turn is genuinely ambiguous; do
    # not silently inherit the first topic.
    if not previous_text or len(_topic_domains(previous_text)) > 1:
        payload = {
            "original": current,
            "standalone": current,
            "reason": "no_unambiguous_user_anchor",
            "turn_ids": [],
        }
        return DeterministicFollowupRewriteV1(
            original_query=current,
            standalone_query=current,
            rewrite_applied=False,
            reason_code="no_unambiguous_user_anchor",
            inherited_turn_ids=(),
            checksum=_checksum(payload),
        )

    turn_id = _message_id(previous) if previous is not None else ""
    standalone = (
        f"{current}. Ngữ cảnh câu hỏi trước của người dùng: "
        f"{previous_text[:900]}"
    )
    turn_ids = (turn_id,) if turn_id else ()
    payload = {
        "original": current,
        "standalone": standalone,
        "reason": "anaphoric_user_turn_inherited",
        "turn_ids": list(turn_ids),
    }
    return DeterministicFollowupRewriteV1(
        original_query=current,
        standalone_query=standalone,
        rewrite_applied=True,
        reason_code="anaphoric_user_turn_inherited",
        inherited_turn_ids=turn_ids,
        checksum=_checksum(payload),
    )

_OUT_OF_SCOPE_MARKERS = (
    "viết thơ",
    "sáng tác bài hát",
    "dự báo thời tiết",
    "thời tiết",
    "hôm nay mưa",
    "ngày mai mưa",
    "nhiệt độ",
    "kết quả bóng đá",
    "tỉ số bóng đá",
    "bóng đá",
    "bài hát",
    "nấu cơm",
    "công thức",
    "iphone",
    "samsung",
    "tử vi",
    "review phim",
    "phim hay",
    "cocktail",
    "hôm nay ăn",
    "giá vàng",
    "giá usd",
    "hello world",
    "vé máy bay",
    "giảm cân",
    "chuyện ma",
    "chuyện cổ tích",
    "xổ số",
    "yoga",
    "tour du lịch",
    "nuôi mèo",
    "trồng rau",
    "laptop",
    "spa gần",
    "bài tập toán",
    "tán tỉnh",
    "dịch sang",
    "dịch menu",
    "làm website",
    "soạn cv",
    "kể chuyện cười",
    "chuyện cười",
    "kể chuyện vui",
    "viết code",
    "lập trình python",
    "chơi game",
    "cổ phiếu",
    "chứng khoán",
    "bitcoin",
    "tiền điện tử",
    "tỷ giá",
    "tin tức giải trí",
    "lịch chiếu",
)

_STOPWORDS = {
    "anh", "chị", "bạn", "của", "cho", "các", "có", "được", "gì",
    "không", "là", "một", "như", "này", "thì", "trên", "tôi", "và",
    "về", "với", "vừa", "đó", "nào", "những", "theo", "phải",
}


def context_budget_environ_v2(
    *,
    router: bool = False,
    model_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a bounded context profile for the selected generation model.

    V1 installations previously allowed a very large environment value.  V2
    deliberately clamps the packet instead of inheriting that value. Local
    Qwen models get a smaller history window because prompt-prefill latency
    grows sharply in long conversations; DeepSeek keeps a larger window. This
    never mutates process env and never changes the persisted transcript.
    """

    values = dict(os.environ if environ is None else environ)
    if router:
        # Phase B: 0.5B router is capped at 1024 context and short JSON.
        values["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] = "1024"
        values["CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE"] = "0"
        values["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] = "64"
        return values

    normalized_model = _fold(model_name)
    if "qwen2.5" in normalized_model:
        input_budget, evidence_reserve, output_reserve = 10_000, 5_000, 1_536
    elif "qwen3.5" in normalized_model or "qwen3" in normalized_model:
        input_budget, evidence_reserve, output_reserve = 12_000, 5_500, 2_048
    elif "deepseek" in normalized_model:
        input_budget, evidence_reserve, output_reserve = 20_000, 6_000, 4_096
    else:
        input_budget, evidence_reserve, output_reserve = 16_000, 6_000, 4_096
    values["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] = str(input_budget)
    values["CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE"] = str(evidence_reserve)
    values["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] = str(output_reserve)
    return values


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _compact(value).casefold())
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")


def _checksum(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    values = os.environ if environ is None else environ
    try:
        return max(minimum, int(values.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def is_conversational_orchestrator_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    if _fold(values.get("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED", "false")) not in _TRUE_VALUES:
        return False
    normalized_role = _compact(role or "citizen").casefold()
    if normalized_role not in _ALLOWED_ROLES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ROLES", "")
        ).split(",")
        if item.strip()
    }
    return normalized_role in roles


def is_conversational_orchestrator_shadow_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    if is_conversational_orchestrator_enabled(role, environ=values):
        return False
    if _fold(values.get("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_SHADOW", "false")) not in _TRUE_VALUES:
        return False
    normalized_role = _compact(role or "citizen").casefold()
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ROLES", "")
        ).split(",")
        if item.strip()
    }
    return normalized_role in _ALLOWED_ROLES and normalized_role in roles


def is_llm_router_v2_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    if _fold(values.get("CHAT_LLM_ROUTER_V2_ENABLED", "false")) not in _TRUE_VALUES:
        return False
    normalized_role = _compact(role or "citizen").casefold()
    roles = {
        item.strip().casefold()
        for item in str(values.get("CHAT_LLM_ROUTER_V2_ROLES", "")).split(",")
        if item.strip()
    }
    return normalized_role in _ALLOWED_ROLES and normalized_role in roles


def is_routing_memory_v3_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the additive V3 routing reconciliation is active.

    V2 remains the rollback path.  V3 only reconciles explicit conversational
    instructions (for example, ``không tra cứu, chỉ nhắc lại``) and does not
    add a second domain classifier.
    """

    values = os.environ if environ is None else environ
    if _fold(values.get("CHAT_ROUTING_MEMORY_V3_ENABLED", "false")) not in _TRUE_VALUES:
        return False
    normalized_role = _compact(role or "citizen").casefold()
    roles = {
        item.strip().casefold()
        for item in str(values.get("CHAT_ROUTING_MEMORY_V3_ROLES", "")).split(",")
        if item.strip()
    }
    return normalized_role in _ALLOWED_ROLES and normalized_role in roles


def is_context_compaction_v2_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    if _fold(values.get("CHAT_CONTEXT_COMPACTION_V2_ENABLED", "false")) not in _TRUE_VALUES:
        return False
    normalized_role = _compact(role or "citizen").casefold()
    roles = {
        item.strip().casefold()
        for item in str(
            values.get(
                "CHAT_CONTEXT_COMPACTION_V2_ROLES",
                values.get("CHAT_LLM_ROUTER_V2_ROLES", ""),
            )
        ).split(",")
        if item.strip()
    }
    return normalized_role in _ALLOWED_ROLES and normalized_role in roles


@dataclass(frozen=True)
class ConversationIntentIssueV2:
    issue_id: str
    standalone_query: str
    domain_candidate: str
    required_facets: tuple[str, ...] = ()
    actor_anchors: tuple[str, ...] = ()
    authority_anchors: tuple[str, ...] = ()
    legal_object_anchors: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "standalone_query": self.standalone_query,
            "domain_candidate": self.domain_candidate,
            "required_facets": list(self.required_facets),
            "actor_anchors": list(self.actor_anchors),
            "authority_anchors": list(self.authority_anchors),
            "legal_object_anchors": list(self.legal_object_anchors),
        }


@dataclass(frozen=True)
class ConversationIntentDecisionV2:
    version: str
    route: ConversationRoute
    confidence: float
    reason_code: str
    current_question: str
    issues: tuple[ConversationIntentIssueV2, ...]
    active_document_id: str | None
    needs_clarification: bool
    referenced_turn_ids: tuple[str, ...]
    active_document_required: bool
    active_document_available: bool
    router_fallback: bool
    checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "route": self.route,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "current_question": self.current_question,
            "issues": [issue.to_payload() for issue in self.issues],
            "active_document_id": self.active_document_id,
            "needs_clarification": self.needs_clarification,
            "referenced_turn_ids": list(self.referenced_turn_ids),
            "active_document_required": self.active_document_required,
            "active_document_available": self.active_document_available,
            "router_fallback": self.router_fallback,
            "checksum": self.checksum,
        }


def _message_id(message: Mapping[str, Any]) -> str:
    return _compact(message.get("id") or message.get("message_id"))


def fallback_conversation_intent_v2(
    question: str,
    *,
    role: str,
    allowed_domains: Sequence[str] = (),
    active_document: Mapping[str, Any] | None = None,
    reason_code: str = "router_fallback",
) -> ConversationIntentDecisionV2:
    # V2 keeps the LLM router as the primary decision. If the provider times
    # out or emits an unusable payload, reuse V1 only as a bounded non-legal
    # safety net so greetings, conversation recall and explicit document
    # follow-ups do not pay retrieval. All other inputs still fail open to one
    # legal issue containing the current question verbatim.
    legacy = decide_conversation_turn(
        question,
        active_document=(dict(active_document) if active_document else None),
    )
    fallback_route: ConversationRoute = (
        legacy.route
        if legacy.route in {"chat_meta", "document_followup", "out_of_scope"}
        else "legal_query"
    )
    normalized_domains = [
        canonicalize_legal_domain(item)
        for item in allowed_domains
        if canonicalize_legal_domain(item)
    ]
    domain = (
        normalized_domains[0]
        if _compact(role).casefold() == "officer" and normalized_domains
        else "unknown"
    )
    issues = (
        (
            ConversationIntentIssueV2(
                issue_id="issue-1",
                standalone_query=_compact(question)[:2000],
                domain_candidate=domain,
                required_facets=(),
            ),
        )
        if fallback_route in {"legal_query", "document_followup"}
        else ()
    )
    payload = {
        "version": TURN_DECISION_V2_VERSION,
        "route": fallback_route,
        "confidence": 0.0,
        "reason_code": reason_code,
        "question": _compact(question),
        "issues": [issue.to_payload() for issue in issues],
        "active_document_id": (
            _compact((active_document or {}).get("document_id")) or None
            if fallback_route == "document_followup"
            else None
        ),
        "needs_clarification": False,
        "referenced_turn_ids": [],
        "router_fallback": True,
    }
    return ConversationIntentDecisionV2(
        version=TURN_DECISION_V2_VERSION,
        route=fallback_route,
        confidence=0.0,
        reason_code=reason_code,
        current_question=_compact(question),
        issues=issues,
        active_document_id=(
            _compact((active_document or {}).get("document_id")) or None
            if fallback_route == "document_followup"
            else None
        ),
        needs_clarification=False,
        referenced_turn_ids=(),
        active_document_required=legacy.active_document_required,
        active_document_available=bool(
            active_document and active_document.get("document_id")
        ),
        router_fallback=True,
        checksum=_checksum(payload),
    )


def _latest_prior_user_message(
    history_messages: Sequence[Mapping[str, Any]],
    *,
    current_question: str,
) -> Mapping[str, Any] | None:
    """Return the latest completed user message before the current request.

    Conversation persistence can already contain the current user message by
    the time orchestration runs.  Comparing normalized text prevents us from
    rewriting a question against itself.
    """

    current = _compact(current_question).casefold()
    skipped_current = False
    for message in reversed(list(history_messages)):
        if _message_role(message) != "user" or not _eligible_message(message):
            continue
        content = _compact(message.get("content"))
        if not skipped_current and content.casefold() == current:
            skipped_current = True
            continue
        return message
    return None


def _is_contextual_legal_followup(question: str) -> bool:
    """Recognize a short legal continuation without classifying its domain.

    This is intentionally a small, domain-neutral rule.  It only decides
    whether the previous *user* question should accompany the current query;
    retrieval and the existing deterministic legal planner still own domain,
    procedure, temporal and form decisions.
    """

    folded = _fold(question).rstrip(".!? ")
    if not folded or len(folded) > 220 or _topic_domains(question):
        return False
    if _is_operational_legal_followup(question):
        return True
    return bool(
        re.search(
            r"\b(?:con|the|vay|thi sao|truong hop (?:nay|do)|"
            r"cai (?:nay|do|kia)|viec (?:nay|do)|nhu vua noi|"
            r"noi tren|vua neu|vua hoi)\b",
            folded,
        )
        or any(
            marker in folded
            for marker in (
                "ho so gom",
                "can giay to gi",
                "thoi han bao lau",
                "nop o dau",
                "co mat khong",
                "di cung khong",
                "mau nao",
                "bieu mau nao",
                "le phi bao nhieu",
            )
        )
    )


def is_clear_independent_legal_query(
    question: str,
    *,
    history_messages: Sequence[Mapping[str, Any]] = (),
    state: Mapping[str, Any] | None = None,
    active_document: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether the V2 router can safely be skipped for this turn.

    This is a transport optimization, not a legal classifier. The legal
    planner and retrieval still run immediately afterwards. Any deictic or
    incomplete continuation remains on the router path so it can resolve the
    user's reference before evidence selection.
    """

    current = _compact(question)
    if not current or len(current) > 1200:
        return False
    decision = deterministic_conversation_intent_v2(
        current,
        role="citizen",
        history_messages=history_messages,
        state=state,
        active_document=active_document,
    )
    if decision.route != "legal_query" or decision.needs_clarification:
        return False
    folded = _fold(current)
    if _is_standalone_legal_identifier_query(current) and not re.search(
        r"\b(?:do|nay|tren|vua roi|vua neu|nhu tren|noi tren)\b",
        folded,
    ):
        return True
    if _is_contextual_legal_followup(current):
        return False
    # These references are unsafe to resolve from the current turn alone,
    # even when the question also contains a recognizable legal topic.
    if re.search(
        r"\b(?:vua neu|vua roi|nhu tren|noi tren|cua tren|cua do|"
        r"cai nay|cai do|truong hop nay|quy dinh nay|van ban nay|"
        r"dieu nay|con lai|tiep tuc)\b",
        folded,
    ):
        return False
    if history_messages and re.search(
        r"^(?:con|the|vay|the con|noi chung|nhu vay|sao|thi sao)\b",
        folded,
    ):
        return False
    return True


def deterministic_conversation_intent_v2(
    question: str,
    *,
    role: str,
    history_messages: Sequence[Mapping[str, Any]] = (),
    state: Mapping[str, Any] | None = None,
    allowed_domains: Sequence[str] = (),
    active_document: Mapping[str, Any] | None = None,
) -> ConversationIntentDecisionV2:
    """Build the V2 conversation contract without calling a language model.

    The selected chat model is reserved for the final answer.  This decision
    only handles the few transport-level branches needed by the application:
    conversation meta, active-document follow-up, out-of-scope requests and a
    legal query.  The existing legal planner remains authoritative for issue
    splitting and domain/facet classification.
    """

    legacy = decide_conversation_turn(
        question,
        active_document=(dict(active_document) if active_document else None),
        history_messages=history_messages,
    )
    explicit_route = _explicit_conversation_route(question)
    route: ConversationRoute = (
        explicit_route
        or ("legal_query" if _is_standalone_legal_identifier_query(question) else legacy.route)
    )
    state = state or {}
    normalized_domains = [
        canonicalize_legal_domain(item)
        for item in allowed_domains
        if canonicalize_legal_domain(item)
    ]
    state_domain = canonicalize_legal_domain(state.get("canonical_domain"))
    inherited_domain = state_domain if state_domain in _ROUTER_ALLOWED_DOMAINS else "unknown"
    officer_domain = (
        normalized_domains[0]
        if _compact(role).casefold() == "officer" and normalized_domains
        else inherited_domain
    )
    prior = _latest_prior_user_message(
        history_messages,
        current_question=question,
    )
    referenced_turn_ids: tuple[str, ...] = ()
    issues: tuple[ConversationIntentIssueV2, ...] = ()

    # Only create an explicit issue for an incomplete continuation.  A full
    # legal question is left to the current deterministic issue planner, which
    # already handles multi-issue questions and facet extraction.
    if (
        route == "legal_query"
        and prior is not None
        and _is_contextual_legal_followup(question)
    ):
        prior_content = _compact(prior.get("content"))[:1400]
        current = _compact(question)[:500]
        standalone = f"{prior_content}. Câu hỏi tiếp theo: {current}"[:2000]
        prior_id = _message_id(prior)
        if prior_id:
            referenced_turn_ids = (prior_id,)
        issues = (
            ConversationIntentIssueV2(
                issue_id="issue-1",
                standalone_query=standalone,
                domain_candidate=officer_domain,
                required_facets=(),
            ),
        )
    elif route == "legal_query" and _is_contextual_legal_followup(question):
        # Long conversations may compact the original turn out of the loaded
        # page. Reconstruct only from backend-owned state, never old assistant
        # prose. Explicit facts in the current question still win because this
        # branch only handles domain-neutral/incomplete continuations.
        procedure = state.get("procedure") if isinstance(state.get("procedure"), Mapping) else {}
        anchors = [
            _compact(procedure.get("name")),
            *[_compact(item) for item in state.get("legal_objects") or []],
            *[_compact(item) for item in state.get("actors") or []],
        ]
        anchors = list(dict.fromkeys(item for item in anchors if item))[:6]
        if anchors:
            standalone = (
                f"Ngữ cảnh đã xác minh: {', '.join(anchors)}. "
                f"Câu hỏi tiếp theo: {_compact(question)[:500]}"
            )[:2000]
            issues = (
                ConversationIntentIssueV2(
                    issue_id="issue-1",
                    standalone_query=standalone,
                    domain_candidate=officer_domain,
                    required_facets=(),
                ),
            )

    active_document_id = (
        _compact((active_document or {}).get("document_id")) or None
        if route == "document_followup"
        else None
    )
    reason_code = (
        "deterministic_explicit_intent"
        if explicit_route is not None
        else "deterministic_history_followup"
        if issues
        else f"deterministic_{legacy.reason_code}"
    )
    needs_clarification = bool(
        legacy.reason_code == "ambiguous_multi_topic_followup"
        and not _is_standalone_legal_identifier_query(question)
    )
    normalized_payload = {
        "version": TURN_DECISION_V2_VERSION,
        "route": route,
        "confidence": 1.0,
        "reason_code": reason_code,
        "question": _compact(question),
        "issues": [issue.to_payload() for issue in issues],
        "active_document_id": active_document_id,
        "needs_clarification": needs_clarification,
        "referenced_turn_ids": list(referenced_turn_ids),
        "router_fallback": False,
    }
    return ConversationIntentDecisionV2(
        version=TURN_DECISION_V2_VERSION,
        route=route,
        confidence=1.0,
        reason_code=reason_code,
        current_question=_compact(question),
        issues=issues,
        active_document_id=active_document_id,
        needs_clarification=needs_clarification,
        referenced_turn_ids=referenced_turn_ids,
        active_document_required=route == "document_followup",
        active_document_available=bool(active_document_id),
        router_fallback=False,
        checksum=_checksum(normalized_payload),
    )


_LLM_ROUTER_FORBIDDEN_KEYS = frozenset(
    {
        "acl",
        "account_domain",
        "allowed_domains",
        "authorization",
        "department",
        "effectivity",
        "effectivity_status",
        "grants",
        "grants_checksum",
        "organization_unit_id",
        "primary_unit_id",
        "unit_id",
    }
)


def strip_llm_router_forbidden_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop department / ACL / effectivity keys if a model emits them."""

    if not isinstance(payload, Mapping):
        return {}
    forbidden = {key.casefold() for key in _LLM_ROUTER_FORBIDDEN_KEYS}
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).casefold() in forbidden:
            continue
        if isinstance(value, Mapping):
            cleaned[str(key)] = strip_llm_router_forbidden_fields(value)
        elif isinstance(value, list):
            cleaned[str(key)] = [
                strip_llm_router_forbidden_fields(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            cleaned[str(key)] = value
    return cleaned


def build_conversation_router_prompt_short_v2(
    *,
    question: str,
    role: str,
    context_packet: "ConversationContextPacketV1",
) -> str:
    """Compact 0.5B classify prompt: conversation_route + confidence only."""

    history_block = _compact(getattr(context_packet, "prompt_block", "") or "")[:800]
    return (
        "Bạn là bộ định tuyến hội thoại cho trợ lý pháp luật Hải Phòng. "
        "Chỉ phân loại; không trả lời pháp luật; không chọn phòng ban, ACL "
        "hoặc hiệu lực văn bản.\n"
        "Xuất đúng một JSON object: "
        '{"conversation_route":"legal_query","confidence":0.0}\n'
        "conversation_route ∈ {legal_query, document_followup, chat_meta, out_of_scope}.\n"
        f"Vai trò: {_compact(role)}\n"
        f"Lịch sử (rút gọn):\n{history_block or '(trống)'}\n"
        f"Câu hỏi: {_compact(question)}\n"
    )


def build_conversation_router_prompt_v2(
    *,
    question: str,
    role: str,
    context_packet: "ConversationContextPacketV1",
    state: Mapping[str, Any] | None,
    allowed_domains: Sequence[str] = (),
) -> str:
    state = state or {}
    recent_sources = [
        {
            "document_id": _compact(item.get("document_id")),
            "title": _compact(item.get("title")),
            "law_number": _compact(item.get("law_number")),
        }
        for item in state.get("recent_source_refs") or []
        if isinstance(item, Mapping) and _compact(item.get("document_id"))
    ][:5]
    canonical_domains = list(
        dict.fromkeys(
            canonicalize_legal_domain(item)
            for item in allowed_domains
            if canonicalize_legal_domain(item)
        )
    )
    officer_constraint = (
        "Cán bộ chỉ được dùng các domain trong allowed_domains; không tự mở rộng."
        if _compact(role).casefold() == "officer"
        else "Người dân có thể có nhiều issue thuộc các domain khác nhau."
    )
    return (
        "Bạn là bộ định tuyến hội thoại cho trợ lý pháp luật Hải Phòng. "
        "Chỉ phân loại và viết lại câu hỏi; không trả lời pháp luật, không tạo "
        "điều luật, thủ tục, thời hạn, URL hoặc ID văn bản.\n"
        "Đọc lịch sử để hiểu đại từ và câu nối tiếp. Dữ kiện nói rõ trong câu "
        "hiện tại luôn thắng lịch sử.\n"
        f"Vai trò: {_compact(role)}. {officer_constraint}\n"
        f"allowed_domains: {json.dumps(canonical_domains, ensure_ascii=False)}\n"
        f"domain_enums: {json.dumps(sorted(_ROUTER_ALLOWED_DOMAINS), ensure_ascii=False)}\n"
        f"facet_enums: {json.dumps(sorted(_ROUTER_ALLOWED_FACETS), ensure_ascii=False)}\n"
        "route_enums: [chat_meta, document_followup, legal_query, out_of_scope]\n"
        "chat_meta gồm chào hỏi, cảm ơn, hỏi lại/tóm tắt lịch sử và xin gợi ý; "
        "một câu chào chỉ nói sẽ hỏi về thủ tục nhưng chưa hỏi nội dung pháp lý "
        "vẫn là chat_meta. document_followup là câu đang hỏi chính văn bản/điều "
        "đã nêu hoặc đã ghim. legal_query là yêu cầu cần kết luận pháp luật.\n"
        "Với câu có ý định rõ ràng, confidence phải từ 0.80 trở lên; chỉ dùng "
        "confidence dưới 0.55 khi câu thực sự không đủ nghĩa để phân loại.\n"
        f"Mỗi hành vi pháp lý độc lập là một issue, tối đa {ROUTER_MAX_ISSUES}. Với legal_query, "
        "standalone_query phải giữ chủ thể, hành vi, cơ quan và đối tượng pháp lý. "
        "Không tự chọn phòng ban; domain_candidate chỉ là nhãn gợi ý để backend "
        "chuẩn hóa và kiểm quyền.\n"
        "active_document_id chỉ được chọn từ recent_sources.\n\n"
        "LỊCH SỬ HỘI THOẠI — KHÔNG PHẢI CĂN CỨ PHÁP LUẬT\n"
        f"{context_packet.prompt_block or '(trống)'}\n\n"
        f"recent_sources: {json.dumps(recent_sources, ensure_ascii=False)}\n\n"
        f"CÂU HỎI HIỆN TẠI\n{_compact(question)}\n\n"
        "Xuất đúng một JSON object: "
        '{"route":"legal_query","confidence":0.0,"reason_code":"...",'
        '"issues":[{"issue_id":"issue-1","standalone_query":"...",'
        '"domain_candidate":"unknown","required_facets":[],"actor_anchors":[],'
        '"authority_anchors":[],"legal_object_anchors":[]}],'
        '"active_document_id":null,"needs_clarification":false,'
        '"referenced_turn_ids":[]}'
    )


def parse_conversation_intent_v2(
    raw: Any,
    *,
    question: str,
    role: str,
    history_messages: Sequence[Mapping[str, Any]] = (),
    allowed_domains: Sequence[str] = (),
    recent_source_refs: Sequence[Mapping[str, Any]] = (),
    active_document: Mapping[str, Any] | None = None,
    minimum_confidence: float = 0.55,
    environ: Mapping[str, str] | None = None,
) -> ConversationIntentDecisionV2:
    text = str(raw or "").strip()
    candidate = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate)
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return fallback_conversation_intent_v2(
            question,
            role=role,
            allowed_domains=allowed_domains,
            active_document=active_document,
            reason_code="router_invalid_json",
        )
    if not isinstance(payload, Mapping):
        return fallback_conversation_intent_v2(
            question,
            role=role,
            allowed_domains=allowed_domains,
            active_document=active_document,
            reason_code="router_invalid_payload",
        )
    payload = strip_llm_router_forbidden_fields(payload)
    route = _compact(payload.get("route") or payload.get("conversation_route")).casefold()
    if route not in {"chat_meta", "document_followup", "legal_query", "out_of_scope"}:
        # An invalid model enum is not evidence of a legal question.  Re-run
        # the deterministic policy so malformed output cannot accidentally
        # enter retrieval.
        return fallback_conversation_intent_v2(
            question,
            role=role,
            allowed_domains=allowed_domains,
            active_document=active_document,
            reason_code="router_invalid_route",
        )
    explicit_route = (
        _explicit_conversation_route(question)
        if is_routing_memory_v3_enabled(role, environ=environ)
        else None
    )
    if explicit_route is not None:
        route = explicit_route
    try:
        confidence = min(1.0, max(0.0, float(payload.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < minimum_confidence:
        return fallback_conversation_intent_v2(
            question,
            role=role,
            allowed_domains=allowed_domains,
            active_document=active_document,
            reason_code="router_low_confidence",
        )

    normalized_allowed = {
        canonicalize_legal_domain(item)
        for item in allowed_domains
        if canonicalize_legal_domain(item)
    }
    officer = _compact(role).casefold() == "officer"
    issues: list[ConversationIntentIssueV2] = []
    for index, raw_issue in enumerate(payload.get("issues") or []):
        if not isinstance(raw_issue, Mapping) or len(issues) >= ROUTER_MAX_ISSUES:
            continue
        query = _compact(raw_issue.get("standalone_query"))[:2000]
        if not query:
            continue
        domain = canonicalize_legal_domain(raw_issue.get("domain_candidate")) or "unknown"
        if domain not in _ROUTER_ALLOWED_DOMAINS:
            domain = "unknown"
        if officer:
            if normalized_allowed:
                # Drop an explicitly out-of-scope issue instead of coercing
                # it to the first allowed domain.  An unknown domain remains
                # as unknown so the backend can perform a scoped fallback.
                if domain != "unknown" and domain not in normalized_allowed:
                    continue
            else:
                domain = "unknown"
        facets = tuple(
            dict.fromkeys(
                _compact(item).casefold()
                for item in raw_issue.get("required_facets") or []
                if _compact(item).casefold() in _ROUTER_ALLOWED_FACETS
            )
        )[:ROUTER_MAX_FACETS_PER_ISSUE]

        def anchors(key: str) -> tuple[str, ...]:
            return tuple(
                dict.fromkeys(
                    _compact(item)[:200]
                    for item in raw_issue.get(key) or []
                    if _compact(item)
                )
            )[:ROUTER_MAX_ANCHORS_PER_ISSUE]

        issues.append(
            ConversationIntentIssueV2(
                issue_id=f"issue-{index + 1}",
                standalone_query=query,
                domain_candidate=domain,
                required_facets=facets or ("unknown",),
                actor_anchors=anchors("actor_anchors"),
                authority_anchors=anchors("authority_anchors"),
                legal_object_anchors=anchors("legal_object_anchors"),
            )
        )
    if route in {"chat_meta", "out_of_scope"}:
        issues = []
    if route in {"legal_query", "document_followup"} and not issues:
        fallback = fallback_conversation_intent_v2(
            question,
            role=role,
            allowed_domains=allowed_domains,
            active_document=active_document,
            reason_code="router_missing_issues",
        )
        if route == "document_followup":
            replacement_payload = {
                **fallback.to_payload(),
                "route": "document_followup",
                "active_document_required": True,
            }
            fallback = replace(
                fallback,
                route="document_followup",
                active_document_required=True,
                checksum=_checksum(replacement_payload),
            )
        return fallback

    valid_source_ids = {
        _compact(item.get("document_id")).casefold(): _compact(item.get("document_id"))
        for item in recent_source_refs
        if isinstance(item, Mapping) and _compact(item.get("document_id"))
    }
    requested_document = _compact(payload.get("active_document_id"))
    active_document_id = valid_source_ids.get(requested_document.casefold())
    if route == "document_followup" and not active_document_id and active_document:
        active_document_id = _compact(active_document.get("document_id")) or None
    valid_turn_ids = {
        _message_id(message) for message in history_messages if _message_id(message)
    }
    referenced_turn_ids = tuple(
        dict.fromkeys(
            _compact(item)
            for item in payload.get("referenced_turn_ids") or []
            if _compact(item) in valid_turn_ids
        )
    )[:12]
    active_available = bool(
        active_document_id
        or (active_document and active_document.get("document_id"))
    )
    normalized_payload = {
        "version": TURN_DECISION_V2_VERSION,
        "route": route,
        "confidence": confidence,
        "reason_code": (
            "deterministic_explicit_intent"
            if explicit_route is not None
            else (_compact(payload.get("reason_code"))[:120] or "llm_router")
        ),
        "question": _compact(question),
        "issues": [issue.to_payload() for issue in issues],
        "active_document_id": active_document_id,
        "needs_clarification": bool(payload.get("needs_clarification")),
        "referenced_turn_ids": list(referenced_turn_ids),
        "router_fallback": False,
    }
    return ConversationIntentDecisionV2(
        version=TURN_DECISION_V2_VERSION,
        route=route,  # type: ignore[arg-type]
        confidence=confidence,
        reason_code=normalized_payload["reason_code"],
        current_question=_compact(question),
        issues=tuple(issues),
        active_document_id=active_document_id,
        needs_clarification=bool(payload.get("needs_clarification")),
        referenced_turn_ids=referenced_turn_ids,
        active_document_required=route == "document_followup",
        active_document_available=active_available,
        router_fallback=False,
        checksum=_checksum(normalized_payload),
    )


@dataclass(frozen=True)
class ConversationTurnDecisionV1:
    version: str
    route: ConversationRoute
    reason_code: str
    current_question: str
    active_document_required: bool
    active_document_available: bool
    checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "route": self.route,
            "reason_code": self.reason_code,
            "active_document_required": self.active_document_required,
            "active_document_available": self.active_document_available,
            "checksum": self.checksum,
        }


def decide_conversation_turn(
    question: str,
    *,
    active_document: Mapping[str, Any] | None = None,
    history_messages: Sequence[Mapping[str, Any]] | None = None,
) -> ConversationTurnDecisionV1:
    """Choose a route without an LLM. Ambiguity deliberately stays legal."""

    current = _compact(question)
    folded = _fold(current)
    folded_clean = folded.rstrip(".!? ")
    route: ConversationRoute = "legal_query"
    reason = "default_legal_query"
    document_required = False
    folded_meta = bool(
        len(folded) <= 260
        and (
            folded_clean in {
                "chao",
                "xin chao",
                "cam on",
                "thanks",
                "thank you",
                "ban la ai",
                "ban la chatbot",
                "ban co phai chatbot khong",
                "ban co the lam gi",
            }
            or (
                folded_clean.startswith("cam on")
                and len(folded_clean) <= 48
                and not _topic_domains(current)
                and re.search(
                    r"\b(?:dieu|khoan|luat|nghi dinh|thong tu|thu tuc|ho so)\b",
                    folded_clean,
                )
                is None
            )
            or (
                re.search(r"^(?:xin\s+)?chao\b", folded_clean) is not None
                and any(
                    marker in folded_clean
                    for marker in (
                        "se hoi",
                        "hoi noi tiep",
                        "hoi nhieu viec",
                        "hoi nhieu thu tuc",
                    )
                )
                and not _topic_domains(current)
            )
            or ("toi vua hoi" in folded_clean and folded_clean.endswith("gi"))
            or ("minh vua hoi" in folded_clean and folded_clean.endswith("gi"))
            or (
                re.search(
                    r"\b(?:t|toi|minh)\s+(?:vua|moi)\s+hoi\b",
                    folded_clean,
                )
                is not None
                and folded_clean.endswith("gi")
            )
            or ("cau hoi truoc" in folded_clean and "gi" in folded_clean)
            or ("yeu cau truoc" in folded_clean and "gi" in folded_clean)
            or (
                ("ban la ai" in folded_clean or "ban la chatbot" in folded_clean)
                and not any(
                    marker in folded_clean
                    for marker in ("dieu ", "khoan ", "nghi dinh", "thong tu")
                )
            )
            or (
                "ban" in folded_clean
                and ("giup duoc" in folded_clean or "giup duoc nhung viec" in folded_clean)
                and folded_clean.endswith("gi")
            )
            or (
                not _topic_domains(current)
                and re.search(
                    r"\b(?:m|may|ban|chatbot)\b.*\b(?:giup|lam)\b.*\bgi\b",
                    folded_clean,
                )
                is not None
            )
            or (
                len(folded_clean) <= 80
                and not _topic_domains(current)
                and any(
                    marker in folded_clean
                    for marker in (
                        "con me",
                        "dit me",
                        "dm ",
                        "do ngu",
                    )
                )
            )
            or (
                re.search(r"\b(?:t|toi|minh)\s+dang\s+hoi\b", folded_clean)
                is not None
                and ("gi" in folded_clean or "ay nhi" in folded_clean)
            )
            or (
                "tom tat" in folded_clean
                and any(marker in folded_clean for marker in ("dang hoi", "gan nhat", "phien"))
            )
            or (
                "toan bo phien" in folded_clean
                and any(marker in folded_clean for marker in ("linh vuc", "chu de", "da hoi"))
            )
            or (
                ("de xuat" in folded_clean or "goi y" in folded_clean)
                and "cau hoi tiep theo" in folded_clean
            )
        )
    )
    explicit_route = _explicit_conversation_route(current)
    if explicit_route == "chat_meta":
        route = "chat_meta"
        reason = "explicit_history_or_meta_request"
    elif explicit_route == "out_of_scope":
        route = "out_of_scope"
        reason = "explicit_non_legal_request"
    elif _is_standalone_legal_identifier_query(current):
        route = "legal_query"
        reason = "standalone_legal_identifier"
    elif _is_short_active_document_followup(current, active_document):
        route = "document_followup"
        reason = "active_document_followup"
        document_required = True
    elif explicit_route == "document_followup":
        route = "document_followup"
        reason = "explicit_document_followup"
        document_required = True
    elif explicit_route == "legal_query":
        route = "legal_query"
        reason = "explicit_operational_legal_followup"
    elif folded_meta or any(
        re.fullmatch(pattern, current, flags=re.IGNORECASE)
        for pattern in _META_PATTERNS
    ):
        route = "chat_meta"
        reason = "explicit_conversation_meta"
    elif (
        any(_fold(marker) in folded for marker in _DOCUMENT_MARKERS)
        or _DOCUMENT_DEICTIC_RE.search(folded) is not None
    ):
        route = "document_followup"
        reason = "deictic_document_reference"
        document_required = True
    elif (
        any(_fold(marker) in folded for marker in _OUT_OF_SCOPE_MARKERS)
        or re.search(r"\b(?:viet|lam|sang tac)\b.*\bbai tho\b", folded) is not None
    ):
        route = "out_of_scope"
        reason = "explicit_non_legal_request"
    elif _needs_multi_topic_clarification(current, history_messages or ()):
        # A short facet such as "Còn lệ phí?" cannot safely inherit one topic
        # when the immediately preceding user turn asked three independent
        # legal jobs.  Let the same DeepSeek call ask which job the citizen
        # means; do not perform a broad or arbitrarily scoped retrieval.
        route = "chat_meta"
        reason = "ambiguous_multi_topic_followup"
    available = bool(active_document and active_document.get("document_id"))
    payload = {
        "version": TURN_DECISION_VERSION,
        "route": route,
        "reason": reason,
        "question": current,
        "active_document_required": document_required,
        "active_document_available": available,
    }
    return ConversationTurnDecisionV1(
        version=TURN_DECISION_VERSION,
        route=route,
        reason_code=reason,
        current_question=current,
        active_document_required=document_required,
        active_document_available=available,
        checksum=_checksum(payload),
    )


def _topic_domains(value: Any) -> set[str]:
    folded = _fold(value)
    groups: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("complaint", ("khieu nai", "to cao", "xu phat", "quyet dinh phat", "muc phat")),
        ("residence", ("tam tru", "thuong tru", "cu tru", "can cuoc", "cccd", "luu tru", "tam vang", "ho khau")),
        ("social", ("tro cap", "huu tri xa hoi", "huu tri xh", "bao tro", "bhyt", "bhxh", "bao hiem xa hoi", "that nghiep", "om dau", "om da", "mai tang")),
        ("civil", ("khai sinh", "khai tu", "ket hon", "ho tich", "chung thuc", "cong chung", "ly hon", "con nuoi", "hon nhan", "di chuc", "giam ho", "doc than")),
        ("land", ("dat dai", "thua dat", "tach thua", "so do", "so hong", "xay dung", "sang ten", "dat o", "su dung dat", "dat nong nghiep", "chuyen muc dich", "chuyen nhuong", "xay nha", "giay chung nhan", "quyen su dung dat", "dat dang the chap")),
    )
    return {
        domain
        for domain, markers in groups
        if any(marker in folded for marker in markers)
    }


def _needs_multi_topic_clarification(
    question: str,
    history_messages: Sequence[Mapping[str, Any]],
) -> bool:
    folded = _fold(question).rstrip(".!? ")
    if len(folded) > 90:
        return False
    facet_markers = (
        "con le phi",
        "con phi",
        "con ho so",
        "con giay to",
        "con thoi han",
        "con bieu mau",
        "con thu tuc",
        "nop o dau",
        "con noi nop",
    )
    if not any(marker in folded for marker in facet_markers):
        return False
    current = _compact(question).casefold()
    for message in reversed(list(history_messages)):
        if _message_role(message) != "user" or not _eligible_message(message):
            continue
        content = _compact(message.get("content"))
        if content.casefold() == current:
            continue
        return len(_topic_domains(content)) >= 2
    return False


def _message_role(message: Mapping[str, Any]) -> str:
    role = _compact(message.get("role") or message.get("sender_role") or "assistant")
    return "user" if role in {"user", "human"} else "assistant"


def _eligible_message(message: Mapping[str, Any]) -> bool:
    if _message_role(message) not in {"user", "assistant"}:
        return False
    status = _compact(message.get("status"))
    return bool(_compact(message.get("content"))) and status not in {"pending", "error", "cancelled"}


def _message_tokens(message: Mapping[str, Any]) -> int:
    return token_count(_compact(message.get("content"))) + 6


def _terms(value: Any) -> set[str]:
    return {
        term
        for term in re.findall(r"[\wÀ-ỹĐđ]+", _compact(value).casefold())
        if len(term) >= 3 and term not in _STOPWORDS
    }


def _relevance_score(message: Mapping[str, Any], current_question: str) -> tuple[int, int]:
    query_terms = _terms(current_question)
    content_terms = _terms(message.get("content"))
    overlap = len(query_terms.intersection(content_terms))
    citation_bonus = 1 if message.get("citations") or message.get("citations_snapshot") else 0
    return overlap, citation_bonus


def _state_digest_lines(state: Mapping[str, Any] | None) -> list[str]:
    state = state or {}
    lines: list[str] = []
    for key, label in (
        ("canonical_domain", "Lĩnh vực"),
        ("temporal_scope", "Phạm vi thời gian"),
        ("legal_as_of", "Ngày áp dụng"),
    ):
        value = _compact(state.get(key))
        if value:
            lines.append(f"- {label}: {value}")
    procedure = state.get("procedure")
    if isinstance(procedure, Mapping):
        value = _compact(procedure.get("name") or procedure.get("id"))
        if value:
            lines.append(f"- Thủ tục đang trao đổi: {value}")
    active_document = state.get("active_document")
    if isinstance(active_document, Mapping):
        title = _compact(
            active_document.get("title") or active_document.get("law_number")
        )
        refs = [
            _compact(item)
            for item in active_document.get("article_refs") or []
            if _compact(item)
        ]
        if title:
            suffix = f" ({', '.join(refs)})" if refs else ""
            lines.append(f"- Văn bản đang trao đổi: {title}{suffix}")
    digest_v2 = state.get("conversation_digest_v2")
    if isinstance(digest_v2, Mapping):
        topic_summary = _compact(digest_v2.get("topic_summary"))
        current_goal = _compact(digest_v2.get("current_goal"))
        if topic_summary:
            lines.append(f"- Tóm tắt chủ đề: {topic_summary}")
        if current_goal:
            lines.append(f"- Mục tiêu hiện tại: {current_goal}")
        topics = [
            _compact(item)
            for item in digest_v2.get("topics") or []
            if _compact(item)
        ][:8]
        if topics:
            lines.append(f"- Các chủ đề: {', '.join(topics)}")
        facts = [
            _compact(item.get("text"))
            for item in digest_v2.get("user_facts") or []
            if isinstance(item, Mapping) and _compact(item.get("text"))
        ][:10]
        if facts:
            lines.append(f"- Dữ kiện do người dùng nêu: {' | '.join(facts)}")
        open_questions = [
            _compact(item)
            for item in digest_v2.get("open_questions") or []
            if _compact(item)
        ][:8]
        if open_questions:
            lines.append(f"- Câu hỏi còn mở: {' | '.join(open_questions)}")
    else:
        stored_digest = _compact(state.get("conversation_digest"))
        if stored_digest:
            lines.append(f"- Các câu hỏi cũ của người dùng: {stored_digest}")
    for key, label in (
        ("actors", "Chủ thể"),
        ("legal_objects", "Đối tượng pháp lý"),
        ("locations", "Địa bàn"),
    ):
        values = [_compact(item) for item in state.get(key) or [] if _compact(item)]
        if values:
            lines.append(f"- {label}: {', '.join(values[:5])}")
    return lines


def _render_messages(messages: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        label = "Người dùng" if _message_role(message) == "user" else "Trợ lý"
        message_id = _message_id(message)
        id_prefix = f"[{message_id}] " if message_id else ""
        lines.append(f"{id_prefix}{label}: {_compact(message.get('content'))}")
    return "\n".join(lines)


@dataclass(frozen=True)
class ConversationContextPacketV1:
    version: str
    history_mode: Literal["empty", "full", "compacted"]
    messages_considered: int
    messages_included: int
    history_tokens: int
    input_token_budget: int
    history_token_budget: int
    older_messages_compacted: int
    included_message_ids: tuple[str, ...]
    digest_revision: int
    digest_through_message_id: str | None
    compaction_item_checksum: str | None
    prompt_block: str
    checksum: str

    def usage(self, *, state_revision: int = 0) -> dict[str, Any]:
        return {
            "used": self.messages_included > 0,
            "history_mode": self.history_mode,
            "messages_considered": self.messages_considered,
            "messages_included": self.messages_included,
            "history_tokens": self.history_tokens,
            "input_token_budget": self.input_token_budget,
            "history_token_budget": self.history_token_budget,
            "older_messages_compacted": self.older_messages_compacted,
            "state_revision": int(state_revision or 0),
            "digest_revision": self.digest_revision,
            "digest_through_message_id": self.digest_through_message_id,
            "compaction_item_checksum": self.compaction_item_checksum,
            "context_checksum": self.checksum,
        }


def build_conversation_context_packet(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_question: str,
    state: Mapping[str, Any] | None = None,
    referenced_turn_ids: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> ConversationContextPacketV1:
    """Build full history when it fits, otherwise a deterministic compact view."""

    input_budget = _int_env(
        "CHAT_CONTEXT_INPUT_TOKEN_BUDGET",
        _DEFAULT_INPUT_BUDGET,
        minimum=8_000,
        environ=environ,
    )
    evidence_reserve = _int_env(
        "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE",
        _DEFAULT_EVIDENCE_RESERVE,
        minimum=2_000,
        environ=environ,
    )
    output_reserve = _int_env(
        "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE",
        _DEFAULT_OUTPUT_RESERVE,
        minimum=1_024,
        environ=environ,
    )
    history_budget = max(1_500, input_budget - evidence_reserve - output_reserve)
    eligible = [dict(message) for message in messages if _eligible_message(message)]
    # The UI normally pre-persists the current user turn. It belongs in the
    # dedicated current-question block and must not be duplicated in history.
    if (
        eligible
        and _message_role(eligible[-1]) == "user"
        and _compact(eligible[-1].get("content")) == _compact(current_question)
    ):
        eligible = eligible[:-1]
    considered = len(eligible)
    # Avoid tokenizing an obviously oversized 1,000-turn transcript just to
    # discover that it must be compacted. Four characters per token is a
    # conservative upper bound for the supported Vietnamese/English chat; the
    # exact tokenizer is still used whenever the transcript might fit.
    estimated_full_chars = sum(
        len(_compact(message.get("content"))) + 32 for message in eligible
    )
    definitely_oversized = estimated_full_chars > history_budget * 4
    full_prompt = "" if definitely_oversized else _render_messages(eligible)
    full_tokens = (
        history_budget + 1
        if definitely_oversized
        else token_count(full_prompt)
    )
    if not eligible:
        mode: Literal["empty", "full", "compacted"] = "empty"
        included: list[dict[str, Any]] = []
        prompt_block = ""
        compacted = 0
    elif full_tokens <= history_budget:
        mode = "full"
        included = eligible
        prompt_block = full_prompt
        compacted = 0
    else:
        mode = "compacted"
        recent = eligible[-_RECENT_MESSAGE_LIMIT:]
        older = eligible[:-_RECENT_MESSAGE_LIMIT]
        referenced_ids = {
            _compact(item) for item in referenced_turn_ids if _compact(item)
        }
        query_terms = _terms(current_question)

        def relevance_for_current(message: Mapping[str, Any]) -> tuple[int, int]:
            content_terms = _terms(message.get("content"))
            overlap = len(query_terms.intersection(content_terms))
            citation_bonus = (
                1
                if message.get("citations")
                or message.get("citations_snapshot")
                else 0
            )
            return overlap, citation_bonus

        ranked_older = sorted(
            enumerate(older),
            key=lambda item: (
                1 if _message_id(item[1]) in referenced_ids else 0,
                relevance_for_current(item[1]),
                item[0],
            ),
            reverse=True,
        )
        relevant = [item for _, item in ranked_older[:_RELEVANT_OLDER_LIMIT]]
        relevant.sort(key=lambda item: older.index(item))
        included = [*relevant, *recent]
        recent_ids = {_message_id(item) for item in recent if _message_id(item)}

        def drop_lowest_priority_message() -> None:
            if not included:
                return
            index = next(
                (
                    idx
                    for idx, item in enumerate(included)
                    if _message_id(item) not in referenced_ids
                    and _message_id(item) not in recent_ids
                ),
                None,
            )
            if index is None:
                index = next(
                    (
                        idx
                        for idx, item in enumerate(included)
                        if _message_id(item) not in referenced_ids
                    ),
                    0,
                )
            included.pop(index)

        # Keep whole messages; if the bounded selection still exceeds the
        # budget, drop the lowest-priority relevant/oldest message first.
        while len(included) > 2 and sum(_message_tokens(item) for item in included) > history_budget:
            drop_lowest_priority_message()
        digest_lines = _state_digest_lines(state)
        if _is_history_recall_request(current_question):
            # For explicit conversation-recall questions, retain a compact
            # deterministic ledger from user turns even when an LLM-authored
            # digest exists. It is not legal evidence and contains no old
            # assistant prose.
            older_user_questions = [
                _compact(item.get("content"))[:180]
                for item in older
                if _message_role(item) == "user"
                and _compact(item.get("content"))
            ][-20:]
        else:
            older_user_questions = (
                []
                if (
                    isinstance((state or {}).get("conversation_digest_v2"), Mapping)
                    or _compact((state or {}).get("conversation_digest"))
                )
                else [
                _compact(item.get("content"))[:240]
                for item in older
                if _message_role(item) == "user"
                and _compact(item.get("content"))
                ][-8:]
            )
        def render_compacted() -> str:
            all_digest_lines = list(digest_lines)
            if older_user_questions:
                all_digest_lines.append(
                    "- Các câu hỏi cũ của người dùng: "
                    + " | ".join(older_user_questions)
                )
            digest = "\n".join(all_digest_lines)
            retained = _render_messages(included)
            summary_header = (
                "TÓM TẮT LLM DO LANGGRAPH QUẢN LÝ\n"
                if _compact((state or {}).get("compaction_summary_mode"))
                == "langgraph_llm"
                else "TÓM TẮT DETERMINISTIC PHẦN CŨ\n"
            )
            return (
                (
                    summary_header
                    + "MỤC NÉN HỘI THOẠI V1 — KHÔNG PHẢI CĂN CỨ PHÁP LUẬT\n"
                    + digest
                    + "\n\n"
                )
                if digest
                else ""
            ) + (
                "CÁC TIN NHẮN ĐƯỢC GIỮ NGUYÊN\n" + retained
                if retained
                else ""
            )

        prompt_block = render_compacted()
        # Reserve evidence/output before history. Never slice a message or a
        # legal unit: drop whole history messages, then old user-question
        # summaries, until the packet fits the configured token budget.
        while included and token_count(prompt_block) > history_budget:
            drop_lowest_priority_message()
            prompt_block = render_compacted()
        while older_user_questions and token_count(prompt_block) > history_budget:
            older_user_questions.pop(0)
            prompt_block = render_compacted()
        if token_count(prompt_block) > history_budget:
            digest_lines = []
            prompt_block = render_compacted()
        compacted = max(0, considered - len(included))
    history_tokens = token_count(prompt_block)
    ids = tuple(str(message.get("id") or "") for message in included)
    digest_revision = int((state or {}).get("digest_revision") or 0)
    digest_through_message_id = (
        _compact((state or {}).get("digest_through_message_id")) or None
    )
    compaction_item_checksum = (
        _checksum(
            {
                "digest_revision": digest_revision,
                "digest_through_message_id": digest_through_message_id,
                "digest": _state_digest_lines(state),
                "older_messages_compacted": compacted,
            }
        )
        if mode == "compacted"
        else None
    )
    checksum = _checksum(
        {
            "version": CONTEXT_PACKET_VERSION,
            "mode": mode,
            "ids": ids,
            "history_tokens": history_tokens,
            "state_revision": int((state or {}).get("revision") or 0),
            "digest_revision": digest_revision,
            "digest_through_message_id": digest_through_message_id,
            "compaction_item_checksum": compaction_item_checksum,
        }
    )
    return ConversationContextPacketV1(
        version=CONTEXT_PACKET_VERSION,
        history_mode=mode,
        messages_considered=considered,
        messages_included=len(included),
        history_tokens=history_tokens,
        input_token_budget=input_budget,
        history_token_budget=history_budget,
        older_messages_compacted=compacted,
        included_message_ids=ids,
        digest_revision=digest_revision,
        digest_through_message_id=digest_through_message_id,
        compaction_item_checksum=compaction_item_checksum,
        prompt_block=prompt_block,
        checksum=checksum,
    )


def document_reference_from_citation(
    citation: Mapping[str, Any],
    *,
    pinned: bool = False,
) -> dict[str, Any] | None:
    document_id = _compact(
        citation.get("document_id")
        or citation.get("doc_id")
        or citation.get("law_number")
        or citation.get("source_url")
    )
    title = _compact(
        citation.get("document_title")
        or citation.get("law_name")
        or citation.get("law_number")
    )
    if not document_id or not title:
        return None
    article_refs: list[str] = []
    for key, prefix in (
        ("article_number", "Điều"),
        ("clause_number", "Khoản"),
        ("point_number", "Điểm"),
    ):
        value = _compact(citation.get(key))
        if value:
            article_refs.append(value if _fold(value).startswith(_fold(prefix)) else f"{prefix} {value}")
    return {
        "document_id": document_id,
        "title": title,
        "law_number": _compact(citation.get("law_number")) or None,
        "source_url": _compact(citation.get("source_url") or citation.get("viewer_url")) or None,
        "article_refs": article_refs,
        "effective_status": _compact(citation.get("effective_status")) or None,
        "legal_as_of": _compact(citation.get("legal_as_of")) or None,
        "validity_sync": (
            dict(citation.get("validity_sync"))
            if isinstance(citation.get("validity_sync"), Mapping)
            else None
        ),
        "pinned": bool(pinned),
    }


def render_active_document_metadata(
    active_document: Mapping[str, Any] | None,
) -> str:
    """Render only backend-verified metadata for a document follow-up."""

    if not isinstance(active_document, Mapping):
        return ""
    lines = [
        value
        for value in (
            f"Tên văn bản: {_compact(active_document.get('title'))}"
            if _compact(active_document.get("title"))
            else "",
            f"Số hiệu: {_compact(active_document.get('law_number'))}"
            if _compact(active_document.get("law_number"))
            else "",
            f"Trạng thái hiệu lực: {_compact(active_document.get('effective_status'))}"
            if _compact(active_document.get("effective_status"))
            else "",
            f"Ngày đối chiếu: {_compact(active_document.get('legal_as_of'))}"
            if _compact(active_document.get("legal_as_of"))
            else "",
            f"Nguồn chính thức: {_compact(active_document.get('source_url'))}"
            if _compact(active_document.get("source_url"))
            else "",
        )
        if value
    ]
    validity = active_document.get("validity_sync")
    if isinstance(validity, Mapping):
        labels = {
            "status": "Trạng thái đồng bộ",
            "effective_from": "Hiệu lực từ",
            "effective_to": "Hiệu lực đến",
            "display_label": "Nhãn hiệu lực",
            "reason_code": "Mã lý do",
        }
        for key, label in labels.items():
            value = _compact(validity.get(key))
            if value:
                lines.append(f"{label}: {value}")
    return "\n".join(f"- {line}" for line in lines)


def project_related_documents(
    citations: Sequence[Mapping[str, Any]] | None,
    *,
    active_document_id: str | None = None,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in citations or ():
        reference = document_reference_from_citation(citation)
        if not reference:
            continue
        identity = _fold(reference["document_id"])
        if not identity or identity in seen:
            continue
        seen.add(identity)
        reference["active"] = bool(
            active_document_id and _fold(active_document_id) == identity
        )
        documents.append(reference)
        if len(documents) >= 3:
            break
    return documents


def resolve_explicit_document_reference(
    question: str,
    documents: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Resolve a document named in the current question from backend refs."""

    folded_question = _fold(question)
    for raw in documents or ():
        law_number = _fold(raw.get("law_number"))
        title = _fold(raw.get("title") or raw.get("document_title"))
        if law_number and law_number in folded_question:
            return dict(raw)
        if title and len(title) <= 120 and title in folded_question:
            return dict(raw)
    return None


def document_reference_identities(value: Mapping[str, Any] | None) -> set[str]:
    if not value:
        return set()
    identities: set[str] = set()
    for key in (
        "document_id",
        "doc_id",
        "law_number",
        "source_url",
        "viewer_url",
    ):
        normalized = _fold(str(value.get(key) or "").rstrip("/"))
        if normalized:
            identities.add(normalized)
    return identities


def matches_document_reference(
    candidate: Mapping[str, Any],
    active_document: Mapping[str, Any] | None,
) -> bool:
    return bool(
        document_reference_identities(candidate).intersection(
            document_reference_identities(active_document)
        )
    )


def _build_legacy_conversational_prompt(
    *,
    question: str,
    role: str,
    route: ConversationRoute,
    context_packet: ConversationContextPacketV1,
    state: Mapping[str, Any] | None = None,
    active_document: Mapping[str, Any] | None = None,
    conversation_patch_envelope: bool = False,
    system_prompt_addendum: str | None = None,
) -> str:
    """Build the one-call prompt for non-retrieval conversational turns."""

    officer = _compact(role).casefold() == "officer"
    address = "Thưa anh/chị cán bộ," if officer else "Thưa anh/chị,"
    role_guidance = (
        "Giữ giọng điệu nghiệp vụ, tôn trọng phạm vi phân công của cán bộ."
        if officer
        else "Giải thích thân thiện, rõ ràng và hướng tới hành động tiếp theo."
    )
    active_block = ""
    if active_document:
        active_block = (
            "\n\nVĂN BẢN ĐANG TRAO ĐỔI — METADATA BACKEND\n"
            f"- Tên: {_compact(active_document.get('title'))}\n"
            f"- Số hiệu: {_compact(active_document.get('law_number')) or 'không có'}\n"
            f"- Điều/khoản: {', '.join(active_document.get('article_refs') or []) or 'chưa xác định'}\n"
            "Metadata này chỉ giúp hiểu tham chiếu; không tự suy ra nội dung pháp luật chưa có nguồn."
        )
    history = context_packet.prompt_block or "(Chưa có lượt hội thoại hoàn tất trước đó.)"
    patch_contract = (
        ',"conversation_patch":{"topic_summary":"...","current_goal":"...",'
        '"topics":[],"user_facts":[{"text":"...","source_message_id":"ID từ lịch sử",'
        '"status":"user_stated"}],"open_questions":[],"referenced_turn_ids":[]}'
        if conversation_patch_envelope
        else ""
    )
    patch_rules = (
        "\nconversation_patch chỉ lưu chủ đề, mục tiêu và dữ kiện do người dùng tự nêu. "
        "Không ghi URL, citation, số hiệu văn bản, thời hạn, phí hoặc kết luận pháp luật. "
        "Mọi message ID phải lấy nguyên từ lịch sử."
        if conversation_patch_envelope
        else ""
    )
    meta_rules = ""
    if route == "chat_meta":
        meta_rules = (
            "\nĐây là lượt hội thoại/meta, không tra cứu. Nếu người dùng hỏi vừa hỏi gì, "
            "câu hỏi trước, các chủ đề hoặc yêu cầu tóm tắt, hãy nhắc lại bằng đúng "
            "các cụm từ trong các tin nhắn Người dùng ở lịch sử; không thay bằng câu "
            "chung chung và không bịa nội dung. Nếu người dùng xin gợi ý, chỉ đưa các "
            "câu hỏi gợi ý, không trả lời thay."
        )
    addendum = _compact(system_prompt_addendum or "")[:8000]
    addendum_block = (
        "\n\nHƯỚNG DẪN DIỄN ĐẠT BỔ SUNG DO ADMIN CẤU HÌNH\n"
        f"{addendum}\n"
        "Chỉ điều chỉnh cách trình bày; không thay thế căn cứ pháp luật, nguồn, "
        "bảo mật hoặc quy tắc an toàn."
        if addendum
        else ""
    )
    return (
        "Bạn là Trợ lý Pháp luật phường/xã tại Hải Phòng. "
        "Hãy giao tiếp ấm áp, chuyên nghiệp và tự nhiên.\n"
        "Phân tích âm thầm ý định, tham chiếu hội thoại và bước tiếp theo hữu ích; "
        "không xuất chuỗi suy luận, chain-of-thought hoặc thẻ <think>.\n"
        f"{role_guidance}\n"
        f"Mở đầu phù hợp bằng “{address}” nhưng không lặp lời chào dài.\n"
        "Không tạo điều luật, số hiệu, thời hạn, cơ quan, biểu mẫu hoặc URL. "
        "Nếu người dùng hỏi nội dung pháp luật cần nguồn mà turn này không có evidence, "
        "hãy đề nghị họ hỏi rõ hoặc chuyển sang tra cứu pháp luật; không kết luận từ lịch sử.\n\n"
        "LỊCH SỬ HỘI THOẠI — KHÔNG PHẢI CĂN CỨ PHÁP LUẬT\n"
        f"{history}"
        f"{active_block}\n\n"
        f"ROUTE HIỆN TẠI: {route}\n"
        f"CÂU HỎI HIỆN TẠI\n{_compact(question)}\n\n"
        f"{addendum_block}\n"
        f"{meta_rules}\n"
        "Xuất đúng một JSON object hợp lệ, không đặt trong code fence:\n"
        "{\"answer_markdown\":\"...\",\"suggested_questions\":["
        "{\"text\":\"...\",\"issue_id\":\"conversation\",\"facet\":\"next_action\"}]"
        f"{patch_contract}}}\n"
        "Tạo 2–4 câu hỏi nối tiếp ngắn, phù hợp phạm vi trợ lý pháp luật, không chứa URL."
        " Trong `answer_markdown`, chỉ dùng Markdown thuần: tuyệt đối không dùng bất kỳ "
        "thẻ HTML nào như <br>, <u>, <table> hoặc thẻ dạng <...> khác; không đặt "
        "ký tự | ở đầu dòng nếu không tạo bảng hoàn chỉnh, "
        "và phải đóng đủ mọi cặp ký hiệu **."
        f"{patch_rules}"
    )


def build_conversational_prompt(
    *,
    question: str,
    role: str,
    route: ConversationRoute,
    context_packet: ConversationContextPacketV1,
    state: Mapping[str, Any] | None = None,
    active_document: Mapping[str, Any] | None = None,
    conversation_patch_envelope: bool = False,
    system_prompt_addendum: str | None = None,
) -> str:
    """Route every conversational turn through UnifiedChatAnswerPromptV1."""

    # Local import avoids coupling the structural answer module to the
    # conversation state/orchestrator import graph.
    from api.legal_structured_answer import (
        build_unified_chat_answer_prompt,
        unified_chat_prompt_enabled,
    )

    if not unified_chat_prompt_enabled(role):
        return _build_legacy_conversational_prompt(
            question=question,
            role=role,
            route=route,
            context_packet=context_packet,
            state=state,
            active_document=active_document,
            conversation_patch_envelope=conversation_patch_envelope,
            system_prompt_addendum=system_prompt_addendum,
        )

    active_context = ""
    if active_document:
        active_context = "\n".join(
            value
            for value in (
                f"Tên: {_compact(active_document.get('title'))}",
                f"Số hiệu: {_compact(active_document.get('law_number')) or 'không có'}",
                f"Điều/khoản: {', '.join(active_document.get('article_refs') or []) or 'chưa xác định'}",
            )
            if value
        )
    return build_unified_chat_answer_prompt(
        question=question,
        role=role,
        route=str(route),
        context="",
        system_prompt_addendum=system_prompt_addendum,
        conversation_context=context_packet.prompt_block,
        active_document_context=active_context,
        conversation_is_first_turn=context_packet.messages_considered == 0,
        suggestion_limit=2,
        conversation_patch_envelope=conversation_patch_envelope,
    )
