"""Pure domain contracts for Feature 017 procedure/form governance.

This module has no database or provider dependency.  Legal identity, workflow
transitions and attestation hashes are deterministic and can therefore be used
by PostgreSQL, JSON-shadow and isolated tests without changing their meaning.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FormWorkflowStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NEEDS_SUPPLEMENT = "needs_supplement"
    RESUBMITTED = "resubmitted"
    SOURCE_APPROVED = "source_approved"
    LEGAL_ENRICHMENT = "legal_enrichment"
    READY_FOR_ATTESTATION = "ready_for_attestation"
    ATTESTED = "attested"
    RELEASE_CANDIDATE = "release_candidate"
    RELEASED = "released"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"


class FormCoverageStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RELEASED = "released"
    VERIFIED_GAP = "verified_gap"
    NOT_APPLICABLE = "not_applicable"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    OWNER_DEFERRED = "owner_deferred"


class FormSyncStatus(StrEnum):
    PENDING = "pending"
    PROJECTED = "projected"
    FAILED = "failed"


class AssetKind(StrEnum):
    FILE = "file"
    EFORM = "eform"


class BindingRequirement(StrEnum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"


Role = Literal["citizen", "officer", "admin"]
Audience = Literal["citizen", "officer", "both"]


class ActorContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=200)
    role: Role
    domains: list[str] = Field(default_factory=list)
    organization_routing_mode: str = "legacy"
    managed_procedure_ids: list[str] = Field(default_factory=list)


class LegalProcedureDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str = Field(min_length=1, max_length=240)
    procedure_code: str | None = Field(default=None, max_length=240)
    name: str = Field(min_length=3, max_length=1000)
    domain: str = Field(min_length=1, max_length=160)
    # Organization ownership belongs to the procedure release. Forms and FAQ
    # project these values and never accept an independent department write.
    primary_organization_unit_id: str | None = Field(default=None, max_length=240)
    supporting_organization_unit_ids: list[str] = Field(default_factory=list)
    authority: str = Field(min_length=2, max_length=500)
    jurisdiction: str = Field(default="Hai Phong", max_length=240)
    applicant_description: str | None = Field(default=None, max_length=2000)
    official_source_url: str = Field(min_length=8, max_length=3000)
    official_source_checksum: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    legal_as_of: date
    coverage_status: FormCoverageStatus = FormCoverageStatus.UNRESOLVED
    coverage_reason: str | None = Field(default=None, max_length=2000)

    @field_validator("official_source_checksum")
    @classmethod
    def _checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.casefold().strip()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("FORM_CHECKSUM_INVALID")
        return value

    @field_validator("supporting_organization_unit_ids")
    @classmethod
    def _supporting_units(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value if str(item).strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("FORM_PROCEDURE_UNIT_DUPLICATE")
        return normalized


class LegalFormAssetDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form_id: str = Field(min_length=1, max_length=240)
    form_code: str | None = Field(default=None, max_length=240)
    canonical_name: str = Field(min_length=3, max_length=1000)
    asset_kind: AssetKind
    source_url: str = Field(min_length=8, max_length=3000)
    source_checksum: str
    source_classification: str = Field(default="official", max_length=120)
    issuing_instrument: str | None = Field(default=None, max_length=500)
    effective_from: date | None = None
    effective_to: date | None = None
    audiences: list[Audience] = Field(min_length=1)
    coverage_status: FormCoverageStatus = FormCoverageStatus.UNRESOLVED
    page_number: int | None = Field(default=None, ge=1)

    @field_validator("source_checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        value = value.casefold().strip()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("FORM_CHECKSUM_INVALID")
        return value


class ProcedureFormBindingDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str = Field(min_length=1, max_length=240)
    form_id: str | None = Field(default=None, max_length=240)
    requirement: BindingRequirement
    condition: str | None = Field(default=None, max_length=3000)
    audience: Audience
    display_order: int = Field(default=0, ge=0)
    legal_basis: str | None = Field(default=None, max_length=2000)
    coverage_status: FormCoverageStatus = FormCoverageStatus.UNRESOLVED

    @field_validator("condition")
    @classmethod
    def _conditional_requires_text(cls, value: str | None, info):
        requirement = info.data.get("requirement")
        if requirement == BindingRequirement.CONDITIONAL and not str(value or "").strip():
            raise ValueError("FORM_CONDITION_REQUIRED")
        return value


class FormQuestionAlias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str
    alias: str = Field(min_length=3, max_length=1000)
    alias_kind: Literal["exact", "natural", "exclude", "hard_negative"] = "natural"


class FormReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    procedure_id: str
    domain: str
    title: str = Field(min_length=3, max_length=1000)
    source_url: str = Field(min_length=8, max_length=3000)
    source_checksum: str | None = None
    asset_kind: AssetKind = AssetKind.FILE
    note: str = Field(default="", max_length=2000)
    page_number: int | None = Field(default=None, ge=1)

    @field_validator("source_checksum")
    @classmethod
    def _source_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.casefold().strip()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("FORM_CHECKSUM_INVALID")
        return value


class FormReviewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    officer_id: str
    domain: str
    procedure_id: str
    title: str
    status: FormWorkflowStatus
    revision: int = Field(ge=1)
    current_submission: FormReviewSubmission
    source_reviewed_by: str | None = None
    source_reviewed_at: datetime | None = None
    legal_metadata: dict[str, Any] = Field(default_factory=dict)
    attestation_fingerprint: str | None = None
    attested_by: str | None = None
    attested_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int = Field(default=1, ge=1)


class WorkflowEvent(BaseModel):
    event_id: str
    object_type: str
    object_id: str
    actor_id: str
    actor_role: Role
    action: str
    from_status: FormWorkflowStatus | None
    to_status: FormWorkflowStatus | None
    reason_code: str | None = None
    detail_hash: str
    occurred_at: datetime


class FormReleaseManifest(BaseModel):
    """Typed envelope; legal row details remain schema-validated dictionaries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["form-release-v1"] = "form-release-v1"
    release_id: str = Field(min_length=1, max_length=240)
    version: int = Field(ge=1)
    legal_as_of: date
    source_snapshot_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    previous_release_id: str | None = None
    procedures: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    bindings: list[dict[str, Any]]
    aliases: list[dict[str, Any]]
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    exclusions: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any]
    build: dict[str, Any]


