"""Evidence-quoted candidate article mapping for owner-reviewed legal bases.

This produces review candidates only. Lexical overlap cannot replace
independent legal judgment, so the resulting artifact is runtime-ineligible.
"""

from __future__ import annotations

from collections import defaultdict
import re
import sqlite3
from typing import Any, Mapping
import unicodedata

from api.legal_basis_auto_review import verification_sha256
from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_STOP = {
    "cap", "cho", "cua", "duoc", "giay", "ho", "la", "lam", "mot", "nguoi",
    "quy", "thuc", "thu", "tuc", "va", "ve", "viec", "xin",
}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")


def _tokens(value: object) -> set[str]:
    return {
        token for token in (_fold(item) for item in _TOKEN.findall(str(value or "")))
        if len(token) >= 3 and token not in _STOP
    }


def _article_rows_by_document(
    connection: sqlite3.Connection, document_ids: set[int]
) -> dict[int, list[sqlite3.Row]]:
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    rows = connection.execute(
        "SELECT chunk_revision_id,document_id,article_number,structural_path,content,source_url "
        f"FROM chunks WHERE document_id IN ({placeholders}) AND document_serving_state='current_retrievable' "
        "AND article_number IS NOT NULL AND trim(article_number)<>'' "
        "ORDER BY document_id,article_number,chunk_revision_id",
        tuple(sorted(document_ids)),
    ).fetchall()
    output: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        output[int(row["document_id"])].append(row)
    return output


def _rank_articles(
    query: str,
    rows: list[sqlite3.Row],
    allowed: set[str],
    token_cache: dict[str, tuple[set[str], set[str]]],
) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        article = str(row["article_number"] or "").strip()
        if article in allowed:
            grouped[article].append(row)
    ranked: list[dict[str, Any]] = []
    for article, article_rows in grouped.items():
        best: tuple[float, int, int, sqlite3.Row] | None = None
        for row in article_rows:
            chunk_id = str(row["chunk_revision_id"])
            if chunk_id not in token_cache:
                token_cache[chunk_id] = (
                    _tokens(row["structural_path"]),
                    _tokens(row["content"]),
                )
            heading_tokens, content_tokens = token_cache[chunk_id]
            heading_overlap = len(query_tokens & heading_tokens)
            content_overlap = len(query_tokens & content_tokens)
            score = heading_overlap * 3.0 + content_overlap * 1.0
            candidate = (score, heading_overlap, content_overlap, row)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
        if best:
            ranked.append({
                "article_number": article,
                "score": best[0],
                "heading_overlap": best[1],
                "content_overlap": best[2],
                "row": best[3],
            })
    return sorted(ranked, key=lambda item: (-item["score"], -item["heading_overlap"], item["article_number"]))


def build_article_mapping(
    adjudication: Mapping[str, Any], connection: sqlite3.Connection
) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = []
    document_ids = {
        int(basis["document_id"])
        for entry in adjudication.get("entries", [])
        for basis in entry.get("legal_bases", [])
        if basis.get("final_decision") == "APPROVE_INDEXED"
    }
    document_cache = _article_rows_by_document(connection, document_ids)
    token_cache: dict[str, tuple[set[str], set[str]]] = {}
    for entry in adjudication.get("entries", []):
        query = " ".join([str(entry.get("procedure_name") or ""), *(entry.get("aliases") or [])])
        for basis in entry.get("legal_bases", []):
            if basis.get("final_decision") != "APPROVE_INDEXED":
                continue
            document_id = int(basis["document_id"])
            rows = document_cache.get(document_id, [])
            ranked = _rank_articles(
                query,
                rows,
                {str(value) for value in basis.get("available_article_numbers") or []},
                token_cache,
            )
            best = ranked[0] if ranked else None
            # Two distinct heading terms, or one heading plus two content terms,
            # is the minimum deterministic evidence for a review candidate.
            supported = bool(
                best
                and (
                    best["heading_overlap"] >= 2
                    or (best["heading_overlap"] >= 1 and best["content_overlap"] >= 2)
                )
            )
            support = []
            selected: list[str] = []
            if supported and best:
                row = best["row"]
                selected = [best["article_number"]]
                support = [{
                    "article_number": best["article_number"],
                    "chunk_revision_id": row["chunk_revision_id"],
                    "source_url": row["source_url"],
                    "structural_path": row["structural_path"],
                    "quote": str(row["content"] or "")[:700],
                    "score": best["score"],
                }]
            mappings.append({
                "entry_id": entry.get("entry_id"),
                "procedure_code": entry.get("procedure_code"),
                "procedure_name": entry.get("procedure_name"),
                "aliases": list(entry.get("aliases") or []),
                "basis_id": basis.get("basis_id"),
                "law_number": basis.get("law_number"),
                "decision": "MAPPED_CANDIDATE" if supported else "BLOCKED_NO_DIRECT_SUPPORT",
                "selected_article_numbers": selected,
                "support": support,
                "top_candidate_articles": [
                    {key: item[key] for key in ("article_number", "score", "heading_overlap", "content_overlap")}
                    for item in ranked[:3]
                ],
            })
    counts: dict[str, int] = defaultdict(int)
    for item in mappings:
        counts[item["decision"]] += 1
    result = {
        "schema_version": "legal-basis-article-mapping-candidate-v1",
        "adjudication_sha256": adjudication["adjudication_sha256"],
        "mappings": mappings,
        "summary": {
            "indexed_candidate_count": len(mappings),
            "decision_counts": dict(sorted(counts.items())),
        },
        "mapping_method": "deterministic_heading_content_overlap_with_direct_quote",
        "independent_human_legal_qa_complete": False,
        "runtime_eligible": False,
        "database_mutated": False,
    }
    result["mapping_sha256"] = verification_sha256(result, "mapping_sha256")
    return result


def query_article_hints(
    query: str, mapping: Mapping[str, Any], *, maximum: int = 6
) -> list[str]:
    """Resolve query-only article hints from the reviewed procedure catalog."""

    query_tokens = _tokens(query)
    ranked: list[tuple[float, int, str]] = []
    for item in mapping.get("mappings", []):
        if item.get("decision") != "MAPPED_CANDIDATE":
            continue
        names = " ".join(
            [str(item.get("procedure_name") or ""), *(item.get("aliases") or [])]
        )
        name_tokens = _tokens(names)
        overlap = len(query_tokens & name_tokens)
        if overlap < 3 or not name_tokens:
            continue
        containment = overlap / min(len(query_tokens), len(name_tokens))
        if containment < 0.5:
            continue
        for article in item.get("selected_article_numbers") or []:
            ranked.append(
                (
                    containment,
                    overlap,
                    f"{normalize_exact(item.get('law_number'))}|{normalize_exact(article)}",
                )
            )
    return list(
        dict.fromkeys(value for _, _, value in sorted(ranked, key=lambda row: (-row[0], -row[1], row[2])))
    )[:maximum]


__all__ = ["build_article_mapping", "query_article_hints"]
