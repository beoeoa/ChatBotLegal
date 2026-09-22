"""Keep dossier list points and explicit amendments from eligible candidates.

No corpus lookup, legal-effect inference or consolidation of amended text.
"""

from __future__ import annotations

from collections import OrderedDict
import re
import unicodedata
from typing import Any, Mapping, Sequence


def _fold(value: Any) -> str:
    return " ".join("".join(
        char for char in unicodedata.normalize("NFD", str(value or "").casefold())
        if not unicodedata.combining(char)
    ).replace("đ", "d").split())


def _law(value: Any) -> str:
    return re.sub(r"^so\s*:\s*", "", _fold(value))


def _identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("document_id") or ""),
        str(row.get("chunk_revision_id") or row.get("chunk_id") or ""),
        _law(row.get("law_number")), str(row.get("article_number") or ""),
        str(row.get("effective_from") or ""), str(row.get("effective_to") or ""),
        _fold(row.get("content")),
    )


_STOP = set("toi tai thi can giay to gi nhung nao va cua ve cho co duoc phai la gom bao mot trong sau ho so tai lieu dang ky".split())
_UNREQUESTED_ACTIONS = ("xoa dang ky", "gia han", "cap lai", "thu hoi", "huy bo")


def _group(row: Mapping[str, Any], query: str) -> tuple[tuple[str, ...], int] | None:
    content = str(row.get("content") or "").strip()
    # structural_path alone can misidentify nested quoted amendments. Require
    # an identical authored list introduction in the source passage itself.
    lead = _fold(content.splitlines()[0] if content else "")
    core = bool(re.search(r"\bho so\b.{0,180}\b(?:bao gom|gom|nhu sau)\b", lead))
    proof = bool(re.search(r"\b(?:giay to|tai lieu)\b.{0,220}\b(?:sau|bao gom|gom)\b", lead))
    if not (core or proof) or "sua doi" in lead:
        return None
    folded_query = _fold(query)
    if any(action in lead and action not in folded_query for action in _UNREQUESTED_ACTIONS):
        return None
    # Shared words such as thuê/nhà/trú do not make permanent-residence
    # paperwork evidence for a temporary-residence registration request.
    query_procedure = re.search(r"\bdang ky\s+(\w+\s+\w+)\b", folded_query)
    source_procedure = re.search(r"\bdang ky\s+(\w+\s+\w+)\b", lead)
    if query_procedure and (not source_procedure or query_procedure.group(1) != source_procedure.group(1)):
        return None
    for alternatives in (("tam tru", "thuong tru"), ("khai sinh", "khai tu", "ket hon", "ly hon")):
        requested = {topic for topic in alternatives if topic in folded_query}
        mentioned = {topic for topic in alternatives if topic in lead}
        if requested and mentioned and not requested & mentioned:
            return None
        if requested and not mentioned:
            return None
    # A list for a special applicant category is not the general citizen
    # dossier merely because its heading also says "đăng ký tạm trú".
    # Inspect the provision heading, not an authority mentioned in a point.
    heading = _fold(str(row.get("structural_path") or "").split(">")[0])
    special_applicants = (
        ("cong an nhan dan", "can bo chien si"),
        ("quan doi nhan dan", "quan nhan"),
        ("nguoi nuoc ngoai",),
        ("nguoi viet nam dinh cu o nuoc ngoai",),
    )
    scope_text = heading + " " + lead
    for subjects in special_applicants:
        if any(subject in scope_text for subject in subjects) and not any(subject in folded_query for subject in subjects):
            return None
    query_terms = set(re.findall(r"[a-z0-9]+", folded_query)) - _STOP
    lead_terms = set(re.findall(r"[a-z0-9]+", lead)) - _STOP
    if len(query_terms & lead_terms) < 2:
        return None
    version = str(row.get("document_id") or row.get("source_url") or "")
    article = str(row.get("article_id") or row.get("article_number") or "")
    if not version or not article or not row.get("law_number"):
        return None
    clause = str(row.get("clause_number") or "")
    if not clause:
        authored_clause = re.match(r"^(\d+)[.)]\s", content)
        path_clauses = re.findall(r"\bkhoan\s+(\d+)\b", _fold(row.get("structural_path")))
        clause = authored_clause.group(1) if authored_clause else path_clauses[0] if len(path_clauses) == 1 else ""
    # Unknown parent-list identity never authorizes combining two passages.
    clause = clause or repr(_identity(row))
    return ((version, _law(row["law_number"]), article, clause,
             str(row.get("effective_from") or ""),
             str(row.get("effective_to") or ""), lead), 0 if core else 1)


def _source_order(row: Mapping[str, Any]) -> tuple[int, str]:
    try:
        index = int(row.get("chunk_index"))
    except (TypeError, ValueError):
        index = 10**12
    return index, str(row.get("chunk_revision_id") or row.get("chunk_id") or "")


