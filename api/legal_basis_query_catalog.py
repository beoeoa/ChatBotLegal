"""Conservative query matching over checksum-bound owner-approved article mappings."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Any, Mapping
import unicodedata

from api.legal_basis_auto_review import verification_sha256
from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_STOP = {"ai", "bao", "can", "cach", "cho", "co", "cua", "duoc", "gi", "khong", "la", "lam", "nao", "nhu", "the", "thi", "toi", "trong", "va", "ve", "voi", "xin"}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")


def _tokens(value: object) -> set[str]:
    return {
        token for token in (_fold(item) for item in _TOKEN.findall(str(value or "")))
        if len(token) >= 2 and token not in _STOP
    }


def _verify(mapping: Mapping[str, Any], acceptance: Mapping[str, Any]) -> set[str]:
    if acceptance.get("acceptance_sha256") != verification_sha256(
        acceptance, "acceptance_sha256"
    ):
        raise ValueError("article_owner_acceptance_checksum_mismatch")
    if acceptance.get("mapping_sha256") != mapping.get("mapping_sha256"):
        raise ValueError("article_owner_acceptance_mapping_mismatch")
    return {str(value) for value in acceptance.get("accepted_basis_ids") or []}


def query_owner_approved_article_hints(
    query: str,
    mapping: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    *,
    blocked_resolution: Mapping[str, Any] | None = None,
    maximum: int = 8,
) -> list[str]:
    accepted_ids = _verify(mapping, acceptance)
    rows = [
        item for item in mapping.get("mappings", [])
        if str(item.get("basis_id") or "") in accepted_ids
        and item.get("decision") == "MAPPED_CANDIDATE"
    ]
    for item in (blocked_resolution or {}).get("resolutions", []):
        if item.get("decision") == "APPROVE_DIRECT_ARTICLE":
            rows.append(item)

    by_procedure: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for item in rows:
        key = (str(item.get("procedure_code") or ""), str(item.get("procedure_name") or ""))
        by_procedure[key].append(item)

    query_tokens = _tokens(query)
    ranked: list[tuple[float, float, float, tuple[str, str], list[Mapping[str, Any]]]] = []
    for key, items in by_procedure.items():
        names = [key[1], *(items[0].get("aliases") or [])]
        best = (0.0, 0.0, 0.0)
        for name in names:
            name_tokens = _tokens(name)
            overlap = len(query_tokens & name_tokens)
            if overlap < 2:
                continue
            name_coverage = overlap / max(1, len(name_tokens))
            query_coverage = overlap / max(1, len(query_tokens))
            score = name_coverage * 0.7 + query_coverage * 0.3
            best = max(best, (score, name_coverage, query_coverage))
        if best[1] >= 0.6 and best[2] >= 0.2:
            ranked.append((*best, key, items))
    ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
    if not ranked:
        return []
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08:
        first_keys = {
            (normalize_exact(item.get("law_number")), normalize_exact(article))
            for item in ranked[0][4]
            for article in item.get("selected_article_numbers") or []
        }
        second_keys = {
            (normalize_exact(item.get("law_number")), normalize_exact(article))
            for item in ranked[1][4]
            for article in item.get("selected_article_numbers") or []
        }
        common = first_keys & second_keys
        return [f"{law}|{article}" for law, article in sorted(common)][:maximum]
    hints = [
        f"{normalize_exact(item.get('law_number'))}|{normalize_exact(article)}"
        for item in ranked[0][4]
        for article in item.get("selected_article_numbers") or []
    ]
    return list(dict.fromkeys(hints))[:maximum]


__all__ = ["query_owner_approved_article_hints"]
