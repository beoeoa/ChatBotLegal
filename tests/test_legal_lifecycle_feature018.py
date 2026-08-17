from datetime import date, timedelta
from pathlib import Path

from api.legal_lifecycle_service import (
    LegalChangeEvent,
    LegalLifecycleDocument,
    LegalProvisionEffectivity,
    LifecycleBucket,
    LifecycleProjectionService,
    provision_is_effective,
)


AS_OF = date(2026, 8, 13)


def document(*, expires_in: int | None = None) -> LegalLifecycleDocument:
    return LegalLifecycleDocument(
        id="doc-1",
        effective_from=date(2025, 1, 1),
        effective_to=AS_OF + timedelta(days=expires_in) if expires_in is not None else None,
        source_url="https://vbpl.vn/example",
    )


def test_expiry_buckets_and_alert_thresholds_are_deterministic():
    service = LifecycleProjectionService()
    expected = {
        91: LifecycleBucket.ACTIVE,
        90: LifecycleBucket.EXPIRING_90,
        30: LifecycleBucket.EXPIRING_30,
        7: LifecycleBucket.EXPIRING_7,
        1: LifecycleBucket.EXPIRING_1,
        0: LifecycleBucket.EXPIRED,
    }
    projections = [
        service.project(document(expires_in=days), legal_as_of=AS_OF)
        for days in expected
    ]
    assert [item.bucket for item in projections] == list(expected.values())
    assert [item["threshold_days"] for item in service.alerts(projections)] == [90, 30, 7, 1]


def test_candidate_or_incomplete_event_cannot_change_serving_state():
    service = LifecycleProjectionService()
    events = (
        LegalChangeEvent(
            id="candidate",
            document_id="doc-1",
            event_type="replace",
            effective_from=AS_OF,
            source_url="https://vbpl.vn/candidate",
        ),
        LegalChangeEvent(
            id="missing-source",
            document_id="doc-1",
            event_type="repeal",
            effective_from=AS_OF,
            source_url=None,
            status="confirmed",
        ),
    )
    projection = service.project(document(), legal_as_of=AS_OF, events=events)
    assert projection.bucket == LifecycleBucket.ACTIVE
    assert projection.current_serving_allowed is True
    assert set(projection.ignored_event_ids) == {"candidate", "missing-source"}


def test_confirmed_replacement_changes_state_without_copying_content():
    event = LegalChangeEvent(
        id="confirmed-replacement",
        document_id="doc-1",
        event_type="replace",
        effective_from=AS_OF,
        source_url="https://vbpl.vn/replacement",
        status="confirmed",
        reviewer_user_id="admin-1",
    )
    projection = LifecycleProjectionService().project(
        document(), legal_as_of=AS_OF, events=(event,)
    )
    assert projection.bucket == LifecycleBucket.REPLACED
    assert projection.current_serving_allowed is False
    assert projection.applied_event_ids == (event.id,)


def test_provision_effectivity_supports_partial_expiry():
    current = LegalProvisionEffectivity(
        document_id="doc-1",
        provision_identity="Điều 1",
        effective_from=date(2025, 1, 1),
        source_url="https://vbpl.vn/example",
    )
    expired = LegalProvisionEffectivity(
        document_id="doc-1",
        provision_identity="Điều 2 khoản 1",
        effective_from=date(2025, 1, 1),
        effective_to=AS_OF,
        source_url="https://vbpl.vn/example",
    )
    projection = LifecycleProjectionService().project(
        document(), legal_as_of=AS_OF, provisions=(current, expired)
    )
    assert projection.bucket == LifecycleBucket.PARTIALLY_EXPIRED
    assert projection.effective_provisions == ("Điều 1",)
    assert projection.inactive_provisions == ("Điều 2 khoản 1",)
    assert provision_is_effective(expired, legal_as_of=AS_OF) is False


def test_lifecycle_schema_is_additive_and_does_not_rewrite_legal_corpus():
    root = Path(__file__).resolve().parents[1]
    up = (root / "scripts/feature018_migrations/002_lifecycle_impact_index_up.sql").read_text(
        encoding="utf-8"
    )
    down = (root / "scripts/feature018_migrations/002_lifecycle_impact_index_down.sql").read_text(
        encoding="utf-8"
    )
    for table in (
        "legal_change_event",
        "legal_document_relation_candidate",
        "legal_provision_effectivity",
        "legal_impact_case",
        "legal_index_manifest",
        "legal_index_job",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up
        assert f"DROP TABLE IF EXISTS {table}" in down
    assert "UPDATE legal_documents" not in up
    assert "DELETE FROM legal_documents" not in up
    assert "DROP TABLE IF EXISTS legal_documents" not in down
    assert "ON DELETE CASCADE" not in up
