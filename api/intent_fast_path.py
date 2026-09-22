"""Deterministic matching for released question-and-answer intents.

Intent records are the reviewed answer layer above the managed procedure
catalogue.  This module never turns a draft or source-less record into public
evidence: a record must be released, audience-eligible and carry at least one
official source reference before it can match.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from api.data_paths import notebook_data_dir
from api.procedure_fast_path import build_query_variants, score_normalized_query


INTENT_FAST_PATH_VERSION = "intent-fast-v1"


def _catalog_path() -> Path:
    configured = str(os.getenv("PROCEDURE_INTENT_FAST_PATH_CATALOG") or "").strip()
    if configured:
        return Path(configured)
    return notebook_data_dir() / "intents" / "managed_intents_runtime_v1.json"


def _record_id(value: Any) -> str:
    return str(value or "").strip()


def _safe_list(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(value)


def _compile_record(row: Mapping[str, Any]) -> dict[str, Any]:
    compiled = dict(row)
    alias_values: list[Any] = [
        row.get("canonical_question"),
        row.get("question"),
        *_safe_list(row.get("question_variants")),
        *_safe_list(row.get("aliases")),
    ]
    aliases: list[str] = []
    seen: set[str] = set()
    for value in alias_values:
        for variant in build_query_variants(value):
            if variant and variant not in seen:
                seen.add(variant)
                aliases.append(variant)
    negatives: list[str] = []
    for value in _safe_list(row.get("negative_examples")):
        negatives.extend(build_query_variants(value))
    compiled["_fast_aliases"] = tuple(aliases)
    compiled["_fast_negatives"] = tuple(dict.fromkeys(negatives))
    return compiled


@lru_cache(maxsize=4)
def _load_catalog_cached(path_string: str, stamp: int) -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(Path(path_string).read_text(encoding="utf-8-sig"))
        if isinstance(payload, Mapping):
            # A candidate or half-written release must never serve guests,
            # even if individual rows still carry reviewed=true.
            if payload.get("serving") is not True or payload.get("activation_status") != "active":
                return ()
        rows = payload.get("intents") if isinstance(payload, Mapping) else payload
        if isinstance(rows, list):
            if isinstance(payload, Mapping):
                release_id = str(payload.get("release_id") or "").strip()
                if (
                    not release_id
                    or int(payload.get("record_count") or -1) != len(rows)
                    or any(str(row.get("release_id") or "") != release_id for row in rows if isinstance(row, Mapping))
                ):
                    return ()
            return tuple(_compile_record(row) for row in rows if isinstance(row, Mapping))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return ()


def clear_intent_fast_path_cache() -> None:
    _load_catalog_cached.cache_clear()
    _catalog_release_id_cached.cache_clear()
    _catalog_alias_index_cached.cache_clear()
    _catalog_token_index_cached.cache_clear()


def load_intent_catalog() -> tuple[dict[str, Any], ...]:
    path = _catalog_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _load_catalog_cached(str(path), stamp)


def intent_fast_path_release_id(items: Iterable[Mapping[str, Any]] | None = None) -> str:
    rows = list(items if items is not None else load_intent_catalog())
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{INTENT_FAST_PATH_VERSION}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


@lru_cache(maxsize=4)
def _catalog_release_id_cached(path_string: str, stamp: int) -> str:
    try:
        payload = json.loads(Path(path_string).read_text(encoding="utf-8-sig"))
        if isinstance(payload, Mapping) and str(payload.get("release_id") or "").strip():
            return str(payload["release_id"])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return intent_fast_path_release_id(_load_catalog_cached(path_string, stamp))


@lru_cache(maxsize=4)
def _catalog_alias_index_cached(path_string: str, stamp: int) -> dict[str, tuple[int, ...]]:
    buckets: dict[str, list[int]] = {}
    for index, row in enumerate(_load_catalog_cached(path_string, stamp)):
        for alias in _aliases(row):
            buckets.setdefault(alias, []).append(index)
    return {alias: tuple(indices) for alias, indices in buckets.items()}


_TOKEN_STOPWORDS = frozenset({
    "anh", "bao", "ban", "biet", "cac", "can", "cho", "co", "cua", "duoc",
    "gi", "hay", "hoi", "khong", "lam", "minh", "mot", "muon", "nao", "nay",
    "nhung", "o", "phai", "the", "thi", "thu", "toi", "va", "viec", "xin",
})


def _index_tokens(values: Iterable[str]) -> set[str]:
    return {
        token
        for value in values
        for token in value.split()
        if len(token) >= 3 and token not in _TOKEN_STOPWORDS
    }


@lru_cache(maxsize=4)
def _catalog_token_index_cached(path_string: str, stamp: int) -> dict[str, tuple[int, ...]]:
    buckets: dict[str, list[int]] = {}
    for index, row in enumerate(_load_catalog_cached(path_string, stamp)):
        for token in _index_tokens(_aliases(row)):
            buckets.setdefault(token, []).append(index)
    return {token: tuple(indices) for token, indices in buckets.items()}


def _shortlist_indices(
    question_variants: list[str],
    token_index: Mapping[str, tuple[int, ...]],
    *,
    limit: int = 160,
) -> list[int]:
    overlap: Counter[int] = Counter()
    for token in _index_tokens(question_variants):
        overlap.update(token_index.get(token, ()))
    if not overlap:
        return []
    best = max(overlap.values())
    floor = max(1, best - 1)
    ranked = sorted(
        (index for index, count in overlap.items() if count >= floor),
        key=lambda index: (-overlap[index], index),
    )
    return ranked[:limit]


def _allowed_audience(row: Mapping[str, Any], audience: str) -> bool:
    allowed = row.get("eligible_roles") or row.get("audience")
    if not allowed:
        return True
    if isinstance(allowed, str):
        allowed = [allowed]
    values = {str(item).casefold() for item in allowed}
    return bool({audience.casefold(), "citizen", "guest", "both", "all"} & values)


def _source_refs(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("source_refs") or row.get("verified_source_refs") or row.get("citations")
    refs: list[dict[str, Any]] = []
    for item in _safe_list(raw):
        if isinstance(item, Mapping):
            value = dict(item)
            url = str(value.get("source_url") or value.get("url") or "").strip()
            if url:
                value["source_url"] = url
                refs.append(value)
    for key in ("source_url", "official_source_url", "official_source_page"):
        url = str(row.get(key) or "").strip()
        if url and not any(item.get("source_url") == url for item in refs):
            refs.append({"source_url": url, "label": "Nguồn chính thức"})
    return refs


def is_intent_fast_path_eligible(row: Mapping[str, Any], *, audience: str = "citizen") -> bool:
    if not _record_id(row.get("intent_id") or row.get("id")):
        return False
    if row.get("archived") is True or row.get("is_archived") is True:
        return False
    review_status = str(row.get("review_status") or "").casefold()
    public_state = str(row.get("public_state") or row.get("catalog_status") or "").casefold()
    if review_status not in {"released", "published"} or public_state not in {"released", "published", "active"}:
        return False
    source_status = str(row.get("source_status") or "managed_live").casefold()
    if source_status in {"expired", "withdrawn", "stale", "invalid", "excluded", "archived"}:
        return False
    if not _allowed_audience(row, audience):
        return False
    require_source = str(os.getenv("PROCEDURE_INTENT_FAST_PATH_REQUIRE_SOURCE", "true")).strip().casefold()
    if require_source in {"1", "true", "yes", "on"} and not _source_refs(row):
        return False
    return bool(str(row.get("answer_text") or row.get("answer") or "").strip())


def _aliases(row: Mapping[str, Any]) -> tuple[str, ...]:
    compiled = row.get("_fast_aliases")
    if isinstance(compiled, tuple):
        return compiled
    values: list[Any] = [
        row.get("canonical_question"),
        row.get("question"),
        *_safe_list(row.get("question_variants")),
        *_safe_list(row.get("aliases")),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for variant in build_query_variants(value):
            if variant and variant not in seen:
                seen.add(variant)
                result.append(variant)
    return tuple(result)


def _negative_examples(row: Mapping[str, Any]) -> tuple[str, ...]:
    compiled = row.get("_fast_negatives")
    if isinstance(compiled, tuple):
        return compiled
    result: list[str] = []
    for value in _safe_list(row.get("negative_examples")):
        result.extend(build_query_variants(value))
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True)
class IntentFastPathMatch:
    decision: str
    reason_code: str
    score: float = 0.0
    second_score: float = 0.0
    intent: dict[str, Any] | None = None
    answer: str | None = None
    release_id: str | None = None
    source_refs: tuple[dict[str, Any], ...] = ()

    @property
    def hit(self) -> bool:
        return self.decision == "hit" and self.intent is not None and bool(self.answer)


def match_intent_fast_path(
    question: str,
    *,
    audience: str = "citizen",
    catalog: Iterable[Mapping[str, Any]] | None = None,
) -> IntentFastPathMatch:
    question_variants = build_query_variants(question)
    if not question_variants or max(map(len, question_variants), default=0) < 4:
        return IntentFastPathMatch("miss", "QUESTION_TOO_SHORT")
    if catalog is None:
        path = _catalog_path()
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = 0
        all_rows = _load_catalog_cached(str(path), stamp)
        alias_index = _catalog_alias_index_cached(str(path), stamp)
        exact_indices = {
            index
            for question_variant in question_variants
            for index in alias_index.get(question_variant, ())
        }
        if exact_indices:
            rows = [all_rows[index] for index in sorted(exact_indices)]
        else:
            token_indices = _shortlist_indices(
                question_variants,
                _catalog_token_index_cached(str(path), stamp),
            )
            rows = [all_rows[index] for index in token_indices] if token_indices else list(all_rows)
        release_id = _catalog_release_id_cached(str(path), stamp)
    else:
        rows = [_compile_record(row) for row in catalog]
        release_id = intent_fast_path_release_id(rows)
        exact_rows = [row for row in rows if set(question_variants) & set(_aliases(row))]
        if exact_rows:
            rows = exact_rows
    candidates: list[tuple[float, dict[str, Any]]] = []
    normalized_negative_cache: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if not is_intent_fast_path_eligible(row, audience=audience):
            continue
        aliases = _aliases(row)
        best = max(
            (score_normalized_query(question_variant, alias) for question_variant in question_variants for alias in aliases),
            default=0.0,
        )
        if best < 0.50:
            continue
        negatives = normalized_negative_cache.setdefault(_record_id(row.get("intent_id") or row.get("id")), _negative_examples(row))
        negative_best = max(
            (score_normalized_query(question_variant, negative) for question_variant in question_variants for negative in negatives),
            default=0.0,
        )
        exact_own_alias = bool(set(question_variants) & set(aliases))
        # A nearby procedure used as a hard negative may share a long prefix.
        # It must not suppress the *exact* reviewed alias unless the negative
        # itself is also exact (a genuine catalogue collision).
        if (
            negative_best >= 0.72
            and negative_best >= best - 0.05
            and (not exact_own_alias or negative_best >= 0.999)
        ):
            continue
        candidates.append((best, row))
    candidates.sort(key=lambda item: (-item[0], _record_id(item[1].get("intent_id") or item[1].get("id"))))
    if not candidates:
        return IntentFastPathMatch("miss", "NO_CANDIDATE", release_id=release_id)
    best_score, best_row = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    if best_score < 0.62:
        return IntentFastPathMatch("miss", "MATCH_BELOW_THRESHOLD", best_score, second_score, release_id=release_id)
    required_margin = 0.06 if best_score >= 0.80 else 0.08
    if second_score >= 0.58 and best_score - second_score < required_margin:
        return IntentFastPathMatch("ambiguous", "MULTIPLE_INTENTS", best_score, second_score, release_id=release_id)
    refs = tuple(_source_refs(best_row))
    return IntentFastPathMatch(
        "hit",
        "INTENT_MATCH_CONFIDENT",
        best_score,
        second_score,
        best_row,
        str(best_row.get("answer_text") or best_row.get("answer") or "").strip(),
        str(best_row.get("release_id") or release_id),
        refs,
    )


def intent_match_to_response(
    match: IntentFastPathMatch,
    *,
    question: str,
    rich: bool = False,
) -> dict[str, Any] | None:
    if not match.hit:
        return None
    row = dict(match.intent or {})
    refs = [dict(item) for item in match.source_refs]
    citations = [
        {
            "document_title": ref.get("document_title") or ref.get("title") or "Nguồn thủ tục đã phát hành",
            "source_url": ref.get("source_url"),
            "label": ref.get("label") or "Nguồn chính thức",
            "verification_status": ref.get("verification_status") or "managed_approved",
        }
        for ref in refs
    ]
    response = {
        "answer": match.answer,
        "question": question,
        "citations": citations,
        "procedure_summary": row.get("procedure_name") or row.get("canonical_question"),
        "procedure_id": row.get("procedure_id"),
        "intent_id": row.get("intent_id") or row.get("id"),
        "answer_facet": row.get("intent_type") or row.get("answer_facet") or "overview",
        "grounding_status": "fully_grounded",
        "answer_status": "verified",
        "outcome": "answered",
        "reason_code": match.reason_code,
        "retryable": False,
        "answer_mode": "intent_fast_path",
        "answer_route": "intent_fast_path",
        "llm_used": False,
        "fast_path_release_id": match.release_id,
        "release_id": match.release_id,
        "source_gap": [],
        "generation_provenance": {
            "mode": "intent_fast_path",
            "provider_label": "deterministic",
            "model_calls": 0,
        },
    }
    if rich:
        from api.quick_chat_presentation import enrich_intent_response

        return enrich_intent_response(response, intent=row)
    return response


__all__ = [
    "IntentFastPathMatch",
    "clear_intent_fast_path_cache",
    "intent_fast_path_release_id",
    "intent_match_to_response",
    "is_intent_fast_path_eligible",
    "load_intent_catalog",
    "match_intent_fast_path",
]
