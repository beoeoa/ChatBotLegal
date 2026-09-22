"""Fail-closed automated resolution for article mappings lacking direct support."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
import sqlite3
from typing import Any, Mapping
import unicodedata

from api.legal_basis_auto_review import verification_sha256


_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_STOP = {"cho", "cua", "duoc", "la", "lam", "mot", "nguoi", "quy", "thuc", "thu", "tuc", "va", "ve", "viec", "xin"}
_GENERIC_HEADING = re.compile(
    r"\b(?:pham vi|doi tuong|trach nhiem|to chuc thuc hien|dieu khoan thi hanh|"
    r"hieu luc thi hanh|sua doi|bo sung|bai bo)\b"
)


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char)).replace("đ", "d")


def _tokens(value: object) -> list[str]:
    return [
        token for token in (_fold(item) for item in _TOKEN.findall(str(value or "")))
        if len(token) >= 2 and token not in _STOP
    ]


def _rows(connection: sqlite3.Connection, document_id: int) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT chunk_revision_id,document_id,article_number,structural_path,content,source_url "
        "FROM chunks WHERE document_id=? AND document_serving_state='current_retrievable' "
        "AND article_number IS NOT NULL AND trim(article_number)<>'' "
        "ORDER BY article_number,chunk_revision_id",
        (document_id,),
    ).fetchall()


def _rank(query: str, rows: list[sqlite3.Row], allowed: set[str]) -> list[dict[str, Any]]:
    query_terms = set(_tokens(query))
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        article = str(row["article_number"] or "").strip()
        if article in allowed:
            grouped[article].append(row)
    ranked: list[dict[str, Any]] = []
    for article, values in grouped.items():
        best: dict[str, Any] | None = None
        for row in values:
            heading = set(_tokens(row["structural_path"]))
            content = set(_tokens(row["content"]))
            heading_overlap = len(query_terms & heading)
            content_overlap = len(query_terms & content)
            coverage = len(query_terms & (heading | content)) / max(1, len(query_terms))
            score = heading_overlap * 4 + content_overlap + coverage
            candidate = {
                "article_number": article,
                "score": score,
                "heading_overlap": heading_overlap,
                "content_overlap": content_overlap,
                "query_coverage": coverage,
                "row": row,
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        if best:
            ranked.append(best)
    return sorted(ranked, key=lambda item: (-item["score"], item["article_number"]))


def _instrument_decision(title: str) -> str:
    folded = _fold(title)
    if "bai bo" in folded:
        return "EFFECTIVITY_ONLY"
    return "APPROVE_INSTRUMENT_SCOPE"


def resolve_blocked_mappings(
    adjudication: Mapping[str, Any],
    mapping: Mapping[str, Any],
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    blocked_ids = {
        str(item.get("basis_id") or "")
        for item in mapping.get("mappings", [])
        if item.get("decision") == "BLOCKED_NO_DIRECT_SUPPORT"
    }
    resolutions: list[dict[str, Any]] = []
    for entry in adjudication.get("entries", []):
        query = " ".join([str(entry.get("procedure_name") or ""), *(entry.get("aliases") or [])])
        for basis in entry.get("legal_bases", []):
            basis_id = str(basis.get("basis_id") or "")
            if basis_id not in blocked_ids:
                continue
            ranked = _rank(
                query,
                _rows(connection, int(basis["document_id"])),
                {str(value) for value in basis.get("available_article_numbers") or []},
            )
            best = ranked[0] if ranked else None
            runner_up = ranked[1]["score"] if len(ranked) > 1 else 0.0
            heading_text = _fold(best["row"]["structural_path"]) if best else ""
            direct = bool(
                best
                and best["query_coverage"] >= 0.5
                and best["heading_overlap"] >= 2
                and not _GENERIC_HEADING.search(heading_text)
                and best["score"] - runner_up >= 0.5
            )
            support: list[dict[str, Any]] = []
            selected: list[str] = []
            if direct and best:
                row = best["row"]
                decision = "APPROVE_DIRECT_ARTICLE"
                selected = [best["article_number"]]
                support = [{
                    "article_number": best["article_number"],
                    "chunk_revision_id": row["chunk_revision_id"],
                    "source_url": row["source_url"],
                    "structural_path": row["structural_path"],
                    "quote": str(row["content"] or "")[:700],
                    "score": best["score"],
                    "query_coverage": best["query_coverage"],
                }]
            else:
                decision = _instrument_decision(str(basis.get("title") or ""))
            resolutions.append({
                "entry_id": entry.get("entry_id"),
                "procedure_code": entry.get("procedure_code"),
                "procedure_name": entry.get("procedure_name"),
                "basis_id": basis_id,
                "law_number": basis.get("law_number"),
                "official_url": basis.get("official_url"),
                "official_title": basis.get("title"),
                "serving_state": basis.get("serving_state"),
                "decision": decision,
                "selected_article_numbers": selected,
                "support": support,
                "claim_evidence_eligible": decision == "APPROVE_DIRECT_ARTICLE",
                "scope_note": (
                    "Direct article evidence" if direct
                    else "Official instrument retained only for amendment/form/effectivity context"
                ),
            })
    if {item["basis_id"] for item in resolutions} != blocked_ids:
        raise ValueError("blocked_mapping_scope_mismatch")
    counts = Counter(item["decision"] for item in resolutions)
    result = {
        "schema_version": "legal-basis-blocked-resolution-v1",
        "adjudication_sha256": adjudication.get("adjudication_sha256"),
        "input_mapping_sha256": mapping.get("mapping_sha256"),
        "resolutions": resolutions,
        "summary": {"input_blocked_count": len(blocked_ids), "decision_counts": dict(sorted(counts.items()))},
        "official_sources_only": True,
        "automated_owner_directed": True,
        "independent_human_legal_qa_complete": False,
        "runtime_eligible": False,
        "database_mutated": False,
    }
    result["resolution_sha256"] = verification_sha256(result, "resolution_sha256")
    return result


__all__ = ["resolve_blocked_mappings"]
