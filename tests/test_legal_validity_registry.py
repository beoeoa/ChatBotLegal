from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.legal_validity_models import (
    EvidenceStatus,
    IdentityStatus,
    LegalValidityObservation,
    NormalizedValidityStatus,
)
from api.legal_validity_registry import LegalValidityRegistry, ValiditySnapshotCache


class MemoryBackend:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}

    async def create(self, table: str, payload: dict):
        rows = self.tables.setdefault(table, [])
        if table == "legal_validity_sync_lease" and any(
            row.get("name") == payload.get("name") for row in rows
        ):
            raise RuntimeError("unique lease")
        key_field = {
            "legal_validity_observation": "observation_key",
            "legal_validity_event": "event_key",
            "legal_validity_decision": "replay_key",
        }.get(table)
        if key_field and any(row.get(key_field) == payload.get(key_field) for row in rows):
            raise RuntimeError("unique key")
        row = {**payload, "id": f"{table}:{len(rows) + 1}"}
        rows.append(row)
        return [dict(row)]

    async def update(self, table: str, record_id, payload: dict):
        identity = str(record_id)
        for row in self.tables.setdefault(table, []):
            if str(row.get("id")) == identity:
                row.update(payload)
                return [dict(row)]
        return []

    async def query(self, sql: str, params=None):
        params = params or {}
        if "FROM legal_validity_observation WHERE observation_key" in sql:
            return [
                dict(row)
                for row in self.tables.get("legal_validity_observation", [])
                if row.get("observation_key") == params.get("observation_key")
            ][:1]
        if "FROM legal_validity_observation WHERE law_number" in sql:
            rows = [
                dict(row)
                for row in self.tables.get("legal_validity_observation", [])
                if row.get("law_number") == params.get("law_number")
            ]
            return sorted(rows, key=lambda item: item["observed_at"], reverse=True)[:1]
        if "FROM legal_validity_observation ORDER BY observed_at" in sql:
            rows = sorted(
                [dict(row) for row in self.tables.get("legal_validity_observation", [])],
                key=lambda item: item["observed_at"],
                reverse=True,
            )
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or len(rows) or 1)
            return rows[offset : offset + limit]
        if "FROM legal_validity_event WHERE event_key" in sql:
            return [
                dict(row)
                for row in self.tables.get("legal_validity_event", [])
                if row.get("event_key") == params.get("event_key")
            ][:1]
        if "FROM legal_validity_event WHERE id" in sql:
            return [
                dict(row)
                for row in self.tables.get("legal_validity_event", [])
                if str(row.get("id")) == str(params.get("id"))
            ][:1]
        if "FROM legal_validity_event WHERE review_status = 'rejected_match'" in sql:
            return [
                {"current_fingerprint": row.get("current_fingerprint")}
                for row in self.tables.get("legal_validity_event", [])
                if row.get("review_status") == "rejected_match"
            ]
        if "FROM legal_validity_decision WHERE replay_key" in sql:
            return [
                dict(row)
                for row in self.tables.get("legal_validity_decision", [])
                if row.get("replay_key") == params.get("replay_key")
            ][:1]
        if "FROM legal_validity_sync_lease WHERE name" in sql:
            return [
                dict(row)
                for row in self.tables.get("legal_validity_sync_lease", [])
                if row.get("name") == params.get("name")
            ][:1]
        return []


def observation(
    status: NormalizedValidityStatus,
    *,
    observed_at: datetime,
    effective_to: str | None = None,
    evidence_status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
) -> LegalValidityObservation:
    return LegalValidityObservation(
        document_id="42",
        law_number="12/2020/NĐ-CP",
        issuing_agency="Chính phủ",
        issued_date="2020-02-02",
        source_url="https://vbpl.vn/van-ban/chi-tiet/example--42",
        source_kind="vbpl",
        raw_status=status.value,
        normalized_status=status,
        effective_from="2020-03-02",
        effective_to=effective_to,
        affecting_document_number=None,
        affected_provisions=(),
        identity_status=IdentityStatus.EXACT,
        evidence_status=evidence_status,
        observed_at=observed_at,
        source_updated_at=None,
    )


@pytest.fixture
def backend():
    return MemoryBackend()


@pytest.fixture
def registry(tmp_path: Path, backend: MemoryBackend):
    return LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "legal-validity.json",
    )


@pytest.mark.asyncio
async def test_observation_is_idempotent_and_status_diff_creates_one_event(registry, backend):
    active = observation(
        NormalizedValidityStatus.ACTIVE,
        observed_at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc),
    )
    first = await registry.record_observation(active)
    replay = await registry.record_observation(active)
    expired = observation(
        NormalizedValidityStatus.EXPIRED,
        observed_at=datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc),
        effective_to="2026-08-01",
    )
    changed = await registry.record_observation(expired)
    repeated_change = await registry.record_observation(expired)

    assert first["created"] is True
    assert first["event"] is None
    assert replay["created"] is False
    assert changed["created"] is True
    assert changed["event"]["event_type"] == "status_changed"
    assert changed["event"]["severity"] == "critical"
    assert changed["event"]["serving_action"] == "historical_only"
    assert repeated_change["created"] is False
    assert len(backend.tables["legal_validity_observation"]) == 2
    assert len(backend.tables["legal_validity_event"]) == 1


