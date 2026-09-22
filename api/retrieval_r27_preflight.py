"""Read-only source inventory for the Retrieval r27 preflight.

Expected labels are used only to audit immutable SQLite coverage after a
candidate run.  This module is deliberately separate from query-time ranking.
"""

from __future__ import annotations

import sqlite3
import re
import unicodedata
from typing import Any, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact, source_matches
from api.retrieval_candidate_r27 import (
    explicit_law_article_keys_r27,
    explicit_law_numbers_r27,
)


SCHEMA_VERSION = "legal-retrieval-r27-source-inventory-v1"
_REQUIRED_METADATA = (
    "chunk_revision_id",
    "document_id",
    "article_id",
    "document_serving_state",
    "law_number",
    "article_number",
    "domain_slug",
    "source_url",
    "effective_from",
    "content",
    "structural_path",
)
_QUERY_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_QUERY_STOPWORDS = {
    "ai", "ban", "bao", "biet", "can", "cach", "cho", "co", "cua", "dinh",
    "diem", "doi", "dong", "duoc", "gan", "gi", "hien", "hoi", "khong", "khi",
    "la", "lam", "nao", "neu", "nguon", "nhu", "quy", "ra", "rieng", "sao",
    "sap", "tai", "theo", "the", "thi", "thoi", "thuc", "toi", "trong", "tuong", "tung",
    "va", "van", "ve", "voi", "xac", "xet", "xin",
}
_QUOTED_FACET = re.compile(r"[“\"]([^”\"]{8,})[”\"]")
_ARTICLE_TOKEN = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)


