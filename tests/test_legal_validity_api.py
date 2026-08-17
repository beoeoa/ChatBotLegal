from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from api.routers import legal_search
from open_notebook.database.repository import ensure_record_id


def _request(role: str = "admin", user_id: str = "user:admin") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/legal/validity",
            "headers": [],
        }
    )
    request.state.user_role = role
    request.state.user_id = user_id
    request.state.username = "admin"
    return request


class FakeService:
    def __init__(self) -> None:
        self.run_calls: list[dict] = []

    async def status(self) -> dict:
        return {
            "mode": "protect",
            "status": "healthy",
            "coverage": {"eligible": 10, "observed": 9, "fresh": 9},
            "counts": {"active": 8, "blocked": 1, "warning": 0, "open_events": 1},
            "sources": [{"source_kind": "vbpl", "status": "healthy"}],
        }

    async def run(self, **kwargs) -> dict:
        self.run_calls.append(kwargs)
        return {
            "status": "completed",
            "events_created": 1,
            "failures": {},
            "scanned": {"documents": 3, "forms": 0},
        }


class FakeRegistry:
    def __init__(self) -> None:
        self.decisions: list[dict] = []

    async def list_events(self, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "legal_validity_event:evt-1",
                    "document_id": "doc-1",
                    "law_number": "31/2024/QH15",
                    "source_url": "https://vbpl.vn/van-ban/example",
                    "review_status": "open",
                }
            ],
            "next_cursor": None,
        }

    async def get_event(self, event_id: str):
        if event_id.endswith("missing"):
            return None
        return {
            "id": "legal_validity_event:evt-1",
            "law_number": "31/2024/QH15",
            "review_status": "open",
        }

    async def document_timeline(self, document_id: str) -> dict | None:
        if document_id == "missing":
            return None
        return {
            "document_id": document_id,
            "observations": [],
            "events": [],
            "decisions": [],
        }

    async def record_decision(self, **kwargs) -> dict:
        self.decisions.append(kwargs)
        return {
            "id": "legal_validity_decision:decision-1",
            "action": kwargs["action"],
            "reason": kwargs["reason"],
            "new_review_status": "confirmed",
        }


def test_validity_response_contracts_accept_service_projections():
    status = legal_search.LegalValidityStatusResponse(
        mode="protect",
        status="healthy",
        coverage={"eligible": 10, "observed": 9, "fresh": 9},
        counts={"active": 8, "blocked": 1, "warning": 0, "open_events": 1},
        sources=[{"source_kind": "vbpl", "status": "healthy"}],
    )
    run = legal_search.LegalValidityRunResponse(
        status="completed",
        scanned={"documents": 3, "forms": 0},
    )

    assert status.coverage.fresh == 9
    assert run.scanned["documents"] == 3


def test_event_projection_converts_database_record_ids_before_api_validation():
    projected = legal_search.default_registry._event_projection(
        {
            "id": ensure_record_id("legal_validity_event:evt-1"),
            "document_id": ensure_record_id("source:doc-1"),
            "law_number": "31/2024/QH15",
            "severity": "critical",
            "review_status": "open",
        }
    )

    response = legal_search.LegalValidityEventResponse(**projected)

    assert response.id == "legal_validity_event:evt-1"
    assert response.document_id == "source:doc-1"


@pytest.mark.asyncio
async def test_validity_endpoints_reject_non_admin(monkeypatch):
    monkeypatch.setattr(legal_search, "get_legal_validity_sync_service", lambda: FakeService())

    with pytest.raises(HTTPException) as denied:
        await legal_search.legal_validity_status(_request(role="officer"))

    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_manual_run_requires_reason_and_audits_before_mutation(monkeypatch):
    service = FakeService()
    order: list[str] = []

    async def audit(**kwargs):
        order.append("audit")
        assert kwargs["action"] == "admin.legal_validity.run"
        assert kwargs["details"]["reason"] == "Kiểm tra ngay sau thông báo văn bản mới"

    async def run(**kwargs):
        order.append("run")
        return await FakeService.run(service, **kwargs)

    service.run = run
    monkeypatch.setattr(legal_search, "write_audit_log", audit)
    monkeypatch.setattr(legal_search, "get_legal_validity_sync_service", lambda: service)
    monkeypatch.setattr(legal_search, "_notify_validity_admin", AsyncMock())

    result = await legal_search.legal_validity_run(
        legal_search.LegalValidityRunRequest(
            reason="Kiểm tra ngay sau thông báo văn bản mới",
            scope=["central", "haiphong"],
            limit=3,
        ),
        _request(),
    )

    assert order == ["audit", "run"]
    assert result["status"] == "completed"
    assert service.run_calls[0]["requested_by"] == "user:admin"
    assert service.run_calls[0]["scopes"] == ("central", "haiphong")


