"""Read-only legal change impact discovery for Feature 018.

The engine creates review candidates only. It never edits released procedures,
forms, FAQs, Golden cases, citations, caches, or vector records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable

from api.legal_lifecycle_service import LegalChangeEvent


DEPENDENT_TYPES = {
    "procedure",
    "form",
    "faq",
    "golden",
    "cache",
    "citation",
    "index_record",
}


class LegalImpactError(RuntimeError):
    pass


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegalDocumentRelationshipCandidate:
    id: str
    from_document_id: str
    to_document_id: str
    relationship_type: str
    effective_from: date
    source_url: str
    confirmation_state: str = "candidate"

    def validate(self) -> None:
        if self.from_document_id == self.to_document_id:
            raise LegalImpactError("relationship_self_reference")
        if not self.source_url.strip():
            raise LegalImpactError("relationship_source_required")
        if self.confirmation_state not in {
            "candidate",
            "confirmed",
            "rejected",
            "superseded",
        }:
            raise LegalImpactError("relationship_confirmation_state_invalid")


@dataclass(frozen=True)
class LegalDependency:
    dependent_type: str
    dependent_id: str
    document_id: str
    provision_identities: tuple[str, ...] = ()
    evidence: dict[str, Any] | None = None

    def validate(self) -> None:
        if self.dependent_type not in DEPENDENT_TYPES:
            raise LegalImpactError("impact_dependent_type_invalid")
        if not self.dependent_id or not self.document_id:
            raise LegalImpactError("impact_dependency_identity_required")


@dataclass(frozen=True)
class LegalImpactCase:
    id: str
    change_event_id: str
    dependent_type: str
    dependent_id: str
    detected_reason: str
    old_evidence: dict[str, Any]
    new_evidence: dict[str, Any]
    diff_candidate: dict[str, Any]
    evidence_sha256: str
    status: str = "needs_review"
    reviewer_user_id: str | None = None
    reviewed_at: datetime | None = None
    decision_reason: str | None = None


class LegalImpactEngine:
    def scan(
        self,
        event: LegalChangeEvent,
        dependencies: Iterable[LegalDependency],
        *,
        relationship: LegalDocumentRelationshipCandidate | None = None,
        old_content_sha256: str | None = None,
        new_content_sha256: str | None = None,
    ) -> list[LegalImpactCase]:
        if (
            event.status != "confirmed"
            or not event.source_url
            or not event.effective_from
            or not event.reviewer_user_id
        ):
            return []
        if relationship is not None:
            relationship.validate()
            if relationship.confirmation_state != "confirmed":
                return []
            if relationship.from_document_id != event.document_id:
                raise LegalImpactError("relationship_event_document_mismatch")

        event_type = event.event_type.casefold().strip()
        replacement_differs = (
            event_type in {"replace", "replaced"}
            and bool(old_content_sha256 and new_content_sha256)
            and old_content_sha256 != new_content_sha256
        )
        cases: list[LegalImpactCase] = []
        for dependency in dependencies:
            dependency.validate()
            if dependency.document_id != event.document_id:
                continue
            if event.scope == "provisions" and not set(event.provisions).intersection(
                dependency.provision_identities
            ):
                continue

            reason = (
                "replacement_content_differs"
                if replacement_differs
                else f"confirmed_{event_type}_may_affect_dependency"
            )
            old_evidence = {
                "document_id": dependency.document_id,
                "provision_identities": list(dependency.provision_identities),
                "dependency_evidence_sha256": _fingerprint(dependency.evidence or {}),
                "content_sha256": old_content_sha256,
            }
            new_evidence = {
                "event_id": event.id,
                "event_type": event_type,
                "source_url": event.source_url,
                "effective_from": event.effective_from.isoformat(),
                "replacement_document_id": relationship.to_document_id
                if relationship
                else None,
                "content_sha256": new_content_sha256,
            }
            diff_candidate = {
                "requires_human_review": True,
                "content_equivalence_assumed": False,
                "scope": event.scope,
                "provisions": list(event.provisions),
            }
            evidence_sha256 = _fingerprint(
                {
                    "old": old_evidence,
                    "new": new_evidence,
                    "diff": diff_candidate,
                }
            )
            case_id = "impact-" + _fingerprint(
                [event.id, dependency.dependent_type, dependency.dependent_id, evidence_sha256]
            )[:24]
            cases.append(
                LegalImpactCase(
                    id=case_id,
                    change_event_id=event.id,
                    dependent_type=dependency.dependent_type,
                    dependent_id=dependency.dependent_id,
                    detected_reason=reason,
                    old_evidence=old_evidence,
                    new_evidence=new_evidence,
                    diff_candidate=diff_candidate,
                    evidence_sha256=evidence_sha256,
                )
            )
        return sorted(cases, key=lambda item: (item.dependent_type, item.dependent_id))

    def decide(
        self,
        case: LegalImpactCase,
        *,
        decision: str,
        reviewer_user_id: str,
        reason: str,
        reviewed_at: datetime | None = None,
    ) -> LegalImpactCase:
        if case.status != "needs_review":
            raise LegalImpactError("impact_case_already_decided")
        if decision not in {"confirmed", "rejected"}:
            raise LegalImpactError("impact_decision_invalid")
        if not reviewer_user_id.strip():
            raise LegalImpactError("impact_reviewer_required")
        if len(reason.strip()) < 10:
            raise LegalImpactError("impact_decision_reason_required")
        return replace(
            case,
            status=decision,
            reviewer_user_id=reviewer_user_id.strip(),
            reviewed_at=reviewed_at or datetime.now(timezone.utc),
            decision_reason=reason.strip(),
        )
