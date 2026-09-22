"""Bounded natural-language recovery for the public Quick Chat route.

The regular intent matcher remains the first and authoritative path.  This
module is consulted only after that matcher misses or reports ambiguity.  It
groups released intents by their reviewed logical procedure name, resolves a
high-confidence topic family, and then selects only an already released
intent.  It never writes a legal answer and never calls an LLM.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping

from api.intent_fast_path import (
    IntentFastPathMatch,
    is_intent_fast_path_eligible,
    load_intent_catalog,
    match_intent_fast_path,
)
from api.procedure_fast_path import normalize_procedure_query


QUICK_NATURAL_ROUTER_VERSION = "quick-natural-router-v1"

# Only high-confidence, meaning-preserving chat corrections belong here.  The
# values are retrieval variants, not edits to the user's stored question.
_REVIEWED_CHAT_CORRECTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:lam|lm)\s+soa\b"), "lam sao"),
    (re.compile(r"\blm\s+sao\b"), "lam sao"),
    (re.compile(r"\bli\s+hon\b"), "ly hon"),
    (re.compile(r"\bkhum\b"), "khong"),
    (re.compile(r"\bko\b"), "khong"),
)

_QUERY_STOPWORDS = frozenset(
    {
        "a",
        "ad",
        "anh",
        "ban",
        "can",
        "chi",
        "cho",
        "co",
        "cua",
        "duoc",
        "em",
        "gi",
        "giup",
        "hoi",
        "khong",
        "lam",
        "minh",
        "muon",
        "nao",
        "nay",
        "phai",
        "sao",
        "soa",
        "t",
        "tao",
        "the",
        "thi",
        "toi",
        "uk",
        "u",
        "vay",
        "voi",
        "xin",
        # Facet words should not decide the legal topic family.
        "bao",
        "buoc",
        "chuan",
        "chi",
        "dia",
        "dau",
        "le",
        "may",
        "ngay",
        "nop",
        "o",
        "phi",
        "quy",
        "tai",
        "tien",
        "trinh",
    }
)

_FAMILY_STOPWORDS = frozenset(
    {
        "cap",
        "cua",
        "dang",
        "de",
        "dinh",
        "doi",
        "giai",
        "hien",
        "ky",
        "nghi",
        "phuong",
        "quyet",
        "tai",
        "tham",
        "thong",
        "thuc",
        "tin",
        "trong",
        "truong",
        "tuc",
        "tuyen",
        "thu",
        "uy",
        "va",
        "ve",
        "voi",
        "xa",
    }
)

_SUGGESTION_FACET_ORDER = (
    "documents",
    "submission_place",
    "duration",
    "fee",
    "eligibility",
    "authority",
    "overview",
    "steps",
    "forms_special",
    "special_case",
)

_NATURAL_FACET_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documents", ("ho so", "giay to", "can mang", "chuan bi", "tai lieu")),
    ("submission_place", ("o dau", "noi nop", "nop o", "den dau", "tiep nhan")),
    ("duration", ("bao lau", "thoi han", "may ngay", "mat bao lau", "ngay lam viec")),
    # Bare "tiền" is intentionally excluded: phrases such as "vay tiền" or
    # "nhận tiền" are not a request for a procedure fee.
    ("fee", ("le phi", "an phi", "chi phi", "mat phi", "phi", "bao nhieu tien", "thu bao nhieu")),
    ("steps", ("lam sao", "trinh tu", "cac buoc", "bat dau", "quy trinh")),
)


@dataclass(frozen=True)
class QuickNaturalResolution:
    decision: str
    reason_code: str
    family_key: str | None = None
    family_label: str | None = None
    family_score: float = 0.0
    second_family_score: float = 0.0
    facet: str = "overview"
    match: IntentFastPathMatch | None = None
    suggestions: tuple[str, ...] = ()

    @property
    def hit(self) -> bool:
        return self.decision == "hit" and self.match is not None and self.match.hit

    @property
    def needs_clarification(self) -> bool:
        return self.decision == "clarify"


@dataclass(frozen=True)
class _Family:
    key: str
    label: str
    anchor_tokens: frozenset[str]
    anchor_variants: tuple[tuple[str, frozenset[str]], ...]
    detail_tokens: frozenset[str]
    row_detail_tokens: tuple[frozenset[str], ...]
    rows: tuple[dict[str, Any], ...]


def normalize_quick_chat_natural_query(question: str) -> str:
    """Return a bounded retrieval-only normalization for noisy guest text."""

    normalized = normalize_procedure_query(question)
    # Collapse emphatic vowel runs (``saoooo``) without damaging identifiers
    # such as CCCD, which legitimately repeat consonants.
    normalized = re.sub(r"([aeiouy])\1{2,}", r"\1", normalized)
    for pattern, replacement in _REVIEWED_CHAT_CORRECTIONS:
        normalized = pattern.sub(replacement, normalized)
    return " ".join(normalized.split())


def _detect_natural_facet(normalized_question: str) -> str:
    padded = f" {normalized_question} "
    for facet, terms in _NATURAL_FACET_TERMS:
        if any(f" {term} " in padded for term in terms):
            return facet
    return "overview"


def _tokens(value: str, *, stopwords: frozenset[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in value.split()
        if len(token) >= 2 and token not in stopwords and not token.isdigit()
    )


def _logical_family(row: Mapping[str, Any]) -> tuple[str, str]:
    label = str(row.get("procedure_name") or "").strip()
    if not label:
        label = str(row.get("procedure_id") or "").replace("_", " ").strip()
    key = normalize_procedure_query(label)
    return key, label


def _build_families(rows: Iterable[Mapping[str, Any]]) -> tuple[_Family, ...]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    labels: dict[str, str] = {}
    for raw in rows:
        if not is_intent_fast_path_eligible(raw, audience="guest"):
            continue
        key, label = _logical_family(raw)
        if not key:
            continue
        grouped[key].append(dict(raw))
        labels.setdefault(key, label)

    families: list[_Family] = []
    for key, family_rows in grouped.items():
        anchor_values: list[str] = [key]
        label = labels[key]
        # Parenthetical citizen wording and managed aliases are reviewed
        # metadata, and are safer family anchors than inventing synonyms in
        # code.  Do not use full intent questions here because their facet
        # words ("phí", "ở đâu"...) are not procedure identities.
        anchor_values.extend(
            normalize_procedure_query(part)
            for part in re.findall(r"\(([^()]*)\)", label)
            if part.strip()
        )
        label_without_parentheses = normalize_procedure_query(
            re.sub(r"\([^()]*\)", " ", label)
        )
        if label_without_parentheses:
            anchor_values.append(label_without_parentheses)
        for row in family_rows:
            snapshot = row.get("procedure_snapshot")
            snapshot_name = (
                normalize_procedure_query(snapshot.get("name"))
                if isinstance(snapshot, Mapping)
                else ""
            )
            # A curated topic family may deliberately link to several adjacent
            # procedures (for example divorce -> later civil-status updates).
            # Their snapshot aliases must not become aliases of the parent
            # family, or "đăng ký kết hôn" could incorrectly match divorce.
            aliases = (
                snapshot.get("aliases")
                if isinstance(snapshot, Mapping) and snapshot_name == key
                else None
            )
            if isinstance(aliases, (list, tuple)):
                anchor_values.extend(
                    normalize_procedure_query(alias)
                    for alias in aliases
                    if str(alias or "").strip()
                )

        variants: list[tuple[str, frozenset[str]]] = []
        seen_variants: set[frozenset[str]] = set()
        for value in anchor_values:
            variant_tokens = _tokens(value, stopwords=_FAMILY_STOPWORDS)
            if not variant_tokens or variant_tokens in seen_variants:
                continue
            seen_variants.add(variant_tokens)
            variants.append((value, variant_tokens))
        anchors = frozenset(token for _, tokens in variants for token in tokens)
        if not anchors:
            continue
        detail_tokens: set[str] = set(anchors)
        row_detail_tokens: list[frozenset[str]] = []
        for row in family_rows:
            detail_values: list[Any] = [
                row.get("canonical_question"),
                row.get("question"),
            ]
            question_variants = row.get("question_variants")
            if isinstance(question_variants, (list, tuple)):
                detail_values.extend(question_variants)
            compiled_aliases = row.get("_fast_aliases")
            if isinstance(compiled_aliases, tuple):
                detail_values.extend(compiled_aliases)
            row_tokens: set[str] = set()
            for value in detail_values:
                row_tokens.update(
                    _tokens(
                        normalize_procedure_query(value),
                        stopwords=_QUERY_STOPWORDS | _FAMILY_STOPWORDS,
                    )
                )
            detail_tokens.update(row_tokens)
            row_detail_tokens.append(frozenset(row_tokens))
        families.append(
            _Family(
                key=key,
                label=label,
                anchor_tokens=anchors,
                anchor_variants=tuple(variants),
                detail_tokens=frozenset(detail_tokens),
                row_detail_tokens=tuple(row_detail_tokens),
                rows=tuple(family_rows),
            )
        )
    return tuple(sorted(families, key=lambda item: item.key))


@lru_cache(maxsize=4)
def _cached_families(release_id: str) -> tuple[_Family, ...]:
    # Quick-chat releases are immutable.  A new catalogue therefore always
    # has a new release id, which makes this cache safe without a timer.
    del release_id
    return _build_families(load_intent_catalog())


def clear_quick_chat_natural_router_cache() -> None:
    _cached_families.cache_clear()


def _families_for_catalog(
    catalog: Iterable[Mapping[str, Any]] | None,
) -> tuple[_Family, ...]:
    if catalog is not None:
        return _build_families(catalog)
    rows = load_intent_catalog()
    release_id = str(rows[0].get("release_id") or "unreleased") if rows else "empty"
    return _cached_families(release_id)


def _family_scores(
    normalized_question: str,
    families: tuple[_Family, ...],
) -> list[tuple[float, _Family]]:
    # Apply the same structural-word removal on both sides.  Otherwise a
    # citizen who types the complete official procedure name can paradoxically
    # receive a lower score because words such as "thủ tục", "đăng ký" and
    # "cấp xã" were removed only from the family label.
    query_tokens = _tokens(
        normalized_question,
        stopwords=_QUERY_STOPWORDS | _FAMILY_STOPWORDS,
    )
    if not query_tokens:
        return []
    document_frequency: Counter[str] = Counter()
    for family in families:
        document_frequency.update(family.anchor_tokens)
    family_count = max(1, len(families))

    def token_weight(token: str) -> float:
        return 1.0 + math.log((family_count + 1) / (document_frequency[token] + 1))

    query_weight = sum(token_weight(token) for token in query_tokens)
    scored: list[tuple[float, _Family]] = []
    for family in families:
        unknown_tokens = query_tokens - family.detail_tokens
        if len(unknown_tokens) >= 2:
            unknown_weight = sum(token_weight(token) for token in unknown_tokens)
            # Extra content words can materially change jurisdiction.  For
            # example, "tố cáo lừa đảo trên mạng" must not be silently reduced
            # to the generic commune-level "tố cáo" family.  Facet/chat words
            # were already removed above, so a large unknown share fails safe.
            if unknown_weight / max(query_weight, 1e-9) >= 0.35:
                continue
        best_variant_score = 0.0
        for anchor_value, anchor_tokens in family.anchor_variants:
            overlap = query_tokens & anchor_tokens
            if not overlap:
                continue
            # A single short/common token (for example ``hon``) must never
            # select a family by itself. Exact one-token aliases remain
            # eligible only when distinctive across the whole release.
            if len(overlap) == 1:
                token = next(iter(overlap))
                if (
                    len(anchor_tokens) > 1
                    or len(token) < 5
                    or document_frequency[token] > 1
                ):
                    continue
            overlap_weight = sum(token_weight(token) for token in overlap)
            family_weight = sum(token_weight(token) for token in anchor_tokens)
            family_coverage = overlap_weight / max(family_weight, 1e-9)
            query_coverage = overlap_weight / max(query_weight, 1e-9)
            anchor_phrase = " ".join(
                token for token in anchor_value.split() if token in anchor_tokens
            )
            phrase_bonus = (
                0.10
                if anchor_phrase and anchor_phrase in normalized_question
                else 0.0
            )
            variant_score = min(
                1.0,
                0.58 * family_coverage + 0.32 * query_coverage + phrase_bonus,
            )
            best_variant_score = max(best_variant_score, variant_score)
        if best_variant_score:
            scored.append((round(best_variant_score, 6), family))
    return sorted(scored, key=lambda item: (-item[0], item[1].key))


def _row_facet(row: Mapping[str, Any]) -> str:
    return str(row.get("intent_type") or row.get("answer_facet") or "overview").strip()


def _specific_family_row(question: str, family: _Family) -> dict[str, Any] | None:
    """Select a reviewed sub-intent only when detail words are distinctive."""

    normalized = normalize_quick_chat_natural_query(question)
    query_tokens = _tokens(
        normalized,
        stopwords=_QUERY_STOPWORDS | _FAMILY_STOPWORDS,
    ) - family.anchor_tokens
    if not query_tokens:
        return None

    document_frequency: Counter[str] = Counter()
    for tokens in family.row_detail_tokens:
        document_frequency.update(tokens - family.anchor_tokens)
    row_count = max(1, len(family.rows))

    def weight(token: str) -> float:
        return 1.0 + math.log((row_count + 1) / (document_frequency[token] + 1))

    query_weight = sum(weight(token) for token in query_tokens)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for row, raw_tokens in zip(family.rows, family.row_detail_tokens):
        row_tokens = raw_tokens - family.anchor_tokens
        overlap = query_tokens & row_tokens
        if not overlap:
            continue
        if len(overlap) == 1:
            token = next(iter(overlap))
            if len(token) < 5 or document_frequency[token] > 1:
                continue
        overlap_weight = sum(weight(token) for token in overlap)
        row_weight = sum(weight(token) for token in row_tokens)
        query_coverage = overlap_weight / max(query_weight, 1e-9)
        row_coverage = overlap_weight / max(row_weight, 1e-9)
        score = 0.70 * query_coverage + 0.30 * row_coverage
        ranked.append((score, str(row.get("intent_id") or ""), dict(row)))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return None
    best_score = ranked[0][0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < 0.74 or (second_score >= 0.55 and best_score - second_score < 0.16):
        return None
    return ranked[0][2]


def _match_single_released_row(row: Mapping[str, Any]) -> IntentFastPathMatch:
    # The family index contains only guest-eligible released rows. Re-running
    # the general matcher against a one-row catalogue would recompile all
    # aliases and dominate latency on every recovered question, so construct
    # the already-verified match directly and preserve its reviewed sources.
    if not is_intent_fast_path_eligible(row, audience="guest"):
        return IntentFastPathMatch("miss", "INTENT_NOT_ELIGIBLE")
    raw_refs = row.get("source_refs") or row.get("verified_source_refs") or row.get("citations")
    refs: list[dict[str, Any]] = []
    if isinstance(raw_refs, (list, tuple)):
        for item in raw_refs:
            if not isinstance(item, Mapping):
                continue
            ref = dict(item)
            url = str(ref.get("source_url") or ref.get("url") or "").strip()
            if url:
                ref["source_url"] = url
                refs.append(ref)
    for key in ("source_url", "official_source_url", "official_source_page"):
        url = str(row.get(key) or "").strip()
        if url and not any(ref.get("source_url") == url for ref in refs):
            refs.append({"source_url": url, "label": "Nguồn chính thức"})
    answer = str(row.get("answer_text") or row.get("answer") or "").strip()
    return IntentFastPathMatch(
        "hit",
        "INTENT_MATCH_CONFIDENT",
        score=1.0,
        second_score=0.0,
        intent=dict(row),
        answer=answer,
        release_id=str(row.get("release_id") or "") or None,
        source_refs=tuple(refs),
    )


def _choose_released_row(
    question: str,
    *,
    family: _Family,
    facet: str,
) -> tuple[dict[str, Any] | None, str]:
    rows = list(family.rows)

    # Explicit fact facets take precedence over contextual detail words.
    facet_rows = [row for row in rows if _row_facet(row) == facet]
    if facet not in {"overview", "steps"}:
        if len(facet_rows) == 1:
            return facet_rows[0], "QUICK_NATURAL_FAMILY_FACET"
        if len(facet_rows) > 1:
            within_family = match_intent_fast_path(
                normalize_quick_chat_natural_query(question),
                audience="guest",
                catalog=facet_rows,
            )
            if within_family.hit and within_family.intent:
                return dict(within_family.intent), "QUICK_NATURAL_FAMILY_FACET"

    specific = _specific_family_row(question, family)
    if specific is not None:
        return specific, "QUICK_NATURAL_FAMILY_DETAIL"

    # A bare question such as "tôi muốn ly hôn thì làm sao" is an orientation
    # request, not evidence that the citizen already has a judgment and wants
    # the post-judgment civil-status steps.  Referral families therefore use
    # their reviewed authority row for vague overview/process wording.  More
    # specific questions still hit the normal matcher before reaching here.
    if facet in {"overview", "steps"} and "dinh tuyen" in family.key:
        authority = [row for row in rows if _row_facet(row) == "authority"]
        if len(authority) == 1:
            return authority[0], "QUICK_NATURAL_FAMILY_DEFAULT"

    if len(facet_rows) == 1:
        return facet_rows[0], "QUICK_NATURAL_FAMILY_FACET"
    if len(facet_rows) > 1:
        within_family = match_intent_fast_path(
            normalize_quick_chat_natural_query(question),
            audience="guest",
            catalog=facet_rows,
        )
        if within_family.hit and within_family.intent:
            return dict(within_family.intent), "QUICK_NATURAL_FAMILY_FACET"

    explicit_defaults = [row for row in rows if row.get("quick_chat_family_default") is True]
    if len(explicit_defaults) == 1:
        return explicit_defaults[0], "QUICK_NATURAL_FAMILY_DEFAULT"

    # Curated referral families intentionally contain an authority answer that
    # safely orients a vague citizen question before offering narrower chips.
    # This selects reviewed text only; no jurisdiction fact is synthesized.
    if "dinh tuyen" in family.key:
        authority = [row for row in rows if _row_facet(row) == "authority"]
        if len(authority) == 1:
            return authority[0], "QUICK_NATURAL_FAMILY_DEFAULT"

    overview = [row for row in rows if _row_facet(row) == "overview"]
    if len(overview) == 1 and facet == "overview":
        return overview[0], "QUICK_NATURAL_FAMILY_DEFAULT"
    return None, "QUICK_NATURAL_FAMILY_CLARIFICATION"


def _suggestions(
    family: _Family,
    *,
    exclude_intent_id: str | None = None,
    preferred_procedure_id: str | None = None,
) -> tuple[str, ...]:
    rows_by_facet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in family.rows:
        if str(row.get("intent_id") or "") == str(exclude_intent_id or ""):
            continue
        question = str(row.get("canonical_question") or "").strip()
        if question:
            rows_by_facet[_row_facet(row)].append(row)
    output: list[str] = []
    facet_order = _SUGGESTION_FACET_ORDER
    if "dinh tuyen" in family.key:
        # Referral families can contain several adjacent, but materially
        # different, follow-up procedures.  Keep the first chips on the core
        # court-referral questions instead of leading with a niche side case.
        facet_order = (
            "documents",
            "duration",
            "fee",
            "eligibility",
            "overview",
            "special_case",
            "submission_place",
            "steps",
            "forms_special",
        )
    for facet in facet_order:
        candidates = sorted(
            rows_by_facet.get(facet, ()),
            key=lambda row: (
                0
                if preferred_procedure_id
                and str(row.get("procedure_id") or "") == preferred_procedure_id
                else 1,
                str(row.get("intent_id") or ""),
            ),
        )
        if not candidates:
            continue
        question = str(candidates[0].get("canonical_question") or "").strip()
        if question and question not in output:
            output.append(question)
        if len(output) >= 4:
            break
    return tuple(output)


def resolve_quick_chat_natural_question(
    question: str,
    *,
    catalog: Iterable[Mapping[str, Any]] | None = None,
) -> QuickNaturalResolution:
    """Resolve one noisy question after the normal intent matcher declined it."""

    normalized = normalize_quick_chat_natural_query(question)
    families = _families_for_catalog(catalog)
    ranked = _family_scores(normalized, families)
    if not ranked:
        return QuickNaturalResolution("miss", "QUICK_NATURAL_NO_FAMILY")
    best_score, best_family = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < 0.70:
        return QuickNaturalResolution(
            "miss",
            "QUICK_NATURAL_FAMILY_BELOW_THRESHOLD",
            family_key=best_family.key,
            family_label=best_family.label,
            family_score=best_score,
            second_family_score=second_score,
        )
    unambiguous_exact_alias = best_score >= 0.98 and best_score - second_score >= 0.05
    if (
        second_score >= 0.55
        and best_score - second_score < 0.16
        and not unambiguous_exact_alias
    ):
        return QuickNaturalResolution(
            "miss",
            "QUICK_NATURAL_MULTIPLE_FAMILIES",
            family_key=best_family.key,
            family_label=best_family.label,
            family_score=best_score,
            second_family_score=second_score,
        )

    facet = _detect_natural_facet(normalized)
    row, reason_code = _choose_released_row(question, family=best_family, facet=facet)
    if row is None:
        suggestions = _suggestions(best_family)
        if not suggestions:
            return QuickNaturalResolution(
                "miss",
                "QUICK_NATURAL_NO_SAFE_INTENT",
                family_key=best_family.key,
                family_label=best_family.label,
                family_score=best_score,
                second_family_score=second_score,
                facet=facet,
            )
        return QuickNaturalResolution(
            "clarify",
            reason_code,
            family_key=best_family.key,
            family_label=best_family.label,
            family_score=best_score,
            second_family_score=second_score,
            facet=facet,
            suggestions=suggestions,
        )

    match = _match_single_released_row(row)
    if not match.hit:
        return QuickNaturalResolution(
            "miss",
            "QUICK_NATURAL_SELECTED_INTENT_INELIGIBLE",
            family_key=best_family.key,
            family_label=best_family.label,
            family_score=best_score,
            second_family_score=second_score,
            facet=facet,
        )
    return QuickNaturalResolution(
        "hit",
        reason_code,
        family_key=best_family.key,
        family_label=best_family.label,
        family_score=best_score,
        second_family_score=second_score,
        facet=facet,
        match=match,
        suggestions=_suggestions(
            best_family,
            exclude_intent_id=str(row.get("intent_id") or ""),
            preferred_procedure_id=str(row.get("procedure_id") or ""),
        ),
    )


def natural_clarification_response(
    question: str,
    resolution: QuickNaturalResolution,
) -> dict[str, Any]:
    """Render a non-legal clarification; suggestions remain reviewed questions."""

    label = str(resolution.family_label or "nội dung này").strip()
    return {
        "answer": (
            f"Mình hiểu anh/chị đang hỏi về {label}. "
            "Để trả lời đúng thông tin đã công bố, anh/chị muốn biết nội dung nào dưới đây?"
        ),
        "question": question,
        "citations": [],
        "grounding_status": "not_applicable",
        "answer_status": "clarification_required",
        "outcome": "clarification_required",
        "reason_code": resolution.reason_code,
        "retryable": False,
        "answer_mode": "public_quick_chat_natural",
        "llm_used": False,
        "source_gap": [],
        "procedure_detail": None,
        "suggested_questions": list(resolution.suggestions),
        "natural_language_resolution": {
            "version": QUICK_NATURAL_ROUTER_VERSION,
            "family_key": resolution.family_key,
            "family_label": resolution.family_label,
            "family_score": resolution.family_score,
            "second_family_score": resolution.second_family_score,
            "facet": resolution.facet,
        },
        "generation_provenance": {
            "mode": "public_quick_chat_natural",
            "provider_label": "deterministic",
            "model_calls": 0,
        },
    }


__all__ = [
    "QUICK_NATURAL_ROUTER_VERSION",
    "QuickNaturalResolution",
    "clear_quick_chat_natural_router_cache",
    "natural_clarification_response",
    "normalize_quick_chat_natural_query",
    "resolve_quick_chat_natural_question",
]
