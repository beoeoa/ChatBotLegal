from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from api.legal_effectivity_service import (
    DocumentInventory,
    LegalValiditySyncService,
    _canonical_scope,
    notify_validity_admin,
)
from api.legal_validity_registry import LegalValidityRegistry
from api.legal_validity_source import ValidityFetchResult, VBPLValiditySource
from test_legal_validity_registry import MemoryBackend


class FixtureSource:
    def __init__(self, fixture_cases: dict[str, dict], case_by_instrument: dict[str, str]):
        self.fixture_cases = fixture_cases
        self.case_by_instrument = case_by_instrument
        self.parser = VBPLValiditySource()
        self.calls: list[str] = []

    async def fetch(self, *, instrument, document_id, as_of, expected_issuing_agency=None, expected_issued_date=None):
        self.calls.append(instrument)
        case_name = self.case_by_instrument.get(instrument)
        if case_name == "failure":
            return ValidityFetchResult(
                reason_code="OFFICIAL_SOURCE_CONNECT_TIMEOUT",
                observation=None,
                source_status="failed",
            )
        case = self.fixture_cases[case_name]
        return self.parser.parse_items(
            instrument=instrument,
            items=case["items"],
            document_id=document_id,
            expected_issuing_agency=expected_issuing_agency,
            expected_issued_date=expected_issued_date,
            observed_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
            as_of=as_of,
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Trung ương - toàn quốc", "central"),
        ("Hải Phòng", "haiphong"),
        ("Văn bản địa phương cấp xã", "local"),
        ("không rõ", None),
    ],
)
def test_inventory_scope_labels_are_normalized_deterministically(raw, expected):
    assert _canonical_scope(raw) == expected


@pytest.fixture
def fixture_cases():
    import json

    path = Path(__file__).parent / "fixtures" / "vbpl_validity_responses.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: item for item in payload["cases"]}


@pytest.mark.asyncio
async def test_sync_is_bounded_persistent_and_idempotent(tmp_path, fixture_cases):
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    documents = [
        {
            "doc_id": "1",
            "law_number": "31/2024/QH15",
            "issuing_agency": "Quốc hội",
            "issued_date": "2024-01-18",
            "scope": "central",
        },
        {
            "doc_id": "2",
            "law_number": "10/2020/NĐ-CP",
            "issuing_agency": "Chính phủ",
            "issued_date": "2020-01-01",
            "scope": "central",
        },
        {"doc_id": "3", "law_number": "bad identity", "scope": "central"},
    ]

    async def loader(*, scopes, limit, offset, as_of):
        assert scopes == ("central",)
        assert offset == 0
        assert as_of == date(2026, 8, 8)
        return documents[:limit]

    source = FixtureSource(
        fixture_cases,
        {"31/2024/QH15": "active", "10/2020/NĐ-CP": "expired"},
    )
    service = LegalValiditySyncService(
        registry=registry,
        source=source,
        document_loader=loader,
        now=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        batch_size=2,
        max_concurrency=2,
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )

    first = await service.run(trigger="scheduler", scopes=("central",), limit=100)
    second = await service.run(trigger="scheduler", scopes=("central",), limit=100)

    assert first["status"] == "completed"
    assert first["scanned"]["documents"] == 2
    assert first["observations_created"] == 2
    assert first["events_created"] == 1
    assert source.calls == [
        "31/2024/QH15",
        "10/2020/NĐ-CP",
        "31/2024/QH15",
        "10/2020/NĐ-CP",
    ]
    assert second["observations_created"] == 0
    assert second["events_created"] == 0
    assert (tmp_path / "snapshot.json").is_file()
    assert (tmp_path / "report.json").is_file()
    assert len(backend.tables["legal_validity_observation"]) == 2


@pytest.mark.asyncio
async def test_source_failure_is_degraded_and_keeps_prior_snapshot(tmp_path, fixture_cases):
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    active = FixtureSource(fixture_cases, {"31/2024/QH15": "active"})

    async def loader(*, scopes, limit, offset, as_of):
        return [{"doc_id": "1", "law_number": "31/2024/QH15", "scope": "central"}]

    service = LegalValiditySyncService(
        registry=registry,
        source=active,
        document_loader=loader,
        now=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )
    await service.run(trigger="scheduler")
    before = (tmp_path / "snapshot.json").read_bytes()
    service.source = FixtureSource(fixture_cases, {"31/2024/QH15": "failure"})

    degraded = await service.run(trigger="scheduler")

    assert degraded["status"] == "degraded"
    assert degraded["failures"] == {"OFFICIAL_SOURCE_CONNECT_TIMEOUT": 1}
    assert (tmp_path / "snapshot.json").read_bytes() == before


@pytest.mark.asyncio
async def test_held_lease_skips_duplicate_batch(tmp_path, fixture_cases):
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    now = datetime(2026, 8, 8, tzinfo=timezone.utc)
    assert await registry.acquire_lease(owner="other", now=now, ttl_seconds=300)

    async def loader(**kwargs):
        raise AssertionError("loader must not run while lease is held")

    service = LegalValiditySyncService(
        registry=registry,
        source=FixtureSource(fixture_cases, {}),
        document_loader=loader,
        now=lambda: now,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )

    assert (await service.run(trigger="scheduler"))["status"] == "skipped_lease"