_ADMIN = frozenset({"admin"})
_OFFICER = frozenset({"officer"})
_TRANSITIONS: dict[tuple[FormWorkflowStatus, FormWorkflowStatus], frozenset[str]] = {
    (FormWorkflowStatus.DRAFT, FormWorkflowStatus.SUBMITTED): _OFFICER,
    (FormWorkflowStatus.DRAFT, FormWorkflowStatus.WITHDRAWN): _OFFICER,
    (FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.NEEDS_SUPPLEMENT): _ADMIN,
    (FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.SOURCE_APPROVED): _ADMIN,
    (FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.REJECTED): _ADMIN,
    (FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.WITHDRAWN): _OFFICER,
    (FormWorkflowStatus.NEEDS_SUPPLEMENT, FormWorkflowStatus.RESUBMITTED): _OFFICER,
    (FormWorkflowStatus.NEEDS_SUPPLEMENT, FormWorkflowStatus.WITHDRAWN): _OFFICER,
    (FormWorkflowStatus.RESUBMITTED, FormWorkflowStatus.NEEDS_SUPPLEMENT): _ADMIN,
    (FormWorkflowStatus.RESUBMITTED, FormWorkflowStatus.SOURCE_APPROVED): _ADMIN,
    (FormWorkflowStatus.RESUBMITTED, FormWorkflowStatus.REJECTED): _ADMIN,
    (FormWorkflowStatus.SOURCE_APPROVED, FormWorkflowStatus.LEGAL_ENRICHMENT): _ADMIN,
    (FormWorkflowStatus.SOURCE_APPROVED, FormWorkflowStatus.REJECTED): _ADMIN,
    (FormWorkflowStatus.SOURCE_APPROVED, FormWorkflowStatus.SUPERSEDED): _ADMIN,
    (FormWorkflowStatus.LEGAL_ENRICHMENT, FormWorkflowStatus.READY_FOR_ATTESTATION): _ADMIN,
    (FormWorkflowStatus.LEGAL_ENRICHMENT, FormWorkflowStatus.NEEDS_SUPPLEMENT): _ADMIN,
    (FormWorkflowStatus.LEGAL_ENRICHMENT, FormWorkflowStatus.REJECTED): _ADMIN,
    (FormWorkflowStatus.READY_FOR_ATTESTATION, FormWorkflowStatus.ATTESTED): _ADMIN,
    (FormWorkflowStatus.READY_FOR_ATTESTATION, FormWorkflowStatus.LEGAL_ENRICHMENT): _ADMIN,
    (FormWorkflowStatus.READY_FOR_ATTESTATION, FormWorkflowStatus.REJECTED): _ADMIN,
    (FormWorkflowStatus.ATTESTED, FormWorkflowStatus.RELEASE_CANDIDATE): _ADMIN,
    # A blocked release candidate can be reopened for a checksum/source
    # correction. The service clears the attestation before allowing edits.
    (FormWorkflowStatus.RELEASE_CANDIDATE, FormWorkflowStatus.LEGAL_ENRICHMENT): _ADMIN,
    (FormWorkflowStatus.ATTESTED, FormWorkflowStatus.SUPERSEDED): _ADMIN,
    (FormWorkflowStatus.ATTESTED, FormWorkflowStatus.EXPIRED): _ADMIN,
    (FormWorkflowStatus.ATTESTED, FormWorkflowStatus.QUARANTINED): _ADMIN,
    (FormWorkflowStatus.RELEASE_CANDIDATE, FormWorkflowStatus.RELEASED): _ADMIN,
    (FormWorkflowStatus.RELEASE_CANDIDATE, FormWorkflowStatus.ATTESTED): _ADMIN,
    (FormWorkflowStatus.RELEASE_CANDIDATE, FormWorkflowStatus.QUARANTINED): _ADMIN,
    (FormWorkflowStatus.RELEASED, FormWorkflowStatus.SUPERSEDED): _ADMIN,
    (FormWorkflowStatus.RELEASED, FormWorkflowStatus.EXPIRED): _ADMIN,
    (FormWorkflowStatus.RELEASED, FormWorkflowStatus.QUARANTINED): _ADMIN,
}


