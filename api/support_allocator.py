"""Transaction-safe support assignment and presence lease orchestration."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from api.support_repository import (
    OfficerPresenceRecord,
    SupportActor,
    SupportAssignmentRecord,
    SupportRepository,
    utcnow,
)


DEFAULT_PRESENCE_LEASE_SECONDS = 45
DEFAULT_ASSIGNMENT_LEASE_SECONDS = 30


class AllocationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assignment: SupportAssignmentRecord
    lease_token: str


class SupportAllocator:
    def __init__(
        self,
        repository: SupportRepository | Any,
        *,
        presence_lease_seconds: int = DEFAULT_PRESENCE_LEASE_SECONDS,
        assignment_lease_seconds: int = DEFAULT_ASSIGNMENT_LEASE_SECONDS,
    ) -> None:
        self.repository = repository
        self.presence_lease_seconds = max(5, int(presence_lease_seconds))
        self.assignment_lease_seconds = max(5, int(assignment_lease_seconds))

    def heartbeat(
        self,
        actor: SupportActor,
        *,
        domains: Iterable[str],
        max_capacity: int = 3,
        now: datetime | None = None,
    ) -> OfficerPresenceRecord:
        observed_at = now or utcnow()
        return self.repository.set_presence(
            actor,
            domains=domains,
            max_capacity=max_capacity,
            now=observed_at,
            lease_seconds=self.presence_lease_seconds,
        )

    def claim_next(
        self,
        actor: SupportActor,
        *,
        now: datetime | None = None,
    ) -> AllocationResult | None:
        if actor.role != "officer":
            from api.support_repository import SupportAccessError

            raise SupportAccessError("officer_role_required")
        claimed = self.repository.claim_next_atomic(
            actor.user_id,
            now=now or utcnow(),
            lease_seconds=self.assignment_lease_seconds,
        )
        if claimed is None:
            return None
        assignment, token = claimed
        return AllocationResult(assignment=assignment, lease_token=token)

    def activate(
        self,
        allocation: AllocationResult,
        *,
        now: datetime | None = None,
    ) -> SupportAssignmentRecord:
        return self.repository.activate_assignment(
            allocation.assignment.id,
            allocation.lease_token,
            now=now or utcnow(),
        )

    def activate_by_id(
        self,
        assignment_id: str,
        lease_token: str,
        actor: SupportActor,
        *,
        now: datetime | None = None,
    ) -> SupportAssignmentRecord:
        if actor.role != "officer":
            from api.support_repository import SupportAccessError

            raise SupportAccessError("officer_role_required")
        return self.repository.activate_assignment(
            assignment_id,
            lease_token,
            now=now or utcnow(),
            expected_officer_user_id=actor.user_id,
        )

    def requeue_expired(self, *, now: datetime | None = None) -> list[str]:
        return self.repository.requeue_expired_assignments(now=now or utcnow())
