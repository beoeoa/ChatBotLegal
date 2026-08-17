from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from api.support_repository import (
    InMemorySupportRepository,
    JsonReadOnlySupportAdapter,
    PostgresSupportRepository,
    ReadOnlySupportRepositoryError,
    SupportAccessError,
    SupportActor,
    SupportConflictError,
    SupportStateError,
    TicketStatus,
)


NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def citizen(user_id: str) -> SupportActor:
    return SupportActor(user_id=user_id, role="citizen")


def officer(user_id: str, *domains: str) -> SupportActor:
    return SupportActor(user_id=user_id, role="officer", domains=domains)


def test_ticket_state_machine_is_versioned_and_hash_chained():
    repository = InMemorySupportRepository()
    owner = citizen("citizen-1")
    ticket = repository.create_ticket(
        owner,
        domain="ho_tich_chung_thuc",
        question_summary="Cần hỗ trợ khai sinh",
    )

    with pytest.raises(SupportStateError, match="queued->active"):
        repository.transition_ticket(
            ticket.id, TicketStatus.ACTIVE, owner, expected_version=ticket.version
        )
    cancelled = repository.transition_ticket(
        ticket.id,
        TicketStatus.CANCELLED,
        owner,
        expected_version=ticket.version,
        reason_code="citizen_cancelled",
    )
    assert cancelled.version == ticket.version + 1
    assert cancelled.status == TicketStatus.CANCELLED
    with pytest.raises(SupportConflictError):
        repository.transition_ticket(
            ticket.id,
            TicketStatus.EXPIRED,
            SupportActor(user_id="system", role="system"),
            expected_version=ticket.version,
        )
    events = repository.state_events(ticket.id)
    assert len(events) == 1
    assert events[0].detail_sha256 != events[0].entry_hash
    assert events[0].to_status == TicketStatus.CANCELLED


def test_owner_officer_domain_and_admin_content_boundaries_are_server_enforced():
    repository = InMemorySupportRepository()
    ticket = repository.create_ticket(
        citizen("owner"),
        domain="dat_dai_xay_dung",
        question_summary="Thông tin hồ sơ đất đai riêng tư",
        priority="high",
    )
    with pytest.raises(SupportAccessError, match="owner_required"):
        repository.get_ticket(ticket.id, citizen("other"))
    with pytest.raises(SupportAccessError, match="assignment_required"):
        repository.get_ticket(ticket.id, officer("officer-land", "dat_dai_xay_dung"))
    with pytest.raises(SupportAccessError, match="reason_required"):
        repository.get_ticket(
            ticket.id, SupportActor(user_id="admin", role="admin"), include_content=True
        )
    assert repository.get_ticket(
        ticket.id,
        SupportActor(
            user_id="admin", role="admin", access_reason="Điều tra khiếu nại mã 123"
        ),
        include_content=True,
    ).id == ticket.id


def test_officer_queue_is_domain_scoped_and_contains_no_owner_or_question_content():
    repository = InMemorySupportRepository()
    repository.create_ticket(
        citizen("citizen-land"),
        domain="dat_dai_xay_dung",
        question_summary="Nội dung đất đai không được lộ trong queue",
    )
    repository.create_ticket(
        citizen("citizen-civil"),
        domain="ho_tich_chung_thuc",
        question_summary="Nội dung hộ tịch không được lộ trong queue",
    )

    queue = repository.queue_for_officer(officer("land", "dat_dai_xay_dung"))
    assert len(queue) == 1
    assert queue[0].canonical_domain == "dat_dai_xay_dung"
    payload = queue[0].model_dump(mode="json")
    assert "owner_user_id" not in payload
    assert "question_summary" not in payload


def test_presence_capacity_is_capped_at_three_and_domains_cannot_expand_actor_scope():
    repository = InMemorySupportRepository()
    actor = officer("officer-1", "ho_tich_chung_thuc")
    with pytest.raises(SupportStateError, match="capacity_out_of_range"):
        repository.set_presence(
            actor,
            domains=actor.domains,
            max_capacity=4,
            now=NOW,
            lease_seconds=45,
        )
    with pytest.raises(SupportAccessError, match="domain_scope_violation"):
        repository.set_presence(
            actor,
            domains=("ho_tich_chung_thuc", "dat_dai_xay_dung"),
            max_capacity=3,
            now=NOW,
            lease_seconds=45,
        )
    presence = repository.set_presence(
        actor,
        domains=actor.domains,
        max_capacity=3,
        now=NOW,
        lease_seconds=45,
    )
    assert presence.max_capacity == 3
    assert presence.active_count == 0


def test_json_compatibility_adapter_is_read_only_and_preserves_acl(tmp_path):
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    source = tickets / "legacy-ticket.json"
    payload = {
        "id": "legacy-ticket",
        "citizen_id": "legacy-owner",
        "domain": "hanh_chinh_cong",
        "question": "Nội dung legacy",
        "status": "waiting",
        "created_at": "2026-08-13T08:00:00Z",
    }
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = source.read_bytes()
    adapter = JsonReadOnlySupportAdapter(tickets)

    assert adapter.get_ticket("legacy-ticket", citizen("legacy-owner")).status == TicketStatus.QUEUED
    with pytest.raises(SupportAccessError):
        adapter.get_ticket("legacy-ticket", citizen("other"))
    with pytest.raises(ReadOnlySupportRepositoryError):
        adapter.create_ticket(
            citizen("legacy-owner"),
            domain="hanh_chinh_cong",
            question_summary="Không được ghi",
        )
    assert source.read_bytes() == before


def test_postgres_claim_contract_uses_row_lock_skip_locked():
    sql = " ".join(PostgresSupportRepository.CLAIM_NEXT_SQL.split()).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "STATUS = 'QUEUED'" in sql
    assert "CANONICAL_DOMAIN = ANY" in sql
