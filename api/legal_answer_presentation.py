"""Backend-owned projection for the Feature 018 legal answer card.

The adapter intentionally does not parse form IDs, URLs, legal citations or
procedure identities from generated prose.  Those values are copied only from
the structured fields that have already passed the server-side legal gates.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


PRESENTATION_VERSION = "legal-answer-v1"

AnswerStatus = Literal[
    "grounded",
    "partial_grounded",
    "broad_grounded",
    "clarifying",
    "source_gap",
    "provider_error",
]
AnswerRoute = Literal[
    "exact_article",
    "procedure_form",
    "general_legal",
    "historical",
]


class LegalAnswerPresentationSections(BaseModel):
    """The single stable section order rendered for every legal answer."""

    model_config = ConfigDict(extra="forbid")

    short_answer: str | None = None
    actions: list[str] = Field(default_factory=list)
    dossier: list[str] = Field(default_factory=list)
    procedure: dict[str, Any] | None = None
    recommended_forms: list[dict[str, Any]] = Field(default_factory=list)
    legal_bases: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)


class LegalAnswerPresentationV1(BaseModel):
    """Versioned public projection consumed by the unified answer renderer."""

    model_config = ConfigDict(extra="forbid")

    presentation_version: Literal["legal-answer-v1"] = PRESENTATION_VERSION
    answer_status: AnswerStatus
    answer_route: AnswerRoute
    legal_as_of: date | None = None
    pipeline_version: str | None = None
    data_release_id: str | None = None
    index_fingerprint: str | None = None
    validity_snapshot: str | None = None
    evidence_count: int = Field(0, ge=0)
    verification_label: str | None = None
    historical_label: str | None = None
    sections: LegalAnswerPresentationSections

    @model_validator(mode="after")
    def validate_labels(self) -> "LegalAnswerPresentationV1":
        if self.evidence_count == 0 and self.verification_label is not None:
            raise ValueError("verification label requires eligible evidence")
        if self.answer_route == "historical":
            if self.legal_as_of is None:
                raise ValueError("historical answers require legal_as_of")
            if not self.historical_label:
                raise ValueError("historical answers require a plain historical label")
        elif self.historical_label is not None:
            raise ValueError("historical label is only valid for historical answers")
        return self


def _mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="python")
    if isinstance(payload, Mapping):
        return dict(payload)
    raise TypeError("legal answer payload must be a mapping or Pydantic model")


def _text(value: Any) -> str | None:
    rendered = str(value or "").strip()
    return rendered or None


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _structured_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _normalize_item_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^\s*(?:[-*•]\s*|\d+[.)]\s*)", "", text).strip()
    return " ".join(text.casefold().split())


def _append_text(target: list[str], value: Any) -> None:
    item = _text(value)
    if not item:
        return
    # Strip raw markdown (**, ###, etc) to prevent leakage
    item = re.sub(r"#{1,6}\s*", "", item)
    item = re.sub(r"\*\*([^*]+)\*\*", r"\1", item)
    item = re.sub(r"__([^>]+)__", r"\1", item)
    item = item.strip()
    
    norm = _normalize_item_text(item)
    if not norm:
        return
    if not any(_normalize_item_text(existing) == norm for existing in target):
        target.append(item)


def _trace_value(payload: Mapping[str, Any], key: str) -> Any:
    direct = payload.get(key)
    if direct not in (None, ""):
        return direct
    trace = payload.get("rag_trace")
    if isinstance(trace, Mapping):
        return trace.get(key)
    return None


def _answer_route(payload: Mapping[str, Any]) -> AnswerRoute:
    candidate = _text(_trace_value(payload, "answer_route"))
    if candidate in {"exact_article", "procedure_form", "general_legal", "historical"}:
        return candidate  # type: ignore[return-value]
    if payload.get("procedure_detail") or payload.get("recommended_forms"):
        return "procedure_form"
    return "general_legal"


def _procedure(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    source = payload.get("procedure_detail")
    if not isinstance(source, Mapping):
        return None
    # Forms have their own backend-owned slot.  Dropping form-like keys here
    # prevents a legacy nested value from bypassing the canonical form set.
    excluded = {"forms", "recommended_forms", "form_ids"}
    return deepcopy({key: value for key, value in source.items() if key not in excluded})


def _section_projection(
    payload: Mapping[str, Any],
    *,
    actions: list[str],
    dossier: list[str],
    caveats: list[str],
    clarifying_questions: list[str],
) -> list[dict[str, Any]]:
    section_citations: list[dict[str, Any]] = []
    raw_sections = payload.get("answer_sections")
    if not isinstance(raw_sections, Sequence) or isinstance(
        raw_sections, (str, bytes, bytearray)
    ):
        return section_citations

    for raw in raw_sections:
        if isinstance(raw, BaseModel):
            section = raw.model_dump(mode="python")
        elif isinstance(raw, Mapping):
            section = dict(raw)
        else:
            continue
        status = _text(section.get("status"))
        facet = _text(section.get("facet"))
        answer = _text(section.get("answer"))
        claim_types = {
            str(item)
            for item in (section.get("claim_types") or [])
            if isinstance(item, str)
        }

        if status == "sufficiently_evidenced":
            if facet == "documents":
                _append_text(dossier, answer)
            if "next_action" in claim_types:
                _append_text(actions, answer)
            section_citations.extend(_structured_list(section.get("citations")))
        else:
            _append_text(caveats, section.get("limitation"))
            if status == "partially_evidenced":
                _append_text(actions, section.get("guidance"))
        _append_text(clarifying_questions, section.get("clarifying_question"))
    return section_citations


def _deduplicate_structured(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def project_legal_answer_presentation(payload: Any) -> LegalAnswerPresentationV1:
    """Project an existing Ask-style response into the V1 answer-card schema.

    The function is backward compatible with legacy dictionaries and current
    ``AskResponse`` models.  It is deliberately a pure projection: no legal
    lookup, inference, URL extraction or provider call occurs here.
    """

    source = _mapping(payload)
    procedure = _procedure(source)
    actions: list[str] = []
    dossier: list[str] = []
    caveats: list[str] = []
    clarifying_questions: list[str] = []

    if procedure:
        for item in procedure.get("steps") or []:
            _append_text(actions, item)
        for item in procedure.get("documents_required") or []:
            _append_text(dossier, item)

    section_citations = _section_projection(
        source,
        actions=actions,
        dossier=dossier,
        caveats=caveats,
        clarifying_questions=clarifying_questions,
    )
    from api.legal_answer_quality import vietnamese_section_names

    coverage_warning = source.get("coverage_warning")
    if coverage_warning:
        _append_text(caveats, coverage_warning)
    elif source.get("source_gap"):
        gaps = vietnamese_section_names(source.get("source_gap") or [])
        if gaps:
            _append_text(caveats, f"Thông tin chưa xác minh được từ nguồn hiện có: {', '.join(gaps)}.")
    for item in source.get("clarifying_questions") or []:
        _append_text(clarifying_questions, item)

    legal_bases = _structured_list(source.get("citations"))
    if not legal_bases:
        legal_bases = section_citations
    legal_bases = _deduplicate_structured(legal_bases)
    recommended_forms = _structured_list(source.get("recommended_forms"))

    explicit_evidence_count = source.get("evidence_count")
    evidence_count = (
        max(0, int(explicit_evidence_count))
        if explicit_evidence_count not in (None, "")
        else len(legal_bases)
    )
    route = _answer_route(source)
    legal_as_of = _date(source.get("legal_as_of"))
    historical_label = None
    if route == "historical" and legal_as_of is not None:
        historical_label = (
            f"Thông tin lịch sử — áp dụng tại ngày {legal_as_of.strftime('%d/%m/%Y')}"
        )
        _append_text(caveats, historical_label)

    return LegalAnswerPresentationV1(
        answer_status=source.get("answer_status") or "source_gap",
        answer_route=route,
        legal_as_of=legal_as_of,
        pipeline_version=_text(_trace_value(source, "pipeline_version")),
        data_release_id=_text(_trace_value(source, "data_release_id")),
        index_fingerprint=_text(_trace_value(source, "index_fingerprint")),
        validity_snapshot=_text(_trace_value(source, "validity_snapshot")),
        evidence_count=evidence_count,
        verification_label=(
            "Đã xác minh từ nguồn pháp lý" if evidence_count > 0 else None
        ),
        historical_label=historical_label,
        sections=LegalAnswerPresentationSections(
            short_answer=_text(source.get("answer")),
            actions=actions,
            dossier=dossier,
            procedure=procedure,
            recommended_forms=recommended_forms,
            legal_bases=legal_bases,
            caveats=caveats,
            clarifying_questions=clarifying_questions,
        ),
    )
