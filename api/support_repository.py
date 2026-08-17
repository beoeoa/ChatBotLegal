"""Canonical support storage with PostgreSQL and read-only JSON compatibility.

All actor, owner and domain checks happen in this boundary. The JSON adapter is
intentionally read-only and can be retained for one release during shadow mode.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Protocol

from pydantic import BaseModel, ConfigDict, Field


MAX_OFFICER_CAPACITY = 3
SUPPORT_CONTENT_RETENTION_DAYS = 180


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _as_datetime(value: Any, default: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif value:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        result = default or utcnow()
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


class SupportRepositoryError(RuntimeError):
    pass


class SupportNotFoundError(SupportRepositoryError):
    pass


class SupportAccessError(SupportRepositoryError):
    pass


class SupportStateError(SupportRepositoryError):
    pass


class SupportConflictError(SupportRepositoryError):
    pass


class ReadOnlySupportRepositoryError(SupportRepositoryError):
    pass


class TicketStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    ACTIVE = "active"
    WAITING_CITIZEN = "waiting_citizen"
    WAITING_OFFICER = "waiting_officer"
    RESOLVED = "resolved"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AssignmentStatus(StrEnum):
    LEASED = "leased"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.QUEUED: {TicketStatus.ASSIGNED, TicketStatus.CANCELLED, TicketStatus.EXPIRED},
    TicketStatus.ASSIGNED: {TicketStatus.ACTIVE, TicketStatus.QUEUED, TicketStatus.CANCELLED, TicketStatus.EXPIRED},
    TicketStatus.ACTIVE: {TicketStatus.WAITING_CITIZEN, TicketStatus.WAITING_OFFICER, TicketStatus.RESOLVED, TicketStatus.QUEUED},
    TicketStatus.WAITING_CITIZEN: {TicketStatus.ACTIVE, TicketStatus.RESOLVED, TicketStatus.QUEUED},
    TicketStatus.WAITING_OFFICER: {TicketStatus.ACTIVE, TicketStatus.RESOLVED, TicketStatus.QUEUED},
    TicketStatus.RESOLVED: {TicketStatus.CLOSED},
    TicketStatus.CLOSED: set(),
    TicketStatus.CANCELLED: set(),
    TicketStatus.EXPIRED: set(),
}


class SupportActor(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    role: str
    domains: tuple[str, ...] = ()
    access_reason: str | None = None


class SupportTicketRecord(BaseModel):
    id: str
    owner_user_id: str
    canonical_domain: str
    question_summary: str
    status: TicketStatus = TicketStatus.QUEUED
    priority: str = "normal"
    assigned_officer_id: str | None = None
    assignment_generation: int = 0
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    first_response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    retention_expires_at: datetime = Field(
        default_factory=lambda: utcnow() + timedelta(days=SUPPORT_CONTENT_RETENTION_DAYS)
    )


class SupportQueueItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    canonical_domain: str
    status: TicketStatus
    priority: str
    created_at: datetime
    queue_sequence: int


class SupportStateEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    ticket_id: str
    actor_user_id: str
    actor_role: str
    from_status: TicketStatus | None
    to_status: TicketStatus
    reason_code: str | None = None
    detail_sha256: str
    previous_hash: str | None = None
    entry_hash: str
    occurred_at: datetime = Field(default_factory=utcnow)


class OfficerPresenceRecord(BaseModel):
    officer_user_id: str
    canonical_domains: tuple[str, ...]
    presence_status: str = "available"
    max_capacity: int = 3
    active_count: int = 0
    heartbeat_at: datetime
    lease_expires_at: datetime
    version: int = 1


class SupportAssignmentRecord(BaseModel):
    id: str
    ticket_id: str
    officer_user_id: str
    canonical_domain: str
    generation: int
    lease_token_sha256: str
    lease_expires_at: datetime
    status: AssignmentStatus = AssignmentStatus.LEASED
    assigned_at: datetime = Field(default_factory=utcnow)
    activated_at: datetime | None = None
    released_at: datetime | None = None
    release_reason: str | None = None


class SupportMessageRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    ticket_id: str
    sender_user_id: str
    sender_role: str
    sequence_number: int
    content: str
    content_sha256: str
    created_at: datetime
    retention_expires_at: datetime


class SupportRepository(Protocol):
    def create_ticket(self, actor: SupportActor, *, domain: str, question_summary: str, question_content: str | None = None, priority: str = "normal") -> SupportTicketRecord: ...
    def get_ticket(self, ticket_id: str, actor: SupportActor, *, include_content: bool = True) -> SupportTicketRecord: ...
    def list_mine(self, actor: SupportActor) -> list[SupportTicketRecord]: ...
    def queue_for_officer(self, actor: SupportActor) -> list[SupportQueueItem]: ...
    def queue_position(self, ticket_id: str, actor: SupportActor) -> dict[str, Any]: ...
    def list_messages(self, ticket_id: str, actor: SupportActor) -> list[SupportMessageRecord]: ...
    def admin_ticket_metadata(self, actor: SupportActor, *, status: str | None = None, domain: str | None = None) -> list[dict[str, Any]]: ...
    def transition_ticket(self, ticket_id: str, to_status: TicketStatus, actor: SupportActor, *, expected_version: int, reason_code: str | None = None) -> SupportTicketRecord: ...
    def set_presence(self, actor: SupportActor, *, domains: Iterable[str], max_capacity: int, now: datetime, lease_seconds: int) -> OfficerPresenceRecord: ...


def _validate_capacity(max_capacity: int, active_count: int = 0) -> None:
    if not 1 <= max_capacity <= MAX_OFFICER_CAPACITY:
        raise SupportStateError("officer_capacity_out_of_range")
    if active_count < 0 or active_count > max_capacity:
        raise SupportStateError("officer_active_count_exceeds_capacity")


def _validate_transition(current: TicketStatus, target: TicketStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise SupportStateError(f"invalid_support_transition:{current.value}->{target.value}")


def _assert_ticket_access(ticket: SupportTicketRecord, actor: SupportActor, *, include_content: bool) -> None:
    if actor.role == "citizen":
        if ticket.owner_user_id != actor.user_id:
            raise SupportAccessError("support_ticket_owner_required")
        return
    if actor.role == "officer":
        if ticket.assigned_officer_id != actor.user_id:
            raise SupportAccessError("support_ticket_assignment_required")
        if ticket.canonical_domain not in actor.domains:
            raise SupportAccessError("support_ticket_domain_required")
        return
    if actor.role == "admin":
        if include_content and len((actor.access_reason or "").strip()) < 8:
            raise SupportAccessError("admin_content_access_reason_required")
        return
    if actor.role != "system":
        raise SupportAccessError("support_role_not_allowed")


def _assert_transition_actor(ticket: SupportTicketRecord, target: TicketStatus, actor: SupportActor) -> None:
    if actor.role == "system":
        return
    if actor.role == "citizen":
        if ticket.owner_user_id != actor.user_id or target != TicketStatus.CANCELLED:
            raise SupportAccessError("citizen_support_transition_not_allowed")
        return
    if actor.role == "officer":
        if ticket.assigned_officer_id != actor.user_id or ticket.canonical_domain not in actor.domains:
            raise SupportAccessError("officer_support_assignment_required")
        if target not in {TicketStatus.ACTIVE, TicketStatus.WAITING_CITIZEN, TicketStatus.WAITING_OFFICER, TicketStatus.RESOLVED}:
            raise SupportAccessError("officer_support_transition_not_allowed")
        return
    if actor.role == "admin":
        if len((actor.access_reason or "").strip()) < 8:
            raise SupportAccessError("admin_support_reason_required")
        return
    raise SupportAccessError("support_role_not_allowed")


class InMemorySupportRepository:
    """Deterministic repository used by allocator and authorization tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets: dict[str, SupportTicketRecord] = {}
        self._queue_sequences: dict[str, int] = {}
        self._events: list[SupportStateEvent] = []
        self._messages: dict[str, list[SupportMessageRecord]] = {}
        self._presence: dict[str, OfficerPresenceRecord] = {}
        self._assignments: dict[str, SupportAssignmentRecord] = {}
        self._admin_access: list[dict[str, Any]] = []
        self._next_queue_sequence = 1

    def create_ticket(
        self,
        actor: SupportActor,
        *,
        domain: str,
        question_summary: str,
        question_content: str | None = None,
        priority: str = "normal",
    ) -> SupportTicketRecord:
        if actor.role != "citizen" or not actor.user_id:
            raise SupportAccessError("citizen_account_required")
        if not domain.strip():
            raise SupportStateError("canonical_domain_required")
        now = utcnow()
        response_minutes = {"urgent": 15, "high": 30, "normal": 60, "low": 120}.get(priority, 60)
        record = SupportTicketRecord(
            id=uuid.uuid4().hex,
            owner_user_id=actor.user_id,
            canonical_domain=domain,
            question_summary=question_summary.strip(),
            priority=priority,
            created_at=now,
            updated_at=now,
            first_response_due_at=now + timedelta(minutes=response_minutes),
            resolution_due_at=now + timedelta(hours=8),
            retention_expires_at=now + timedelta(days=SUPPORT_CONTENT_RETENTION_DAYS),
        )
        with self._lock:
            self._tickets[record.id] = record
            self._queue_sequences[record.id] = self._next_queue_sequence
            self._next_queue_sequence += 1
            initial_content = (question_content or question_summary).strip()
            self._messages[record.id] = [
                SupportMessageRecord(
                    id=uuid.uuid4().hex,
                    ticket_id=record.id,
                    sender_user_id=actor.user_id,
                    sender_role=actor.role,
                    sequence_number=1,
                    content=initial_content,
                    content_sha256=hashlib.sha256(initial_content.encode("utf-8")).hexdigest(),
                    created_at=now,
                    retention_expires_at=record.retention_expires_at,
                )
            ]
        return record.model_copy(deep=True)

    def get_ticket(
        self,
        ticket_id: str,
        actor: SupportActor,
        *,
        include_content: bool = True,
    ) -> SupportTicketRecord:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                raise SupportNotFoundError("support_ticket_not_found")
            _assert_ticket_access(ticket, actor, include_content=include_content)
            return ticket.model_copy(deep=True)

    def list_mine(self, actor: SupportActor) -> list[SupportTicketRecord]:
        if actor.role != "citizen":
            raise SupportAccessError("citizen_role_required")
        with self._lock:
            return [
                item.model_copy(deep=True)
                for item in sorted(self._tickets.values(), key=lambda value: value.updated_at, reverse=True)
                if item.owner_user_id == actor.user_id
            ]

    def queue_for_officer(self, actor: SupportActor) -> list[SupportQueueItem]:
        if actor.role != "officer" or not actor.domains:
            raise SupportAccessError("officer_domain_required")
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        with self._lock:
            tickets = [
                ticket for ticket in self._tickets.values()
                if ticket.status == TicketStatus.QUEUED and ticket.canonical_domain in actor.domains
            ]
            tickets.sort(key=lambda value: (priority_order.get(value.priority, 2), self._queue_sequences[value.id]))
            return [
                SupportQueueItem(
                    id=ticket.id,
                    canonical_domain=ticket.canonical_domain,
                    status=ticket.status,
                    priority=ticket.priority,
                    created_at=ticket.created_at,
                    queue_sequence=self._queue_sequences[ticket.id],
                )
                for ticket in tickets
            ]

    def queue_position(self, ticket_id: str, actor: SupportActor) -> dict[str, Any]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                raise SupportNotFoundError("support_ticket_not_found")
            _assert_ticket_access(ticket, actor, include_content=False)
            if ticket.status != TicketStatus.QUEUED:
                return {
                    "ticket_id": ticket.id,
                    "status": ticket.status.value,
                    "position": None,
                    "estimated_wait_seconds": None,
                }
            sequence = self._queue_sequences[ticket.id]
            ahead = sum(
                1
                for other in self._tickets.values()
                if other.status == TicketStatus.QUEUED
                and other.canonical_domain == ticket.canonical_domain
                and self._queue_sequences[other.id] < sequence
            )
            return {
                "ticket_id": ticket.id,
                "status": ticket.status.value,
                "position": ahead + 1,
                "estimated_wait_seconds": (ahead + 1) * 120,
            }

    def admin_ticket_metadata(
        self,
        actor: SupportActor,
        *,
        status: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        if actor.role != "admin":
            raise SupportAccessError("admin_role_required")
        now = utcnow()
        with self._lock:
            rows = []
            for ticket in self._tickets.values():
                if status and ticket.status.value != status:
                    continue
                if domain and ticket.canonical_domain != domain:
                    continue
                rows.append({
                    "id": ticket.id,
                    "canonical_domain": ticket.canonical_domain,
                    "status": ticket.status.value,
                    "priority": ticket.priority,
                    "assigned_officer_id": ticket.assigned_officer_id,
                    "assignment_generation": ticket.assignment_generation,
                    "created_at": ticket.created_at.isoformat(),
                    "updated_at": ticket.updated_at.isoformat(),
                    "first_response_due_at": ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
                    "resolution_due_at": ticket.resolution_due_at.isoformat() if ticket.resolution_due_at else None,
                    "overdue": bool(ticket.first_response_due_at and ticket.status == TicketStatus.QUEUED and ticket.first_response_due_at < now),
                })
            return sorted(rows, key=lambda item: (not item["overdue"], item["created_at"]))

    def transition_ticket(
        self,
        ticket_id: str,
        to_status: TicketStatus,
        actor: SupportActor,
        *,
        expected_version: int,
        reason_code: str | None = None,
    ) -> SupportTicketRecord:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                raise SupportNotFoundError("support_ticket_not_found")
            if ticket.version != expected_version:
                raise SupportConflictError("support_ticket_version_conflict")
            _validate_transition(ticket.status, to_status)
            _assert_transition_actor(ticket, to_status, actor)
            previous = ticket.status
            now = utcnow()
            updated = ticket.model_copy(update={"status": to_status, "version": ticket.version + 1, "updated_at": now})
            self._tickets[ticket_id] = updated
            self._append_state_event(updated, previous, actor, reason_code, now)
            return updated.model_copy(deep=True)

    def _append_state_event(
        self,
        ticket: SupportTicketRecord,
        previous: TicketStatus | None,
        actor: SupportActor,
        reason_code: str | None,
        occurred_at: datetime,
    ) -> None:
        previous_hash = next(
            (
                event.entry_hash
                for event in reversed(self._events)
                if event.ticket_id == ticket.id
            ),
            None,
        )
        detail = {
            "ticket_id": ticket.id,
            "from": previous.value if previous else None,
            "to": ticket.status.value,
            "actor": actor.user_id,
            "reason": reason_code,
            "version": ticket.version,
        }
        detail_sha = _sha256(detail)
        entry_sha = _sha256({"previous_hash": previous_hash, "detail_sha256": detail_sha})
        self._events.append(
            SupportStateEvent(
                id=uuid.uuid4().hex,
                ticket_id=ticket.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                from_status=previous,
                to_status=ticket.status,
                reason_code=reason_code,
                detail_sha256=detail_sha,
                previous_hash=previous_hash,
                entry_hash=entry_sha,
                occurred_at=occurred_at,
            )
        )

    def state_events(self, ticket_id: str) -> tuple[SupportStateEvent, ...]:
        with self._lock:
            return tuple(event for event in self._events if event.ticket_id == ticket_id)

    def set_presence(
        self,
        actor: SupportActor,
        *,
        domains: Iterable[str],
        max_capacity: int,
        now: datetime,
        lease_seconds: int,
    ) -> OfficerPresenceRecord:
        if actor.role != "officer":
            raise SupportAccessError("officer_role_required")
        normalized_domains = tuple(sorted(set(domains)))
        if not normalized_domains or not set(normalized_domains).issubset(set(actor.domains)):
            raise SupportAccessError("officer_domain_scope_violation")
        current = self._presence.get(actor.user_id)
        active_count = current.active_count if current else 0
        _validate_capacity(max_capacity, active_count)
        record = OfficerPresenceRecord(
            officer_user_id=actor.user_id,
            canonical_domains=normalized_domains,
            presence_status="busy" if active_count >= max_capacity else "available",
            max_capacity=max_capacity,
            active_count=active_count,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            version=(current.version + 1) if current else 1,
        )
        with self._lock:
            self._presence[actor.user_id] = record
        return record.model_copy(deep=True)

    def get_presence(self, officer_user_id: str) -> OfficerPresenceRecord | None:
        with self._lock:
            item = self._presence.get(officer_user_id)
            return item.model_copy(deep=True) if item else None

    def append_message(self, ticket_id: str, actor: SupportActor, content: str) -> SupportMessageRecord:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                raise SupportNotFoundError("support_ticket_not_found")
            _assert_ticket_access(ticket, actor, include_content=True)
            if ticket.status not in {TicketStatus.ACTIVE, TicketStatus.WAITING_CITIZEN, TicketStatus.WAITING_OFFICER}:
                raise SupportStateError("support_ticket_not_active")
            messages = self._messages.setdefault(ticket_id, [])
            now = utcnow()
            record = SupportMessageRecord(
                id=uuid.uuid4().hex,
                ticket_id=ticket_id,
                sender_user_id=actor.user_id,
                sender_role=actor.role,
                sequence_number=len(messages) + 1,
                content=content,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                created_at=now,
                retention_expires_at=ticket.retention_expires_at,
            )
            messages.append(record)
            return record

    def list_messages(self, ticket_id: str, actor: SupportActor) -> list[SupportMessageRecord]:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if not ticket:
                raise SupportNotFoundError("support_ticket_not_found")
            _assert_ticket_access(ticket, actor, include_content=True)
            return list(self._messages.get(ticket_id) or [])

    def claim_next_atomic(
        self,
        officer_user_id: str,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> tuple[SupportAssignmentRecord, str] | None:
        with self._lock:
            presence = self._presence.get(officer_user_id)
            if not presence or presence.lease_expires_at <= now or presence.presence_status not in {"available", "busy"}:
                return None
            if presence.active_count >= presence.max_capacity:
                return None
            queue = self.queue_for_officer(
                SupportActor(user_id=officer_user_id, role="officer", domains=presence.canonical_domains)
            )
            if not queue:
                return None
            ticket = self._tickets[queue[0].id]
            token = secrets.token_urlsafe(32)
            generation = ticket.assignment_generation + 1
            updated = ticket.model_copy(update={
                "status": TicketStatus.ASSIGNED,
                "assigned_officer_id": officer_user_id,
                "assignment_generation": generation,
                "version": ticket.version + 1,
                "updated_at": now,
            })
            self._tickets[ticket.id] = updated
            assignment = SupportAssignmentRecord(
                id=uuid.uuid4().hex,
                ticket_id=ticket.id,
                officer_user_id=officer_user_id,
                canonical_domain=ticket.canonical_domain,
                generation=generation,
                lease_token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                assigned_at=now,
            )
            self._assignments[assignment.id] = assignment
            self._presence[officer_user_id] = presence.model_copy(update={
                "active_count": presence.active_count + 1,
                "presence_status": "busy" if presence.active_count + 1 >= presence.max_capacity else "available",
                "version": presence.version + 1,
            })
            self._append_state_event(
                updated,
                ticket.status,
                SupportActor(user_id="support-allocator", role="system"),
                "allocator_claim",
                now,
            )
            return assignment.model_copy(deep=True), token

    def activate_assignment(
        self,
        assignment_id: str,
        lease_token: str,
        *,
        now: datetime,
        expected_officer_user_id: str | None = None,
    ) -> SupportAssignmentRecord:
        with self._lock:
            assignment = self._assignments.get(assignment_id)
            if not assignment:
                raise SupportNotFoundError("support_assignment_not_found")
            if expected_officer_user_id and assignment.officer_user_id != expected_officer_user_id:
                raise SupportAccessError("support_assignment_owner_required")
            if assignment.status != AssignmentStatus.LEASED or assignment.lease_expires_at <= now:
                raise SupportStateError("support_assignment_lease_expired")
            if not secrets.compare_digest(
                assignment.lease_token_sha256,
                hashlib.sha256(lease_token.encode("utf-8")).hexdigest(),
            ):
                raise SupportAccessError("support_assignment_token_invalid")
            ticket = self._tickets[assignment.ticket_id]
            updated_ticket = ticket.model_copy(update={
                "status": TicketStatus.ACTIVE,
                "version": ticket.version + 1,
                "updated_at": now,
            })
            self._tickets[ticket.id] = updated_ticket
            updated = assignment.model_copy(update={"status": AssignmentStatus.ACTIVE, "activated_at": now})
            self._assignments[assignment.id] = updated
            self._append_state_event(
                updated_ticket,
                ticket.status,
                SupportActor(user_id=assignment.officer_user_id, role="officer", domains=(assignment.canonical_domain,)),
                "assignment_activated",
                now,
            )
            return updated.model_copy(deep=True)

    def requeue_expired_assignments(self, *, now: datetime) -> list[str]:
        requeued: list[str] = []
        with self._lock:
            for assignment_id, assignment in list(self._assignments.items()):
                presence = self._presence.get(assignment.officer_user_id)
                expired = assignment.lease_expires_at <= now or not presence or presence.lease_expires_at <= now
                if assignment.status not in {AssignmentStatus.LEASED, AssignmentStatus.ACTIVE} or not expired:
                    continue
                ticket = self._tickets[assignment.ticket_id]
                if ticket.assignment_generation != assignment.generation:
                    continue
                updated_ticket = ticket.model_copy(update={
                    "status": TicketStatus.QUEUED,
                    "assigned_officer_id": None,
                    "version": ticket.version + 1,
                    "updated_at": now,
                })
                self._tickets[ticket.id] = updated_ticket
                self._assignments[assignment_id] = assignment.model_copy(update={
                    "status": AssignmentStatus.EXPIRED,
                    "released_at": now,
                    "release_reason": "lease_expired",
                })
                if presence:
                    next_count = max(0, presence.active_count - 1)
                    self._presence[presence.officer_user_id] = presence.model_copy(update={
                        "active_count": next_count,
                        "presence_status": "offline" if presence.lease_expires_at <= now else "available",
                        "version": presence.version + 1,
                    })
                self._append_state_event(
                    updated_ticket,
                    ticket.status,
                    SupportActor(user_id="support-allocator", role="system"),
                    "lease_expired_requeue",
                    now,
                )
                requeued.append(ticket.id)
        return requeued

    def assignments(self) -> tuple[SupportAssignmentRecord, ...]:
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._assignments.values())

    def record_admin_content_access(
        self,
        ticket_id: str,
        actor: SupportActor,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        ticket = self.get_ticket(ticket_id, actor.model_copy(update={"access_reason": reason}), include_content=True)
        observed_at = now or utcnow()
        grant = {
            "id": uuid.uuid4().hex,
            "ticket_id": ticket.id,
            "admin_user_id": actor.user_id,
            "reason": reason.strip(),
            "granted_at": observed_at,
            "expires_at": observed_at + timedelta(minutes=15),
            "audit_event_id": uuid.uuid4().hex,
        }
        with self._lock:
            self._admin_access.append(grant)
        return dict(grant)


class JsonReadOnlySupportAdapter(InMemorySupportRepository):
    """Legacy JSON reader that enforces canonical ACL and refuses all writes."""

    def __init__(self, tickets_dir: Path) -> None:
        super().__init__()
        if tickets_dir.is_dir():
            for path in sorted(tickets_dir.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                owner = str(raw.get("citizen_id") or raw.get("owner_user_id") or "").strip()
                domain = str(raw.get("domain") or raw.get("assigned_department") or "").strip()
                if not owner or not domain:
                    continue
                status_map = {
                    "open": TicketStatus.QUEUED,
                    "waiting": TicketStatus.QUEUED,
                    "answered": TicketStatus.ACTIVE,
                    **{item.value: item for item in TicketStatus},
                }
                status = status_map.get(str(raw.get("status") or "waiting"), TicketStatus.QUEUED)
                record = SupportTicketRecord(
                    id=str(raw.get("id") or path.stem),
                    owner_user_id=owner,
                    canonical_domain=domain,
                    question_summary=str(raw.get("ai_summary") or raw.get("question") or "Yêu cầu hỗ trợ"),
                    status=status,
                    priority=str(raw.get("priority") or "normal"),
                    assigned_officer_id=raw.get("assigned_officer_id"),
                    created_at=_as_datetime(raw.get("created_at")),
                    updated_at=_as_datetime(raw.get("updated_at"), _as_datetime(raw.get("created_at"))),
                )
                self._tickets[record.id] = record
                self._queue_sequences[record.id] = self._next_queue_sequence
                self._next_queue_sequence += 1

    @staticmethod
    def _read_only(*args: Any, **kwargs: Any) -> Any:
        raise ReadOnlySupportRepositoryError("legacy_json_support_is_read_only")

    create_ticket = _read_only
    transition_ticket = _read_only
    set_presence = _read_only
    append_message = _read_only
    claim_next_atomic = _read_only
    activate_assignment = _read_only
    requeue_expired_assignments = _read_only
    record_admin_content_access = _read_only


class PostgresSupportRepository:
    """PostgreSQL implementation for canonical support state."""

    CLAIM_NEXT_SQL = """
        SELECT id FROM support_ticket
        WHERE status = 'queued' AND canonical_domain = ANY(:domains)
        ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                 queue_sequence
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    """

    def __init__(self, database_url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(database_url, future=True, pool_pre_ping=True)

    @staticmethod
    def _insert_state_event(
        connection: Any,
        *,
        ticket_id: str,
        actor: SupportActor,
        from_status: TicketStatus,
        to_status: TicketStatus,
        reason_code: str,
        version: int,
    ) -> None:
        from sqlalchemy import text

        detail_sha = _sha256({
            "ticket": ticket_id,
            "from": from_status.value,
            "to": to_status.value,
            "actor": actor.user_id,
            "reason": reason_code,
            "version": version,
        })
        previous_hash = connection.execute(text("""
            SELECT entry_hash FROM support_state_event
            WHERE ticket_ref = :ticket ORDER BY event_sequence DESC LIMIT 1
        """), {"ticket": ticket_id}).scalar_one_or_none()
        entry_hash = _sha256({"previous_hash": previous_hash, "detail_sha256": detail_sha})
        connection.execute(text("""
            INSERT INTO support_state_event
                (id, ticket_ref, actor_user_id, actor_role, from_status, to_status,
                 reason_code, detail_sha256, previous_hash, entry_hash)
            VALUES (:id, :ticket, :actor, :role, :from_status, :to_status,
                    :reason, :detail, :previous, :entry)
        """), {
            "id": uuid.uuid4().hex,
            "ticket": ticket_id,
            "actor": actor.user_id,
            "role": actor.role,
            "from_status": from_status.value,
            "to_status": to_status.value,
            "reason": reason_code,
            "detail": detail_sha,
            "previous": previous_hash,
            "entry": entry_hash,
        })

    @staticmethod
    def _ticket(row: Any) -> SupportTicketRecord:
        data = dict(row)
        return SupportTicketRecord(
            id=str(data["id"]),
            owner_user_id=str(data["owner_user_id"]),
            canonical_domain=str(data["canonical_domain"]),
            question_summary=str(data["question_summary"]),
            status=TicketStatus(data["status"]),
            priority=str(data["priority"]),
            assigned_officer_id=data.get("assigned_officer_id"),
            assignment_generation=int(data.get("assignment_generation") or 0),
            version=int(data.get("version") or 1),
            created_at=_as_datetime(data.get("created_at")),
            updated_at=_as_datetime(data.get("updated_at")),
            retention_expires_at=_as_datetime(data.get("retention_expires_at")),
            first_response_due_at=_as_datetime(data["first_response_due_at"]) if data.get("first_response_due_at") else None,
            resolution_due_at=_as_datetime(data["resolution_due_at"]) if data.get("resolution_due_at") else None,
        )

    def create_ticket(self, actor: SupportActor, *, domain: str, question_summary: str, question_content: str | None = None, priority: str = "normal") -> SupportTicketRecord:
        from sqlalchemy import text

        if actor.role != "citizen" or not actor.user_id:
            raise SupportAccessError("citizen_account_required")
        now = utcnow()
        response_minutes = {"urgent": 15, "high": 30, "normal": 60, "low": 120}.get(priority, 60)
        values = {
            "id": uuid.uuid4().hex,
            "owner": actor.user_id,
            "domain": domain,
            "summary": question_summary.strip(),
            "priority": priority,
            "now": now,
            "retention": now + timedelta(days=SUPPORT_CONTENT_RETENTION_DAYS),
            "first_response_due": now + timedelta(minutes=response_minutes),
            "resolution_due": now + timedelta(hours=8),
        }
        with self.engine.begin() as connection:
            row = connection.execute(text("""
                INSERT INTO support_ticket
                    (id, owner_user_id, canonical_domain, question_summary, status,
                     priority, retention_expires_at, first_response_due_at,
                     resolution_due_at, created_at, updated_at)
                VALUES (:id, :owner, :domain, :summary, 'queued', :priority, :retention,
                        :first_response_due, :resolution_due, :now, :now)
                RETURNING *
            """), values).mappings().one()
            initial_content = (question_content or question_summary).strip()
            connection.execute(text("""
                INSERT INTO support_message
                    (id, ticket_ref, sender_user_id, sender_role, sequence_number,
                     content, content_sha256, created_at, retention_expires_at)
                VALUES (:id, :ticket, :sender, 'citizen', 1,
                        :content, :checksum, :now, :retention)
            """), {
                "id": uuid.uuid4().hex,
                "ticket": values["id"],
                "sender": actor.user_id,
                "content": initial_content,
                "checksum": hashlib.sha256(initial_content.encode("utf-8")).hexdigest(),
                "now": now,
                "retention": values["retention"],
            })
        return self._ticket(row)

    def _get_unscoped(self, ticket_id: str) -> SupportTicketRecord:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM support_ticket WHERE id = :id"), {"id": ticket_id}
            ).mappings().first()
        if not row:
            raise SupportNotFoundError("support_ticket_not_found")
        return self._ticket(row)

    def get_ticket(self, ticket_id: str, actor: SupportActor, *, include_content: bool = True) -> SupportTicketRecord:
        ticket = self._get_unscoped(ticket_id)
        _assert_ticket_access(ticket, actor, include_content=include_content)
        return ticket

    def list_mine(self, actor: SupportActor) -> list[SupportTicketRecord]:
        from sqlalchemy import text

        if actor.role != "citizen":
            raise SupportAccessError("citizen_role_required")
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT * FROM support_ticket WHERE owner_user_id = :owner
                ORDER BY updated_at DESC
            """), {"owner": actor.user_id}).mappings().all()
        return [self._ticket(row) for row in rows]

    def queue_for_officer(self, actor: SupportActor) -> list[SupportQueueItem]:
        from sqlalchemy import text

        if actor.role != "officer" or not actor.domains:
            raise SupportAccessError("officer_domain_required")
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id, canonical_domain, status, priority, created_at, queue_sequence
                FROM support_ticket
                WHERE status = 'queued' AND canonical_domain = ANY(:domains)
                ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
                         queue_sequence
            """), {"domains": list(actor.domains)}).mappings().all()
        return [SupportQueueItem(**dict(row)) for row in rows]

    def queue_position(self, ticket_id: str, actor: SupportActor) -> dict[str, Any]:
        from sqlalchemy import text

        ticket = self.get_ticket(ticket_id, actor, include_content=False)
        if ticket.status != TicketStatus.QUEUED:
            return {
                "ticket_id": ticket.id,
                "status": ticket.status.value,
                "position": None,
                "estimated_wait_seconds": None,
            }
        with self.engine.connect() as connection:
            position = int(connection.execute(text("""
                SELECT COUNT(*) + 1 FROM support_ticket current_ticket
                JOIN support_ticket queued
                  ON queued.canonical_domain = current_ticket.canonical_domain
                 AND queued.status = 'queued'
                 AND queued.queue_sequence < current_ticket.queue_sequence
                WHERE current_ticket.id = :ticket
            """), {"ticket": ticket_id}).scalar_one())
        return {
            "ticket_id": ticket.id,
            "status": ticket.status.value,
            "position": position,
            "estimated_wait_seconds": position * 120,
        }

    def admin_ticket_metadata(
        self,
        actor: SupportActor,
        *,
        status: str | None = None,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        if actor.role != "admin":
            raise SupportAccessError("admin_role_required")
        clauses = ["TRUE"]
        params: dict[str, Any] = {}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if domain:
            clauses.append("canonical_domain = :domain")
            params["domain"] = domain
        with self.engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT id, canonical_domain, status, priority, assigned_officer_id,
                       assignment_generation, created_at, updated_at,
                       first_response_due_at, resolution_due_at,
                       (status = 'queued' AND first_response_due_at < CURRENT_TIMESTAMP) AS overdue
                FROM support_ticket WHERE {' AND '.join(clauses)}
                ORDER BY overdue DESC, created_at
                LIMIT 500
            """), params).mappings().all()
        return [
            {
                **dict(row),
                "id": str(row["id"]),
                "created_at": _as_datetime(row["created_at"]).isoformat(),
                "updated_at": _as_datetime(row["updated_at"]).isoformat(),
                "first_response_due_at": _as_datetime(row["first_response_due_at"]).isoformat() if row["first_response_due_at"] else None,
                "resolution_due_at": _as_datetime(row["resolution_due_at"]).isoformat() if row["resolution_due_at"] else None,
                "overdue": bool(row["overdue"]),
            }
            for row in rows
        ]

    def transition_ticket(self, ticket_id: str, to_status: TicketStatus, actor: SupportActor, *, expected_version: int, reason_code: str | None = None) -> SupportTicketRecord:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT * FROM support_ticket WHERE id = :id FOR UPDATE"), {"id": ticket_id}
            ).mappings().first()
            if not row:
                raise SupportNotFoundError("support_ticket_not_found")
            ticket = self._ticket(row)
            if ticket.version != expected_version:
                raise SupportConflictError("support_ticket_version_conflict")
            _validate_transition(ticket.status, to_status)
            _assert_transition_actor(ticket, to_status, actor)
            updated = connection.execute(text("""
                UPDATE support_ticket SET status = :status, version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id AND version = :version
                RETURNING *
            """), {"status": to_status.value, "id": ticket_id, "version": expected_version}).mappings().first()
            if not updated:
                raise SupportConflictError("support_ticket_version_conflict")
            self._insert_state_event(
                connection,
                ticket_id=ticket_id,
                actor=actor,
                from_status=ticket.status,
                to_status=to_status,
                reason_code=reason_code or "manual_transition",
                version=int(updated["version"]),
            )
        return self._ticket(updated)

    def set_presence(self, actor: SupportActor, *, domains: Iterable[str], max_capacity: int, now: datetime, lease_seconds: int) -> OfficerPresenceRecord:
        from sqlalchemy import text

        if actor.role != "officer":
            raise SupportAccessError("officer_role_required")
        normalized = tuple(sorted(set(domains)))
        if not normalized or not set(normalized).issubset(set(actor.domains)):
            raise SupportAccessError("officer_domain_scope_violation")
        _validate_capacity(max_capacity)
        with self.engine.begin() as connection:
            row = connection.execute(text("""
                INSERT INTO officer_presence
                    (officer_user_id, canonical_domains, presence_status, max_capacity,
                     active_count, heartbeat_at, lease_expires_at, version)
                VALUES (:officer, CAST(:domains AS JSONB), 'available', :capacity, 0, :now, :expires, 1)
                ON CONFLICT (officer_user_id) DO UPDATE SET
                    canonical_domains = EXCLUDED.canonical_domains,
                    max_capacity = EXCLUDED.max_capacity,
                    heartbeat_at = EXCLUDED.heartbeat_at,
                    lease_expires_at = EXCLUDED.lease_expires_at,
                    presence_status = CASE WHEN officer_presence.active_count >= EXCLUDED.max_capacity THEN 'busy' ELSE 'available' END,
                    version = officer_presence.version + 1
                WHERE officer_presence.active_count <= EXCLUDED.max_capacity
                RETURNING *
            """), {
                "officer": actor.user_id, "domains": json.dumps(normalized), "capacity": max_capacity,
                "now": now, "expires": now + timedelta(seconds=lease_seconds),
            }).mappings().first()
        if not row:
            raise SupportStateError("officer_active_count_exceeds_capacity")
        return OfficerPresenceRecord(
            officer_user_id=str(row["officer_user_id"]),
            canonical_domains=tuple(row["canonical_domains"]),
            presence_status=str(row["presence_status"]),
            max_capacity=int(row["max_capacity"]),
            active_count=int(row["active_count"]),
            heartbeat_at=_as_datetime(row["heartbeat_at"]),
            lease_expires_at=_as_datetime(row["lease_expires_at"]),
            version=int(row["version"]),
        )

    def get_presence(self, officer_user_id: str) -> OfficerPresenceRecord | None:
        from sqlalchemy import text

        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT * FROM officer_presence WHERE officer_user_id = :officer
            """), {"officer": officer_user_id}).mappings().first()
        if not row:
            return None
        return OfficerPresenceRecord(
            officer_user_id=str(row["officer_user_id"]),
            canonical_domains=tuple(row["canonical_domains"]),
            presence_status=str(row["presence_status"]),
            max_capacity=int(row["max_capacity"]),
            active_count=int(row["active_count"]),
            heartbeat_at=_as_datetime(row["heartbeat_at"]),
            lease_expires_at=_as_datetime(row["lease_expires_at"]),
            version=int(row["version"]),
        )

    def append_message(self, ticket_id: str, actor: SupportActor, content: str) -> SupportMessageRecord:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            row = connection.execute(text("""
                SELECT * FROM support_ticket WHERE id = :id FOR UPDATE
            """), {"id": ticket_id}).mappings().first()
            if not row:
                raise SupportNotFoundError("support_ticket_not_found")
            ticket = self._ticket(row)
            _assert_ticket_access(ticket, actor, include_content=True)
            if ticket.status not in {TicketStatus.ACTIVE, TicketStatus.WAITING_CITIZEN, TicketStatus.WAITING_OFFICER}:
                raise SupportStateError("support_ticket_not_active")
            sequence = int(connection.execute(text("""
                SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM support_message WHERE ticket_ref = :ticket
            """), {"ticket": ticket_id}).scalar_one())
            now = utcnow()
            message_id = uuid.uuid4().hex
            content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            connection.execute(text("""
                INSERT INTO support_message
                    (id, ticket_ref, sender_user_id, sender_role, sequence_number,
                     content, content_sha256, created_at, retention_expires_at)
                VALUES (:id, :ticket, :sender, :role, :sequence,
                        :content, :checksum, :now, :retention)
            """), {
                "id": message_id,
                "ticket": ticket_id,
                "sender": actor.user_id,
                "role": actor.role,
                "sequence": sequence,
                "content": content,
                "checksum": content_sha,
                "now": now,
                "retention": ticket.retention_expires_at,
            })
        return SupportMessageRecord(
            id=message_id,
            ticket_id=ticket_id,
            sender_user_id=actor.user_id,
            sender_role=actor.role,
            sequence_number=sequence,
            content=content,
            content_sha256=content_sha,
            created_at=now,
            retention_expires_at=ticket.retention_expires_at,
        )

    def list_messages(self, ticket_id: str, actor: SupportActor) -> list[SupportMessageRecord]:
        from sqlalchemy import text

        ticket = self.get_ticket(ticket_id, actor, include_content=True)
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT * FROM support_message WHERE ticket_ref = :ticket
                ORDER BY sequence_number
            """), {"ticket": ticket.id}).mappings().all()
        return [
            SupportMessageRecord(
                id=str(row["id"]),
                ticket_id=str(row["ticket_ref"]),
                sender_user_id=str(row["sender_user_id"]),
                sender_role=str(row["sender_role"]),
                sequence_number=int(row["sequence_number"]),
                content=str(row["content"]),
                content_sha256=str(row["content_sha256"]),
                created_at=_as_datetime(row["created_at"]),
                retention_expires_at=_as_datetime(row["retention_expires_at"]),
            )
            for row in rows
        ]

    def claim_next_atomic(self, officer_user_id: str, *, now: datetime, lease_seconds: int) -> tuple[SupportAssignmentRecord, str] | None:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            presence_row = connection.execute(text("""
                SELECT * FROM officer_presence WHERE officer_user_id = :officer FOR UPDATE
            """), {"officer": officer_user_id}).mappings().first()
            if not presence_row or _as_datetime(presence_row["lease_expires_at"]) <= now:
                return None
            if int(presence_row["active_count"]) >= int(presence_row["max_capacity"]):
                return None
            domains = list(presence_row["canonical_domains"])
            ticket_row = connection.execute(text(self.CLAIM_NEXT_SQL), {"domains": domains}).mappings().first()
            if not ticket_row:
                return None
            ticket_id = str(ticket_row["id"])
            token = secrets.token_urlsafe(32)
            token_sha = hashlib.sha256(token.encode("utf-8")).hexdigest()
            ticket = connection.execute(text("""
                UPDATE support_ticket SET status = 'assigned', assigned_officer_id = :officer,
                    assignment_generation = assignment_generation + 1, version = version + 1,
                    assigned_at = :now, updated_at = :now
                WHERE id = :ticket AND status = 'queued'
                RETURNING *
            """), {"officer": officer_user_id, "ticket": ticket_id, "now": now}).mappings().one()
            assignment_id = uuid.uuid4().hex
            expires = now + timedelta(seconds=lease_seconds)
            connection.execute(text("""
                INSERT INTO support_assignment
                    (id, ticket_ref, officer_user_id, canonical_domain, generation,
                     lease_token_sha256, lease_expires_at, status, assigned_at)
                VALUES (:id, :ticket, :officer, :domain, :generation, :token, :expires, 'leased', :now)
            """), {
                "id": assignment_id, "ticket": ticket_id, "officer": officer_user_id,
                "domain": ticket["canonical_domain"], "generation": ticket["assignment_generation"],
                "token": token_sha, "expires": expires, "now": now,
            })
            connection.execute(text("""
                UPDATE officer_presence SET active_count = active_count + 1,
                    presence_status = CASE WHEN active_count + 1 >= max_capacity THEN 'busy' ELSE 'available' END,
                    version = version + 1
                WHERE officer_user_id = :officer AND active_count < max_capacity
            """), {"officer": officer_user_id})
            self._insert_state_event(
                connection,
                ticket_id=ticket_id,
                actor=SupportActor(user_id="support-allocator", role="system"),
                from_status=TicketStatus.QUEUED,
                to_status=TicketStatus.ASSIGNED,
                reason_code="allocator_claim",
                version=int(ticket["version"]),
            )
        return SupportAssignmentRecord(
            id=assignment_id,
            ticket_id=ticket_id,
            officer_user_id=officer_user_id,
            canonical_domain=str(ticket["canonical_domain"]),
            generation=int(ticket["assignment_generation"]),
            lease_token_sha256=token_sha,
            lease_expires_at=expires,
            assigned_at=now,
        ), token

    def activate_assignment(
        self,
        assignment_id: str,
        lease_token: str,
        *,
        now: datetime,
        expected_officer_user_id: str | None = None,
    ) -> SupportAssignmentRecord:
        from sqlalchemy import text

        token_sha = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        with self.engine.begin() as connection:
            assignment = connection.execute(text("""
                SELECT * FROM support_assignment WHERE id = :id FOR UPDATE
            """), {"id": assignment_id}).mappings().first()
            if not assignment:
                raise SupportNotFoundError("support_assignment_not_found")
            if expected_officer_user_id and str(assignment["officer_user_id"]) != expected_officer_user_id:
                raise SupportAccessError("support_assignment_owner_required")
            if str(assignment["status"]) != AssignmentStatus.LEASED.value or _as_datetime(assignment["lease_expires_at"]) <= now:
                raise SupportStateError("support_assignment_lease_expired")
            if not secrets.compare_digest(str(assignment["lease_token_sha256"]), token_sha):
                raise SupportAccessError("support_assignment_token_invalid")
            ticket = connection.execute(text("""
                SELECT * FROM support_ticket WHERE id = :id FOR UPDATE
            """), {"id": assignment["ticket_ref"]}).mappings().one()
            if int(ticket["assignment_generation"]) != int(assignment["generation"]):
                raise SupportConflictError("support_assignment_generation_conflict")
            updated_assignment = connection.execute(text("""
                UPDATE support_assignment SET status = 'active', activated_at = :now
                WHERE id = :id AND status = 'leased' RETURNING *
            """), {"id": assignment_id, "now": now}).mappings().one()
            updated_ticket = connection.execute(text("""
                UPDATE support_ticket SET status = 'active', active_at = :now,
                    version = version + 1, updated_at = :now
                WHERE id = :ticket AND status = 'assigned' RETURNING *
            """), {"ticket": assignment["ticket_ref"], "now": now}).mappings().first()
            if not updated_ticket:
                raise SupportConflictError("support_ticket_assignment_conflict")
            self._insert_state_event(
                connection,
                ticket_id=str(assignment["ticket_ref"]),
                actor=SupportActor(
                    user_id=str(assignment["officer_user_id"]),
                    role="officer",
                    domains=(str(assignment["canonical_domain"]),),
                ),
                from_status=TicketStatus.ASSIGNED,
                to_status=TicketStatus.ACTIVE,
                reason_code="assignment_activated",
                version=int(updated_ticket["version"]),
            )
        return SupportAssignmentRecord(
            id=str(updated_assignment["id"]),
            ticket_id=str(updated_assignment["ticket_ref"]),
            officer_user_id=str(updated_assignment["officer_user_id"]),
            canonical_domain=str(updated_assignment["canonical_domain"]),
            generation=int(updated_assignment["generation"]),
            lease_token_sha256=str(updated_assignment["lease_token_sha256"]),
            lease_expires_at=_as_datetime(updated_assignment["lease_expires_at"]),
            status=AssignmentStatus(str(updated_assignment["status"])),
            assigned_at=_as_datetime(updated_assignment["assigned_at"]),
            activated_at=_as_datetime(updated_assignment["activated_at"]),
        )

    def requeue_expired_assignments(self, *, now: datetime) -> list[str]:
        from sqlalchemy import text

        with self.engine.begin() as connection:
            rows = connection.execute(text("""
                SELECT a.id, a.ticket_ref, a.officer_user_id, a.generation
                FROM support_assignment a
                LEFT JOIN officer_presence p ON p.officer_user_id = a.officer_user_id
                WHERE a.status IN ('leased','active')
                  AND (a.lease_expires_at <= :now OR p.lease_expires_at IS NULL OR p.lease_expires_at <= :now)
                FOR UPDATE OF a SKIP LOCKED
            """), {"now": now}).mappings().all()
            requeued: list[str] = []
            for row in rows:
                ticket_before = connection.execute(text("""
                    SELECT status, version FROM support_ticket
                    WHERE id = :ticket AND assignment_generation = :generation
                    FOR UPDATE
                """), {"ticket": row["ticket_ref"], "generation": row["generation"]}).mappings().first()
                changed = connection.execute(text("""
                    UPDATE support_ticket SET status = 'queued', assigned_officer_id = NULL,
                        version = version + 1, updated_at = :now
                    WHERE id = :ticket AND assignment_generation = :generation
                      AND status IN ('assigned','active','waiting_citizen','waiting_officer')
                    RETURNING id, version
                """), {"ticket": row["ticket_ref"], "generation": row["generation"], "now": now}).mappings().first()
                connection.execute(text("""
                    UPDATE support_assignment SET status = 'expired', released_at = :now,
                        release_reason = 'lease_expired' WHERE id = :id
                """), {"id": row["id"], "now": now})
                if changed:
                    changed_id = str(changed["id"])
                    changed_version = int(changed["version"])
                    requeued.append(changed_id)
                    connection.execute(text("""
                        UPDATE officer_presence SET active_count = GREATEST(0, active_count - 1),
                            presence_status = CASE WHEN lease_expires_at <= :now THEN 'offline' ELSE 'available' END,
                            version = version + 1 WHERE officer_user_id = :officer
                    """), {"officer": row["officer_user_id"], "now": now})
                    self._insert_state_event(
                        connection,
                        ticket_id=changed_id,
                        actor=SupportActor(user_id="support-allocator", role="system"),
                        from_status=TicketStatus(str(ticket_before["status"])),
                        to_status=TicketStatus.QUEUED,
                        reason_code="lease_expired_requeue",
                        version=changed_version,
                    )
        return requeued

    def record_admin_content_access(
        self,
        ticket_id: str,
        actor: SupportActor,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        from sqlalchemy import text

        checked_actor = actor.model_copy(update={"access_reason": reason})
        self.get_ticket(ticket_id, checked_actor, include_content=True)
        observed_at = now or utcnow()
        grant = {
            "id": uuid.uuid4().hex,
            "ticket": ticket_id,
            "admin": actor.user_id,
            "reason": reason.strip(),
            "granted": observed_at,
            "expires": observed_at + timedelta(minutes=15),
            "audit": uuid.uuid4().hex,
        }
        with self.engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO support_content_access
                    (id, ticket_ref, admin_user_id, reason, granted_at, expires_at, audit_event_id)
                VALUES (:id, :ticket, :admin, :reason, :granted, :expires, :audit)
            """), grant)
        return {
            "id": grant["id"],
            "ticket_id": ticket_id,
            "admin_user_id": actor.user_id,
            "granted_at": observed_at,
            "expires_at": grant["expires"],
            "audit_event_id": grant["audit"],
        }
