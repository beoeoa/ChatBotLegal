"""Pure contracts and guards for the simplified legal-answer pipeline.

This module deliberately performs no retrieval and no model call.  It upgrades
the request-local decision, rewrites issue queries from bounded conversation
memory, and selects actor/facet-safe structural evidence.  Keeping these steps
pure makes the serving flag an immediate rollback boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Literal, Mapping, Sequence

from api.legal_answer_router import LegalQueryDecisionV1, facets_for_issue
from api.legal_section_grounding import LegalIssue


DECISION_VERSION = "legal-query-decision-v2"
REWRITE_VERSION = "standalone-legal-query-v1"
PIPELINE_VERSION = "legal-answer-simplified-v1"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_direct_rag_pipeline_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return the immutable serving architecture for this application release.

    The arguments remain temporarily for source compatibility with pure legacy
    helpers, but neither role nor environment can switch the public runtime
    back to a second answer pipeline. Rollback is the previous application
    image/commit.
    """

    del role, environ
    return True


def _bounded_positive_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def direct_packet_chunk_limit(
    *,
    exact_article: bool = False,
    issue_count: int = 1,
    direct_contract: bool = False,
    environ: Mapping[str, Any] | None = None,
) -> int:
    """Return the bounded number of rows sent to one model packet.

    v6r26 keeps a larger candidate pool for recall.  The answer path must not
    send every eligible candidate to a small model, however: unrelated
    lexical matches then compete with the exact legal passage.  Direct RAG
    uses a strict ten-unit ceiling; the legacy compatibility mode retains its
    historical eight/twelve defaults until that path is removed.
    """

    values = os.environ if environ is None else environ
    # The historical raw-retrieval compatibility helper keeps its old
    # eight/twelve defaults for rollback tests.  The production Direct RAG
    # contract has a stricter, unconditional ten-unit ceiling.
    default_normal = "10" if direct_contract else "8"
    default_hard = "10" if direct_contract else "12"
    maximum = 10 if direct_contract else 12
    normal_limit = _bounded_positive_int(
        values.get("LEGAL_DIRECT_PACKET_CHUNKS", default_normal),
        default=int(default_normal),
        minimum=1,
        maximum=maximum,
    )
    hard_limit = _bounded_positive_int(
        values.get("LEGAL_DIRECT_PACKET_HARD_CHUNKS", default_hard),
        default=int(default_hard),
        minimum=normal_limit,
        maximum=maximum,
    )
    return hard_limit if exact_article or int(issue_count or 1) > 1 else normal_limit