@pytest.mark.asyncio
async def test_atomic_snapshot_and_corrupt_file_keep_last_good_state(registry, tmp_path):
    active = observation(
        NormalizedValidityStatus.ACTIVE,
        observed_at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc),
    )
    snapshot = registry.write_snapshot(
        observations=[active],
        last_success_at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc),
        eligible_count=2,
        mode="protect",
    )
    path = tmp_path / "legal-validity.json"
    cache = ValiditySnapshotCache(path)

    first = cache.load()
    path.write_text("{broken", encoding="utf-8")
    second = cache.load()

    assert snapshot["coverage"] == {"eligible": 2, "observed": 1, "fresh": 1}
    assert first["documents"]["12/2020/NĐ-CP"]["serving_action"] == "allow"
    assert second == first
    assert not (tmp_path / "legal-validity.json.tmp").exists()


@pytest.mark.asyncio
async def test_latest_observations_reads_all_pages_before_building_snapshot(
    registry, backend
):
    for index in range(7):
        item = observation(
            NormalizedValidityStatus.EXPIRED if index == 0 else NormalizedValidityStatus.ACTIVE,
            observed_at=datetime(2026, 8, 8, index, 0, tzinfo=timezone.utc),
            effective_to="2026-08-01" if index == 0 else None,
        )
        payload = item.to_dict()
        payload.update(
            {
                "document_id": str(index + 1),
                "law_number": f"{index + 1:02d}/2026/NQ-TEST",
                "fingerprint": f"fingerprint-{index}",
                "observation_key": f"observation-{index}",
            }
        )
        backend.tables.setdefault("legal_validity_observation", []).append(payload)

    latest = await registry.latest_observations(page_size=2, max_records=20)
    snapshot = registry.write_snapshot(
        observations=latest,
        last_success_at=datetime(2026, 8, 8, 8, 0, tzinfo=timezone.utc),
        eligible_count=7,
        mode="protect",
    )

    assert len(latest) == 7
    assert snapshot["coverage"] == {"eligible": 7, "observed": 7, "fresh": 7}
    assert snapshot["documents"]["01/2026/NQ-TEST"]["serving_action"] == "historical_only"


def test_snapshot_health_reports_stale_without_deleting_projection(registry):
    snapshot = registry.write_snapshot(
        observations=[
            observation(
                NormalizedValidityStatus.ACTIVE,
                observed_at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc),
            )
        ],
        last_success_at=datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc),
        eligible_count=1,
        mode="protect",
    )
    health = registry.snapshot_health(
        snapshot,
        now=datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc),
        stale_after_seconds=6 * 3600,
    )

    assert health["status"] == "stale"
    assert health["reason_code"] == "validity_snapshot_stale"
    assert snapshot["documents"]


@pytest.mark.asyncio
async def test_lease_is_exclusive_and_recoverable(registry):
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)

    assert await registry.acquire_lease(owner="worker-a", now=now, ttl_seconds=60) is True
    assert await registry.acquire_lease(owner="worker-b", now=now, ttl_seconds=60) is False
    await registry.release_lease(owner="worker-a", now=now + timedelta(seconds=1))
    assert await registry.acquire_lease(
        owner="worker-b", now=now + timedelta(seconds=2), ttl_seconds=60
    ) is True


@pytest.mark.asyncio
async def test_admin_decision_is_idempotent_and_never_updates_observation(registry, backend):
    expired = observation(
        NormalizedValidityStatus.EXPIRED,
        observed_at=datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc),
        effective_to="2026-08-01",
    )
    result = await registry.record_observation(expired)
    event_id = result["event"]["id"]

    first = await registry.record_decision(
        event_id=event_id,
        action="confirm_mapping",
        reason="Đã đối chiếu chính xác số, cơ quan và ngày ban hành.",
        actor_user_id="admin-1",
    )
    replay = await registry.record_decision(
        event_id=event_id,
        action="confirm_mapping",
        reason="Đã đối chiếu chính xác số, cơ quan và ngày ban hành.",
        actor_user_id="admin-1",
    )

    assert first["id"] == replay["id"]
    assert backend.tables["legal_validity_event"][0]["review_status"] == "confirmed"
    assert backend.tables["legal_validity_observation"][0]["normalized_status"] == "expired"
    assert len(backend.tables["legal_validity_decision"]) == 1


@pytest.mark.asyncio
async def test_reject_match_removes_only_serving_mapping_and_survives_reprojection(
    registry, backend, tmp_path
):
    expired = observation(
        NormalizedValidityStatus.EXPIRED,
        observed_at=datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc),
        effective_to="2026-08-01",
    )
    result = await registry.record_observation(expired)
    registry.write_snapshot(
        observations=[expired],
        last_success_at=datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc),
        eligible_count=1,
        mode="protect",
    )

    await registry.record_decision(
        event_id=result["event"]["id"],
        action="reject_match",
        reason="Kết quả nguồn khớp sai văn bản sau khi đối chiếu thủ công.",
        actor_user_id="admin-1",
    )
    first_projection = ValiditySnapshotCache(tmp_path / "legal-validity.json").load()
    serving = await registry.latest_serving_observations()
    second_projection = registry.write_snapshot(
        observations=serving,
        last_success_at=datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc),
        eligible_count=1,
        mode="protect",
    )

    assert first_projection["documents"] == {}
    assert second_projection["documents"] == {}
    assert len(backend.tables["legal_validity_observation"]) == 1
    assert backend.tables["legal_validity_observation"][0]["fingerprint"] == expired.fingerprint