def assert_transition(
    current: FormWorkflowStatus | str,
    target: FormWorkflowStatus | str,
    actor_role: str,
) -> None:
    current = FormWorkflowStatus(current)
    target = FormWorkflowStatus(target)
    roles = _TRANSITIONS.get((current, target))
    if roles is None:
        raise ValueError(f"FORM_TRANSITION_INVALID:{current.value}->{target.value}")
    if actor_role not in roles:
        raise ValueError(f"FORM_TRANSITION_FORBIDDEN:{actor_role}")


def canonical_payload(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_payload(value)).hexdigest()


def attestation_fingerprint(
    *,
    case_id: str,
    revision: int,
    procedure: LegalProcedureDraft,
    asset: LegalFormAssetDraft,
    bindings: list[ProcedureFormBindingDraft],
    aliases: list[str],
    reviewer: ActorContext,
) -> str:
    payload = {
        "schema": "form-attestation-v1",
        "case_id": case_id,
        "revision": revision,
        "procedure": procedure.model_dump(mode="json"),
        "asset": asset.model_dump(mode="json"),
        "bindings": sorted(
            (item.model_dump(mode="json") for item in bindings),
            key=lambda item: (
                item["procedure_id"],
                item.get("form_id") or "",
                item["audience"],
                item["requirement"],
                item.get("condition") or "",
            ),
        ),
        "aliases": sorted({str(item).strip() for item in aliases if str(item).strip()}),
        "reviewer_id": reviewer.user_id,
        "reviewer_role": reviewer.role,
    }
    return canonical_sha256(payload)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
