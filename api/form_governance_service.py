"""Application service for Feature 017 form workflow and immutable releases."""

from __future__ import annotations

import json
import hashlib
import uuid
from datetime import date
from typing import Any

from api.form_governance_models import (
    ActorContext, FormReviewCase, FormReviewSubmission, FormWorkflowStatus,
    LegalFormAssetDraft, LegalProcedureDraft, ProcedureFormBindingDraft,
    WorkflowEvent, assert_transition, attestation_fingerprint, canonical_sha256,
    utc_now,
)
from api.form_governance_repository import FormGovernanceRepository, configured_repository
from api.form_governance_release_validator import (
    HttpFormSourceVerifier,
    SourceVerifier,
    validate_release_manifest,
)
from api.form_procedure_scope import (
    ProcedureScopeLookup,
    configured_procedure_scope_lookup,
)
from api.official_source_adapters import is_allowlisted_official_url
from api.legal_domains import canonicalize_legal_domain, legal_domain_values


DOMAIN_ALIASES: dict[str, frozenset[str]] = {
    "ho_tich_chung_thuc": frozenset({"ho_tich_chung_thuc", "ho_tich", "chung_thuc"}),
    "dat_dai_xay_dung": frozenset({"dat_dai_xay_dung", "dat_dai", "xay_dung"}),
    "an_sinh_y_te_giao_duc": frozenset({"an_sinh_y_te_giao_duc"}),
    "cu_tru_an_ninh": frozenset({"cu_tru_an_ninh", "cu_tru", "an_ninh"}),
    "khieu_nai_to_cao_xu_phat": frozenset(
        {"khieu_nai_to_cao_xu_phat", "khieu_nai", "to_cao", "xu_phat"}
    ),
}


