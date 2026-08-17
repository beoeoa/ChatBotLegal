from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

from api.form_governance_models import ActorContext, FormReviewSubmission, FormWorkflowStatus
from api.form_governance_repository import InMemoryFormGovernanceRepository
from api.form_governance_service import FormGovernanceService
from api.form_procedure_scope import fixed_procedure_scope_lookup


def _service(repo, mapping=None):
    return FormGovernanceService(
        repo,
        procedure_scope_lookup=fixed_procedure_scope_lookup(
            mapping or {"proc-1": "cu_tru_an_ninh", "proc-2": "cu_tru_an_ninh", "p": "cu_tru_an_ninh"}
        ),
    )


def _metadata(checksum: str = "a" * 64) -> dict:
    return {
        "procedure": {"procedure_id": "proc-1", "name": "Đăng ký thủ tục mẫu", "domain": "cu_tru", "authority": "UBND cấp xã", "official_source_url": "https://dichvucong.gov.vn/proc-1", "legal_as_of": "2026-08-11", "coverage_status": "released"},
        "asset": {"form_id": "form-1", "canonical_name": "Tờ khai thủ tục mẫu", "asset_kind": "file", "source_url": "https://vbpl.vn/form-1.pdf", "source_checksum": checksum, "audiences": ["citizen"], "coverage_status": "released"},
        "bindings": [{"procedure_id": "proc-1", "form_id": "form-1", "requirement": "required", "audience": "citizen", "coverage_status": "released"}],
        "aliases": ["làm thủ tục mẫu"],
    }


def _attested():
    repo = InMemoryFormGovernanceRepository(); service = _service(repo)
    officer = ActorContext(user_id="officer-1", role="officer", domains=["cu_tru"])
    admin = ActorContext(user_id="admin-1", role="admin", domains=[])
    case = service.submit(officer, FormReviewSubmission(procedure_id="proc-1", domain="cu_tru", title="Mẫu", source_url="https://vbpl.vn/form-1.pdf", source_checksum="a" * 64))
    case = service.transition(admin, case.case_id, FormWorkflowStatus.SOURCE_APPROVED)
    case = service.enrich(admin, case.case_id, _metadata())
    case = service.ready_for_attestation(admin, case.case_id)
    preview = service.attestation_preview(admin, case.case_id)
    case = service.attest(admin, case.case_id, preview["fingerprint"])
    return service, repo, admin, case


def test_source_approval_is_not_runtime_eligible_and_tampered_fingerprint_fails():
    repo = InMemoryFormGovernanceRepository(); service = _service(repo)
    officer = ActorContext(user_id="o", role="officer", domains=["cu_tru"]); admin = ActorContext(user_id="a", role="admin", domains=[])
    case = service.submit(officer, FormReviewSubmission(
        procedure_id="p",
        domain="cu_tru",
        title="Mẫu",
        source_url="https://vbpl.vn/a",
        source_checksum="a" * 64,
    ))
    approved = service.transition(admin, case.case_id, FormWorkflowStatus.SOURCE_APPROVED)
    assert approved.status == FormWorkflowStatus.SOURCE_APPROVED
    assert repo.active_release() is None


def test_failed_release_gate_keeps_active_pointer_and_valid_release_activates():
    service, repo, admin, case = _attested()
    release = service.build_release(admin, [case.case_id], legal_as_of=date(2026, 8, 11), source_snapshot_sha256="b" * 64)
    assert release["manifest"]["bindings"][0]["binding_id"].startswith("binding-")
    validated = service.validate_release(admin, release["release_id"])
    assert validated["status"] == "validated"
    pointer = service.activate_release(admin, release["release_id"])
    assert pointer["release_id"] == release["release_id"]

    blocked = dict(release)
    blocked["release_id"] = "blocked-release"; blocked["version"] = 2
    blocked["manifest"] = {**release["manifest"], "release_id": "blocked-release", "assets": [{**release["manifest"]["assets"][0], "source_checksum": "bad"}]}
    blocked["status"] = "candidate"; repo.save_release(blocked)
    result = service.validate_release(admin, "blocked-release")
    assert result["status"] == "blocked"
    with pytest.raises(ValueError, match="FORM_RELEASE_GATE_FAILED"):
        service.activate_release(admin, "blocked-release")
    assert repo.active_release()["release_id"] == release["release_id"]


def test_notification_projection_failure_does_not_roll_back_legal_transition():
    class FailingNotificationRepo(InMemoryFormGovernanceRepository):
        def enqueue_notification(self, notification):
            raise RuntimeError("surreal unavailable")

    repo = FailingNotificationRepo(); service = _service(repo)
    officer = ActorContext(user_id="o", role="officer", domains=["cu_tru"]); admin = ActorContext(user_id="a", role="admin", domains=[])
    case = service.submit(officer, FormReviewSubmission(procedure_id="p", domain="cu_tru", title="Mẫu", source_url="https://vbpl.vn/a"))
    service.transition(admin, case.case_id, FormWorkflowStatus.NEEDS_SUPPLEMENT, reason="Thiếu nguồn")
    assert repo.get_case(case.case_id).status == FormWorkflowStatus.NEEDS_SUPPLEMENT


def test_source_approval_requires_official_checksum_bound_submission():
    repo = InMemoryFormGovernanceRepository(); service = _service(repo)
    officer = ActorContext(user_id="o", role="officer", domains=["cu_tru"])
    admin = ActorContext(user_id="a", role="admin", domains=[])
    case = service.submit(officer, FormReviewSubmission(
        procedure_id="p",
        domain="cu_tru",
        title="Mẫu cư trú",
        source_url="https://vbpl.vn/a",
    ))
    with pytest.raises(ValueError, match="FORM_CHECKSUM_REQUIRED"):
        service.transition(admin, case.case_id, FormWorkflowStatus.SOURCE_APPROVED)