def cap_direct_packet_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    exact_article: bool = False,
    issue_count: int = 1,
    direct_contract: bool = False,
    environ: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Keep retrieval rank order while bounding model-facing evidence rows."""

    limit = direct_packet_chunk_limit(
        exact_article=exact_article,
        issue_count=issue_count,
        direct_contract=direct_contract,
        environ=environ,
    )
    # A multi-issue packet has one global ceiling. Round-robin the already
    # rank-ordered issue groups so the first issue cannot consume all twelve
    # slots and starve a later, independently asked issue.
    source_rows = [dict(row) for row in rows]
    if int(issue_count or 1) > 1:
        grouped: dict[str, list[dict[str, Any]]] = {}
        group_order: list[str] = []
        for item in source_rows:
            key = _compact(item.get("issue_id") or "__unbound__")
            if key not in grouped:
                grouped[key] = []
                group_order.append(key)
            grouped[key].append(item)
        source_rows = []
        cursor = 0
        while len(source_rows) < sum(len(values) for values in grouped.values()):
            added = False
            for key in group_order:
                values = grouped[key]
                if cursor < len(values):
                    source_rows.append(values[cursor])
                    added = True
            if not added:
                break
            cursor += 1

    selected: list[dict[str, Any]] = []
    trace: list[dict[str, str]] = []
    for item in source_rows:
        source_id = _compact(
            item.get("source_id")
            or item.get("chunk_id")
            or item.get("evidence_id")
            or ""
        )
        if len(selected) < limit:
            selected.append(item)
            continue
        trace.append(
            {
                "source_id": source_id,
                "status": "not_selected",
                "reason_code": "DIRECT_PACKET_LIMIT",
            }
        )
    return selected, trace


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _compact(value).casefold())
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def explicit_current_domain(question: str) -> str | None:
    """Return a domain only for a strong subject stated in the current turn.

    Supporting facts such as a land certificate or residence database must not
    outweigh the named legal procedure. The helper is intentionally
    conservative and is used only to prevent router/history contamination.
    """

    folded = _fold(question)
    if not folded:
        return None
    priority_markers: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "an_sinh_y_te_giao_duc",
            (
                "tro cap huu tri xa hoi",
                "tro cap nguoi cao tuoi",
                "bao tro xa hoi",
                "tre khong co nguon nuoi duong",
                "ho tro ho ngheo",
                "an sinh",
                "bao hiem y te",
                "giao duc",
            ),
        ),
        (
            "ho_tich_chung_thuc",
            (
                "dang ky lai khai sinh",
                "dang ky khai sinh",
                "dang ky ket hon",
                "dang ky khai tu",
                "trich luc ho tich",
                "chung thuc ban sao",
                "ho tich",
                "khai sinh",
                "khai tu",
            ),
        ),
        (
            "khieu_nai_to_cao_xu_phat",
            (
                "khieu nai lan dau",
                "don khieu nai",
                "don to cao",
                "quyet dinh xu phat",
                "khieu nai",
                "to cao",
            ),
        ),
        (
            "cu_tru_an_ninh",
            (
                "dang ky thuong tru",
                "dang ky tam tru",
                "thong bao luu tru",
                "xac nhan thong tin cu tru",
                "to khai ct01",
                "mau ct01",
                "bieu mau ct01",
                "ct01",
                "tam tru",
                "thuong tru",
                "cu tru",
            ),
        ),
        (
            "dat_dai_xay_dung",
            (
                "giay phep xay dung",
                "gpxd",
                "tach thua",
                "tang cho quyen su dung dat",
                "cap giay chung nhan quyen su dung dat",
                "dat dai",
                "thua dat",
                "so do",
                "so hong",
                "xay dung",
            ),
        ),
    )
    for domain, markers in priority_markers:
        if any(marker in folded for marker in markers):
            return domain
    return None


def _stable_checksum(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_ACTOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bchu tich\s+(?:uy ban nhan dan|ubnd)\s+(?:cap\s+)?phuong\b"
        ),
        "chủ tịch ubnd phường",
    ),
    (
        re.compile(
            r"\bchu tich\s+(?:uy ban nhan dan|ubnd)\s+(?:cap\s+)?xa\b"
        ),
        "chủ tịch ubnd cấp xã",
    ),
    (
        re.compile(r"\bchu tich\s+(?:uy ban nhan dan|ubnd)\s+(?:cap\s+)?huyen\b"),
        "chủ tịch ubnd cấp huyện",
    ),
    (
        re.compile(r"\bthu truong\s+co quan\s+cong an\b"),
        "thủ trưởng cơ quan công an",
    ),
    (re.compile(r"\bcan bo\s+tiep nhan\b"), "cán bộ tiếp nhận"),
    (re.compile(r"\bchu so huu\b"), "chủ sở hữu"),
    (re.compile(r"\bchu nha\b"), "chủ nhà"),
    (re.compile(r"\bnguoi khieu nai\b"), "người khiếu nại"),
    (re.compile(r"\bnguoi to cao\b"), "người tố cáo"),
)


def extract_actor_anchors(value: Any) -> tuple[str, ...]:
    """Return conservative canonical actors explicitly present in text."""

    folded = _fold(value)
    return tuple(
        dict.fromkeys(
            label for pattern, label in _ACTOR_PATTERNS if pattern.search(folded)
        )
    )


def _actor_markers(actor: str) -> tuple[str, ...]:
    folded = _fold(actor)
    if folded == "chu tich ubnd phuong":
        return (
            "chu tich ubnd phuong",
            "chu tich uy ban nhan dan phuong",
            "chu tich ubnd cap xa",
            "chu tich uy ban nhan dan cap xa",
        )
    if folded == "chu tich ubnd cap xa":
        return (
            "chu tich ubnd cap xa",
            "chu tich uy ban nhan dan cap xa",
            "chu tich ubnd phuong",
            "chu tich uy ban nhan dan phuong",
            "chu tich ubnd xa",
            "chu tich uy ban nhan dan xa",
        )
    return (folded,)


def evidence_matches_actor_anchors(
    source: Mapping[str, Any], actor_anchors: Sequence[str]
) -> bool:
    """Fail closed when an explicit actor conflicts with the source unit."""

    if not actor_anchors:
        return True
    body = _fold(
        " ".join(
            _compact(source.get(key))
            for key in (
                "evidence_capsule",
                "parent_context",
                "clean_content",
                "content",
                "article_title",
                "chunk_heading",
                "issuing_agency",
                "authority_label",
            )
        )
    )
    material_actors = [
        actor
        for actor in actor_anchors
        if _fold(actor) not in {"can bo tiep nhan", "nguoi khieu nai", "nguoi to cao"}
    ]
    if not material_actors:
        return True
    return all(any(marker in body for marker in _actor_markers(actor)) for actor in material_actors)


def _extract_legal_objects(value: Any) -> tuple[str, ...]:
    text = _compact(value)
    folded = _fold(text)
    objects: list[str] = []
    for marker, label in (
        ("quyet dinh xu phat", "quyết định xử phạt"),
        ("quyet dinh hanh chinh", "quyết định hành chính"),
        ("dang ky lai khai sinh", "đăng ký lại khai sinh"),
        ("tro cap huu tri xa hoi", "trợ cấp hưu trí xã hội"),
        ("dang ky tam tru", "đăng ký tạm trú"),
        ("khieu nai lan dau", "khiếu nại lần đầu"),
        ("to cao", "tố cáo"),
    ):
        if marker in folded:
            objects.append(label)
    return tuple(dict.fromkeys(objects))


def _extract_locations(value: Any) -> tuple[str, ...]:
    text = _compact(value)
    folded = _fold(text)
    locations: list[str] = []
    if "hai phong" in folded:
        locations.append("Hải Phòng")
    for match in re.finditer(
        r"\b(?:phường|xã|quận|huyện|thành phố)\s+[A-ZÀ-ỸĐ][\wÀ-ỹĐđ.-]*(?:\s+[A-ZÀ-ỸĐ][\wÀ-ỹĐđ.-]*){0,3}",
        text,
        flags=re.UNICODE,
    ):
        candidate = _compact(match.group(0)).rstrip(".,;:?!")
        if candidate and candidate not in locations:
            locations.append(candidate)
    return tuple(locations[:4])


def is_simplified_legal_pipeline_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    if is_direct_rag_pipeline_enabled(role, environ=values):
        return True
    if (
        str(values.get("LEGAL_ANSWER_SIMPLIFIED_PIPELINE_V1_ENABLED", "false"))
        .strip()
        .casefold()
        not in _TRUE_VALUES
    ):
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("LEGAL_ANSWER_SIMPLIFIED_PIPELINE_V1_ROLES", "")
        ).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def is_answer_d_v1_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the Phase-D structured answer path is enabled.

    Phase D is deliberately independent from the older simplified Markdown
    route.  When enabled, the caller must use the shared structured answer
    contract for the selected roles.  The flag is default-off so an operator
    can complete credential and shadow gates without changing the live answer
    path.  It never changes the router model or retrieval release.
    """

    values = os.environ if environ is None else environ
    enabled = str(values.get("LEGAL_ANSWER_D_V1_ENABLED", "false"))
    if enabled.strip().casefold() not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("LEGAL_ANSWER_D_V1_ROLES", "citizen,officer,admin")
        ).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def is_raw_retrieval_direct_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Enable the raw-question -> v6r26 -> LLM serving path for a role.

    This switch deliberately controls only legal retrieval. Conversation-level
    meta/document routing, account authorization and legal validity remain
    outside this contract. An empty role allow-list keeps the path disabled so
    rollback does not require changing the active v6r26 pointer.
    """

    values = os.environ if environ is None else environ
    if is_direct_rag_pipeline_enabled(role, environ=values):
        return True
    if (
        str(values.get("LEGAL_RAW_RETRIEVAL_DIRECT_V1_ENABLED", "false"))
        .strip()
        .casefold()
        not in _TRUE_VALUES
    ):
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("LEGAL_RAW_RETRIEVAL_DIRECT_V1_ROLES", "")
        ).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


@dataclass(frozen=True)
class LegalQueryDecisionV2:
    version: str
    canonical_domain: str
    domain_source: str
    temporal_scope: str
    legal_as_of: str | None
    temporal_reason: str
    answer_route: str
    issues: tuple[LegalIssue, ...]
    required_facets: tuple[str, ...]
    actor_anchors: tuple[str, ...]
    issuing_authority_anchors: tuple[str, ...]
    legal_object_anchors: tuple[str, ...]
    location_anchors: tuple[str, ...]
    procedure_candidate: str | None
    identity_status: Literal["confirmed", "ambiguous", "unsupported"]
    form_status: Literal["resolved", "source_gap", "not_requested"]
    clarifying_questions: tuple[str, ...]
    memory_inheritance: Mapping[str, tuple[str, ...]]
    original_question: str
    decision_checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "canonical_domain": self.canonical_domain,
            "domain_source": self.domain_source,
            "temporal_scope": self.temporal_scope,
            "legal_as_of": self.legal_as_of,
            "temporal_reason": self.temporal_reason,
            "answer_route": self.answer_route,
            "required_facets": list(self.required_facets),
            "actor_anchors": list(self.actor_anchors),
            "issuing_authority_anchors": list(self.issuing_authority_anchors),
            "legal_object_anchors": list(self.legal_object_anchors),
            "location_anchors": list(self.location_anchors),
            "procedure_candidate": self.procedure_candidate,
            "identity_status": self.identity_status,
            "form_status": self.form_status,
            "clarifying_questions": list(self.clarifying_questions),
            "issue_count": len(self.issues),
            "memory_inheritance": {
                key: list(values) for key, values in self.memory_inheritance.items()
            },
            "decision_checksum": self.decision_checksum,
            "issues": [
                {
                    "issue_id": issue.issue_id,
                    "domain": issue.domain,
                    "intent": issue.intent,
                }
                for issue in self.issues
            ],
        }


@dataclass(frozen=True)
class StandaloneLegalQueryV1:
    version: str
    issue_id: str
    original_query: str
    standalone_query: str
    actor_anchors: tuple[str, ...]
    procedure_anchor: str | None
    legal_object_anchors: tuple[str, ...]
    location_anchors: tuple[str, ...]
    inherited_turn_ids: tuple[str, ...]
    rewrite_applied: bool
    rewrite_reason: str
    rewrite_checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "issue_id": self.issue_id,
            "original_query": self.original_query,
            "standalone_query": self.standalone_query,
            "actor_anchors": list(self.actor_anchors),
            "procedure_anchor": self.procedure_anchor,
            "legal_object_anchors": list(self.legal_object_anchors),
            "location_anchors": list(self.location_anchors),
            "inherited_turn_ids": list(self.inherited_turn_ids),
            "rewrite_applied": self.rewrite_applied,
            "rewrite_reason": self.rewrite_reason,
            "rewrite_checksum": self.rewrite_checksum,
        }


def _bounded_memory(
    messages: Sequence[Mapping[str, Any]], *, max_user_turns: int = 3
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    user_turns = [
        message
        for message in messages
        if str(message.get("role") or message.get("sender_role") or "").casefold()
        == "user"
        and _compact(message.get("content"))
    ][-max_user_turns:]
    completed_metadata = [
        message
        for message in messages
        if str(message.get("role") or message.get("sender_role") or "").casefold()
        == "assistant"
        and str(message.get("status") or "complete").casefold() == "complete"
    ][-max_user_turns:]
    return user_turns, completed_metadata


def _metadata_procedure(messages: Sequence[Mapping[str, Any]]) -> str | None:
    for message in reversed(messages):
        detail = message.get("procedure_detail")
        if isinstance(detail, Mapping):
            candidate = _compact(detail.get("procedure_id") or detail.get("id"))
            if candidate:
                return candidate
    return None


def _metadata_domain(messages: Sequence[Mapping[str, Any]]) -> str | None:
    for message in reversed(messages):
        candidate = _compact(message.get("canonical_domain"))
        if candidate and _fold(candidate) not in {
            "unknown",
            "administrative",
            "khong xac dinh",
            "chua xac dinh",
        }:
            return candidate
    return None


def _is_procedure_followup(question: str) -> bool:
    """Return true when the current turn names only a facet of prior work."""

    folded = _fold(question)
    if len(folded) > 220 or "chuyen sang" in folded:
        return False
    return any(
        marker in folded
        for marker in (
            "ho so cu the",
            "ho so gom",
            "con ho so",
            "con thoi han",
            "thoi han giai quyet",
            "con le phi",
            "bieu mau ct01",
            "mau ct01",
            "ct01",
            "trong truong hop nay",
            "thi sao",
            "vua neu",
            "vua dan",
        )
    )


def _metadata_memory_state(messages: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for message in reversed(messages):
        state = message.get("memory_state")
        if isinstance(state, Mapping):
            return state
    return {}


def build_legal_query_decision_v2(
    decision: LegalQueryDecisionV1,
    *,
    original_question: str,
    history_messages: Sequence[Mapping[str, Any]] = (),
    identity_status: str | None = None,
    form_status: str | None = None,
) -> LegalQueryDecisionV2:
    """Upgrade one authoritative V1 decision without reclassifying the query."""

    user_turns, metadata_turns = _bounded_memory(history_messages)
    memory_state = _metadata_memory_state(metadata_turns)
    current_explicit_domain = explicit_current_domain(original_question)
    memory_domain = _metadata_domain(metadata_turns)
    # The current turn is the only authoritative signal for a topic switch.
    # Conversation state may fill omitted fields in a follow-up, but it must
    # never carry the previous actor/object/procedure into a newly named
    # citizen topic. Officer account scope remains hard because an account
    # decision has ``domain_source=account`` and is handled below.
    topic_transition = bool(
        current_explicit_domain
        and memory_domain
        and current_explicit_domain != memory_domain
        and decision.domain_source != "account"
    )
    current_actors = extract_actor_anchors(original_question)
    current_objects = _extract_legal_objects(original_question)
    current_locations = _extract_locations(original_question)
    inherited: dict[str, tuple[str, ...]] = {}
    inherited_turn_ids = tuple(
        dict.fromkeys(
            [
                *[
                    _compact(turn.get("id"))
                    for turn in user_turns
                    if _compact(turn.get("id"))
                ],
                *[
                    _compact(turn_id)
                    for turn_id in memory_state.get("source_turn_ids") or []
                    if _compact(turn_id)
                ],
            ]
        )
    )

    actor_anchors = current_actors
    if not actor_anchors and not topic_transition:
        actor_anchors = tuple(
            _compact(item)
            for item in memory_state.get("actors") or []
            if _compact(item)
        )
        if actor_anchors:
            inherited["actor_anchors"] = inherited_turn_ids
    if not actor_anchors and not topic_transition:
        for turn in reversed(user_turns):
            actor_anchors = extract_actor_anchors(turn.get("content"))
            if actor_anchors:
                inherited["actor_anchors"] = inherited_turn_ids
                break

    legal_objects = current_objects
    if not legal_objects and not topic_transition:
        legal_objects = tuple(
            _compact(item)
            for item in memory_state.get("legal_objects") or []
            if _compact(item)
        )
        if legal_objects:
            inherited["legal_object_anchors"] = inherited_turn_ids
    if not legal_objects and not topic_transition:
        for turn in reversed(user_turns):
            legal_objects = _extract_legal_objects(turn.get("content"))
            if legal_objects:
                inherited["legal_object_anchors"] = inherited_turn_ids
                break

    location_anchors = current_locations
    if not location_anchors:
        location_anchors = tuple(
            _compact(item)
            for item in memory_state.get("locations") or []
            if _compact(item)
        )
        if location_anchors:
            inherited["location_anchors"] = inherited_turn_ids
    if not location_anchors:
        for turn in reversed(user_turns):
            location_anchors = _extract_locations(turn.get("content"))
            if location_anchors:
                inherited["location_anchors"] = inherited_turn_ids
                break

    procedure_candidate = decision.procedure_candidate
    memory_procedure = _metadata_procedure(metadata_turns)
    domain_compatible = bool(
        memory_procedure
        and (
            decision.canonical_domain in {"", "unknown", "administrative"}
            or not memory_domain
            or memory_domain == decision.canonical_domain
        )
    )
    if not topic_transition and domain_compatible and (
        not procedure_candidate or _is_procedure_followup(original_question)
    ):
        procedure_candidate = memory_procedure
        inherited["procedure_candidate"] = inherited_turn_ids

    canonical_domain = decision.canonical_domain
    domain_source = decision.domain_source
    if current_explicit_domain and domain_source != "account":
        canonical_domain = current_explicit_domain
        domain_source = "request"
    elif canonical_domain in {"", "unknown", "administrative"}:
        inherited_domain = _metadata_domain(metadata_turns)
        if inherited_domain:
            canonical_domain = inherited_domain
            domain_source = "memory"
            inherited["canonical_domain"] = inherited_turn_ids

    required_facets = tuple(
        dict.fromkeys(
            [
                *decision.facets,
                *(
                    facet
                    for issue in decision.issues
                    for facet in facets_for_issue(decision, issue)
                ),
            ]
        )
    )
    resolved_identity = str(identity_status or "").strip().casefold()
    if resolved_identity not in {"confirmed", "ambiguous", "unsupported"}:
        resolved_identity = "confirmed" if procedure_candidate else "unsupported"
    resolved_form = str(form_status or "").strip().casefold()
    if resolved_form not in {"resolved", "source_gap", "not_requested"}:
        resolved_form = "not_requested"

    checksum_payload = {
        "base_version": decision.version,
        "canonical_domain": canonical_domain,
        "temporal_scope": decision.temporal_scope,
        "legal_as_of": decision.legal_as_of,
        "answer_route": decision.answer_route,
        "issues": [issue.issue_id for issue in decision.issues],
        "required_facets": required_facets,
        "actor_anchors": actor_anchors,
        "legal_object_anchors": legal_objects,
        "location_anchors": location_anchors,
        "procedure_candidate": procedure_candidate,
        "identity_status": resolved_identity,
        "form_status": resolved_form,
        "memory_inheritance": inherited,
    }
    return LegalQueryDecisionV2(
        version=DECISION_VERSION,
        canonical_domain=canonical_domain,
        domain_source=domain_source,
        temporal_scope=decision.temporal_scope,
        legal_as_of=decision.legal_as_of,
        temporal_reason=decision.temporal_reason,
        answer_route=decision.answer_route,
        issues=decision.issues,
        required_facets=required_facets,
        actor_anchors=actor_anchors,
        issuing_authority_anchors=actor_anchors,
        legal_object_anchors=legal_objects,
        location_anchors=location_anchors,
        procedure_candidate=procedure_candidate,
        identity_status=resolved_identity,  # type: ignore[arg-type]
        form_status=resolved_form,  # type: ignore[arg-type]
        clarifying_questions=decision.clarifying_questions,
        memory_inheritance=inherited,
        original_question=_compact(original_question),
        decision_checksum=_stable_checksum(checksum_payload),
    )


def build_standalone_legal_queries(
    decision: LegalQueryDecisionV2,
) -> tuple[StandaloneLegalQueryV1, ...]:
    """Run a deterministic standalone rewrite for every planned issue."""

    inherited_ids = tuple(
        dict.fromkeys(
            turn_id
            for values in decision.memory_inheritance.values()
            for turn_id in values
        )
    )
    rewritten: list[StandaloneLegalQueryV1] = []
    for issue in decision.issues:
        original = _compact(issue.query_text or decision.original_question)
        additions: list[str] = []
        folded_original = _fold(original)
        for actor in decision.actor_anchors:
            if not any(marker in folded_original for marker in _actor_markers(actor)):
                additions.append(f"Chủ thể: {actor}")
        if (
            decision.procedure_candidate
            and _fold(decision.procedure_candidate) not in folded_original
        ):
            additions.append(f"Thủ tục: {decision.procedure_candidate}")
        for legal_object in decision.legal_object_anchors:
            if _fold(legal_object) not in folded_original:
                additions.append(f"Đối tượng: {legal_object}")
        for location in decision.location_anchors:
            if _fold(location) not in folded_original:
                additions.append(f"Địa bàn: {location}")
        standalone = original
        if additions:
            standalone = f"{original}. " + ". ".join(additions)
        rewrite_applied = standalone != original
        reason = (
            "memory_anchors_added"
            if rewrite_applied and inherited_ids
            else "decision_anchors_added"
            if rewrite_applied
            else "already_standalone"
        )
        checksum_payload = {
            "version": REWRITE_VERSION,
            "decision_checksum": decision.decision_checksum,
            "issue_id": issue.issue_id,
            "original_query": original,
            "standalone_query": standalone,
            "inherited_turn_ids": inherited_ids,
        }
        rewritten.append(
            StandaloneLegalQueryV1(
                version=REWRITE_VERSION,
                issue_id=issue.issue_id,
                original_query=original,
                standalone_query=standalone,
                actor_anchors=decision.actor_anchors,
                procedure_anchor=decision.procedure_candidate,
                legal_object_anchors=decision.legal_object_anchors,
                location_anchors=decision.location_anchors,
                inherited_turn_ids=inherited_ids,
                rewrite_applied=rewrite_applied,
                rewrite_reason=reason,
                rewrite_checksum=_stable_checksum(checksum_payload),
            )
        )
    return tuple(rewritten)


def prepare_direct_retrieval_chunks(
    decision: LegalQueryDecisionV2,
    *,
    issue_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Forward legally eligible retrieval rows without a second relevance gate.

    Retrieval rank is authoritative on the simplified path.  This helper only
    enforces the request/issue boundary, deduplicates stable source IDs and
    exposes the complete hydrated structural unit when retrieval supplied one.
    Actor, authority and facet labels are prompt context, never rejection
    criteria here.
    """

    issue = next((item for item in decision.issues if item.issue_id == issue_id), None)
    if issue is None:
        return [], [
            {
                "source_id": "",
                "status": "rejected",
                "reason_code": "ISSUE_BINDING_MISMATCH",
            }
        ]

    selected: list[dict[str, Any]] = []
    trace: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        row = dict(raw)
        source_id = _compact(
            row.get("source_id")
            or row.get("chunk_id")
            or row.get("evidence_id")
            or f"candidate-{index + 1}"
        )
        if str(row.get("issue_id") or issue_id) != issue_id:
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "ISSUE_BINDING_MISMATCH",
                }
            )
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        has_hydrated_structural_unit = any(
            str(row.get(field) or "").strip()
            for field in (
                "evidence_capsule",
                "exact_article_assembled_content",
                "parent_context",
            )
        )
        capsule = str(
            row.get("evidence_capsule")
            or row.get("exact_article_assembled_content")
            or row.get("parent_context")
            or row.get("clean_content")
            or row.get("content")
            or ""
        ).strip()
        if not capsule:
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "EMPTY_RETRIEVAL_CHUNK",
                }
            )
            continue
        row["source_id"] = source_id
        row["evidence_capsule"] = capsule
        row.setdefault(
            "structural_unit_status",
            "complete_structural_unit"
            if has_hydrated_structural_unit
            else "retrieved_chunk",
        )
        row["actor_anchors"] = list(decision.actor_anchors)
        row["issuing_authority_anchors"] = list(
            decision.issuing_authority_anchors
        )
        row["required_facets"] = list(decision.required_facets)
        selected.append(row)
        trace.append(
            {
                "source_id": source_id,
                "status": "accepted",
                "reason_code": "DIRECT_RETRIEVAL_CONTEXT",
            }
        )
    return selected, trace


