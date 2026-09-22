"""Issue-bound local publications; IDs come from the reviewed catalog only."""
from __future__ import annotations

from datetime import date
from typing import Any

from api.conversation_turn_plan import TOOL_ACTIONS, TurnPlan
from api.legal_official_procedure_evidence import build_official_procedure_evidence
from api.legal_section_grounding import LegalIssue


def build_direct_procedure_context(plan: TurnPlan, catalog: Any, *, as_of: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in plan.items:
        if item.action not in TOOL_ACTIONS or item.action == "document_read":
            continue
        match = catalog.resolve_procedures(item.subject or item.standalone_query)
        candidates = match.get("matches") or []
        procedure_code = None
        if candidates and not match.get("ambiguous"):
            candidate = candidates[0]
            procedure = catalog.get_procedure(candidate["procedure_id"]) or {}
            # Do not promote loose matches or pending metadata to verified IDs.
            if (candidate.get("score", 0) >= 1000 and procedure.get("approved") is True
                    and procedure.get("review_status") == "approved"
                    and procedure.get("source_status") == "verified"):
                procedure_code = procedure.get("official_procedure_code")
        # Signature/attendance questions need the published dossier and steps,
        # not merely the name of a form. These excerpts retain their actual
        # facet labels; presence is never asserted from a signature box.
        source_facets = list(item.facets)
        if set(item.facets) & {"consent", "attendance"}:
            source_facets.extend(["documents", "process"])
        for facet in dict.fromkeys(source_facets):
            issue = LegalIssue(issue_id=item.item_id, domain=item.domain, intent=facet,
                               query_text=item.standalone_query, subject=item.subject)
            sources, _ = build_official_procedure_evidence(
                issues=[issue], question=item.standalone_query, legal_as_of=as_of,
                procedure_id=procedure_code, required_facets_by_issue={item.item_id: [facet]},
            )
            for source in sources:
                captured_as_of = str(source.get("legal_as_of") or "")[:10]
                if captured_as_of and captured_as_of > as_of.isoformat():
                    continue
                rows.append({**source, "direct_issue_ids": [item.item_id]})
    return rows
