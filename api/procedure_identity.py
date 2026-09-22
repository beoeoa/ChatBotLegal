"""One authoritative procedure/form identity decision for the Ask runtime.

Feature 017 is canonical whenever its configured release is available.  The
legacy catalog is consulted only when Feature 017 is unavailable, never when
the canonical release returns a clarification, source gap or rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any, Callable, Literal, Mapping


IdentityStatus = Literal[
    "resolved",
    "clarification_required",
    "source_gap",
    "unsupported",
    "unavailable",
]
ProcedureIdentityStatus = Literal["confirmed", "ambiguous", "unsupported"]
ProcedureFormStatus = Literal["resolved", "source_gap", "not_requested"]


@dataclass(frozen=True)
class ProcedureIdentityDecision:
    status: IdentityStatus
    source: Literal["feature017", "legacy_fallback", "none"]
    procedure_id: str | None = None
    procedure: Mapping[str, Any] | None = None
    recommended_forms: tuple[dict[str, Any], ...] = ()
    rejected_forms: tuple[dict[str, Any], ...] = ()
    clarifying_questions: tuple[str, ...] = ()
    forms_unavailable: bool = True
    reason: str | None = None
    confirmed: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    # Public V1 contract.  ``status`` above remains for legacy callers and
    # records the resolver's transport outcome; these two fields deliberately
    # separate identity certainty from form availability.
    identity_status: ProcedureIdentityStatus | None = None
    form_status: ProcedureFormStatus | None = None

    def __post_init__(self) -> None:
        if self.identity_status is None:
            object.__setattr__(
                self,
                "identity_status",
                "confirmed"
                if self.confirmed
                else "ambiguous"
                if self.status == "clarification_required"
                else "unsupported",
            )
        if self.form_status is None:
            object.__setattr__(
                self,
                "form_status",
                "resolved"
                if self.recommended_forms
                else "source_gap"
                if self.status == "source_gap"
                else "not_requested",
            )

    def as_form_resolution(self) -> dict[str, Any]:
        projection = dict(self.raw)
        identity_status = self.identity_status or "unsupported"
        form_status = self.form_status or "not_requested"
        projection.update(
            {
                "status": self.status,
                "procedure_id": self.procedure_id,
                "procedure": dict(self.procedure or {}),
                "recommended_forms": [dict(item) for item in self.recommended_forms],
                "rejected_forms": [dict(item) for item in self.rejected_forms],
                "clarifying_questions": list(self.clarifying_questions),
                "forms_unavailable": self.forms_unavailable,
                "reason": self.reason,
                "identity_source": self.source,
                "identity_status": identity_status,
                "form_status": form_status,
                "identity_confirmation": {
                    "confirmed": self.confirmed,
                    "procedure_id": self.procedure_id,
                    "source": self.source,
                },
            }
        )
        return projection


def _decision_from_feature017(result: Mapping[str, Any]) -> ProcedureIdentityDecision:
    raw_status = str(result.get("status") or "unsupported")
    status: IdentityStatus = (
        raw_status
        if raw_status in {
            "resolved", "clarification_required", "source_gap", "unsupported"
        }
        else "unsupported"
    )  # type: ignore[assignment]
    procedure = result.get("procedure")
    procedure_id = str(
        result.get("procedure_id")
        or (procedure or {}).get("procedure_id")
        or ""
    ).strip() or None
    confirmation = result.get("identity_confirmation") or {}
    confirmed = bool(
        procedure_id
        and status in {"resolved", "source_gap"}
        and confirmation.get("confirmed", result.get("confirmed", True)) is True
    )
    return ProcedureIdentityDecision(
        status=status,
        source="feature017",
        procedure_id=procedure_id,
        procedure=dict(procedure) if isinstance(procedure, Mapping) else None,
        recommended_forms=tuple(
            dict(item) for item in result.get("recommended_forms") or []
            if isinstance(item, Mapping)
        ),
        rejected_forms=tuple(
            dict(item) for item in result.get("rejected_forms") or []
            if isinstance(item, Mapping)
        ),
        clarifying_questions=tuple(
            str(item).strip() for item in result.get("clarifying_questions") or []
            if str(item).strip()
        ),
        forms_unavailable=bool(result.get("forms_unavailable", True)),
        reason=str(result.get("reason") or "") or None,
        confirmed=confirmed,
        raw=dict(result),
    )


def resolve_procedure_identity(
    *,
    question: str,
    audience: str,
    legal_as_of: date,
    legacy_procedure_id: str | None = None,
    legacy_identity_resolver: Callable[[str], str | None] | None = None,
    feature017_resolver: Callable[..., Mapping[str, Any] | None] | None = None,
    legacy_resolver: Callable[..., Mapping[str, Any]] | None = None,
) -> ProcedureIdentityDecision:
    """Resolve once, preferring Feature 017 without cross-source override."""

    if feature017_resolver is None:
        from api.form_router_v3 import resolve_from_configured_release

        feature017_resolver = resolve_from_configured_release
    try:
        canonical = feature017_resolver(
            question=question,
            audience=audience,
            legal_as_of=legal_as_of,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        canonical = None
    if canonical is not None and canonical.get("router_mode") == "active":
        return _decision_from_feature017(canonical)

    if not legacy_procedure_id and legacy_identity_resolver is not None:
        legacy_procedure_id = legacy_identity_resolver(question)
    if not legacy_procedure_id:
        return ProcedureIdentityDecision(
            status="unavailable",
            source="none",
            reason="FEATURE017_UNAVAILABLE",
        )
    if legacy_resolver is None:
        from api.routers.search import _get_canonical_form_catalog

        legacy_resolver = _get_canonical_form_catalog().resolve_forms
    legacy = legacy_resolver(
        question,
        role=audience,
        as_of=legal_as_of,
        procedure_ids=[legacy_procedure_id],
        limit=12,
    )
    forms = tuple(
        dict(item) for item in legacy.get("recommended_forms") or []
        if isinstance(item, Mapping)
    )
    # A numeric official procedure code already confirmed by the deterministic
    # router remains an identity confirmation even when the form release has
    # no eligible asset.  This is the key separation between procedure
    # evidence and form availability; legacy slugs are not upgraded merely
    # because a caller supplied them.
    explicit_official_identity = bool(
        re.fullmatch(r"\d+\.\d+", str(legacy_procedure_id or ""))
    )
    return ProcedureIdentityDecision(
        status="resolved" if forms else "source_gap",
        source="legacy_fallback",
        procedure_id=legacy_procedure_id,
        recommended_forms=forms,
        rejected_forms=tuple(
            dict(item) for item in legacy.get("rejected_forms") or []
            if isinstance(item, Mapping)
        ),
        forms_unavailable=not bool(forms),
        reason=None if forms else "LEGACY_FORM_SOURCE_GAP",
        confirmed=bool(forms) or explicit_official_identity,
        raw=dict(legacy),
    )
