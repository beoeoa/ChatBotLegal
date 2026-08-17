"""Deterministic exact-identifier planning for DB-5 legal retrieval.

This module intentionally contains no model calls. Exact legal identifiers are
parsed and checked before ANN so a semantic similarity result cannot override a
law number, provision, procedure ID, form code, field, effectivity, or
jurisdiction constraint stated by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
import unicodedata
from typing import Any, Callable, Iterable, Mapping, Sequence


_LAW_NUMBER_RE = re.compile(
    r"\b(?P<number>\d{1,4})\s*/\s*(?P<year>\d{4})\s*/\s*"
    r"(?P<suffix>[A-ZĐ0-9-]{1,24})\b",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"\b(?:điều|dieu)\s+(?P<number>\d+[a-z]?)\b", re.IGNORECASE)
_CLAUSE_RE = re.compile(r"\b(?:khoản|khoan)\s+(?P<number>\d+[a-z]?)\b", re.IGNORECASE)
_PROCEDURE_ID_RE = re.compile(
    r"\bprocedure_id\s*:\s*(?P<identifier>[a-z][a-z0-9_]{2,100})\b",
    re.IGNORECASE,
)
_FORM_SLASH_RE = re.compile(
    r"\b(?P<number>\d{1,3})\s*/\s*(?P<suffix>ĐK|DK)\b",
    re.IGNORECASE,
)
_FORM_COMPACT_RE = re.compile(r"\b(?P<code>CT\d{2,3})\b", re.IGNORECASE)

# Exact, human-reviewed aliases derived from approved corpus metadata.  These
# are identity bindings, not semantic guesses: a match must contain the whole
# folded phrase and never overrides a law number supplied by the user.
_REVIEWED_TITLE_ALIASES: tuple[tuple[str, str, str | None], ...] = (
    (
        "luat sua doi bo sung mot so dieu cua luat xay dung",
        "62/2020/QH14",
        "1",
    ),
    (
        "sua doi bo sung mot so dieu cua luat xay dung",
        "62/2020/QH14",
        "1",
    ),
    (
        "dang ky ho tich tai uy ban nhan dan cap huyen",
        "60/2014/QH13",
        None,
    ),
    (
        "dang ky ho tich tai co quan dai dien",
        "60/2014/QH13",
        None,
    ),
    (
        "co so du lieu ho tich cap trich luc ho tich",
        "60/2014/QH13",
        None,
    ),
    (
        "luat ho tich",
        "60/2014/QH13",
        None,
    ),
    (
        "hoat dong nghe nghiep quyen va nghia vu",
        "73/2025/QH15",
        None,
    ),
    (
        "nhung viec khong duoc lam",
        "73/2025/QH15",
        "11",
    ),
    (
        "the bao hiem y te",
        "188/2025/ND-CP",
        None,
    ),
    (
        "thoi diem the bao hiem y te co gia tri su dung",
        "188/2025/ND-CP",
        "13",
    ),
    (
        "truong hop khong the thuc hien duoc cac hinh thuc quy dinh tai cac diem",
        "88/2025/QH15",
        "70",
    ),
    (
        "nguoi chiu trach nhiem truoc nha nuoc doi voi viec su dung dat",
        "31/2024/QH15",
        "7",
    ),
    (
        "nguoi chiu trach nhiem truoc nha nuoc doi voi dat duoc giao",
        "31/2024/QH15",
        "7",
    ),
    (
        "quyen cua cong dan ve cu tru",
        "68/2020/QH14",
        "8",
    ),
    (
        "cac hanh vi bi nghiem cam",
        "26/2023/QH15",
        "7",
    ),
    (
        "quyen nghia vu cua nguoi giai quyet khieu nai lan dau",
        "02/2011/QH13",
        "14",
    ),
    (
        "tham quyen quyet dinh cuong che",
        "88/2025/QH15",
        "87",
    ),
)


def _fold_exact_text(value: Any) -> str:
    text = str(value or "").replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^0-9A-Za-z/.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_exact_identifier(value: Any) -> str:
    """Normalize an identifier without turning it into a fuzzy text query."""

    raw_text = str(value or "").strip().upper().replace("Đ", "D").replace("Ð", "D")
    text = unicodedata.normalize("NFD", raw_text)
    text = text.replace("Đ", "D")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"\s*/\s*", "/", text)
    return re.sub(r"\s+", "", text)


@dataclass(frozen=True)
class ExactLookupPlan:
    law_number: str | None
    article_number: str | None
    clause_number: str | None
    procedure_id: str | None
    form_codes: tuple[str, ...]
    article_numbers: tuple[str, ...] = ()
    law_numbers: tuple[str, ...] = ()
    article_law_pairs: tuple[tuple[str, str], ...] = ()

    @property
    def requires_exact_metadata_lookup(self) -> bool:
        return any(
            (
                self.law_number,
                self.law_numbers,
                self.article_number,
                self.clause_number,
                self.procedure_id,
                self.form_codes,
            )
        )


@dataclass(frozen=True)
class RetrievalBudget:
    vector_candidates: int
    lexical_candidates: int
    rerank_window: int


PRIMARY_BUDGET = RetrievalBudget(150, 60, 40)
SUPPORT_BUDGET = RetrievalBudget(100, 40, 40)


def plan_exact_lookup(query: str) -> ExactLookupPlan:
    raw = str(query or "").replace("Đ", "D").replace("đ", "d")
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(char for char in raw if unicodedata.category(char) != "Mn")
    law_matches = list(_LAW_NUMBER_RE.finditer(raw))
    law_match = law_matches[0] if law_matches else None
    article_matches = list(_ARTICLE_RE.finditer(raw))
    article_match = article_matches[0] if article_matches else None
    clause_match = _CLAUSE_RE.search(raw)
    procedure_match = _PROCEDURE_ID_RE.search(raw)

    law_numbers = tuple(
        dict.fromkeys(
            normalize_exact_identifier(
                "/".join(
                    (
                        match.group("number"),
                        match.group("year"),
                        match.group("suffix"),
                    )
                )
            )
            for match in law_matches
        )
    )
    law_number = law_numbers[0] if law_numbers else None
    form_codes = {
        normalize_exact_identifier(match.group(0))
        for match in _FORM_SLASH_RE.finditer(raw)
    }
    form_codes.update(
        normalize_exact_identifier(match.group("code"))
        for match in _FORM_COMPACT_RE.finditer(raw)
    )
    article_numbers = tuple(
        dict.fromkeys(
            normalize_exact_identifier(match.group("number"))
            for match in article_matches
        )
    )
    article_law_pairs: tuple[tuple[str, str], ...] = ()
    # A validity question commonly names an obsolete instrument first and then
    # asks for one exact Article from its current replacement.  Preserve every
    # identifier for audit, but bind the single Article to the nearest law
    # number instead of querying the cross-product of both instruments.  Keep
    # multi-Article questions unchanged because their pairings can be
    # intentionally comparative or may come from deterministic query rewrites.
    if article_matches and law_matches:
        def pair_distance(
            article: re.Match[str], law: re.Match[str]
        ) -> tuple[int, int]:
            if law.start() >= article.end():
                return law.start() - article.end(), 0
            return article.start() - law.end(), 1

        resolved_pairs: list[tuple[str, str]] = []
        for article in article_matches:
            trailing = raw[article.end() : article.end() + 48].casefold()
            if re.search(r"\bcua\s+luat\s+nay\b", trailing):
                continue
            nearest_law = min(
                law_matches,
                key=lambda law: pair_distance(article, law),
            )
            distance, _direction = pair_distance(article, nearest_law)
            # The limit prevents an unrelated law number elsewhere in a long
            # natural-language question from being silently attached to an
            # Article. A later quoted cross-reference remains a facet instead
            # of replacing the adjacent requested Article.
            if distance > 80:
                continue
            resolved_pairs.append(
                (
                    normalize_exact_identifier(
                        "/".join(
                            (
                                nearest_law.group("number"),
                                nearest_law.group("year"),
                                nearest_law.group("suffix"),
                            )
                        )
                    ),
                    normalize_exact_identifier(article.group("number")),
                )
            )
        article_law_pairs = tuple(dict.fromkeys(resolved_pairs))
        if len(article_law_pairs) == 1:
            law_number = article_law_pairs[0][0]
    if not law_numbers:
        folded_query = _fold_exact_text(query)
        reviewed_aliases = [
            (normalize_exact_identifier(alias_law), normalize_exact_identifier(alias_article))
            for phrase, alias_law, alias_article in _REVIEWED_TITLE_ALIASES
            if phrase in folded_query and alias_article
        ]
        if reviewed_aliases:
            article_law_pairs = tuple(dict.fromkeys(reviewed_aliases))
            law_numbers = tuple(dict.fromkeys(law for law, _article in article_law_pairs))
            article_numbers = tuple(dict.fromkeys(article for _law, article in article_law_pairs))
            law_number = law_numbers[0]
        else:
            # A natural-language title alias may identify the law while the
            # user supplies the requested Article separately.  Bind only the
            # article numbers explicitly present in that same query; this is
            # deterministic identity recovery, not semantic source guessing.
            reviewed_laws = tuple(
                dict.fromkeys(
                    normalize_exact_identifier(alias_law)
                    for phrase, alias_law, _alias_article in _REVIEWED_TITLE_ALIASES
                    if phrase in folded_query
                )
            )
            if reviewed_laws:
                law_numbers = reviewed_laws
                law_number = law_numbers[0]
                if article_numbers:
                    article_law_pairs = tuple(
                        (law, article)
                        for law in law_numbers
                        for article in article_numbers
                    )
    return ExactLookupPlan(
        law_number=law_number,
        law_numbers=law_numbers,
        article_number=(article_numbers[0] if article_numbers else None),
        article_numbers=article_numbers,
        article_law_pairs=article_law_pairs,
        clause_number=(
            normalize_exact_identifier(clause_match.group("number"))
            if clause_match
            else None
        ),
        procedure_id=(
            procedure_match.group("identifier").casefold()
            if procedure_match
            else None
        ),
        form_codes=tuple(sorted(form_codes)),
    )


def _date_value(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _current_reason(candidate: Mapping[str, Any], as_of: date) -> str | None:
    if str(candidate.get("document_status") or "").casefold() != "active":
        return "expired_or_not_yet_effective"
    if str(candidate.get("article_status") or "").casefold() not in {"", "active"}:
        return "expired_or_not_yet_effective"
    effective = _date_value(candidate.get("effective_date"))
    expired = _date_value(candidate.get("expired_date"))
    article_from = _date_value(candidate.get("article_effective_from"))
    article_to = _date_value(candidate.get("article_effective_to"))
    if effective and effective > as_of:
        return "expired_or_not_yet_effective"
    if expired and expired <= as_of:
        return "expired_or_not_yet_effective"
    if article_from and article_from > as_of:
        return "expired_or_not_yet_effective"
    if article_to and article_to <= as_of:
        return "expired_or_not_yet_effective"
    return None


def filter_exact_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    plan: ExactLookupPlan,
    domain: str | None,
    as_of: date,
    jurisdiction: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fail closed on exact metadata, field, validity, and jurisdiction."""

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    normalized_domain = str(domain or "").casefold()
    normalized_jurisdiction = str(jurisdiction or "").casefold()
    for source in candidates:
        row = dict(source)
        chunk_id = row.get("chunk_id")
        reason: str | None = None
        if plan.law_number and normalize_exact_identifier(row.get("law_number")) != plan.law_number:
            reason = "wrong_law_number"
        elif plan.article_number and normalize_exact_identifier(
            row.get("article_number")
        ) != plan.article_number:
            reason = "wrong_article"
        elif normalized_domain and str(row.get("domain_slug") or "").casefold() != normalized_domain:
            reason = "wrong_field"
        else:
            reason = _current_reason(row, as_of)
        if reason is None and bool(row.get("is_superseded")):
            reason = "superseded"
        if (
            reason is None
            and normalized_jurisdiction
            and str(row.get("source_jurisdiction") or "").casefold()
            not in {"", normalized_jurisdiction}
        ):
            reason = "wrong_jurisdiction"
        if reason:
            rejected.append({"chunk_id": chunk_id, "reason": reason})
        else:
            accepted.append(row)
    return accepted, rejected


@dataclass(frozen=True)
class TieredRetrievalResult:
    results: tuple[dict[str, Any], ...]
    support_called: bool
    requested_support_facets: tuple[str, ...]
    missing_facets: tuple[str, ...]


def _covered_facets(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    covered: set[str] = set()
    for row in rows:
        covered.update(str(item) for item in (row.get("facets") or ()))
    return covered


def retrieve_primary_then_support(
    *,
    required_facets: Sequence[str],
    primary_retrieve: Callable[[], Sequence[Mapping[str, Any]]],
    support_retrieve: Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]],
) -> TieredRetrievalResult:
    """Retrieve primary first and invoke support at most once for missing facets."""

    primary = [dict(row) for row in primary_retrieve()]
    required = tuple(dict.fromkeys(str(item) for item in required_facets if item))
    missing = tuple(item for item in required if item not in _covered_facets(primary))
    support: list[dict[str, Any]] = []
    if missing:
        support = [dict(row) for row in support_retrieve(missing)]
    combined = primary + support
    remaining = tuple(
        item for item in required if item not in _covered_facets(combined)
    )
    return TieredRetrievalResult(
        results=tuple(combined),
        support_called=bool(missing),
        requested_support_facets=missing,
        missing_facets=remaining,
    )