def _explicit_amendment(row: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    content = str(row.get("content") or "").strip()
    lead = _fold(content.splitlines()[0] if content else "")
    if not re.search(r"\b(?:sua doi|bo sung|thay the|bai bo)\b", lead):
        return False
    number = _law(target.get("law_number"))
    if not number or not re.search(rf"(?<![a-z0-9]){re.escape(number)}(?![a-z0-9])", _fold(row.get("structural_path"))):
        return False
    article = str(target.get("article_number") or "").strip()
    if not article or not re.search(rf"\bdieu\s+{re.escape(article)}(?![a-z0-9])", lead):
        return False
    clause_match = re.search(r"\bkhoan\s+(\d+)\b", lead)
    target_clause = str(target.get("clause_number") or "")
    if not target_clause:
        match = re.search(r"\bkhoan\s+(\d+)\b", _fold(target.get("structural_path")))
        target_clause = match.group(1) if match else ""
    if clause_match and clause_match.group(1) != target_clause:
        return False
    point_match = re.search(r"\bdiem\s+([a-z])\b", lead)
    if point_match:
        target_point = _fold(target.get("point_number"))
        if not target_point:
            match = re.search(r"(?m)^\s*([a-zđ])\)", str(target.get("content") or ""), re.I)
            target_point = _fold(match.group(1)) if match else ""
        if target_point != point_match.group(1):
            return False
    return _law(row.get("law_number")) != number


def select_dossier_evidence(
    query: str,
    ranked_rows: Sequence[Mapping[str, Any]],
    *,
    candidate_rows: Sequence[Mapping[str, Any]] | None = None,
    limit: int = 10,
    classification: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Preserve available list groups without claiming legal completeness.

    Both inputs MUST have passed lifecycle, temporal, domain and ACL checks.
    Never pass an unfiltered vector trace. Linked amendments are independent
    sources; the text and official metadata of every row stay unchanged.
    """
    maximum = max(1, int(limit))
    intents = {str((classification or {}).get("intent") or ""),
               *map(str, (classification or {}).get("secondary_intents") or [])}
    if "REQUIRED_DOCUMENTS" not in intents:
        return [dict(row) for row in ranked_rows[:maximum]], {"enabled": False}
    if any(intent.startswith("EXACT_") for intent in intents):
        return [dict(row) for row in ranked_rows[:maximum]], {
            "enabled": False, "reason": "explicit_provision_request",
        }
    # FORM commonly means the declaration that is itself one dossier item.
    # Keep structural dossier selection for that combination; deadline/fee/
    # authority and other independent facets still retain the ranked packet.
    extra_facets = intents - {"", "REQUIRED_DOCUMENTS", "FORM", "RAW_RETRIEVAL", "GENERAL", "GENERAL_LEGAL"}
    if extra_facets:
        # Narrowing the entire packet to a dossier would silently erase a
        # requested deadline/fee/condition. Keep the ranked mixed-facet packet;
        # the caller can split issues and run dossier expansion per issue.
        return [dict(row) for row in ranked_rows[:maximum]], {
            "enabled": False, "reason": "mixed_requested_facets",
            "legal_completeness_verified": False,
        }
    pool = [dict(row) for row in (candidate_rows if candidate_rows is not None else ranked_rows)]
    groups: OrderedDict[tuple[str, ...], list[dict[str, Any]]] = OrderedDict()
    priorities: dict[tuple[str, ...], int] = {}
    for row in pool:
        matched = _group(row, query)
        if matched:
            key, priority = matched
            groups.setdefault(key, []).append(row)
            priorities[key] = priority
    ordered_keys = list(dict.fromkeys(
        matched[0] for row in ranked_rows
        if (matched := _group(row, query)) and matched[0] in groups
    ))
    ordered_keys.sort(key=lambda key: priorities[key])
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    group_trace: list[dict[str, Any]] = []

    def add(row: Mapping[str, Any]) -> bool:
        identity = _identity(row)
        if identity in seen:
            return True
        if len(selected) >= maximum:
            return False
        seen.add(identity)
        selected.append(dict(row))
        return True

    prepared: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    for key in ordered_keys:
        siblings = sorted(groups[key], key=_source_order)
        siblings = list({_identity(row): row for row in siblings}.values())
        amendments = [row for row in pool if any(_explicit_amendment(row, sibling) for sibling in siblings)]
        amendments = list({_identity(row): row for row in amendments}.values())
        prepared.append((siblings, amendments))
    for group_index, (siblings, amendments) in enumerate(prepared):
        refs = [str(row.get("chunk_revision_id") or row.get("chunk_id") or row.get("source_id") or "") for row in amendments]
        reserved = amendments + siblings
        group_ids = {_identity(row) for row in reserved}
        # Leave at least one slot for each later group while the budget
        # allows. This is structural representation, not a completeness vote.
        later_groups = len(prepared) - group_index - 1
        group_budget = max(1, maximum - len(selected) - min(later_groups, maximum - len(selected) - 1)) if len(selected) < maximum else 0
        added_count = 0
        for row in reserved:
            if _identity(row) not in seen and added_count >= group_budget:
                continue
            item = dict(row)
            if amendments:
                item["dossier_amendment_refs"] = refs
                item["dossier_application_status"] = "read_separate_amendment_sources"
            before_count = len(selected)
            add(item)
            added_count += len(selected) - before_count
        kept_count = sum(_identity(row) in group_ids for row in selected)
        incomplete = kept_count < len(group_ids)
        for row in selected:
            if _identity(row) in group_ids:
                row["dossier_group_truncated"] = incomplete
                row["dossier_available_sibling_count"] = len(siblings)
        group_trace.append({"document_id": siblings[0].get("document_id"),
                            "article_number": siblings[0].get("article_number"),
                            "available_siblings": len(siblings), "amendment_count": len(amendments),
                            "selected_count": kept_count, "truncated": incomplete})
        # Continue enumerating groups after the window fills so omitted
        # groups remain visible in the trace instead of appearing complete.
    if not prepared:
        # No authored dossier group was found: retain the ranked fallback
        # and let the answer service expose the source limitation.
        for row in ranked_rows:
            add(row)
    omitted = sum(group["selected_count"] == 0 for group in group_trace)
    if omitted:
        for row in selected:
            row["dossier_group_truncated"] = True
    return selected, {"enabled": True, "groups": group_trace,
                      "coverage_kind": "available_candidates_only",
                      "legal_completeness_verified": False,
                      "omitted_group_count": omitted,
                      "selected_count": len(selected)}
