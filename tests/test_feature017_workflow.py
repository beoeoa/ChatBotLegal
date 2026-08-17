from __future__ import annotations

from datetime import date

import pytest

from api.form_governance_models import (
    ActorContext,
    FormWorkflowStatus,
    LegalFormAssetDraft,
    LegalProcedureDraft,
    ProcedureFormBindingDraft,
    attestation_fingerprint,
    assert_transition,
)
from api.form_governance_service import FormGovernanceService
from api.form_governance_repository import InMemoryFormGovernanceRepository
from api.form_procedure_scope import fixed_procedure_scope_lookup


def test_state_machine_separates_source_approval_attestation_and_release():
    assert_transition(FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.SOURCE_APPROVED, "admin")
    with pytest.raises(ValueError, match="FORM_TRANSITION_INVALID"):
        assert_transition(FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.RELEASED, "admin")
    with pytest.raises(ValueError, match="FORM_TRANSITION_INVALID"):
        assert_transition(FormWorkflowStatus.SOURCE_APPROVED, FormWorkflowStatus.ATTESTED, "admin")


def test_officer_cannot_perform_admin_transitions():
    with pytest.raises(ValueError, match="FORM_TRANSITION_FORBIDDEN"):
        assert_transition(FormWorkflowStatus.SUBMITTED, FormWorkflowStatus.SOURCE_APPROVED, "officer")


def test_domain_scope_does_not_use_partial_token_intersection():
    service = FormGovernanceService(
        InMemoryFormGovernanceRepository(),
        procedure_scope_lookup=fixed_procedure_scope_lookup({"p": "dat_dai_xay_dung"}),
    )
    actor = ActorContext(user_id="officer", role="officer", domains=["dat_dai_xay_dung"])
    assert service._domain_allowed(actor, "dat_dai_xay_dung") is True
    assert service._domain_allowed(actor, "an_sinh_y_te_giao_duc") is False
    assert service._domain_allowed(actor, "ho_tich_chung_thuc") is False


def test_server_rejects_forged_procedure_id_even_when_submitted_domain_is_allowed():
    service = FormGovernanceService(
        InMemoryFormGovernanceRepository(),
        procedure_scope_lookup=fixed_procedure_scope_lookup({
            "proc-cu-tru": "cu_tru_an_ninh",
            "proc-dat-dai": "dat_dai_xay_dung",
        }),
    )
    actor = ActorContext(user_id="officer", role="officer", domains=["cu_tru_an_ninh"])
    from api.form_governance_models import FormReviewSubmission

    with pytest.raises(PermissionError, match="FORM_PROCEDURE_DOMAIN_MISMATCH"):
        service.submit(actor, FormReviewSubmission(
            procedure_id="proc-dat-dai",
            domain="cu_tru_an_ninh",
            title="Biểu mẫu giả mạo phạm vi",
            source_url="https://vbpl.vn/form.pdf",
        ))


def test_submission_fails_closed_when_server_scope_registry_is_unavailable():
    service = FormGovernanceService(InMemoryFormGovernanceRepository())
    actor = ActorContext(user_id="officer", role="officer", domains=["cu_tru_an_ninh"])
    from api.form_governance_models import FormReviewSubmission

    with pytest.raises(RuntimeError, match="FORM_PROCEDURE_SCOPE_UNAVAILABLE"):
        service.submit(actor, FormReviewSubmission(
            procedure_id="proc-cu-tru",
            domain="cu_tru_an_ninh",
            title="Biểu mẫu cư trú",
            source_url="https://vbpl.vn/form.pdf",
        ))


def test_attestation_fingerprint_changes_for_any_legal_identity_or_checksum_change():
    procedure = LegalProcedureDraft(
        procedure_id="proc-1",
        name="Thủ tục mẫu",
        domain="cu_tru",
        authority="UBND cấp xã",
        official_source_url="https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html",
        legal_as_of=date(2026, 8, 11),
    )
    asset = LegalFormAssetDraft(
        form_id="form-1",
        canonical_name="Tờ khai mẫu",
        asset_kind="file",
        source_url="https://vbpl.vn/file.pdf",
        source_checksum="a" * 64,
        audiences=["citizen"],
    )
    binding = ProcedureFormBindingDraft(
        procedure_id="proc-1",
        form_id="form-1",
        requirement="required",
        audience="citizen",
    )
    actor = ActorContext(user_id="admin-1", role="admin", domains=[])
    first = attestation_fingerprint(
        case_id="case-1",
        revision=2,
        procedure=procedure,
        asset=asset,
        bindings=[binding],
        aliases=["làm thủ tục mẫu"],
        reviewer=actor,
    )
    changed = asset.model_copy(update={"source_checksum": "b" * 64})
    second = attestation_fingerprint(
        case_id="case-1",
        revision=2,
        procedure=procedure,
        asset=changed,
        bindings=[binding],
        aliases=["làm thủ tục mẫu"],
        reviewer=actor,
    )
    assert first != second
    assert len(first) == 64
