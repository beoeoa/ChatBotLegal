"""Avoid a second model router for simple, already-resolved legal questions."""
import re


def can_use_resolved_quick_route(request, decision, *, history, active_document):
    if request.answer_depth != 'quick' or request.attachment_text or history or active_document:
        return False
    if decision.source != 'rule' or decision.conversation_route != 'legal_query':
        return False
    if decision.legal_route != 'procedure_form' or decision.canonical_domain in {None, '', 'unknown'}:
        return False
    question = request.question.strip()
    # Mixed requests and follow-ups retain the semantic planner. The normal
    # legal pipeline still retrieves and checks citations for simple requests.
    return (len(question) <= 220 and question.count('?') <= 1
            and not re.search(r'\n|;|\b(?:đồng thời|ngoài ra|so sánh|hợp đồng|vừa .+ vừa|và|hoặc)\b', question, re.I))
