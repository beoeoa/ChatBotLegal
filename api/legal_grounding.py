"""Deterministic validation of legal references against retrieved evidence.

This module does not infer law.  It only checks whether identifiers, document
numbers, article/document pairs and numeric legal measures are present in the
retrieval packet that was already filtered for applicability and effectivity.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable


_INTERNAL_CITATION_RE = re.compile(r"legal\s*:\s*(\d+)", re.IGNORECASE)
_LAW_NUMBER_RE = re.compile(
    r"\b\d{1,4}/\d{4}/[A-ZÀ-ỸĐ0-9.-]+(?:-[A-ZÀ-ỸĐ0-9.-]+)*\b",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"\bĐiều\s+(\d+[a-z]?)\b", re.IGNORECASE)
_MEASURE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:phút|giờ|ngày|tháng|năm|đồng|triệu|tỷ|%)\b",
    re.IGNORECASE,
)
_DOCUMENT_NAME_RE = re.compile(
    r"\b(?:Luật|Bộ luật|Nghị định|Thông tư(?: liên tịch)?|Nghị quyết|"
    r"Quyết định|Chỉ thị)\s+[^.;,\n]+",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;\n])\s+")


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^\w%/.-]+", " ", text)).strip()


def _source_id(source: dict) -> str:
    raw = source.get("chunk_id") or source.get("id") or ""
    return str(raw).split(":", 1)[-1]


def _article_token(value: object) -> str:
    match = re.search(r"\d+[a-z]?", str(value or ""), re.IGNORECASE)
    return match.group(0).casefold() if match else ""


def _source_text(source: dict) -> str:
    return " ".join(
        str(source.get(key) or "")
        for key in (
            "law_number",
            "document_title",
            "document_type",
            "issuing_agency",
            "article_number",
            "article_title",
            "chunk_heading",
            "content",
        )
    )


@dataclass(frozen=True)
class GroundingValidation:
    status: str
    invalid_internal_ids: tuple[str, ...] = ()
    unsupported_references: tuple[str, ...] = ()
    matched_source_ids: tuple[str, ...] = ()
    has_explicit_reference: bool = False

    @property
    def requires_legal_repair(self) -> bool:
        return self.status == "ungrounded"


def _matching_sources_for_law(law_number: str, evidence: Iterable[dict]) -> list[dict]:
    folded = _fold(law_number)
    return [
        source
        for source in evidence
        if folded
        and (
            folded == _fold(source.get("law_number"))
            or folded in _fold(_source_text(source))
        )
    ]


def validate_legal_references(answer: str, evidence: list[dict]) -> GroundingValidation:
    """Validate observable legal references without guessing missing metadata."""
    if not evidence:
        return GroundingValidation(status="insufficient_evidence")

    allowed_internal_ids = {_source_id(source) for source in evidence if _source_id(source)}
    cited_internal_ids = tuple(dict.fromkeys(_INTERNAL_CITATION_RE.findall(answer or "")))
    invalid_internal_ids = tuple(
        citation for citation in cited_internal_ids if citation not in allowed_internal_ids
    )

    unsupported: list[str] = []
    matched_ids: set[str] = {
        citation for citation in cited_internal_ids if citation in allowed_internal_ids
    }
    law_numbers = tuple(dict.fromkeys(_LAW_NUMBER_RE.findall(answer or "")))
    for law_number in law_numbers:
        matches = _matching_sources_for_law(law_number, evidence)
        if not matches:
            unsupported.append(f"law_number:{law_number}")
        else:
            matched_ids.update(_source_id(source) for source in matches if _source_id(source))

    # Validate article/document pairs sentence by sentence.  This prevents an
    # article from one retrieved law from incorrectly validating another law.
    for sentence in _SENTENCE_SPLIT_RE.split(answer or ""):
        sentence_articles = tuple(dict.fromkeys(_ARTICLE_RE.findall(sentence)))
        if not sentence_articles:
            continue
        sentence_laws = tuple(dict.fromkeys(_LAW_NUMBER_RE.findall(sentence)))
        if sentence_laws:
            for law_number in sentence_laws:
                law_sources = _matching_sources_for_law(law_number, evidence)
                for article in sentence_articles:
                    article_folded = article.casefold()
                    matching = [
                        source
                        for source in law_sources
                        if _article_token(source.get("article_number")) == article_folded
                    ]
                    if not matching:
                        unsupported.append(
                            f"article_document_pair:{law_number}:Điều {article}"
                        )
                    else:
                        matched_ids.update(
                            _source_id(source) for source in matching if _source_id(source)
                        )
        else:
            for article in sentence_articles:
                matching = [
                    source
                    for source in evidence
                    if _article_token(source.get("article_number")) == article.casefold()
                ]
                if not matching:
                    unsupported.append(f"article:Điều {article}")
                else:
                    matched_ids.update(
                        _source_id(source) for source in matching if _source_id(source)
                    )

    combined_source = _fold(" ".join(_source_text(source) for source in evidence))
    for measure in dict.fromkeys(_MEASURE_RE.findall(answer or "")):
        if _fold(measure) not in combined_source:
            unsupported.append(f"measure:{measure}")

    # Numberless document titles are still legal references.  Validate only
    # names that can be matched exactly/within an official retrieved title.
    document_names = tuple(dict.fromkeys(_DOCUMENT_NAME_RE.findall(answer or "")))
    for document_name in document_names:
        if _LAW_NUMBER_RE.search(document_name):
            continue
        folded_name = _fold(document_name)
        matches = [
            source
            for source in evidence
            if folded_name
            and (
                folded_name in _fold(source.get("document_title"))
                or _fold(source.get("document_title")) in folded_name
            )
        ]
        if not matches:
            unsupported.append(f"document_name:{document_name.strip()}")
        else:
            matched_ids.update(_source_id(source) for source in matches if _source_id(source))

    unsupported_tuple = tuple(dict.fromkeys(unsupported))
    explicit = bool(cited_internal_ids or law_numbers or document_names or _ARTICLE_RE.search(answer or ""))
    if invalid_internal_ids or unsupported_tuple:
        status = "ungrounded"
    elif explicit:
        status = "fully_grounded"
    else:
        status = "partially_grounded"
    return GroundingValidation(
        status=status,
        invalid_internal_ids=invalid_internal_ids,
        unsupported_references=unsupported_tuple,
        matched_source_ids=tuple(sorted(matched_ids)),
        has_explicit_reference=explicit,
    )
