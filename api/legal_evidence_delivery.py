"""Lossless evidence delivery observations, not a semantic/legal verifier.

Everything here is request-local. This module neither changes source eligibility
nor imports/rewrites legal records. An observed facet is a retrieval hint only.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def source_text(row: Mapping[str, Any]) -> str:
    if "direct_visible_content" in row:
        return str(row.get("direct_visible_content") or "").strip()
    return str(next((row[k] for k in ("parent_context", "exact_article_assembled_content",
                   "evidence_capsule", "clean_content", "content") if row.get(k)), "")).strip()


def source_facets(row: Mapping[str, Any]) -> set[str]:
    """Use adapter metadata, never equate a keyword match with coverage."""
    values = row.get("supported_facets") or []
    return {str(v) for v in values} if isinstance(values, (list, tuple, set)) else set()


def merge_ranked_sources(primary, supplements, identity: Callable) -> list[dict]:
    """Fuse lane ranks with equal weights; raw scores are incomparable."""
    rows: dict[str, dict] = {}
    ranks: dict[str, dict] = {}
    for lane, candidates in (("retrieval", primary), ("publication", supplements)):
        seen = set()
        for source in candidates:
            key = identity(source)
            if key in seen:
                continue
            seen.add(key)
            ranks.setdefault(key, {})[lane] = len(seen)
            if key not in rows:
                rows[key] = dict(source)
            else:
                rows[key]["supported_facets"] = sorted(source_facets(rows[key]) | source_facets(source))
    ordered = sorted(rows, key=lambda key: (-sum(1 / (60 + r) for r in ranks[key].values()),
                                           ranks[key].get("retrieval", 10**9), key))
    return [{**rows[key], "delivery_fusion_ranks": ranks[key]} for key in ordered]


def pack_whole_sources(rows: Sequence[Mapping[str, Any]], *, max_chars: int,
                       header: Callable[[Mapping, str, bool], str], bounded_window: bool) -> tuple[str, dict]:
    """Reserve whole excerpts per issue before sharing unused capacity.

    A paragraph boundary is not a legal boundary: an exception may be the next
    paragraph. Oversized excerpts are omitted as a unit, never cut into a rule
    without its tail. Exact-article delivery keeps its own integrity contract.
    """
    candidates = list(rows[:24])
    issue_order = list(dict.fromkeys(str(i) for row in candidates
                                   for i in row.get("direct_issue_ids", [row.get("issue_id", "")]) if i))
    selected: list[int] = []
    consumed = 0
    quota = max_chars // max(1, len(issue_order))
    charged = {issue: 0 for issue in issue_order}

    def cost(row):
        # Reserve the widest possible public ID so later numbering cannot overflow.
        return len(header(row, "E24", bounded_window)) + len(source_text(row)) + 2

    def take(index):
        nonlocal consumed
        selected.append(index)
        consumed += cost(candidates[index])

    for issue in issue_order:
        for index, row in enumerate(candidates):
            if issue not in row.get("direct_issue_ids", [row.get("issue_id", "")]):
                continue
            if index in selected:
                continue
            size = cost(row)
            if source_text(row) and charged[issue] + size <= quota and consumed + size <= max_chars:
                take(index)
                charged[issue] += size
    for index, row in enumerate(candidates):
        if index not in selected and source_text(row) and consumed + cost(row) <= max_chars:
            take(index)
    blocks, evidence = [], {}
    for index in sorted(selected):
        row = candidates[index]
        public_id = f"E{len(evidence) + 1}"
        content = source_text(row)
        visible = {**row, "content": content, "direct_visible_content": content,
                   "direct_evidence_id": public_id, "direct_content_truncated": False,
                   "delivery_unit": "whole_retrieved_excerpt",
                   "visible_content_sha256": hashlib.sha256(content.encode()).hexdigest()}
        blocks.append(header(visible, public_id, bounded_window) + content)
        evidence[f"evidence-{len(evidence) + 1}"] = visible
    return "\n\n".join(blocks), evidence


def observe_delivery(issues: Sequence[Mapping], retrieved: Sequence[Mapping], visible: Mapping) -> dict:
    """Describe exact packet membership separately from semantic completeness."""
    output = {}
    for issue in issues:
        issue_id = str(issue["issue_id"])
        candidates = [r for r in retrieved if issue_id in r.get("direct_issue_ids", [r.get("issue_id")])]
        sent = [r for r in visible.values() if issue_id in r.get("direct_issue_ids", [r.get("issue_id")])]
        facets = {}
        for facet in issue.get("facets", []):
            found = [r for r in candidates if facet in source_facets(r)]
            sent_ids = [r["direct_evidence_id"] for r in sent if facet in source_facets(r)]
            facets[facet] = {"evidence_ids": sent_ids,
                "status": "present" if sent_ids else "omitted_from_packet" if found else "not_observed",
                "semantic_support": "not_assessed"}
        output[issue_id] = {"facets": facets, "retrieved_count": len(candidates), "sent_count": len(sent),
                            "evidence_ids": [r["direct_evidence_id"] for r in sent]}
    sent_hashes = {hashlib.sha256(source_text(r).encode()).hexdigest() for r in visible.values()}
    return {"observation_kind": "retrieval_presence_only", "issues": output,
            "omitted_sources": [{"content_sha256": hashlib.sha256(source_text(r).encode()).hexdigest(),
                                 "issue_ids": r.get("direct_issue_ids", []),
                                 "reason": "empty_content" if not source_text(r) else "packet_budget"}
                                for r in retrieved if hashlib.sha256(source_text(r).encode()).hexdigest() not in sent_hashes],
            "semantic_completeness_verified": False}


def bind_item_reports(reports: Sequence[Mapping], visible: Mapping, delivery: Mapping) -> list[dict]:
    """Validate model-reported source membership; never certify its claims."""
    allowed = {str(r.get("direct_evidence_id")): r for r in visible.values()}
    result = []
    for report in reports:
        item_id = str(report.get("item_id") or "")
        links = {}
        for facet, ids in (report.get("facet_sources") or {}).items():
            links[facet] = []
            for value in ids if isinstance(ids, list) else []:
                source_id = str(value).strip().strip("[]").upper()
                source = allowed.get(source_id)
                if source and item_id in source.get("direct_issue_ids", [source.get("issue_id")]):
                    links[facet].append(source_id)
            links[facet] = list(dict.fromkeys(links[facet]))
        result.append({**report, "facet_sources": links, "assessment_kind": "model_reported",
                       "support_status": "not_assessed",
                       "retrieval_observation": (delivery.get("issues") or {}).get(item_id, {})})
    return result
