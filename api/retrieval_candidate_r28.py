"""Final shadow Retrieval r28 query-only coverage recovery.

This module is deliberately standalone so the exact same implementation can
run locally and in a private Kaggle kernel.  It never accepts benchmark case
IDs or expected-source labels and only reads the frozen SQLite/catalog inputs.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping, Sequence
import unicodedata

from api.retrieval_candidate_r27 import explicit_law_article_keys_r27


R28_CANDIDATE_PROFILE = "r28-final-catalog-coverage"
_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.UNICODE)
_QUOTED_FACET = re.compile(r"[“\"]([^”\"]{8,})[”\"]")
# Many benchmark/user prompts put the legal heading after a cue rather than
# quoting it.  Keep this extractor deliberately conservative: it only accepts
# text after a legal-topic cue and stops at a question delimiter or a common
# explanatory tail.  The result is still just an FTS hint; it never creates an
# identity or overrides the serving-state filter.
_NAMED_FACET = re.compile(
    r"(?:\b(?:liên quan đến|quy định về|đối với|về|thực hiện)\b)\s+"
    r"([^?;]+?)(?=(?:;|\?|,?\s+(?:nội dung|điều luật|được áp dụng|có nội dung|xác định|phải)\b|$))",
    re.IGNORECASE,
)
_STOP = {
    "ai", "ban", "bao", "can", "cach", "cho", "co", "cua", "duoc", "gi",
    "hien", "hoi", "khong", "la", "lam", "nao", "nhu", "quy", "the",
    "theo", "thi", "toi", "trong", "va", "ve", "voi", "xin",
}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch)).replace("đ", "d")


def _normalize_exact(value: object) -> str:
    return " ".join(re.findall(r"[0-9a-z]+", _fold(value).upper().casefold())).upper()


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in (_fold(raw) for raw in _TOKEN.findall(str(value or "")))
        if len(token) >= 2 and token not in _STOP and not token.isdigit()
    }


def _trigrams(value: object) -> set[str]:
    compact = " ".join(_fold(value).split())
    return {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}


def _semantic_sha(payload: Mapping[str, Any], field: str) -> str:
    clean = dict(payload)
    clean.pop(field, None)
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _accepted_rows(
    mapping: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    blocked_resolution: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    if acceptance.get("acceptance_sha256") != _semantic_sha(acceptance, "acceptance_sha256"):
        raise ValueError("article_owner_acceptance_checksum_mismatch")
    if acceptance.get("mapping_sha256") != mapping.get("mapping_sha256"):
        raise ValueError("article_owner_acceptance_mapping_mismatch")
    accepted = {str(value) for value in acceptance.get("accepted_basis_ids") or []}
    rows = [
        row for row in mapping.get("mappings", [])
        if str(row.get("basis_id") or "") in accepted
        and row.get("decision") == "MAPPED_CANDIDATE"
    ]
    rows.extend(
        row for row in (blocked_resolution or {}).get("resolutions", [])
        if row.get("decision") == "APPROVE_DIRECT_ARTICLE"
    )
    return rows


def query_catalog_hints_r28(
    query: str,
    mapping: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    *,
    blocked_resolution: Mapping[str, Any] | None = None,
    maximum: int = 10,
) -> list[str]:
    """Return article hints from independently frozen procedure names/aliases."""

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in _accepted_rows(mapping, acceptance, blocked_resolution):
        grouped[(str(row.get("procedure_code") or ""), str(row.get("procedure_name") or ""))].append(row)
    query_tokens = _tokens(query)
    query_grams = _trigrams(query)
    ranked: list[tuple[float, tuple[str, str], list[Mapping[str, Any]]]] = []
    for key, rows in grouped.items():
        names = [key[1], *(rows[0].get("aliases") or [])]
        best = 0.0
        for name in names:
            name_tokens = _tokens(name)
            overlap = len(query_tokens & name_tokens)
            if overlap < 2:
                continue
            precision = overlap / max(1, len(query_tokens))
            recall = overlap / max(1, len(name_tokens))
            token_f1 = 2 * precision * recall / max(1e-9, precision + recall)
            grams = _trigrams(name)
            trigram_dice = 2 * len(query_grams & grams) / max(1, len(query_grams) + len(grams))
            phrase_bonus = 0.12 if _fold(name) in _fold(query) else 0.0
            best = max(best, token_f1 * 0.7 + trigram_dice * 0.3 + phrase_bonus)
        if best >= 0.52:
            ranked.append((best, key, rows))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return []
    selected = [ranked[0]]
    if len(ranked) > 1 and ranked[1][0] >= 0.68 and ranked[0][0] - ranked[1][0] <= 0.06:
        selected.append(ranked[1])
    hints = [
        f"{_normalize_exact(row.get('law_number'))}|{_normalize_exact(article)}"
        for _, _, rows in selected
        for row in rows
        for article in row.get("selected_article_numbers") or []
    ]
    return list(dict.fromkeys(hint for hint in hints if hint != "|"))[:maximum]


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


def _rows_for_key(
    connection: sqlite3.Connection,
    key: str,
    *,
    scope: str,
    as_of: str,
) -> list[sqlite3.Row]:
    scope_sql, scope_params = _scope_clause(scope, as_of)
    rows = connection.execute(
        "SELECT c.* FROM exact_lookup e JOIN chunks c ON c.chunk_revision_id=e.chunk_revision_id "
        f"WHERE e.key_kind='law_article' AND e.normalized_key=? AND {scope_sql} "
        "ORDER BY c.chunk_index,c.chunk_revision_id",
        [key, *scope_params],
    ).fetchall()
    law, _, article = key.partition("|")
    return [
        row for row in rows
        if _normalize_exact(row["law_number"]) == law
        and _normalize_exact(row["article_number"]) == article
    ]


def _quoted_structural_phrases(query: str, *, maximum: int = 8) -> list[str]:
    """Return user-authored legal headings suitable for column-scoped FTS.

    Vietnamese legal headings are commonly supplied inside curly or straight
    quotes.  Keeping their original token order is substantially more precise
    than selecting rare syllables from the whole prompt, especially for a
    multi-issue question.  Only query text is used; no corpus label, case ID or
    expected source enters this branch.
    """

    phrases: list[str] = []
    for raw in _QUOTED_FACET.findall(str(query or "")):
        tokens = _TOKEN.findall(raw)
        if len(tokens) < 3:
            continue
        phrase = " ".join(tokens[:40]).strip()
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= max(1, int(maximum)):
            break
    return phrases


def _issue_facets(
    query: str, *, maximum: int = 8
) -> list[tuple[str, str]]:
    """Extract quoted or cue-delimited issue titles and content hints.

    Quoted facets remain authoritative when present.  Cue-delimited facets are
    a bounded lexical recovery path for natural prompts such as ``quy định về
    Quản lý, sử dụng ...``; they are never treated as exact citations.
    """

    matches = list(_QUOTED_FACET.finditer(str(query or "")))[: max(1, int(maximum))]
    output: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        boundary = matches[index + 1].start() if index + 1 < len(matches) else len(query)
        tail = str(query)[match.end() : boundary]
        opened = tail.find("(")
        closed = tail.rfind(")")
        context = tail[opened + 1 : closed].strip() if 0 <= opened < closed else ""
        context = re.sub(
            r"^\s*(?:phần|phan)\s+\d+\s*:\s*",
            "",
            context,
            flags=re.IGNORECASE,
        )
        output.append((match.group(1).strip(), context))
    if len(output) < max(1, int(maximum)):
        # Only add unquoted facets when no quoted span already covers the same
        # text.  This avoids duplicate votes in mixed prompts.
        existing = {_fold(title) for title, _ in output}
        for match in _NAMED_FACET.finditer(str(query or "")):
            title = re.sub(r"\s+", " ", match.group(1)).strip(" ,:")
            if len(_TOKEN.findall(title)) < 3:
                continue
            folded = _fold(title)
            if not folded or folded in existing:
                continue
            output.append((title, ""))
            existing.add(folded)
            if len(output) >= max(1, int(maximum)):
                break
    if not output:
        # A small fallback handles prompts that introduce a provision with
        # ``thực hiện ... điều luật này`` but do not use a cue above.
        match = re.search(
            r"\bthực hiện\s+([^,?;]+?)(?=,?\s+điều luật\b)",
            str(query or ""),
            flags=re.IGNORECASE,
        )
        if match and len(_TOKEN.findall(match.group(1))) >= 3:
            output.append((match.group(1).strip(), ""))
    return output


def _phrase_variants(value: str, *, maximum: int = 10) -> list[str]:
    """Create bounded exact-token phrases, preferring the authored wording."""

    tokens = _TOKEN.findall(str(value or ""))[:48]
    if len(tokens) < 3:
        return []
    output = [" ".join(tokens)]
    for width in (10, 8, 6, 4):
        if len(tokens) < width:
            continue
        last = len(tokens) - width
        starts = list(dict.fromkeys((0, last, last // 2)))
        for start in starts:
            phrase = " ".join(tokens[start : start + width])
            if phrase not in output:
                output.append(phrase)
            if len(output) >= maximum:
                return output
    return output[:maximum]


def _facet_signal_rows(
    connection: sqlite3.Connection,
    *,
    column: str,
    value: str,
    scope_sql: str,
    scope_params: Sequence[str],
    limit: int,
    allow_shorter_variants: bool = True,
) -> list[sqlite3.Row]:
    if column not in {"structural_path", "content"}:
        raise ValueError("r28_facet_column_invalid")
    # Stop at the first phrase variant with evidence.  Shorter variants are a
    # deterministic degradation path for truncated user quotations, not extra
    # votes that could inflate a noisy identity.
    variants = _phrase_variants(value)
    if not allow_shorter_variants:
        variants = variants[:1]
    for phrase in variants:
        expression = f'{column} : "{phrase}"'
        rows = connection.execute(
            "SELECT c.* FROM chunk_fts JOIN chunks c "
            "ON c.chunk_revision_id=chunk_fts.chunk_revision_id "
            f"WHERE chunk_fts MATCH ? AND {scope_sql} "
            "ORDER BY chunk_fts.rank,c.chunk_revision_id LIMIT ?",
            [expression, *scope_params, max(1, int(limit))],
        ).fetchall()
        if rows:
            return rows
    return []


def recover_candidates_r28(
    connection: sqlite3.Connection,
    query: str,
    *,
    scope: str,
    as_of: str,
    catalog_hints: Sequence[str] = (),
    maximum: int = 30,
) -> list[dict[str, Any]]:
    """Recover unique legal articles from catalog hints and bounded FTS."""

    scope_sql, scope_params = _scope_clause(scope, as_of)
    rows: list[tuple[sqlite3.Row, str]] = []
    for key in dict.fromkeys(explicit_law_article_keys_r27(query)):
        rows.extend(
            (row, "r28_explicit_exact")
            for row in _rows_for_key(
                connection, key, scope=scope, as_of=as_of
            )
        )
    for key in dict.fromkeys(catalog_hints):
        rows.extend(
            (row, "r28_catalog_exact")
            for row in _rows_for_key(
                connection, key, scope=scope, as_of=as_of
            )
        )

    # Score each issue independently, then interleave identities across issues.
    # This preserves both sides of a multi-issue prompt in the bounded Top-10.
    facet_rankings: list[list[tuple[sqlite3.Row, str]]] = []
    for title, context in _issue_facets(query):
        ranked: dict[tuple[str, str], dict[str, Any]] = {}

        def add_signal(
            signal_rows: Sequence[sqlite3.Row],
            *,
            signal: str,
            weight: float,
        ) -> None:
            seen_signal: set[tuple[str, str]] = set()
            for position, row in enumerate(signal_rows):
                identity = (
                    _normalize_exact(row["law_number"]),
                    _normalize_exact(row["article_number"]),
                )
                if identity == ("", "") or identity in seen_signal:
                    continue
                seen_signal.add(identity)
                state = ranked.setdefault(
                    identity,
                    {
                        "row": row,
                        "score": 0.0,
                        "best_position": position,
                        "signals": set(),
                    },
                )
                if signal not in state["signals"]:
                    state["signals"].add(signal)
                    state["score"] += weight
                state["best_position"] = min(state["best_position"], position)

        structural_rows = _facet_signal_rows(
            connection,
            column="structural_path",
            value=title,
            scope_sql=scope_sql,
            scope_params=scope_params,
            limit=maximum * 4,
            allow_shorter_variants=False,
        )
        add_signal(
            structural_rows,
            signal="structural_title",
            # An authored legal heading is stronger than an isolated content
            # fragment, which may be truncated or OCR-damaged.  A matching
            # content hint still disambiguates generic headings because the
            # two independent signals add together.
            weight=8.0,
        )
        # Some generated/user prompts quote the first words of a provision
        # instead of its heading.  Use the content-title fallback only when no
        # structural heading matched; otherwise unrelated citations of that
        # heading can accumulate a misleading second signal.
        if not structural_rows:
            add_signal(
                _facet_signal_rows(
                    connection,
                    column="content",
                    value=title,
                    scope_sql=scope_sql,
                    scope_params=scope_params,
                    limit=maximum * 4,
                ),
                signal="content_title",
                weight=3.0,
            )
        if context:
            add_signal(
                _facet_signal_rows(
                    connection,
                    column="content",
                    value=context,
                    scope_sql=scope_sql,
                    scope_params=scope_params,
                    limit=maximum * 4,
                ),
                signal="content_hint",
                weight=6.0,
            )
        ordered = sorted(
            ranked.items(),
            key=lambda item: (
                -float(item[1]["score"]),
                int(item[1]["best_position"]),
                item[0],
            ),
        )[:3]
        facet_rankings.append(
            [(state["row"], "r28_facet_recovery") for _, state in ordered]
        )
    for position in range(3):
        for ranked in facet_rankings:
            if position < len(ranked):
                rows.append(ranked[position])
    terms = sorted(_tokens(query), key=lambda value: (-len(value), value))[:10]
    expressions: list[str] = []
    if len(terms) >= 3:
        expressions.append(" AND ".join(f'"{term}"' for term in terms[:3]))
    if len(terms) >= 2:
        expressions.append(" AND ".join(f'"{term}"' for term in terms[:2]))
    # Single-token FTS scans are both noisy and too expensive on the 2 GB
    # immutable index.  r28 intentionally requires a conjunction; catalog
    # exact keys remain the fallback for short everyday-language queries.
    for expression in dict.fromkeys(expressions):
        rows.extend(
            (row, "r28_fts_recovery")
            for row in connection.execute(
                "SELECT c.* FROM chunk_fts JOIN chunks c ON c.chunk_revision_id=chunk_fts.chunk_revision_id "
                f"WHERE chunk_fts MATCH ? AND {scope_sql} ORDER BY chunk_fts.rank,c.chunk_revision_id LIMIT ?",
                [expression, *scope_params, maximum * 2],
            ).fetchall()
        )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row, source in rows:
        identity = (_normalize_exact(row["law_number"]), _normalize_exact(row["article_number"]))
        if identity == ("", "") or identity in seen:
            continue
        seen.add(identity)
        candidate = {key: row[key] for key in row.keys()}
        candidate["retrieval_source"] = source
        candidate["retrieval_sources"] = [source]
        candidate["score"] = 1.0 / (len(output) + 1)
        output.append(candidate)
        if len(output) >= maximum:
            break
    return output


def merge_recovery_r28(
    recovery: Sequence[Mapping[str, Any]],
    baseline: Sequence[Mapping[str, Any]],
    *,
    slots: int = 6,
    preserve_article_chunks: bool = False,
) -> list[dict[str, Any]]:
    """Reserve bounded unique-article slots while preserving baseline order."""

    chosen = [dict(row) for row in recovery[:slots]]
    identities = {
        (_normalize_exact(row.get("law_number")), _normalize_exact(row.get("article_number")))
        for row in chosen
    }
    chunk_ids = {str(row.get("chunk_revision_id") or "") for row in chosen}
    return [
        *chosen,
        *(
            dict(row) for row in baseline
            if str(row.get("chunk_revision_id") or "") not in chunk_ids
            and (
                preserve_article_chunks
                or (_normalize_exact(row.get("law_number")), _normalize_exact(row.get("article_number"))) not in identities
            )
        ),
    ]


def required_documents_recovery_r28(
    query: str,
    recovery: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Require dossier support before inferred hints reserve answer slots.

    Catalog acceptance verifies an article's legal identity, not whether its
    first chunk answers this request. Broad syllable-FTS recovery also has no
    procedure identity. Neither may displace a hybrid dossier hit merely by
    receiving the recovery lane's reciprocal-rank score.
    """

    query_text = " ".join(_fold(query).split())
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for value in recovery:
        row = dict(value)
        source = str(row.get("retrieval_source") or "")
        reason = None
        content = " ".join(_fold(row.get("content")).split())
        heading = " ".join(_fold(row.get("structural_path")).split())
        # A shared article heading can cover registration and extension.
        # An explicit matching procedure in the selected clause controls over
        # that broad heading; otherwise retain the heading as context.
        requested_registration = re.search(r"\bdang ky\s+(\w+\s+\w+)", query_text)
        registration_phrase = requested_registration.group(0) if requested_registration else ""
        clause_names_registration = bool(
            registration_phrase and registration_phrase in content
            and f"xoa {registration_phrase}" not in content
        )
        passage = content if clause_names_registration else " ".join((content, heading))
        if source == "r28_explicit_exact":
            # The user's authored citation remains an identity constraint.
            accepted.append(row)
            continue
        if source == "r28_fts_recovery":
            reason = "unscoped_fts_cannot_reserve_dossier_slot"
        elif not any(marker in content for marker in ("ho so", "giay to", "to khai", "tai lieu")):
            reason = "recovery_has_no_dossier_content"
        else:
            # A removal/extension/reissue provision may share nearly every
            # token with the registration procedure while requiring different
            # paperwork. Preserve it only when the user asks for that action.
            for action in ("xoa dang ky", "gia han", "cap lai", "thu hoi", "huy bo"):
                if action in passage and action not in query_text:
                    reason = "recovery_action_not_requested"
                    break
        if reason:
            rejected.append({
                "chunk_revision_id": str(row.get("chunk_revision_id") or ""),
                "retrieval_source": source,
                "reason": reason,
            })
        else:
            accepted.append(row)
    return accepted, rejected


__all__ = [
    "R28_CANDIDATE_PROFILE",
    "merge_recovery_r28",
    "query_catalog_hints_r28",
    "recover_candidates_r28",
    "required_documents_recovery_r28",
]
