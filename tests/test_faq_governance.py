from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.faq_governance_service import (
    FaqActor,
    FaqGovernanceService,
    InMemoryFaqGovernanceRepository,
)


def _active_form_release() -> dict:
    return {
        "id": "form-release-row-1",
        "release_id": "forms-2026-08-13",
        "status": "active",
        "manifest": {
            "schema_version": "form-release-v1",
            "release_id": "forms-2026-08-13",
            "legal_as_of": "2026-08-13",
            "procedures": [{
                "procedure_id": "1.000280",
                "procedure_code": "1.000280",
                "name": "Công nhận trường tiểu học đạt chuẩn quốc gia",
                "domain": "an_sinh_y_te_giao_duc",
                "coverage_status": "released",
                "effective_from": "2026-01-01",
            }],
            "assets": [{
                "form_id": "form-official-1",
                "form_code": "M01",
                "canonical_name": "Mẫu đề nghị chính thức",
                "asset_kind": "eform",
                "source_url": "https://dichvucong.gov.vn/eform/form-official-1",
                "download_url": "https://dichvucong.gov.vn/eform/form-official-1",
                "source_checksum": "a" * 64,
                "audiences": ["citizen"],
                "coverage_status": "released",
                "effective_from": "2026-01-01",
            }],
            "bindings": [{
                "binding_id": "binding-1",
                "procedure_id": "1.000280",
                "form_id": "form-official-1",
                "requirement": "required",
                "audience": "citizen",
                "coverage_status": "released",
                "effective_from": "2026-01-01",
            }],
            "aliases": [],
        },
    }


def _service():
    form_release = _active_form_release()
    return FaqGovernanceService(
        InMemoryFaqGovernanceRepository(),
        form_release_provider=lambda: form_release,
        today=lambda: date(2026, 8, 13),
    ), form_release


def _payload(**overrides):
    payload = {
        "faq_key": "school-standard",
        "question": "Thủ tục công nhận trường tiểu học cần mẫu nào?",
        "answer": "Nộp hồ sơ theo hướng dẫn của cơ quan có thẩm quyền.",
        "canonical_domain": "an_sinh_y_te_giao_duc",
        "confirmed_procedure_id": "1.000280",
        "requires_forms": True,
        "evidence": {"source_refs": [{"document_id": "doc-1"}]},
    }
    payload.update(overrides)
    return payload


def test_revision_write_rejects_manual_form_ids_and_is_not_public_before_release():
    service, _ = _service()
    admin = FaqActor(user_id="admin-1", role="admin")

    with pytest.raises(ValueError, match="FAQ_FORM_IDS_NOT_ACCEPTED"):
        service.create_revision(admin, _payload(form_ids=["invented-form-id"]))

    revision = service.create_revision(admin, _payload())
    assert revision["public_state"] == "pending"
    assert "form_ids" not in revision
    assert service.list_public(audience="citizen") == []


def test_confirm_validate_activate_derives_forms_from_bound_feature017_release():
    service, form_release = _service()
    admin = FaqActor(user_id="admin-1", role="admin")
    revision = service.create_revision(admin, _payload())
    confirmed = service.confirm_revision(admin, revision["id"])
    assert confirmed["public_state"] == "confirmed"

    candidate = service.build_release(admin, [revision["id"]])
    assert candidate["status"] == "candidate"
    assert candidate["form_release_id"] == form_release["release_id"]
    assert service.list_public(audience="citizen") == []

    validated = service.validate_release(admin, candidate["id"])
    assert validated["status"] == "validated"
    assert validated["manifest"]["gate_report"]["passed"] is True
    service.activate_release(admin, candidate["id"])

    public = service.list_public(audience="citizen")
    assert len(public) == 1
    assert public[0]["public_state"] == "released"
    assert public[0]["confirmed_procedure_id"] == "1.000280"
    assert public[0]["form_ids"] == ["form-official-1"]
    assert public[0]["forms"][0]["source_checksum"] == "a" * 64
    assert public[0]["forms"][0]["procedure_identity_confirmed"] is True


def test_form_release_pointer_drift_blocks_faq_activation():
    service, form_release = _service()
    admin = FaqActor(user_id="admin-1", role="admin")
    revision = service.create_revision(admin, _payload(requires_forms=False))
    service.confirm_revision(admin, revision["id"])
    candidate = service.build_release(admin, [revision["id"]])
    service.validate_release(admin, candidate["id"])

    form_release["release_id"] = "forms-changed-after-validation"
    form_release["manifest"]["release_id"] = "forms-changed-after-validation"
    with pytest.raises(ValueError, match="FAQ_FORM_RELEASE_DRIFT"):
        service.activate_release(admin, candidate["id"])
    assert service.list_public(audience="citizen") == []


def test_governance_api_requires_confirmed_procedure_and_rejects_manual_form_ids(monkeypatch):
    from api.routers import faq

    service, _ = _service()
    audits = []

    async def capture_audit(**kwargs):
        audits.append(kwargs)

    app = FastAPI()
    app.include_router(faq.router, prefix="/api")
    app.dependency_overrides[faq.required_faq_governance_service] = lambda: service
    app.dependency_overrides[faq.optional_faq_governance_service] = lambda: service
    monkeypatch.setenv("FAQ_GOVERNANCE_MODE", "postgres_active")
    monkeypatch.setattr(faq, "get_request_role", lambda request: request.headers.get("X-User-Role"))
    monkeypatch.setattr(faq, "get_request_user_id", lambda request: request.headers.get("X-User-Id"))
    monkeypatch.setattr(faq, "write_audit_log", capture_audit)
    client = TestClient(app)
    headers = {"X-User-Role": "admin", "X-User-Id": "admin-1"}
    payload = {
        "faq_key": "school-standard",
        "question": "Thủ tục công nhận trường tiểu học cần mẫu nào?",
        "answer": "Nộp hồ sơ theo hướng dẫn của cơ quan có thẩm quyền.",
        "canonical_domain": "an_sinh_y_te_giao_duc",
        "confirmed_procedure_id": "1.000280",
        "requires_forms": True,
        "evidence": {"source_refs": [{"document_id": "doc-1"}]},
    }

    rejected = client.post(
        "/api/faq/governance/revisions",
        json={**payload, "form_ids": ["manual-form"]},
        headers=headers,
    )
    assert rejected.status_code == 422

    created = client.post("/api/faq/governance/revisions", json=payload, headers=headers)
    assert created.status_code == 201
    assert audits[-1]["action"] == "faq.revision.create"
    revision_id = created.json()["id"]
    assert client.get("/api/faq").json()["items"] == []

    assert client.post(
        f"/api/faq/governance/revisions/{revision_id}/confirm", headers=headers
    ).status_code == 200
    candidate = client.post(
        "/api/faq/governance/releases/preview",
        json={"revision_ids": [revision_id]},
        headers=headers,
    )
    assert candidate.status_code == 200
    release_id = candidate.json()["id"]
    assert client.post(
        f"/api/faq/governance/releases/{release_id}/validate", headers=headers
    ).json()["status"] == "validated"
    assert client.post(
        f"/api/faq/governance/releases/{release_id}/activate", headers=headers
    ).json()["status"] == "active"

    public = client.get("/api/faq").json()["items"]
    assert public[0]["confirmed_procedure_id"] == "1.000280"
    assert public[0]["form_ids"] == ["form-official-1"]