def _json_safe(value: Any) -> Any:
    """Normalize database-native dates/timestamps before release hashing."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class FormGovernanceService:
    def __init__(
        self,
        repository: FormGovernanceRepository,
        *,
        source_verifier: SourceVerifier | None = None,
        procedure_scope_lookup: ProcedureScopeLookup | None = None,
    ) -> None:
        self.repository = repository
        self.source_verifier = source_verifier
        self.procedure_scope_lookup = procedure_scope_lookup

    @staticmethod
    def _require(actor: ActorContext, role: str) -> None:
        if actor.role != role:
            raise PermissionError("FORM_CASE_FORBIDDEN")

    @staticmethod
    def _domain_allowed(actor: ActorContext, domain: str) -> bool:
        if actor.role == "admin": return True
        allowed = set(legal_domain_values(domain)) or {domain}
        return any(str(item).casefold() in allowed for item in actor.domains)

    @staticmethod
    def _canonical_domain(domain: str) -> str:
        return canonicalize_legal_domain(domain) or str(domain or "").casefold()

    def _require_procedure_scope(self, procedure_id: str, submitted_domain: str) -> None:
        if self.procedure_scope_lookup is None:
            raise RuntimeError("FORM_PROCEDURE_SCOPE_UNAVAILABLE")
        expected_domain = self.procedure_scope_lookup(procedure_id)
        if not expected_domain:
            raise PermissionError("FORM_PROCEDURE_OUT_OF_SCOPE")
        if self._canonical_domain(expected_domain) != self._canonical_domain(submitted_domain):
            raise PermissionError("FORM_PROCEDURE_DOMAIN_MISMATCH")

    def _event(self, case: FormReviewCase, actor: ActorContext, action: str, before: FormWorkflowStatus | None, reason: str | None = None) -> WorkflowEvent:
        payload = {"action": action, "from": before, "to": case.status, "reason": reason, "version": case.version}
        event = WorkflowEvent(
            event_id=f"evt-{uuid.uuid4().hex}", object_type="form_review_case", object_id=case.case_id,
            actor_id=actor.user_id, actor_role=actor.role, action=action, from_status=before,
            to_status=case.status, reason_code=reason, detail_hash=canonical_sha256(payload), occurred_at=utc_now(),
        )
        self.repository.append_event(event)
        return event

    def submit(self, actor: ActorContext, submission: FormReviewSubmission) -> FormReviewCase:
        if actor.role not in {"officer", "admin"}:
            raise PermissionError("FORM_CASE_FORBIDDEN")
        if not self._domain_allowed(actor, submission.domain): raise PermissionError("FORM_DOMAIN_FORBIDDEN")
        self._require_procedure_scope(submission.procedure_id, submission.domain)
        now = utc_now()
        case = FormReviewCase(
            case_id=f"frc-{uuid.uuid4().hex}", officer_id=actor.user_id, domain=submission.domain,
            procedure_id=submission.procedure_id, title=submission.title, status=FormWorkflowStatus.SUBMITTED,
            revision=1, current_submission=submission, created_at=now, updated_at=now,
        )
        case = self.repository.create_case(case); self._event(case, actor, "submit", FormWorkflowStatus.DRAFT)
        return case

    def approve_source(self, actor: ActorContext, case_id: str, source_checksum: str) -> FormReviewCase:
        """Verify source and persist a checksum-bound revision in one transition."""
        self._require(actor, "admin")
        case = self.get_case(actor, case_id)
        before = case.status
        assert_transition(before, FormWorkflowStatus.SOURCE_APPROVED, actor.role)
        if not is_allowlisted_official_url(case.current_submission.source_url):
            raise ValueError("FORM_SOURCE_REQUIRED")
        checksum = str(source_checksum or "").casefold().strip()
        if len(checksum) != 64 or any(ch not in "0123456789abcdef" for ch in checksum):
            raise ValueError("FORM_CHECKSUM_INVALID")
        now = utc_now()
        submission = case.current_submission.model_copy(update={"source_checksum": checksum})
        saved = self.repository.save_case(
            case.model_copy(update={
                "status": FormWorkflowStatus.SOURCE_APPROVED,
                "revision": case.revision + 1,
                "current_submission": submission,
                "source_reviewed_by": actor.user_id,
                "source_reviewed_at": now,
                "updated_at": now,
            }),
            expected_version=case.version,
        )
        event = self._event(saved, actor, "verify_source", before)
        try:
            self.repository.enqueue_notification({
                "workflow_event_id": event.event_id,
                "recipient_id": case.officer_id,
                "type": "form_source_approved",
                "case_id": case.case_id,
                "status": FormWorkflowStatus.SOURCE_APPROVED.value,
            })
        except Exception:
            pass
        return saved

    def ingest_legacy_candidate(
        self,
        actor: ActorContext,
        legacy_candidate_id: str,
        submission: FormReviewSubmission,
    ) -> FormReviewCase:
        """Idempotently move a legacy queue row into the canonical workflow."""
        self._require(actor, "admin")
        self._require_procedure_scope(submission.procedure_id, submission.domain)
        digest = hashlib.sha256(str(legacy_candidate_id).encode("utf-8")).hexdigest()[:32]
        case_id = f"frc-legacy-{digest}"
        existing = self.repository.get_case(case_id)
        if existing:
            return existing
        now = utc_now()
        case = FormReviewCase(
            case_id=case_id,
            officer_id=actor.user_id,
            domain=submission.domain,
            procedure_id=submission.procedure_id,
            title=submission.title,
            status=FormWorkflowStatus.SUBMITTED,
            revision=1,
            current_submission=submission,
            created_at=now,
            updated_at=now,
        )
        case = self.repository.create_case(case)
        self._event(case, actor, "legacy_intake_upsert", FormWorkflowStatus.DRAFT)
        return case

    def get_case(self, actor: ActorContext, case_id: str) -> FormReviewCase:
        case = self.repository.get_case(case_id)
        if not case: raise LookupError("FORM_CASE_NOT_FOUND")
        if actor.role == "citizen" or (actor.role == "officer" and (case.officer_id != actor.user_id or not self._domain_allowed(actor, case.domain))):
            raise PermissionError("FORM_CASE_FORBIDDEN")
        return case

    def list_cases(self, actor: ActorContext, *, status: str | None = None, domain: str | None = None) -> list[FormReviewCase]:
        if actor.role == "citizen": raise PermissionError("FORM_CASE_FORBIDDEN")
        result = self.repository.list_cases()
        if actor.role == "officer": result = [x for x in result if x.officer_id == actor.user_id and self._domain_allowed(actor, x.domain)]
        if status: result = [x for x in result if x.status.value == status]
        if domain: result = [x for x in result if x.domain == domain]
        return result

    def transition(self, actor: ActorContext, case_id: str, target: FormWorkflowStatus, *, reason: str | None = None) -> FormReviewCase:
        case = self.get_case(actor, case_id); before = case.status
        assert_transition(before, target, actor.role)
        now = utc_now()
        updates: dict[str, Any] = {"status": target, "updated_at": now}
        if target == FormWorkflowStatus.SOURCE_APPROVED:
            if not is_allowlisted_official_url(case.current_submission.source_url):
                raise ValueError("FORM_SOURCE_REQUIRED")
            if not case.current_submission.source_checksum:
                raise ValueError("FORM_CHECKSUM_REQUIRED")
            updates.update(source_reviewed_by=actor.user_id, source_reviewed_at=now)
        saved = self.repository.save_case(
            case.model_copy(update=updates), expected_version=case.version
        )
        event = self._event(saved, actor, f"transition:{target.value}", before, reason)
        if target in {
            FormWorkflowStatus.NEEDS_SUPPLEMENT,
            FormWorkflowStatus.SOURCE_APPROVED,
            FormWorkflowStatus.REJECTED,
            FormWorkflowStatus.WITHDRAWN,
        }:
            try:
                self.repository.enqueue_notification({
                    "workflow_event_id": event.event_id,
                    "recipient_id": case.officer_id,
                    "type": f"form_{target.value}",
                    "case_id": case.case_id,
                    "status": target.value,
                    "reason": reason,
                })
            except Exception:
                # Notification is a projection, never a second legal source of
                # truth. The canonical transition remains committed and can be
                # reconciled from workflow events later.
                pass
        return saved

    def supplement(self, actor: ActorContext, case_id: str, submission: FormReviewSubmission) -> FormReviewCase:
        case = self.get_case(actor, case_id)
        self._require(actor, "officer")
        if submission.domain != case.domain or submission.procedure_id != case.procedure_id: raise PermissionError("FORM_DOMAIN_FORBIDDEN")
        assert_transition(case.status, FormWorkflowStatus.RESUBMITTED, actor.role)
        saved = self.repository.save_case(case.model_copy(update={"status": FormWorkflowStatus.RESUBMITTED, "revision": case.revision + 1, "current_submission": submission, "updated_at": utc_now()}), expected_version=case.version)
        self._event(saved, actor, "supplement", case.status)
        return saved

    def enrich(self, actor: ActorContext, case_id: str, metadata: dict[str, Any]) -> FormReviewCase:
        self._require(actor, "admin"); case = self.get_case(actor, case_id)
        if case.status == FormWorkflowStatus.SOURCE_APPROVED:
            assert_transition(case.status, FormWorkflowStatus.LEGAL_ENRICHMENT, actor.role)
        elif case.status != FormWorkflowStatus.LEGAL_ENRICHMENT:
            raise ValueError("FORM_TRANSITION_INVALID")
        # Validation happens now, not during attestation.
        procedure = LegalProcedureDraft.model_validate(metadata["procedure"])
        asset = LegalFormAssetDraft.model_validate(metadata["asset"])
        bindings = [ProcedureFormBindingDraft.model_validate(x) for x in metadata["bindings"]]
        if procedure.procedure_id != case.procedure_id or procedure.domain != case.domain:
            raise ValueError("FORM_LEGAL_IDENTITY_MISMATCH")
        if any(item.procedure_id != case.procedure_id for item in bindings):
            raise ValueError("FORM_BINDING_PROCEDURE_MISMATCH")
        if any(item.form_id != asset.form_id for item in bindings if item.form_id):
            raise ValueError("FORM_BINDING_ASSET_MISMATCH")
        if asset.source_url != case.current_submission.source_url:
            raise ValueError("FORM_SOURCE_CHANGED_AFTER_APPROVAL")
        if asset.source_checksum != case.current_submission.source_checksum:
            raise ValueError("FORM_CHECKSUM_MISMATCH")
        aliases = [str(item).strip() for item in metadata.get("aliases") or [] if str(item).strip()]
        if len(aliases) != len(set(aliases)):
            raise ValueError("FORM_ALIAS_DUPLICATE")
        metadata = {
            "procedure": procedure.model_dump(mode="json"),
            "asset": asset.model_dump(mode="json"),
            "bindings": [item.model_dump(mode="json") for item in bindings],
            "aliases": aliases,
        }
        updated = case.model_copy(update={"status": FormWorkflowStatus.LEGAL_ENRICHMENT, "legal_metadata": metadata, "updated_at": utc_now()})
        saved = self.repository.save_case(updated, expected_version=case.version); self._event(saved, actor, "legal_enrichment", case.status)
        return saved

    def ready_for_attestation(self, actor: ActorContext, case_id: str) -> FormReviewCase:
        case = self.get_case(actor, case_id)
        if not (case.legal_metadata.get("aliases") or []):
            raise ValueError("FORM_QUESTION_ALIAS_REQUIRED")
        return self.transition(actor, case_id, FormWorkflowStatus.READY_FOR_ATTESTATION)

    def attestation_preview(self, actor: ActorContext, case_id: str) -> dict[str, Any]:
        self._require(actor, "admin"); case = self.get_case(actor, case_id)
        if case.status != FormWorkflowStatus.READY_FOR_ATTESTATION: raise ValueError("FORM_TRANSITION_INVALID")
        meta = case.legal_metadata
        procedure = LegalProcedureDraft.model_validate(meta["procedure"]); asset = LegalFormAssetDraft.model_validate(meta["asset"])
        bindings = [ProcedureFormBindingDraft.model_validate(x) for x in meta["bindings"]]
        fingerprint = attestation_fingerprint(case_id=case.case_id, revision=case.revision, procedure=procedure, asset=asset, bindings=bindings, aliases=meta.get("aliases") or [], reviewer=actor)
        return {"case_id": case.case_id, "revision": case.revision, "fingerprint": fingerprint, "procedure": procedure.model_dump(mode="json"), "asset": asset.model_dump(mode="json"), "bindings": [x.model_dump(mode="json") for x in bindings], "aliases": meta.get("aliases") or []}

    def attest(self, actor: ActorContext, case_id: str, fingerprint: str) -> FormReviewCase:
        preview = self.attestation_preview(actor, case_id)
        if fingerprint != preview["fingerprint"]: raise ValueError("FORM_ATTESTATION_STALE")
        case = self.get_case(actor, case_id); before = case.status
        assert_transition(before, FormWorkflowStatus.ATTESTED, actor.role)
        now = utc_now()
        saved = self.repository.save_case(case.model_copy(update={
            "status": FormWorkflowStatus.ATTESTED,
            "attestation_fingerprint": fingerprint,
            "attested_by": actor.user_id,
            "attested_at": now,
            "updated_at": now,
        }), expected_version=case.version)
        self._event(saved, actor, "attest", before); return saved

    def build_release(self, actor: ActorContext, case_ids: list[str], *, legal_as_of: date, source_snapshot_sha256: str | None = None) -> dict[str, Any]:
        self._require(actor, "admin")
        unique_case_ids = list(dict.fromkeys(case_ids))
        cases = [self.get_case(actor, value) for value in unique_case_ids]
        if source_snapshot_sha256 is None:
            source_snapshot_sha256 = canonical_sha256([
                {
                    "case_id": item.case_id,
                    "revision": item.revision,
                    "source_url": item.current_submission.source_url,
                    "source_checksum": item.current_submission.source_checksum,
                    "attestation_fingerprint": item.attestation_fingerprint,
                }
                for item in sorted(cases, key=lambda value: value.case_id)
            ])
        if len(source_snapshot_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in source_snapshot_sha256.casefold()
        ):
            raise ValueError("FORM_SOURCE_SNAPSHOT_INVALID")
        if not cases or any(
            item.status != FormWorkflowStatus.ATTESTED
            or not item.attestation_fingerprint
            or not item.attested_by
            for item in cases
        ):
            raise ValueError("FORM_RELEASE_ATTESTATION_REQUIRED")

        previous = self.repository.active_release()
        previous_manifest = (previous or {}).get("manifest") or {}
        procedure_map = {
            str(item["procedure_id"]): dict(item)
            for item in previous_manifest.get("procedures") or []
        }
        asset_map = {
            str(item["form_id"]): dict(item)
            for item in previous_manifest.get("assets") or []
        }
        selected_procedures = {item.procedure_id for item in cases}
        bindings = [
            dict(item) for item in previous_manifest.get("bindings") or []
            if str(item.get("procedure_id") or "") not in selected_procedures
        ]
        aliases = [
            dict(item) for item in previous_manifest.get("aliases") or []
            if str(item.get("procedure_id") or "") not in selected_procedures
        ]
        gap_map = {
            (str(item.get("target_type") or ""), str(item.get("target_id") or "")): _json_safe(item)
            for item in previous_manifest.get("gaps") or []
        }
        for gap in self.repository.list_verified_gaps():
            normalized_gap = _json_safe(gap)
            gap_map[(str(gap.get("target_type") or ""), str(gap.get("target_id") or ""))] = normalized_gap
            metadata = dict(normalized_gap.get("metadata") or {})
            if gap.get("target_type") == "procedure" and metadata.get("procedure"):
                gap_procedure = dict(metadata["procedure"])
                gap_procedure["coverage_status"] = "verified_gap"
                procedure_map[str(gap_procedure["procedure_id"])] = gap_procedure
                aliases.extend({
                    "procedure_id": gap_procedure["procedure_id"],
                    "alias": alias,
                    "alias_kind": "natural",
                } for alias in metadata.get("aliases") or [])
        for case in cases:
            meta = case.legal_metadata
            procedure_map[case.procedure_id] = {
                **meta["procedure"],
                "coverage_status": "released",
            }
            asset_map[str(meta["asset"]["form_id"])] = {
                **meta["asset"],
                "coverage_status": "released",
            }
            for item in meta["bindings"]:
                binding_identity = {
                    "procedure_id": item.get("procedure_id"),
                    "form_id": item.get("form_id"),
                    "requirement": item.get("requirement"),
                    "condition": item.get("condition"),
                    "audience": item.get("audience"),
                }
                bindings.append({
                    **item,
                    "binding_id": item.get("binding_id")
                    or f"binding-{canonical_sha256(binding_identity)[:24]}",
                    "coverage_status": "released",
                })
            aliases.extend({
                "procedure_id": case.procedure_id,
                "alias": alias,
                "alias_kind": "natural",
            } for alias in meta.get("aliases") or [])

        procedures = sorted(procedure_map.values(), key=lambda item: item["procedure_id"])
        bindings = sorted(
            {str(item["binding_id"]): item for item in bindings}.values(),
            key=lambda item: (
                item["procedure_id"],
                int(item.get("display_order") or 0),
                item.get("form_id") or "",
                item["audience"],
            ),
        )
        referenced_assets = {
            str(item.get("form_id") or "") for item in bindings if item.get("form_id")
        }
        assets = sorted(
            (item for key, item in asset_map.items() if key in referenced_assets),
            key=lambda item: item["form_id"],
        )
        aliases = sorted(
            {
                (str(item["procedure_id"]), str(item["alias"]), str(item["alias_kind"])): item
                for item in aliases
            }.values(),
            key=lambda item: (item["procedure_id"], item["alias_kind"], item["alias"]),
        )
        gaps = sorted(
            gap_map.values(),
            key=lambda item: (str(item.get("target_type") or ""), str(item.get("target_id") or "")),
        )
        campaign_coverage = self.coverage(actor)
        gap_counts = {
            target: sum(item.get("target_type") == target for item in gaps)
            for target in ("procedure", "identity", "binding")
        }
        release_coverage = {
            **campaign_coverage,
            "procedure_decided": len(procedures),
            "identity_decided": len(assets) + gap_counts["identity"],
            "binding_decided": len(bindings) + gap_counts["binding"],
        }
        release_coverage["complete"] = (
            release_coverage["procedure_total"] == release_coverage["procedure_decided"]
            and release_coverage["identity_total"] == release_coverage["identity_decided"]
            and release_coverage["binding_total"] == release_coverage["binding_decided"]
        )
        release_id = f"forms-{legal_as_of.isoformat()}-{uuid.uuid4().hex[:12]}"
        manifest = {
            "schema_version": "form-release-v1",
            "release_id": release_id,
            "version": int((previous or {}).get("version", 0)) + 1,
            "legal_as_of": legal_as_of.isoformat(),
            "source_snapshot_sha256": source_snapshot_sha256.casefold(),
            "previous_release_id": (previous or {}).get("release_id"),
            "procedures": procedures,
            "assets": assets,
            "bindings": bindings,
            "aliases": aliases,
            "gaps": gaps,
            "coverage": release_coverage,
            "build": {
                "pipeline_version": "feature017-v1",
                "case_ids": unique_case_ids,
                "previous_manifest_sha256": (previous or {}).get("manifest_sha256"),
            },
        }
        release = {**manifest, "manifest": manifest, "manifest_sha256": canonical_sha256(manifest), "status": "candidate", "created_by": actor.user_id}
        saved_release = self.repository.save_release(release)
        for case in cases:
            self.transition(actor, case.case_id, FormWorkflowStatus.RELEASE_CANDIDATE)
        return saved_release

    def validate_release(self, actor: ActorContext, release_id: str) -> dict[str, Any]:
        self._require(actor, "admin"); release = self.repository.get_release(release_id)
        if not release: raise LookupError("FORM_RELEASE_NOT_FOUND")
        release["gate_report"] = validate_release_manifest(
            release["manifest"],
            expected_manifest_sha256=str(release.get("manifest_sha256") or ""),
            source_verifier=self.source_verifier,
        )
        release["status"] = "validated" if release["gate_report"]["passed"] else "blocked"
        return self.repository.update_release(release)

    def activate_release(self, actor: ActorContext, release_id: str) -> dict[str, Any]:
        self._require(actor, "admin")
        release = self.repository.get_release(release_id)
        if not release:
            raise LookupError("FORM_RELEASE_NOT_FOUND")
        case_ids = list((release.get("manifest") or {}).get("build", {}).get("case_ids") or [])
        before_cases = {
            case_id: self.get_case(actor, case_id) for case_id in case_ids
        }
        pointer = self.repository.set_active_release(release_id, actor_id=actor.user_id)
        for case_id, before_case in before_cases.items():
            released = self.get_case(actor, case_id)
            event = self._event(
                released,
                actor,
                "release",
                before_case.status,
                release_id,
            )
            try:
                self.repository.enqueue_notification({
                    "workflow_event_id": event.event_id,
                    "recipient_id": released.officer_id,
                    "type": "form_released",
                    "case_id": released.case_id,
                    "status": "released",
                    "release_id": release_id,
                })
            except Exception:
                pass
        return pointer

    def rollback_release(self, actor: ActorContext, release_id: str) -> dict[str, Any]:
        self._require(actor, "admin")
        active = self.repository.active_release()
        if not active or active.get("previous_release_id") != release_id:
            raise ValueError("FORM_RELEASE_ROLLBACK_TARGET_INVALID")
        target = self.repository.get_release(release_id)
        if not target or target.get("status") not in {"retired", "validated"}:
            raise ValueError("FORM_RELEASE_ROLLBACK_TARGET_INVALID")
        target["status"] = "validated"
        self.repository.update_release(target)
        return self.repository.set_active_release(release_id, actor_id=actor.user_id)

    def coverage(self, actor: ActorContext) -> dict[str, Any]:
        if actor.role == "citizen": raise PermissionError("FORM_CASE_FORBIDDEN")
        cases = self.repository.list_cases(); statuses = {}
        for case in cases: statuses[case.status.value] = statuses.get(case.status.value, 0) + 1
        gaps = self.repository.list_verified_gaps()
        decided_statuses = {
            FormWorkflowStatus.ATTESTED,
            FormWorkflowStatus.RELEASE_CANDIDATE,
            FormWorkflowStatus.RELEASED,
        }
        active = self.repository.active_release() or {}
        active_manifest = active.get("manifest") or active
        active_procedures = {
            str(item.get("procedure_id") or "")
            for item in active_manifest.get("procedures") or []
            if item.get("procedure_id")
        }
        active_identities = {
            self._asset_identity(item)
            for item in active_manifest.get("assets") or []
        }
        active_bindings = {
            self._binding_identity(item)
            for item in active_manifest.get("bindings") or []
        }
        procedure_ids = active_procedures | {item.procedure_id for item in cases}
        decided_procedures = active_procedures | {
            item.procedure_id for item in cases if item.status in decided_statuses
        }
        gap_procedures = {
            str(item["target_id"]) for item in gaps if item["target_type"] == "procedure"
        }
        identity_gaps = {
            str(item["target_id"]) for item in gaps if item["target_type"] == "identity"
        }
        binding_gaps = {
            str(item["target_id"]) for item in gaps if item["target_type"] == "binding"
        }
        procedure_total = len(procedure_ids | gap_procedures)
        procedure_decided = len(decided_procedures | gap_procedures)
        case_identities = {self._case_identity(item) for item in cases}
        decided_case_identities = {
            self._case_identity(item) for item in cases if item.status in decided_statuses
        }
        case_bindings = {
            identity
            for item in cases
            for identity in self._case_binding_identities(item)
        }
        decided_case_bindings = {
            identity
            for item in cases if item.status in decided_statuses
            for identity in self._case_binding_identities(item)
        }
        identity_total = len(active_identities | case_identities | identity_gaps)
        identity_decided = len(active_identities | decided_case_identities | identity_gaps)
        binding_total = len(active_bindings | case_bindings | binding_gaps)
        binding_decided = len(active_bindings | decided_case_bindings | binding_gaps)
        complete = bool(cases or gaps or active_procedures) and (
            procedure_total == procedure_decided
            and identity_total == identity_decided
            and binding_total == binding_decided
        )
        return {
            "procedure_total": procedure_total,
            "procedure_decided": procedure_decided,
            "identity_total": identity_total,
            "identity_decided": identity_decided,
            "binding_total": binding_total,
            "binding_decided": binding_decided,
            "complete": complete,
            "workflow_statuses": statuses,
            "verified_gaps": len(gaps),
        }

    @staticmethod
    def _asset_identity(asset: dict[str, Any]) -> str:
        return canonical_sha256({
            "form_code": str(asset.get("form_code") or "").strip().casefold(),
            "canonical_name": str(asset.get("canonical_name") or "").strip().casefold(),
            "issuing_instrument": str(asset.get("issuing_instrument") or "").strip().casefold(),
            "source_checksum": str(asset.get("source_checksum") or "").strip().casefold(),
        })

    def _case_identity(self, case: FormReviewCase) -> str:
        asset = dict(case.legal_metadata.get("asset") or {})
        if asset:
            return self._asset_identity(asset)
        submission = case.current_submission
        return canonical_sha256({
            "title": submission.title.strip().casefold(),
            "source_checksum": str(submission.source_checksum or "").casefold(),
            "source_url": submission.source_url.strip().casefold(),
        })

    @staticmethod
    def _binding_identity(binding: dict[str, Any]) -> str:
        return canonical_sha256({
            "procedure_id": binding.get("procedure_id"),
            "form_id": binding.get("form_id"),
            "requirement": binding.get("requirement"),
            "condition": binding.get("condition"),
            "audience": binding.get("audience"),
        })

    def _case_binding_identities(self, case: FormReviewCase) -> set[str]:
        bindings = list(case.legal_metadata.get("bindings") or [])
        if bindings:
            return {self._binding_identity(item) for item in bindings}
        return {canonical_sha256({
            "procedure_id": case.procedure_id,
            "proposed_identity": self._case_identity(case),
            "audience": "citizen",
        })}

    def verify_gap(self, actor: ActorContext, payload: dict[str, Any]) -> dict[str, Any]:
        self._require(actor, "admin")
        if payload.get("target_type") not in {"procedure", "identity", "binding"}: raise ValueError("FORM_VERIFIED_GAP_TARGET_INVALID")
        checksum = str(payload.get("evidence_sha256") or "").casefold()
        if len(checksum) != 64 or any(x not in "0123456789abcdef" for x in checksum): raise ValueError("FORM_CHECKSUM_INVALID")
        evidence_url = str(payload.get("evidence_source_url") or "")
        if not is_allowlisted_official_url(evidence_url):
            raise ValueError("FORM_SOURCE_REQUIRED")
        procedure_ids = sorted({
            str(item).strip() for item in payload.get("procedure_ids") or [] if str(item).strip()
        })
        metadata: dict[str, Any] = {"procedure_ids": procedure_ids}
        if payload["target_type"] == "procedure":
            if str(payload.get("target_id") or "") not in procedure_ids:
                procedure_ids.append(str(payload.get("target_id") or ""))
            if not all(str(payload.get(key) or "").strip() for key in ("domain", "procedure_name", "procedure_source_url")):
                raise ValueError("FORM_VERIFIED_GAP_PROCEDURE_METADATA_REQUIRED")
            if not is_allowlisted_official_url(str(payload["procedure_source_url"])):
                raise ValueError("FORM_SOURCE_REQUIRED")
            metadata.update({
                "procedure": {
                    "procedure_id": str(payload["target_id"]),
                    "name": str(payload["procedure_name"]),
                    "domain": str(payload["domain"]),
                    "authority": "UBND cấp xã",
                    "jurisdiction": "Hai Phong",
                    "official_source_url": str(payload["procedure_source_url"]),
                    "legal_as_of": str(payload.get("legal_as_of") or ""),
                    "coverage_status": "verified_gap",
                    "coverage_reason": str(payload.get("reason_code") or ""),
                },
                "aliases": sorted({
                    str(item).strip() for item in payload.get("aliases") or [] if str(item).strip()
                }),
            })
            if not metadata["aliases"]:
                raise ValueError("FORM_QUESTION_ALIAS_REQUIRED")
        gap = {"gap_id":f"gap-{uuid.uuid4().hex}","target_type":payload["target_type"],"target_id":str(payload.get("target_id") or ""),"reason_code":str(payload.get("reason_code") or ""),"evidence_source_url":evidence_url,"evidence_sha256":checksum,"legal_as_of":str(payload.get("legal_as_of") or ""),"verified_by":actor.user_id,"status":"verified_gap","metadata":metadata}
        if not all(gap[x] for x in ("target_id","reason_code","evidence_source_url","legal_as_of")): raise ValueError("FORM_VERIFIED_GAP_EVIDENCE_REQUIRED")
        return self.repository.save_verified_gap(gap)


_SERVICE: FormGovernanceService | None = None


def get_form_governance_service() -> FormGovernanceService:
    global _SERVICE
    if _SERVICE is None:
        import os

        governance_source = str(os.getenv("FORM_GOVERNANCE_SOURCE") or "json_compat").casefold()
        verify_remote = governance_source == "postgres_active" or str(
            os.getenv("FORM_RELEASE_VERIFY_REMOTE_SOURCES") or "false"
        ).casefold() in {"1", "true", "yes", "on"}
        repository = configured_repository()
        _SERVICE = FormGovernanceService(
            repository,
            source_verifier=HttpFormSourceVerifier() if verify_remote else None,
            procedure_scope_lookup=configured_procedure_scope_lookup(
                repository=repository,
                governance_source=governance_source,
            ),
        )
    return _SERVICE