@pytest.mark.asyncio
async def test_audit_failure_prevents_manual_run(monkeypatch):
    service = FakeService()

    async def unavailable_audit(**kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(legal_search, "write_audit_log", unavailable_audit)
    monkeypatch.setattr(legal_search, "get_legal_validity_sync_service", lambda: service)

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await legal_search.legal_validity_run(
            legal_search.LegalValidityRunRequest(
                reason="Đối chiếu hiệu lực theo yêu cầu quản trị",
            ),
            _request(),
        )

    assert service.run_calls == []


@pytest.mark.asyncio
async def test_event_listing_and_document_timeline_are_admin_only_projections(monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setattr(legal_search, "default_registry", registry)

    events = await legal_search.legal_validity_events(
        _request(), review_status="open", severity="critical", scope=None, limit=20, cursor=None
    )
    timeline = await legal_search.legal_validity_document("doc-1", _request())

    assert events["items"][0]["law_number"] == "31/2024/QH15"
    assert "raw_content" not in events["items"][0]
    assert timeline["document_id"] == "doc-1"
    assert timeline["replacement_discovery"]["status"] == "no_explicit_candidate"


@pytest.mark.asyncio
async def test_vector_cleanup_preview_is_admin_only_and_read_only(monkeypatch):
    request_call = AsyncMock(return_value={"dry_run": True, "state": "blocking_applied"})
    monkeypatch.setenv("LEGAL_VECTOR_CLEANUP_TOKEN", "operation-secret")
    monkeypatch.setattr(legal_search, "_request", request_call)

    result = await legal_search.legal_vector_cleanup_preview("42", _request())

    assert result["dry_run"] is True
    request_call.assert_awaited_once_with(
        "GET",
        "/documents/42/vector-cleanup/preview",
        headers={"X-Legal-Operations-Token": "operation-secret"},
    )
    with pytest.raises(HTTPException) as denied:
        await legal_search.legal_vector_cleanup_preview(
            "42", _request(role="officer")
        )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_vector_cleanup_audits_before_internal_mutation(monkeypatch):
    order: list[str] = []

    async def audit(**kwargs):
        order.append("audit")
        assert kwargs["action"] == "admin.legal_validity.vector_cleanup"
        assert kwargs["entity_id"] == "42"

    async def request(method, path, **kwargs):
        order.append("cleanup")
        assert method == "POST"
        assert path == "/documents/42/vector-cleanup"
        assert kwargs["headers"]["X-Legal-Operations-Token"] == "operation-secret"
        assert kwargs["json"]["requested_by"] == "user:admin"
        return {"state": "vector_cleanup_completed"}

    monkeypatch.setenv("LEGAL_VECTOR_CLEANUP_TOKEN", "operation-secret")
    monkeypatch.setattr(legal_search, "write_audit_log", audit)
    monkeypatch.setattr(legal_search, "_request", request)

    result = await legal_search.legal_vector_cleanup_execute(
        "42",
        legal_search.LegalVectorCleanupRequest(
            reason="Dọn vector sau khi đã kiểm tra trạng thái chặn logic."
        ),
        _request(),
    )

    assert result["state"] == "vector_cleanup_completed"
    assert order == ["audit", "cleanup"]


@pytest.mark.asyncio
async def test_vector_cleanup_refuses_to_run_without_internal_token(monkeypatch):
    audit = AsyncMock()
    mutation = AsyncMock()
    monkeypatch.delenv("LEGAL_VECTOR_CLEANUP_TOKEN", raising=False)
    monkeypatch.setattr(legal_search, "write_audit_log", audit)
    monkeypatch.setattr(legal_search, "_request", mutation)

    with pytest.raises(HTTPException) as unavailable:
        await legal_search.legal_vector_cleanup_execute(
            "42",
            legal_search.LegalVectorCleanupRequest(
                reason="Dọn vector sau khi đã kiểm tra trạng thái chặn logic."
            ),
            _request(),
        )

    assert unavailable.value.status_code == 503
    audit.assert_not_awaited()
    mutation.assert_not_awaited()


@pytest.mark.asyncio
async def test_decision_rejects_id_tampering_and_is_audited_before_recording(monkeypatch):
    registry = FakeRegistry()
    order: list[str] = []

    async def audit(**kwargs):
        order.append("audit")
        assert kwargs["action"] == "admin.legal_validity.decision"

    original_record = registry.record_decision

    async def record(**kwargs):
        order.append("record")
        return await original_record(**kwargs)

    registry.record_decision = record
    monkeypatch.setattr(legal_search, "default_registry", registry)
    monkeypatch.setattr(legal_search, "write_audit_log", audit)

    payload = legal_search.LegalValidityDecisionRequest(
        action="confirm_mapping",
        reason="Đã đối chiếu chính xác số, cơ quan và ngày ban hành.",
    )
    response = await legal_search.legal_validity_decision("evt-1", payload, _request())

    assert order == ["audit", "record"]
    assert response["decision"]["new_review_status"] == "confirmed"

    with pytest.raises(HTTPException) as tampered:
        await legal_search.legal_validity_decision("evt-1; DELETE legal_validity_event", payload, _request())
    assert tampered.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_event_and_document_return_not_found(monkeypatch):
    registry = FakeRegistry()
    monkeypatch.setattr(legal_search, "default_registry", registry)

    with pytest.raises(HTTPException) as missing_event:
        await legal_search.legal_validity_decision(
            "missing",
            legal_search.LegalValidityDecisionRequest(
                action="request_recheck",
                reason="Nguồn cần được tải và kiểm tra lại đầy đủ.",
            ),
            _request(),
        )
    assert missing_event.value.status_code == 404

    with pytest.raises(HTTPException) as missing_document:
        await legal_search.legal_validity_document("missing", _request())
    assert missing_document.value.status_code == 404
