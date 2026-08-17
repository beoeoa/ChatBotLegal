from __future__ import annotations

from datetime import date

from api.legal_impact_service import (
    LegalDependency,
    LegalDocumentRelationshipCandidate,
    LegalImpactEngine,
)
from api.legal_lifecycle_service import (
    LegalChangeEvent,
    LegalLifecycleDocument,
    LifecycleBucket,
    LifecycleProjectionService,
)
from api.legal_validity_registry import apply_validity_overlay
from api.vector_serving_manifest import build_vector_serving_manifest


AS_OF = date(2026, 8, 13)


def test_expiry_replacement_impact_index_and_current_chat_remain_fail_closed():
    old_document = LegalLifecycleDocument(
        id="doc-old",
        effective_from=date(2020, 1, 1),
        source_url="https://vbpl.vn/old",
    )
    replacement_event = LegalChangeEvent(
        id="event-replace",
        document_id="doc-old",
        event_type="replace",
        effective_from=AS_OF,
        source_url="https://vbpl.vn/replacement-proof",
        status="confirmed",
        reviewer_user_id="admin-reviewer",
    )
    lifecycle = LifecycleProjectionService().project(
        old_document,
        legal_as_of=AS_OF,
        events=(replacement_event,),
    )
    assert lifecycle.bucket == LifecycleBucket.REPLACED
    assert lifecycle.current_serving_allowed is False

    dependencies = [
        LegalDependency("procedure", "procedure-1", "doc-old"),
        LegalDependency("form", "form-1", "doc-old"),
        LegalDependency("faq", "faq-1", "doc-old"),
        LegalDependency("golden", "golden-1", "doc-old"),
        LegalDependency("cache", "cache-1", "doc-old"),
        LegalDependency("citation", "citation-1", "doc-old"),
        LegalDependency("index_record", "index-1", "doc-old"),
    ]
    cases = LegalImpactEngine().scan(
        replacement_event,
        dependencies,
        relationship=LegalDocumentRelationshipCandidate(
            id="relation-1",
            from_document_id="doc-old",
            to_document_id="doc-new",
            relationship_type="replaces",
            effective_from=AS_OF,
            source_url="https://vbpl.vn/replacement-proof",
            confirmation_state="confirmed",
        ),
        old_content_sha256="a" * 64,
        new_content_sha256="b" * 64,
    )
    assert len(cases) == 7
    assert all(case.status == "needs_review" for case in cases)
    assert all(case.diff_candidate["content_equivalence_assumed"] is False for case in cases)

    fingerprints = {
        "embedding": "embed-v1",
        "splitter": "split-v1",
        "pipeline": "pipeline-v3",
        "validity_snapshot": "v" * 64,
    }
    manifest = build_vector_serving_manifest(
        expected_chunks=[
            {
                "chunk_id": 1,
                "document_id": "doc-old",
                "document_status": "replaced",
                "serving_action": "historical_only",
            },
            {
                "chunk_id": 2,
                "document_id": "doc-new",
                "document_status": "active",
                "serving_action": "allow",
            },
        ],
        collections=[
            {
                "name": "active-v1",
                "role": "active",
                "fingerprints": fingerprints,
                "records": [
                    {
                        "vector_id": "chunk-1",
                        "chunk_id": 1,
                        "document_id": "doc-old",
                        "fingerprints": fingerprints,
                    }
                ],
            }
        ],
        active_collection="active-v1",
        expected_fingerprints=fingerprints,
        active_pointer_before="active-v1",
        active_pointer_after="active-v1",
    )
    assert manifest["state_chunk_ids"]["historical"] == [1]
    assert manifest["state_chunk_ids"]["missing"] == [2]
    assert manifest["gate_passed"] is False
    assert manifest["active_pointer_unchanged"] is True

    validity_snapshot = {
        "schema_version": "legal-validity-serving-v1",
        "generated_at": "2026-08-13T00:00:00+00:00",
        "last_success_at": "2026-08-13T00:00:00+00:00",
        "mode": "protect",
        "coverage": {"eligible": 2, "observed": 2, "fresh": 2},
        "document_ids": {"doc-old": "01/2020/NĐ-TEST", "doc-new": "02/2026/NĐ-TEST"},
        "documents": {
            "01/2020/NĐ-TEST": {
                "document_id": "doc-old",
                "normalized_status": "replaced",
                "serving_action": "historical_only",
                "source_url": "https://vbpl.vn/old",
                "verified_at": "2026-08-13T00:00:00+00:00",
                "effective_from": "2020-01-01",
                "effective_to": "2026-08-13",
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
                "fingerprint": "old-validity-fingerprint",
            },
            "02/2026/NĐ-TEST": {
                "document_id": "doc-new",
                "normalized_status": "active",
                "serving_action": "allow",
                "source_url": "https://vbpl.vn/new",
                "verified_at": "2026-08-13T00:00:00+00:00",
                "effective_from": "2026-08-13",
                "effective_to": None,
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
                "fingerprint": "new-validity-fingerprint",
            },
        },
    }
    current_chat_evidence = apply_validity_overlay(
        {
            "results": [
                {
                    "chunk_id": 1,
                    "document_id": "doc-old",
                    "law_number": "01/2020/NĐ-TEST",
                    "article_number": "1",
                    "document_status": "active",
                    "content": "Nội dung cũ không được tái sử dụng.",
                }
            ]
        },
        snapshot=validity_snapshot,
        as_of=AS_OF,
        mode="protect",
    )
    assert current_chat_evidence["results"] == []
    assert current_chat_evidence["validity_sync"]["filtered_reasons"] == {"replaced": 1}

    historical_chat_evidence = apply_validity_overlay(
        {
            "results": [
                {
                    "chunk_id": 1,
                    "document_id": "doc-old",
                    "law_number": "01/2020/NĐ-TEST",
                    "article_number": "1",
                    "document_status": "active",
                    "content": "Nội dung lịch sử.",
                }
            ]
        },
        snapshot=validity_snapshot,
        as_of=date(2025, 1, 1),
        mode="protect",
    )
    assert len(historical_chat_evidence["results"]) == 1
    assert historical_chat_evidence["results"][0]["validity_sync"]["warning_code"] == "historical_validity"
