from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.form_governance_models import ActorContext
from api.form_governance_repository import InMemoryFormGovernanceRepository
from api.form_governance_service import FormGovernanceService, get_form_governance_service
from api.form_governance_release_validator import HttpFormSourceVerifier
from api.form_procedure_scope import fixed_procedure_scope_lookup
from api.routers.procedure_forms_catalog import actor_context, router


def _client(actor: ActorContext) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    service = FormGovernanceService(
        InMemoryFormGovernanceRepository(),
        procedure_scope_lookup=fixed_procedure_scope_lookup({
            "1.000280": "an_sinh_y_te_giao_duc",
            "1.001193": "cu_tru_an_ninh",
        }),
    )
    app.dependency_overrides[get_form_governance_service] = lambda: service
    app.dependency_overrides[actor_context] = lambda: actor
    return TestClient(app)


def test_workflow_metadata_names_each_gate_and_keeps_source_approval_non_public():
    client = _client(ActorContext(user_id="admin-1", role="admin", domains=[]))

    response = client.get("/api/procedures/forms-catalog/source-proposal-metadata")

    assert response.status_code == 200
    payload = response.json()
    assert [step["id"] for step in payload["steps"]] == [
        "procedure_selected",
        "source_verified",
        "legal_metadata_completed",
        "legal_attested",
        "release_validated",
        "released",
    ]
    assert payload["steps"][1]["public_after_step"] is False
    assert payload["steps"][-1]["public_after_step"] is True
    assert set(payload["source_inputs"]) == {"official_url", "pdf", "docx", "eform"}
    assert payload["file_contract"] == {
        "accepted_extensions": [".pdf", ".docx"],
        "accepted_mime_types": [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ],
        "checksum": "sha256",
        "transport": "client_metadata_only_until_secure_upload_gate",
    }


def test_procedure_picker_searches_name_code_and_domain_and_enforces_officer_scope():
    admin = _client(ActorContext(user_id="admin-1", role="admin", domains=[]))

    by_name = admin.get(
        "/api/procedures/forms-catalog/procedure-candidates",
        params={"q": "Công nhận trường tiểu học", "limit": 10},
    )
    assert by_name.status_code == 200
    assert any(item["procedure_code"] == "1.000280" for item in by_name.json()["items"])

    by_code = admin.get(
        "/api/procedures/forms-catalog/procedure-candidates",
        params={"q": "1.000280", "limit": 10},
    )
    assert by_code.status_code == 200
    assert by_code.json()["items"][0]["name"] == "Công nhận trường tiểu học đạt chuẩn quốc gia"

    by_domain = admin.get(
        "/api/procedures/forms-catalog/procedure-candidates",
        params={"domain": "an_sinh_y_te_giao_duc", "limit": 5},
    )
    assert by_domain.status_code == 200
    assert by_domain.json()["items"]
    assert {item["domain"] for item in by_domain.json()["items"]} == {"an_sinh_y_te_giao_duc"}

    officer = _client(
        ActorContext(user_id="officer-1", role="officer", domains=["cu_tru_an_ninh"])
    )
    scoped = officer.get(
        "/api/procedures/forms-catalog/procedure-candidates",
        params={"domain": "an_sinh_y_te_giao_duc"},
    )
    assert scoped.status_code == 403


def test_eform_proposal_starts_review_and_is_not_a_public_release():
    client = _client(ActorContext(
        user_id="officer-1",
        role="officer",
        domains=["an_sinh_y_te_giao_duc"],
    ))
    response = client.post(
        "/api/procedures/forms-catalog/review-cases",
        json={
            "procedure_id": "1.000280",
            "domain": "an_sinh_y_te_giao_duc",
            "title": "Biểu mẫu điện tử chính thức",
            "source_url": "https://dichvucong.gov.vn/eform/1.000280",
            "source_checksum": "a" * 64,
            "asset_kind": "eform",
            "note": "Đề xuất để xác minh nguồn",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "submitted"
    assert payload["current_submission"]["asset_kind"] == "eform"
    assert client.get("/api/procedures/forms-catalog/releases/active").status_code == 404


def test_admin_can_submit_from_admin_data_center_and_source_verification_creates_checksum_revision(monkeypatch):
    async def _no_audit(**kwargs):
        return None

    monkeypatch.setattr("api.routers.procedure_forms_catalog.write_audit_log", _no_audit)
    client = _client(ActorContext(user_id="admin-1", role="admin", domains=[]))
    created = client.post(
        "/api/procedures/forms-catalog/review-cases",
        json={
            "procedure_id": "1.000280",
            "domain": "an_sinh_y_te_giao_duc",
            "title": "Biểu mẫu kiểm thử nguồn chính thức",
            "source_url": "https://dichvucong.gov.vn/eform/1.000280",
            "asset_kind": "eform",
            "note": "Đề xuất từ Trung tâm dữ liệu pháp luật",
        },
    )
    assert created.status_code == 200
    case_id = created.json()["case_id"]
    assert created.json()["revision"] == 1
    assert created.json()["current_submission"]["source_checksum"] is None

    monkeypatch.setattr(
        HttpFormSourceVerifier,
        "calculate_checksum",
        lambda self, source_url: ("b" * 64, None),
    )
    verified = client.post(
        f"/api/procedures/forms-catalog/review-cases/{case_id}/approve-source"
    )

    assert verified.status_code == 200
    assert verified.json()["status"] == "source_approved"
    assert verified.json()["revision"] == 2
    assert verified.json()["current_submission"]["source_checksum"] == "b" * 64
    assert client.get("/api/procedures/forms-catalog/releases/active").status_code == 404