# Rollback/test compatibility for callers created before the direct-chunk
# contract removed EvidencePacketV2 from simplified serving.
prepare_direct_retrieval_evidence = prepare_direct_retrieval_chunks


def select_actor_facet_evidence(
    decision: LegalQueryDecisionV2,
    *,
    issue_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Select legally eligible rows by explicit actor and requested facet.

    Legal eligibility is intentionally assumed to have run before this helper.
    The helper never promotes a rejected/expired source and never crops its
    structural capsule.
    """

    issue = next((item for item in decision.issues if item.issue_id == issue_id), None)
    if issue is None:
        return [], [
            {
                "source_id": "",
                "status": "rejected",
                "reason_code": "ISSUE_BINDING_MISMATCH",
            }
        ]
    required_facets = set(facets_for_issue(_decision_v1_adapter(decision), issue))
    selected: list[dict[str, Any]] = []
    trace: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        row = dict(raw)
        source_id = _compact(
            row.get("source_id")
            or row.get("chunk_id")
            or row.get("evidence_id")
            or f"candidate-{index + 1}"
        )
        if str(row.get("issue_id") or issue_id) != issue_id:
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "ISSUE_BINDING_MISMATCH",
                }
            )
            continue
        if not evidence_matches_actor_anchors(row, decision.actor_anchors):
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "ACTOR_MISMATCH",
                }
            )
            continue
        supported = {
            str(value)
            for value in row.get("supported_facets") or ()
            if str(value).strip()
        }
        if supported and required_facets and not supported.intersection(required_facets):
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "FACET_UNSUPPORTED",
                }
            )
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        # Preserve the verified structural unit byte-for-byte apart from outer
        # whitespace.  Newlines often separate an Article lead-in from its
        # clauses and must not be collapsed by query normalization.
        has_hydrated_structural_unit = any(
            str(row.get(field) or "").strip()
            for field in (
                "evidence_capsule",
                "exact_article_assembled_content",
                "parent_context",
            )
        )
        capsule = str(
            row.get("evidence_capsule")
            or row.get("exact_article_assembled_content")
            or row.get("parent_context")
            or row.get("clean_content")
            or row.get("content")
            or ""
        ).strip()
        if not capsule:
            trace.append(
                {
                    "source_id": source_id,
                    "status": "rejected",
                    "reason_code": "STRUCTURAL_UNIT_INCOMPLETE",
                }
            )
            continue
        row["source_id"] = source_id
        row["evidence_capsule"] = capsule
        row.setdefault(
            "structural_unit_status",
            "complete_structural_unit"
            if has_hydrated_structural_unit
            else "chunk_only",
        )
        row["actor_anchors"] = list(decision.actor_anchors)
        row["issuing_authority_anchors"] = list(
            decision.issuing_authority_anchors
        )
        row["required_facets"] = sorted(required_facets)
        selected.append(row)
        trace.append(
            {
                "source_id": source_id,
                "status": "accepted",
                "reason_code": "ACTOR_FACET_MATCH",
            }
        )
    def rank_key(item: Mapping[str, Any], uncovered: set[str]) -> tuple[float, float]:
        supported = set(item.get("supported_facets") or ())
        return (
            float(len(supported.intersection(uncovered))),
            float(item.get("score") or item.get("rerank_score") or 0.0),
        )

    # Put one best row for every still-uncovered facet before diversity rows.
    # The packet builder can then stop at its unit boundary without a long
    # first facet starving a later authority/deadline answer.
    ordered: list[dict[str, Any]] = []
    remaining = list(selected)
    uncovered = set(required_facets)
    while remaining and uncovered:
        best = max(remaining, key=lambda item: rank_key(item, uncovered))
        if rank_key(best, uncovered)[0] <= 0:
            break
        remaining.remove(best)
        ordered.append(best)
        uncovered.difference_update(set(best.get("supported_facets") or ()))
    remaining.sort(
        key=lambda item: rank_key(item, required_facets),
        reverse=True,
    )
    ordered.extend(remaining)
    return ordered, trace


def _decision_v1_adapter(decision: LegalQueryDecisionV2) -> LegalQueryDecisionV1:
    """Compatibility adapter for existing facet query helpers only."""

    return LegalQueryDecisionV1(
        version="legal-query-decision-v1",
        canonical_domain=decision.canonical_domain,
        domain_source=(
            decision.domain_source
            if decision.domain_source in {"account", "request", "classifier"}
            else "classifier"
        ),  # type: ignore[arg-type]
        temporal_scope=decision.temporal_scope,  # type: ignore[arg-type]
        legal_as_of=decision.legal_as_of,
        temporal_reason=decision.temporal_reason,
        answer_route=decision.answer_route,  # type: ignore[arg-type]
        facets=decision.required_facets,
        issues=decision.issues,
        procedure_candidate=decision.procedure_candidate,
        clarifying_questions=decision.clarifying_questions,
    )


def decision_v1_adapter(decision: LegalQueryDecisionV2) -> LegalQueryDecisionV1:
    return _decision_v1_adapter(decision)
