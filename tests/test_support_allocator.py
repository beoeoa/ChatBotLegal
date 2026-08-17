from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from api.support_allocator import SupportAllocator
from api.support_repository import (
    AssignmentStatus,
    InMemorySupportRepository,
    SupportAccessError,
    SupportActor,
    SupportStateError,
    TicketStatus,
)


NOW = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
DOMAIN = "ho_tich_chung_thuc"


def citizen(index: int) -> SupportActor:
    return SupportActor(user_id=f"citizen-{index}", role="citizen")


def officer(index: int, domain: str = DOMAIN) -> SupportActor:
    return SupportActor(user_id=f"officer-{index}", role="officer", domains=(domain,))


def test_concurrent_claim_never_assigns_one_ticket_twice():
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(repository)
    ticket = repository.create_ticket(
        citizen(1), domain=DOMAIN, question_summary="Một ticket duy nhất"
    )
    officers = [officer(index) for index in range(10)]
    for actor in officers:
        allocator.heartbeat(actor, domains=actor.domains, now=NOW)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda actor: allocator.claim_next(actor, now=NOW), officers))

    claims = [result for result in results if result is not None]
    assert len(claims) == 1
    assert claims[0].assignment.ticket_id == ticket.id
    active = [item for item in repository.assignments() if item.status in {AssignmentStatus.LEASED, AssignmentStatus.ACTIVE}]
    assert len(active) == 1


def test_allocator_respects_domain_and_three_session_capacity():
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(repository)
    land_domain = "dat_dai_xay_dung"
    for index in range(5):
        repository.create_ticket(citizen(index), domain=DOMAIN, question_summary=f"Ticket {index}")
    repository.create_ticket(citizen(10), domain=land_domain, question_summary="Land ticket")
    actor = officer(1)
    allocator.heartbeat(actor, domains=actor.domains, max_capacity=3, now=NOW)

    claims = [allocator.claim_next(actor, now=NOW) for _ in range(4)]
    assert sum(item is not None for item in claims) == 3
    assert all(item.assignment.canonical_domain == DOMAIN for item in claims if item)
    assert repository.get_presence(actor.user_id).active_count == 3
    assert repository.get_presence(actor.user_id).presence_status == "busy"
    land_queue = repository.queue_for_officer(
        SupportActor(user_id="land", role="officer", domains=(land_domain,))
    )
    assert len(land_queue) == 1


def test_expired_lease_requeues_once_and_next_claim_increments_generation():
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(
        repository, presence_lease_seconds=10, assignment_lease_seconds=10
    )
    ticket = repository.create_ticket(
        citizen(1), domain=DOMAIN, question_summary="Ticket sẽ requeue"
    )
    first_officer = officer(1)
    allocator.heartbeat(first_officer, domains=first_officer.domains, now=NOW)
    first = allocator.claim_next(first_officer, now=NOW)
    assert first and first.assignment.generation == 1

    requeued = allocator.requeue_expired(now=NOW + timedelta(seconds=11))
    assert requeued == [ticket.id]
    assert allocator.requeue_expired(now=NOW + timedelta(seconds=12)) == []

    second_officer = officer(2)
    allocator.heartbeat(
        second_officer, domains=second_officer.domains, now=NOW + timedelta(seconds=12)
    )
    second = allocator.claim_next(second_officer, now=NOW + timedelta(seconds=12))
    assert second and second.assignment.generation == 2
    assert second.assignment.officer_user_id == second_officer.user_id


def test_assignment_activation_requires_unexpired_one_time_lease_token():
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(repository, assignment_lease_seconds=10)
    repository.create_ticket(citizen(1), domain=DOMAIN, question_summary="Activate")
    actor = officer(1)
    allocator.heartbeat(actor, domains=actor.domains, now=NOW)
    allocation = allocator.claim_next(actor, now=NOW)
    assert allocation

    invalid = allocation.model_copy(update={"lease_token": "wrong-token"})
    with pytest.raises(SupportAccessError, match="token_invalid"):
        allocator.activate(invalid, now=NOW + timedelta(seconds=1))
    activated = allocator.activate(allocation, now=NOW + timedelta(seconds=1))
    assert activated.status == AssignmentStatus.ACTIVE
    with pytest.raises(SupportStateError):
        allocator.activate(allocation, now=NOW + timedelta(seconds=2))


def test_non_officer_cannot_claim():
    repository = InMemorySupportRepository()
    allocator = SupportAllocator(repository)
    with pytest.raises(SupportAccessError, match="officer_role_required"):
        allocator.claim_next(citizen(1), now=NOW)
