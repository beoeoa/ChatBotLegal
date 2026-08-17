from __future__ import annotations

from datetime import date
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.legal_impact_service import LegalDependency
from api.legal_lifecycle_service import LegalLifecycleDocument
from api.routers import legal_management


def request(role: str = "admin", user_id: str = "admin-1") -> Request:
    value = Request({"type": "http", "method": "GET", "path": "/api/legal-management", "headers": []})
    value.state.user_role = role
    value.state.user_id = user_id
    value.state.username = user_id
    return value


@pytest.fixture
def store(monkeypatch):
    value = legal_management.InMemoryLegalManagementStore()
    value.documents["doc-1"] = LegalLifecycleDocument(
        id="doc-1",
        effective_from=date(2025, 1, 1),
        effective_to=date(2026, 9, 1),
        source_url="https://vbpl.vn/doc-1",
    )
    value.vector_states["doc-1"] = "missing"
    value.dependencies.append(LegalDependency("procedure", "procedure-1", "doc-1"))
    legal_management.configure_legal_management_store(value)

    async def audit(**_kwargs):
        return None

    monkeypatch.setattr(legal_management, "write_audit_log", audit)
    yield value
    legal_management.configure_legal_management_store(None)


@pytest.mark.asyncio
async def test_lifecycle_summary_and_filters_are_as_of_and_metadata_only(store):
    summary = await legal_management.lifecycle_summary(
        request(), legal_as_of=date(2026, 8, 13)
    )
    assert summary["counts"]["expiring_30"] == 1
    assert summary["alerts"][0]["threshold_days"] == 30
    rows = await legal_management.lifecycle_documents(
        request(),
        legal_as_of=date(2026, 8, 13),
        bucket="expiring_30",
        vector_state="missing",
    )
    assert rows[0]["document_id"] == "doc-1"
    assert "content" not in rows[0]


@pytest.mark.asyncio
async def test_candidate_confirmation_creates_review_case_without_index_mutation(store):
    candidate = await legal_management.create_change_event_candidate(
        legal_management.ChangeEventCandidateRequest(
            document_id="doc-1",
            event_type="replace",
            effective_from=date(2026, 8, 13),
            source_url="https://vbpl.vn/replacement",
            reason="Ghi nhận đề xuất thay thế từ nguồn chính thức.",
        ),
        request(),
    )
    before = await legal_management.lifecycle_documents(
        request(), legal_as_of=date(2026, 8, 13)
    )
    assert before[0]["bucket"] == "expiring_30"

    confirmed = await legal_management.confirm_change_event(
        candidate["id"],
        legal_management.ConfirmChangeEventRequest(
            evidence_fingerprint=candidate["evidence_fingerprint"],
            reason="Đã đối chiếu nguồn và xác nhận quan hệ thay thế."
        ),
        request(),
    )
    assert confirmed["impact_case_count"] == 1
    after = await legal_management.lifecycle_documents(
        request(), legal_as_of=date(2026, 8, 13)
    )
    assert after[0]["bucket"] == "replaced"
    cases = await legal_management.list_impact_cases(request(), status="needs_review")
    assert len(cases) == 1

    preview = await legal_management.preview_index_job(
        legal_management.IndexPreviewRequest(
            document_id="doc-1", provisions=["Điều 1"]
        ),
        request(),
    )
    assert preview["mode"] == "incremental"
    assert preview["mutation_performed"] is False
    assert preview["active_pointer_change"] is False


@pytest.mark.asyncio
async def test_non_admin_is_rejected_before_store_access(store):
    with pytest.raises(HTTPException) as denied:
        await legal_management.lifecycle_summary(
            request("officer", "officer-1"), legal_as_of=date(2026, 8, 13)
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_active_manifest_projection_omits_storage_paths(store, monkeypatch, tmp_path):
    path = tmp_path / "vector-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "feature018.vector-serving-manifest.v1",
                "read_only": True,
                "gate_passed": False,
                "counts": {"fingerprint_mismatch": 2},
                "manifest_fingerprint": "m" * 64,
                "inventory_sha256": "i" * 64,
                "active_collection": "legal-active",
                "active_pointer_unchanged": True,
                "source_details": {
                    "database_target": "postgresql://secret-host/legal",
                    "chroma_path": "D:/secret/chroma",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(legal_management, "VECTOR_MANIFEST_PATH", path)

    result = await legal_management.active_index_manifest(request())

    assert result["counts"]["fingerprint_mismatch"] == 2
    assert "source_details" not in result
    assert "chroma_path" not in str(result)
    assert "database_target" not in str(result)