@pytest.mark.asyncio
async def test_failure_backoff_and_source_recovery_are_explicit(
    tmp_path, fixture_cases, monkeypatch
):
    monkeypatch.setenv("LEGAL_VALIDITY_SYNC_INTERVAL_SECONDS", "60")
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    clock = [datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)]

    async def loader(**kwargs):
        return [{"doc_id": "1", "law_number": "31/2024/QH15", "scope": "central"}]

    service = LegalValiditySyncService(
        registry=registry,
        source=FixtureSource(fixture_cases, {"31/2024/QH15": "failure"}),
        document_loader=loader,
        now=lambda: clock[0],
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )

    first = await service.run(trigger="scheduler")
    clock[0] = datetime(2026, 8, 8, 0, 1, tzinfo=timezone.utc)
    second = await service.run(trigger="scheduler")
    service.source = FixtureSource(fixture_cases, {"31/2024/QH15": "active"})
    clock[0] = datetime(2026, 8, 8, 0, 3, tzinfo=timezone.utc)
    recovered = await service.run(trigger="scheduler")

    assert first["consecutive_failures"] == 1
    assert first["next_run_at"] == "2026-08-08T00:01:00+00:00"
    assert second["consecutive_failures"] == 2
    assert second["next_run_at"] == "2026-08-08T00:03:00+00:00"
    assert recovered["status"] == "completed"
    assert recovered["recovered"] is True
    assert recovered["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_status_reports_stale_snapshot_without_claiming_source_freshness(
    tmp_path, fixture_cases, monkeypatch
):
    monkeypatch.setenv("LEGAL_VALIDITY_STALE_AFTER_SECONDS", "21600")
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    clock = [datetime(2026, 8, 8, 0, 0, tzinfo=timezone.utc)]

    async def loader(**kwargs):
        return [{"doc_id": "1", "law_number": "31/2024/QH15", "scope": "central"}]

    service = LegalValiditySyncService(
        registry=registry,
        source=FixtureSource(fixture_cases, {"31/2024/QH15": "active"}),
        document_loader=loader,
        now=lambda: clock[0],
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )
    await service.run(trigger="scheduler")
    clock[0] = datetime(2026, 8, 8, 6, 0, 1, tzinfo=timezone.utc)

    status = await service.status()

    assert status["status"] == "stale"
    assert status["reason_code"] == "validity_snapshot_stale"
    assert status["coverage"] == {"eligible": 1, "observed": 1, "fresh": 0}
    assert status["sources"][0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_admin_alert_is_deduplicated_while_unread(monkeypatch):
    from api import legal_effectivity_service as module

    rows: list[dict] = []

    async def query(sql, params=None):
        return [row for row in rows if row.get("dedupe_key") == (params or {}).get("dedupe_key")]

    async def create(table, payload):
        rows.append({**payload, "id": f"{table}:{len(rows) + 1}"})
        return [rows[-1]]

    monkeypatch.setattr(module, "repo_query", query)
    monkeypatch.setattr(module, "repo_create", create)
    report = {
        "status": "degraded",
        "failures": {"OFFICIAL_SOURCE_CONNECT_TIMEOUT": 1},
        "events_created": 0,
    }

    await notify_validity_admin(report)
    await notify_validity_admin(report)

    assert len(rows) == 1
    assert rows[0]["priority"] == "critical"
    assert "snapshot" in rows[0]["message"]


@pytest.mark.asyncio
async def test_sync_uses_ho_chi_minh_legal_date_at_utc_day_boundary(
    tmp_path, fixture_cases
):
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )

    async def loader(*, scopes, limit, offset, as_of):
        assert as_of == date(2026, 8, 9)
        return [{"doc_id": "1", "law_number": "31/2024/QH15", "scope": "central"}]

    service = LegalValiditySyncService(
        registry=registry,
        source=FixtureSource(fixture_cases, {"31/2024/QH15": "active"}),
        document_loader=loader,
        now=lambda: datetime(2026, 8, 8, 18, 30, tzinfo=timezone.utc),
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )

    result = await service.run(trigger="scheduler")

    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_scheduler_rotates_inventory_pages_and_reports_total_coverage(
    tmp_path, fixture_cases
):
    backend = MemoryBackend()
    registry = LegalValidityRegistry(
        query=backend.query,
        create=backend.create,
        update=backend.update,
        snapshot_path=tmp_path / "snapshot.json",
    )
    documents = [
        {"doc_id": "1", "law_number": "31/2024/QH15", "scope": "central"},
        {"doc_id": "2", "law_number": "10/2020/NĐ-CP", "scope": "central"},
        {"doc_id": "3", "law_number": "11/2020/TT-BTP", "scope": "central"},
    ]
    offsets = []

    async def loader(*, scopes, limit, offset, as_of):
        offsets.append(offset)
        page = documents[offset : offset + limit]
        return DocumentInventory(page, eligible_count=3, raw_count=len(page))

    service = LegalValiditySyncService(
        registry=registry,
        source=FixtureSource(
            fixture_cases,
            {
                "31/2024/QH15": "active",
                "10/2020/NĐ-CP": "expired",
                "11/2020/TT-BTP": "suspended",
            },
        ),
        document_loader=loader,
        now=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        batch_size=2,
        rate_limit_seconds=0,
        report_path=tmp_path / "report.json",
        worker_id="test-worker",
    )

    first = await service.run(trigger="scheduler")
    second = await service.run(trigger="scheduler")
    snapshot = json.loads((tmp_path / "snapshot.json").read_text(encoding="utf-8"))

    assert offsets == [0, 2]
    assert first["next_offset"] == 2
    assert second["next_offset"] == 0
    assert snapshot["coverage"] == {"eligible": 3, "observed": 3, "fresh": 3}