def test_incremental_release_preserves_previous_manifest_and_syncs_officer_status():
    service, repo, admin, first_case = _attested()
    first = service.build_release(
        admin,
        [first_case.case_id],
        legal_as_of=date(2026, 8, 11),
        source_snapshot_sha256="b" * 64,
    )
    service.validate_release(admin, first["release_id"])
    service.activate_release(admin, first["release_id"])
    assert repo.get_case(first_case.case_id).status == FormWorkflowStatus.RELEASED

    officer = ActorContext(user_id="officer-2", role="officer", domains=["cu_tru"])
    submission = FormReviewSubmission(
        procedure_id="proc-2",
        domain="cu_tru",
        title="Mẫu cư trú thứ hai",
        source_url="https://vbpl.vn/form-2.pdf",
        source_checksum="c" * 64,
    )
    second_case = service.submit(officer, submission)
    second_case = service.transition(admin, second_case.case_id, FormWorkflowStatus.SOURCE_APPROVED)
    metadata = _metadata("c" * 64)
    metadata["procedure"].update(procedure_id="proc-2", name="Thủ tục thứ hai")
    metadata["asset"].update(form_id="form-2", source_url=submission.source_url)
    metadata["bindings"] = [{
        "procedure_id": "proc-2",
        "form_id": "form-2",
        "requirement": "required",
        "audience": "citizen",
        "coverage_status": "released",
    }]
    second_case = service.enrich(admin, second_case.case_id, metadata)
    second_case = service.ready_for_attestation(admin, second_case.case_id)
    preview = service.attestation_preview(admin, second_case.case_id)
    second_case = service.attest(admin, second_case.case_id, preview["fingerprint"])
    second = service.build_release(
        admin,
        [second_case.case_id],
        legal_as_of=date(2026, 8, 11),
        source_snapshot_sha256="d" * 64,
    )
    assert {item["procedure_id"] for item in second["manifest"]["procedures"]} == {
        "proc-1",
        "proc-2",
    }
    assert {item["form_id"] for item in second["manifest"]["assets"]} == {
        "form-1",
        "form-2",
    }
    service.validate_release(admin, second["release_id"])
    service.activate_release(admin, second["release_id"])
    assert repo.get_case(second_case.case_id).status == FormWorkflowStatus.RELEASED
    assert any(
        item.get("type") == "form_released" and item.get("recipient_id") == "officer-2"
        for item in repo.notifications
    )
    rollback = service.rollback_release(admin, first["release_id"])
    assert rollback["release_id"] == first["release_id"]
    assert repo.active_release()["release_id"] == first["release_id"]


def test_verified_procedure_gap_is_released_as_non_downloadable_source_gap():
    service, _repo, admin, case = _attested()
    gap = service.verify_gap(admin, {
        "target_type": "procedure",
        "target_id": "proc-no-form",
        "reason_code": "NO_OFFICIAL_FORM_LISTED",
        "evidence_source_url": "https://dichvucong.gov.vn/proc-no-form",
        "evidence_sha256": "e" * 64,
        "legal_as_of": "2026-08-11",
        "procedure_ids": ["proc-no-form"],
        "domain": "cu_tru_an_ninh",
        "procedure_name": "Thủ tục không có biểu mẫu chính thức",
        "procedure_source_url": "https://dichvucong.gov.vn/proc-no-form",
        "aliases": ["làm thủ tục không có mẫu"],
    })
    assert gap["status"] == "verified_gap"
    release = service.build_release(
        admin,
        [case.case_id],
        legal_as_of=date(2026, 8, 11),
        source_snapshot_sha256="f" * 64,
    )
    gap_procedure = next(
        item for item in release["manifest"]["procedures"]
        if item["procedure_id"] == "proc-no-form"
    )
    assert gap_procedure["coverage_status"] == "verified_gap"
    assert release["manifest"]["gaps"][0]["target_id"] == "proc-no-form"
    validated = service.validate_release(admin, release["release_id"])
    assert validated["status"] == "validated"


def test_database_native_gap_dates_are_normalized_before_manifest_hashing():
    service, repo, admin, case = _attested()
    repo._gaps.append({
        "gap_id": "gap-native-date",
        "target_type": "procedure",
        "target_id": "proc-native-date",
        "reason_code": "NO_OFFICIAL_FORM_LISTED",
        "evidence_source_url": "https://dichvucong.gov.vn/proc-native-date",
        "evidence_sha256": "f" * 64,
        "legal_as_of": date(2026, 8, 11),
        "verified_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
        "verified_by": "admin-1",
        "status": "verified_gap",
        "metadata": {
            "procedure": {
                "procedure_id": "proc-native-date",
                "name": "Thủ tục có ngày từ PostgreSQL",
                "domain": "cu_tru_an_ninh",
                "authority": "UBND cấp xã",
                "official_source_url": "https://dichvucong.gov.vn/proc-native-date",
                "legal_as_of": "2026-08-11",
                "coverage_status": "verified_gap",
            },
            "aliases": ["thủ tục ngày PostgreSQL"],
        },
    })
    release = service.build_release(admin, [case.case_id], legal_as_of=date(2026, 8, 11))
    json.dumps(release["manifest"], ensure_ascii=False)
    gap = next(item for item in release["manifest"]["gaps"] if item["target_id"] == "proc-native-date")
    assert gap["legal_as_of"] == "2026-08-11"
    assert gap["verified_at"].startswith("2026-08-11")
