from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from api.legal_impact_service import (
    LegalDependency,
    LegalDocumentRelationshipCandidate,
    LegalImpactEngine,
    LegalImpactError,
)
from api.legal_lifecycle_service import LegalChangeEvent


def confirmed_event(**changes):
    values = {
        "id": "event-replace-1",
        "document_id": "doc-old",
        "event_type": "replace",
        "effective_from": date(2026, 9, 1),
        "source_url": "https://vbpl.vn/replacement-source",
        "status": "confirmed",
        "reviewer_user_id": "admin-reviewer",
    }
    values.update(changes)
    return LegalChangeEvent(**values)


def relationship(**changes):
    values = {
        "id": "relation-1",
        "from_document_id": "doc-old",
        "to_document_id": "doc-new",
        "relationship_type": "replaces",
        "effective_from": date(2026, 9, 1),
        "source_url": "https://vbpl.vn/replacement-source",
        "confirmation_state": "confirmed",
    }
    values.update(changes)
    return LegalDocumentRelationshipCandidate(**values)


def test_different_replacement_content_creates_review_cases_without_content_copy():
    dependencies = [
        LegalDependency("procedure", "procedure-1", "doc-old", evidence={"release": "r1"}),
        LegalDependency("faq", "faq-1", "doc-old", evidence={"release": "f1"}),
    ]
    before = deepcopy(dependencies)
    cases = LegalImpactEngine().scan(
        confirmed_event(),
        dependencies,
        relationship=relationship(),
        old_content_sha256="a" * 64,
        new_content_sha256="b" * 64,
    )
    assert len(cases) == 2
    assert all(item.status == "needs_review" for item in cases)
    assert all(item.detected_reason == "replacement_content_differs" for item in cases)
    assert all(item.diff_candidate["content_equivalence_assumed"] is False for item in cases)
    assert "content" not in str([item.old_evidence for item in cases]).replace("content_sha256", "")
    assert dependencies == before


def test_candidate_event_or_relationship_cannot_create_impact_truth():
    dependency = LegalDependency("form", "form-1", "doc-old")
    engine = LegalImpactEngine()
    assert engine.scan(confirmed_event(status="candidate"), [dependency]) == []
    assert engine.scan(
        confirmed_event(),
        [dependency],
        relationship=relationship(confirmation_state="candidate"),
    ) == []


def test_provision_scope_only_selects_matching_dependencies():
    event = confirmed_event(
        event_type="amend",
        scope="provisions",
        provisions=("Điều 5 khoản 2",),
    )
    cases = LegalImpactEngine().scan(
        event,
        [
            LegalDependency("citation", "cite-1", "doc-old", ("Điều 5 khoản 2",)),
            LegalDependency("citation", "cite-2", "doc-old", ("Điều 7",)),
            LegalDependency("faq", "faq-other", "doc-other", ("Điều 5 khoản 2",)),
        ],
    )
    assert [item.dependent_id for item in cases] == ["cite-1"]


def test_relationship_validation_and_review_decision_are_fail_closed():
    with pytest.raises(LegalImpactError, match="self_reference"):
        relationship(to_document_id="doc-old").validate()

    case = LegalImpactEngine().scan(
        confirmed_event(),
        [LegalDependency("golden", "golden-1", "doc-old")],
        relationship=relationship(),
    )[0]
    decided = LegalImpactEngine().decide(
        case,
        decision="confirmed",
        reviewer_user_id="admin-2",
        reason="Đã đối chiếu văn bản nguồn và dependency.",
        reviewed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    assert case.status == "needs_review"
    assert decided.status == "confirmed"
    with pytest.raises(LegalImpactError, match="already_decided"):
        LegalImpactEngine().decide(
            decided,
            decision="rejected",
            reviewer_user_id="admin-2",
            reason="Không còn ảnh hưởng sau khi rà soát.",
        )
