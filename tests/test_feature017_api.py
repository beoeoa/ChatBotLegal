from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
import hashlib

from api.form_governance_models import ActorContext
from api.form_governance_repository import InMemoryFormGovernanceRepository
from api.form_governance_service import FormGovernanceService, get_form_governance_service
from api.form_procedure_scope import fixed_procedure_scope_lookup
from api.routers.procedure_forms_catalog import actor_context, router


def test_api_enforces_actor_scope_and_keeps_source_approval_non_public():
    app=FastAPI(); app.include_router(router, prefix="/api")
    service=FormGovernanceService(
        InMemoryFormGovernanceRepository(),
        procedure_scope_lookup=fixed_procedure_scope_lookup({
            "p1": "cu_tru_an_ninh",
            "p2": "dat_dai_xay_dung",
        }),
    ); current={"actor":ActorContext(user_id="o1",role="officer",domains=["cu_tru"])}
    app.dependency_overrides[get_form_governance_service]=lambda:service
    app.dependency_overrides[actor_context]=lambda:current["actor"]
    client=TestClient(app)
    payload={"procedure_id":"p1","domain":"cu_tru","title":"Biểu mẫu cư trú","source_url":"https://vbpl.vn/p1.pdf","source_checksum":"a"*64,"note":""}
    created=client.post("/api/procedures/forms-catalog/review-cases",json=payload)
    assert created.status_code==200; case_id=created.json()["case_id"]
    forbidden={**payload,"procedure_id":"p2","domain":"dat_dai_xay_dung"}
    assert client.post("/api/procedures/forms-catalog/review-cases",json=forbidden).status_code==403
    forged={**payload,"procedure_id":"p2","domain":"cu_tru"}
    assert client.post("/api/procedures/forms-catalog/review-cases",json=forged).status_code==403
    current["actor"]=ActorContext(user_id="admin",role="admin",domains=[])
    approved=client.post(f"/api/procedures/forms-catalog/review-cases/{case_id}/approve-source")
    assert approved.status_code==200 and approved.json()["status"]=="source_approved"
    assert approved.json()["source_reviewed_by"] == "admin"
    assert client.get("/api/procedures/forms-catalog/releases/active").status_code==404


def test_public_resolver_cannot_escalate_audience_to_officer():
    app = FastAPI(); app.include_router(router, prefix="/api")
    client = TestClient(app)
    response = client.get(
        "/api/procedures/forms-catalog/resolve",
        params={"q": "đăng ký thường trú", "audience": "officer"},
        headers={"X-User-Role": "citizen", "X-User-Id": "citizen-1"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "FORM_CASE_FORBIDDEN"


def test_released_asset_download_serves_exact_checksum_bound_file(
    tmp_path,
    monkeypatch,
):
    payload = b"exact released form"
    checksum = hashlib.sha256(payload).hexdigest()
    runtime_path = "feature017/release-test/form-1.docx"
    source = tmp_path / runtime_path
    source.parent.mkdir(parents=True)
    source.write_bytes(payload)
    release = {
        "manifest": {
            "assets": [{
                "form_id": "form-1",
                "form_code": "01",
                "asset_kind": "file",
                "coverage_status": "released",
                "source_checksum": checksum,
                "runtime_path": runtime_path,
                "download_url": "/api/procedures/forms-catalog/assets/form-1/download",
            }]
        }
    }
    service = SimpleNamespace(
        repository=SimpleNamespace(active_release=lambda: release)
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_form_governance_service] = lambda: service
    monkeypatch.setenv("FORM_RELEASE_ASSET_ROOT", str(tmp_path))
    client = TestClient(app)

    response = client.get(
        "/api/procedures/forms-catalog/assets/form-1/download"
    )

    assert response.status_code == 200
    assert response.content == payload
    assert "01.docx" in response.headers["content-disposition"]

    source.write_bytes(b"tampered")
    tampered = client.get(
        "/api/procedures/forms-catalog/assets/form-1/download"
    )
    assert tampered.status_code == 409
    assert tampered.json()["detail"] == "FORM_CHECKSUM_MISMATCH"