def groups_absent_from_top50(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = list(row.get("candidates") or [])[:50]
    absent: list[dict[str, Any]] = []
    for group in (row.get("case") or {}).get("positive_source_groups") or []:
        sources = [source for source in group.get("sources") or [] if isinstance(source, Mapping)]
        if sources and not any(
            source_matches(candidate, source)
            for candidate in candidates
            for source in sources
        ):
            absent.append(dict(group))
    return absent


def _rows_for_key(connection: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT c.chunk_revision_id, c.document_id, c.article_id,
               c.document_serving_state, c.law_number, c.article_number,
               c.domain_slug, c.source_url, c.effective_from, c.effective_to,
               c.content, c.structural_path
          FROM exact_lookup AS e
          JOIN chunks AS c ON c.chunk_revision_id = e.chunk_revision_id
         WHERE e.key_kind = 'law_article' AND e.normalized_key = ?
         ORDER BY c.chunk_index, c.chunk_revision_id
        """,
        (key,),
    ).fetchall()
    law, _, article = key.partition("|")
    return [
        row
        for row in rows
        if normalize_exact(row["law_number"]) == law
        and normalize_exact(row["article_number"]) == article
    ]


def _law_exists(connection: sqlite3.Connection, law: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM exact_lookup WHERE key_kind='law_number' AND normalized_key=? LIMIT 1",
        (law,),
    ).fetchone() is not None


def audit_source_group(
    connection: sqlite3.Connection,
    group: Mapping[str, Any],
    *,
    metadata_overlays: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    alternatives: list[dict[str, Any]] = []
    exact_rows: list[sqlite3.Row] = []
    any_law = False
    for source in group.get("sources") or []:
        law = normalize_exact(source.get("law_number"))
        article = normalize_exact(source.get("article"))
        if not law:
            continue
        any_law = any_law or _law_exists(connection, law)
        rows = _rows_for_key(connection, f"{law}|{article}") if article else []
        alternatives.append(
            {"law_number_normalized": law, "article_normalized": article, "chunk_count": len(rows)}
        )
        exact_rows.extend(rows)

    if not exact_rows:
        classification = "law_present_article_absent" if any_law else "law_absent"
        return {
            "group_id": str(group.get("group_id") or ""),
            "classification": classification,
            "matching_chunk_count": 0,
            "metadata_missing": [],
            "alternatives": alternatives,
        }

    overlays = metadata_overlays or {}
    overlay_applied = False
    missing = sorted(
        {
            field
            for row in exact_rows
            for field in _REQUIRED_METADATA
            if (
                row[field] is None
                or (isinstance(row[field], str) and not row[field].strip())
            )
            and not str(
                overlays.get(
                    (
                        normalize_exact(row["law_number"]),
                        normalize_exact(row["article_number"]),
                    ),
                    {},
                ).get(field) or ""
            ).strip()
        }
    )
    overlay_applied = any(
        bool(
            overlays.get(
                (normalize_exact(row["law_number"]), normalize_exact(row["article_number"])),
                {},
            )
        )
        for row in exact_rows
    )
    return {
        "group_id": str(group.get("group_id") or ""),
        "classification": (
            "exact_source_metadata_complete" if not missing else "exact_source_metadata_incomplete"
        ),
        "matching_chunk_count": len(exact_rows),
        "metadata_missing": missing,
        "metadata_overlay_applied": overlay_applied,
        "alternatives": alternatives,
    }


def audit_absent_groups(
    connection: sqlite3.Connection,
    rows: Sequence[Mapping[str, Any]],
    *,
    metadata_overlays: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    for row in rows:
        case = row.get("case") or {}
        for group in groups_absent_from_top50(row):
            report.append(
                {
                    "case_id": str(case.get("case_id") or ""),
                    "split": str(case.get("split") or ""),
                    "domain": str(case.get("domain") or ""),
                    **audit_source_group(
                        connection, group, metadata_overlays=metadata_overlays
                    ),
                }
            )
    return report


def _query_terms(query: str) -> list[str]:
    values = [value.casefold() for value in _QUERY_TOKEN.findall(query)]
    return list(
        dict.fromkeys(
            value
            for value in values
            if len(value) >= 2
            and _vocab_key(value).replace("đ", "d") not in _QUERY_STOPWORDS
            and not value.isdigit()
            and not re.fullmatch(
                r"(?:qh|nd|tt|nq|qd)\d*", normalize_exact(value).casefold()
            )
        )
    )


def _vocab_key(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _query_phrases(query: str) -> list[str]:
    """Return bounded Vietnamese bi/tri-grams with substantive content.

    FTS tokenizes Vietnamese into whitespace-delimited syllables.  Keeping
    adjacent syllables together distinguishes legal concepts such as
    ``hợp thửa`` and ``khiếu nại`` from documents that contain the same
    syllables in unrelated positions.
    """

    values = [value.casefold() for value in _QUERY_TOKEN.findall(query)]
    output: list[str] = []
    for width in (3, 2):
        for start in range(0, len(values) - width + 1):
            phrase = values[start : start + width]
            normalized = [_vocab_key(value).replace("đ", "d") for value in phrase]
            if any(value.isdigit() for value in phrase):
                continue
            if any(re.fullmatch(r"(?:qh|nd|tt|nq|qd)\d*", value) for value in normalized):
                continue
            substantive = [value for value in normalized if value not in _QUERY_STOPWORDS]
            if len(substantive) < 2:
                continue
            output.append(" ".join(phrase))
    return list(dict.fromkeys(output))[:24]


def _facets(query: str) -> list[str]:
    quoted = [value.strip() for value in _QUOTED_FACET.findall(query) if value.strip()]
    return list(dict.fromkeys(quoted)) if quoted else [query]


def _scope_clause(scope: str, as_of: str) -> tuple[str, list[str]]:
    state = (
        "c.document_serving_state='current_retrievable'"
        if scope == "current"
        else "c.document_serving_state IN ('current_retrievable','historical_only')"
    )
    return (
        f"{state} AND (c.effective_from IS NULL OR c.effective_from='' OR c.effective_from<=?) "
        "AND (c.effective_to IS NULL OR c.effective_to='' OR c.effective_to>?)",
        [as_of[:10], as_of[:10]],
    )


def recover_query_candidates(
    connection: sqlite3.Connection,
    query: str,
    *,
    scope: str,
    as_of: str,
    maximum: int = 80,
    term_frequencies: Mapping[str, int] | None = None,
    candidate_laws: Sequence[str] = (),
    catalog_article_hints: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Recover a bounded lexical pool using query-only rare-term conjunctions.

    Benchmark labels and case identifiers are intentionally absent from this
    interface.  The function reads the immutable FTS/chunks tables only.
    """

    scope_sql, scope_params = _scope_clause(scope, as_of)
    direct_rows: list[sqlite3.Row] = []
    for key in dict.fromkeys(explicit_law_article_keys_r27(query)):
        direct_rows.extend(_rows_for_key(connection, key))
    catalog_rows: list[sqlite3.Row] = []
    for key in dict.fromkeys(catalog_article_hints):
        catalog_rows.extend(_rows_for_key(connection, key))
    explicit_laws = explicit_law_numbers_r27(query)
    preferred_law_rank = {
        law: index
        for index, law in enumerate(
            dict.fromkeys(
                normalize_exact(value) for value in candidate_laws if normalize_exact(value)
            )
        )
    }
    explicit_articles = list(
        dict.fromkeys(normalize_exact(value) for value in _ARTICLE_TOKEN.findall(query))
    )
    rows: list[sqlite3.Row] = list(direct_rows)
    for facet in _facets(query):
        phrase_frequencies: list[tuple[int, int, str]] = []
        for position, phrase in enumerate(_query_phrases(facet)):
            expression = '"' + phrase.replace('"', " ") + '"'
            found = connection.execute(
                "SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH ?", (expression,)
            ).fetchone()
            frequency = int(found[0] if found else 0)
            if frequency:
                phrase_frequencies.append((frequency, position, phrase))
        phrase_frequencies.sort(key=lambda value: (value[0], value[1], value[2]))
        ranked_phrases = [phrase for _, _, phrase in phrase_frequencies[:6]]
        frequencies: list[tuple[int, int, str]] = []
        for position, term in enumerate(_query_terms(facet)):
            expression = '"' + term.replace('"', " ") + '"'
            if term_frequencies is not None:
                frequency = int(
                    term_frequencies.get(term, term_frequencies.get(_vocab_key(term), 0))
                )
            else:
                found = connection.execute(
                    "SELECT count(*) FROM chunk_fts WHERE chunk_fts MATCH ?", (expression,)
                ).fetchone()
                frequency = int(found[0] if found else 0)
            if frequency:
                frequencies.append((frequency, position, term))
        frequencies.sort(key=lambda value: (value[0], value[1], value[2]))
        ranked_terms = [term for _, _, term in frequencies[:8]]
        if not ranked_terms:
            continue
        facet_rows: list[sqlite3.Row] = []
        attempts: list[str] = []
        if len(ranked_phrases) >= 2:
            attempts.append(
                " AND ".join('"' + phrase.replace('"', " ") + '"' for phrase in ranked_phrases[:2])
            )
        attempts.extend('"' + phrase.replace('"', " ") + '"' for phrase in ranked_phrases[:4])
        for count in range(min(4, len(ranked_terms)), 1, -1):
            attempts.append(
                " AND ".join(
                    '"' + term.replace('"', " ") + '"' for term in ranked_terms[:count]
                )
            )
        attempts.append(
            " OR ".join(
                '"' + term.replace('"', " ") + '"' for term in ranked_terms[:2]
            )
        )
        match_stats: dict[tuple[str, str], dict[str, Any]] = {}
        for attempt_index, expression in enumerate(dict.fromkeys(attempts)):
            filters: list[str] = []
            filter_params: list[str] = []
            if explicit_laws:
                filters.append(
                    "replace(replace(replace(upper(c.law_number),'/',' '),'-',' '),'  ',' ') IN ("
                    + ",".join("?" for _ in explicit_laws)
                    + ")"
                )
                filter_params.extend(explicit_laws)
            if explicit_articles:
                filters.append(
                    "replace(replace(upper(c.article_number),'Đ','D'),' ', '') IN ("
                    + ",".join("?" for _ in explicit_articles)
                    + ")"
                )
                filter_params.extend(value.replace(" ", "") for value in explicit_articles)
            filter_sql = " AND " + " AND ".join(filters) if filters else ""
            attempt_rows = connection.execute(
                "SELECT c.*,chunk_fts.rank AS recovery_rank FROM chunk_fts "
                "JOIN chunks c ON c.chunk_revision_id=chunk_fts.chunk_revision_id "
                f"WHERE chunk_fts MATCH ? AND {scope_sql}{filter_sql} "
                "ORDER BY chunk_fts.rank,c.chunk_revision_id LIMIT ?",
                [expression, *scope_params, *filter_params, maximum * 2],
            ).fetchall()
            seen_attempt: set[tuple[str, str]] = set()
            for row_index, row in enumerate(attempt_rows):
                identity = (
                    normalize_exact(row["law_number"]),
                    normalize_exact(row["article_number"]),
                )
                if identity in seen_attempt:
                    continue
                seen_attempt.add(identity)
                state = match_stats.setdefault(
                    identity,
                    {
                        "row": row,
                        "attempt_hits": 0,
                        "best_attempt": attempt_index,
                        "best_row": row_index,
                    },
                )
                state["attempt_hits"] += 1
                if (attempt_index, row_index) < (state["best_attempt"], state["best_row"]):
                    state.update(row=row, best_attempt=attempt_index, best_row=row_index)
        facet_rows = [
            state["row"]
            for _, state in sorted(
                match_stats.items(),
                key=lambda item: (
                    preferred_law_rank.get(item[0][0], len(preferred_law_rank) + 1),
                    item[1]["best_attempt"],
                    -item[1]["attempt_hits"],
                    item[1]["best_row"],
                    item[0],
                ),
            )[: maximum * 2]
        ]
        rows.extend(facet_rows)
    rows.extend(catalog_rows)
    output: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str]] = set()
    for row in rows:
        identity = (
            normalize_exact(row["law_number"]),
            normalize_exact(row["article_number"]),
        )
        if identity == ("", "") or identity in seen_identities:
            continue
        seen_identities.add(identity)
        output.append({key: row[key] for key in row.keys() if key != "recovery_rank"})
        output[-1]["retrieval_source"] = "r27_document_article_recovery"
        output[-1]["score"] = 1.0 / len(output)
        if len(output) >= maximum:
            break
    return output


__all__ = [
    "SCHEMA_VERSION",
    "audit_absent_groups",
    "audit_source_group",
    "groups_absent_from_top50",
    "recover_query_candidates",
]
