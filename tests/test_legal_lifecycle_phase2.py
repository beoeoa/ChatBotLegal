from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, Response

from api.legal_lifecycle_service import (
    InMemoryLifecycleRepository,
    LifecycleError,
    LifecycleService,
    lifecycle_capabilities,
)
from api.routers import legal_search

VALID_DRAFT = {
    "title": "Quyết định thử nghiệm về hộ tịch",
    "law_number": "01/2026/QĐ-TEST",
    "document_type": "Quyết định",
    "issuing_agency": "Ủy ban nhân dân thành phố Hải Phòng",
    "scope": "Hải Phòng",
    "sector": "Hộ tịch",
    "issued_date": "2026-08-01",
    "effective_date": "2026-08-10",
    "source_url": "https://vbpl.vn/example-official",
    "content": "Điều 1. Nội dung văn bản thử nghiệm, không phải căn cứ pháp lý thật.",
}


@pytest.fixture
def enabled_env(monkeypatch):
    monkeypatch.setenv("LEGAL_LIFECYCLE_WRITES_ENABLED", "true")
    monkeypatch.setenv("LEGAL_LIFECYCLE_EDITORS", "user:editor,user:dual")
    monkeypatch.setenv("LEGAL_LIFECYCLE_REVIEWERS", "user:reviewer,user:dual")


@pytest.fixture
def service(enabled_env):
    return LifecycleService(InMemoryLifecycleRepository(), audit_writer=AsyncMock())


def test_capabilities_are_explicit_and_default_deny(monkeypatch):
    monkeypatch.delenv("LEGAL_LIFECYCLE_WRITES_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_LIFECYCLE_EDITORS", raising=False)
    monkeypatch.delenv("LEGAL_LIFECYCLE_REVIEWERS", raising=False)
    assert lifecycle_capabilities("user:admin", "admin") == {
        "actor_id": "user:admin",
        "authenticated_admin": True,
        "writes_enabled": False,
        "editor": False,
        "reviewer": False,
        "activation_enabled": False,
    }
    assert lifecycle_capabilities("user:editor", "officer")["editor"] is False


@pytest.mark.asyncio
async def test_create_update_etag_and_validation_are_fail_closed(service):
    created = await service.create_draft(
        actor="user:editor",
        role="admin",
        payload=VALID_DRAFT,
        reason="Tạo hồ sơ thử nghiệm cho quy trình Phase 2.",
        idempotency_key="create-draft-0001",
    )
    assert created["state"] == "draft"
    assert created["revision"] == 1

    updated = await service.update_draft(
        created["id"],
        actor="user:editor",
        role="admin",
        payload={"title": "Quyết định thử nghiệm đã chỉnh sửa"},
        expected_revision=1,
        reason="Chỉnh tiêu đề theo hồ sơ nguồn thử nghiệm.",
        idempotency_key="update-draft-0001",
    )
    assert updated["revision"] == 2

    with pytest.raises(LifecycleError) as stale:
        await service.update_draft(
            created["id"],
            actor="user:editor",
            role="admin",
            payload={"title": "Ghi đè cũ"},
            expected_revision=1,
            reason="Thử ghi đè bằng revision cũ phải bị chặn.",
            idempotency_key="update-draft-stale",
        )
    assert stale.value.code == "lifecycle_revision_conflict"
    assert (await service.get_draft(created["id"], actor="user:editor", role="admin"))["title"] == updated["title"]

    validated = await service.validate_draft(
        created["id"],
        actor="user:editor",
        role="admin",
        expected_revision=2,
        reason="Kiểm tra metadata và nguồn trước khi gửi duyệt.",
        idempotency_key="validate-draft-0001",
    )
    assert validated["validation"]["blocking"] == []
    assert validated["revision"] == 3


@pytest.mark.asyncio
async def test_missing_source_or_content_blocks_submission(service):
    payload = {**VALID_DRAFT, "source_url": "", "content": ""}
    created = await service.create_draft(
        actor="user:editor", role="admin", payload=payload,
        reason="Tạo hồ sơ thiếu để kiểm thử fail closed.", idempotency_key="create-invalid-0001",
    )
    validated = await service.validate_draft(
        created["id"], actor="user:editor", role="admin", expected_revision=1,
        reason="Kiểm tra hồ sơ thiếu nguồn và nội dung.", idempotency_key="validate-invalid-0001",
    )
    assert "missing_official_source" in validated["validation"]["blocking"]
    assert "missing_content_or_source_asset" in validated["validation"]["blocking"]
    with pytest.raises(LifecycleError) as blocked:
        await service.submit_draft(
            created["id"], actor="user:editor", role="admin", expected_revision=2,
            reason="Không được gửi hồ sơ còn lỗi chặn.", idempotency_key="submit-invalid-0001",
        )
    assert blocked.value.code == "lifecycle_validation_blocked"


@pytest.mark.asyncio
async def test_distinct_reviewer_immutable_version_and_idempotency(service):
    created = await service.create_draft(
        actor="user:dual", role="admin", payload=VALID_DRAFT,
        reason="Tạo đề xuất phiên bản để thử tách quyền.", idempotency_key="create-review-0001",
    )
    validated = await service.validate_draft(
        created["id"], actor="user:dual", role="admin", expected_revision=1,
        reason="Kiểm tra hồ sơ trước khi gửi duyệt.", idempotency_key="validate-review-0001",
    )
    submitted = await service.submit_draft(
        created["id"], actor="user:dual", role="admin", expected_revision=validated["revision"],
        reason="Gửi hồ sơ đã đủ điều kiện sang người duyệt.", idempotency_key="submit-review-0001",
    )
    with pytest.raises(LifecycleError) as same_actor:
        await service.review_draft(
            created["id"], actor="user:dual", role="admin", expected_revision=submitted["revision"],
            decision="approved", reason="Không được tự duyệt hồ sơ của chính mình.",
            idempotency_key="self-review-0001",
        )
    assert same_actor.value.code == "lifecycle_self_review_forbidden"

    approved = await service.review_draft(
        created["id"], actor="user:reviewer", role="admin", expected_revision=submitted["revision"],
        decision="approved", reason="Đã đối chiếu nguồn thử nghiệm và chấp thuận đề xuất.",
        idempotency_key="approve-review-0001",
    )
    replay = await service.review_draft(
        created["id"], actor="user:reviewer", role="admin", expected_revision=submitted["revision"],
        decision="approved", reason="Đã đối chiếu nguồn thử nghiệm và chấp thuận đề xuất.",
        idempotency_key="approve-review-0001",
    )
    assert replay == approved
    assert approved["state"] == "approved"
    assert approved["version"]["version_key"]
    assert len(service.repository.versions) == 1
    assert len(service.repository.approvals) == 2  # submit + approve


@pytest.mark.asyncio
async def test_ordinary_admin_and_non_admin_cannot_read_draft(service):
    created = await service.create_draft(
        actor="user:editor", role="admin", payload=VALID_DRAFT,
        reason="Tạo hồ sơ để kiểm thử phân quyền đọc.", idempotency_key="create-auth-0001",
    )
    for actor, role in (("user:admin", "admin"), ("user:officer", "officer"), ("user:citizen", "citizen")):
        with pytest.raises(LifecycleError) as denied:
            await service.get_draft(created["id"], actor=actor, role=role)
        assert denied.value.code == "lifecycle_forbidden"


@pytest.mark.asyncio
async def test_activation_preview_is_read_only_and_activation_disabled(service, monkeypatch):
    created = await service.create_draft(
        actor="user:editor", role="admin", payload={**VALID_DRAFT, "logical_document_id": 42, "base_fingerprint": "base-v1"},
        reason="Tạo phiên bản thử nghiệm cho văn bản hiện có.", idempotency_key="create-version-0001",
    )
    validated = await service.validate_draft(
        created["id"], actor="user:editor", role="admin", expected_revision=1,
        reason="Kiểm tra phiên bản đề xuất.", idempotency_key="validate-version-0001",
    )
    submitted = await service.submit_draft(
        created["id"], actor="user:editor", role="admin", expected_revision=validated["revision"],
        reason="Gửi phiên bản đề xuất để duyệt.", idempotency_key="submit-version-0001",
    )
    approved = await service.review_draft(
        created["id"], actor="user:reviewer", role="admin", expected_revision=submitted["revision"],
        decision="approved", reason="Duyệt phiên bản để kiểm tra preview, chưa kích hoạt.",
        idempotency_key="approve-version-0001",
    )
    before = dict(service.repository.versions)
    preview = await service.activation_preview(created["id"], actor="user:reviewer", role="admin")
    assert preview["live_activation_enabled"] is False
    assert preview["version_key"] == approved["version"]["version_key"]
    assert service.repository.versions == before
    with pytest.raises(LifecycleError) as disabled:
        await service.activate_draft(
            created["id"], actor="user:reviewer", role="admin", expected_revision=approved["revision"],
            reason="Thử kích hoạt khi cổng live vẫn đóng.", idempotency_key="activate-disabled-0001",
        )
    assert disabled.value.code == "lifecycle_live_activation_unconfigured"

    monkeypatch.setenv("LEGAL_LIFECYCLE_ACTIVATION_ENABLED", "true")
    service.repository.drafts[created["id"]]["manifest"]["ready"] = True
    service.base_fingerprint_reader = AsyncMock(return_value="base-v2")
    service.activation_adapter = AsyncMock(
        return_value={"sql_verified": True, "collections_verified": ["fast", "expanded"]}
    )
    with pytest.raises(LifecycleError) as drifted:
        await service.activate_draft(
            created["id"], actor="user:reviewer", role="admin", expected_revision=approved["revision"],
            reason="Chặn kích hoạt khi fingerprint văn bản gốc đã đổi.", idempotency_key="activate-drift-0001",
        )
    assert drifted.value.code == "lifecycle_base_fingerprint_conflict"
    service.activation_adapter.assert_not_awaited()

    service.base_fingerprint_reader = AsyncMock(return_value="base-v1")
    activated = await service.activate_draft(
        created["id"], actor="user:reviewer", role="admin", expected_revision=approved["revision"],
        reason="Kích hoạt bằng adapter cô lập sau khi mọi kiểm đếm đạt.", idempotency_key="activate-ready-0001",
    )
    replay = await service.activate_draft(
        created["id"], actor="user:reviewer", role="admin", expected_revision=approved["revision"],
        reason="Kích hoạt bằng adapter cô lập sau khi mọi kiểm đếm đạt.", idempotency_key="activate-ready-0001",
    )
    assert replay == activated
    assert activated["activation_state"] == "active"
    service.activation_adapter.assert_awaited_once()


def _request(role: str, user_id: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/api/legal/lifecycle", "headers": []})
    request.state.user_role = role
    request.state.user_id = user_id
    request.state.username = user_id
    return request


@pytest.mark.asyncio
async def test_router_denies_non_admin_before_lifecycle_store(monkeypatch):
    store = AsyncMock()
    monkeypatch.setattr(legal_search, "_lifecycle_service", store)
    with pytest.raises(HTTPException) as denied:
        await legal_search.legal_lifecycle_capabilities(_request("officer", "user:officer"))
    assert denied.value.status_code == 403
    store.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_create_sets_etag_and_normalizes_errors(monkeypatch, service):
    monkeypatch.setattr(legal_search, "_lifecycle_service", service)
    response = Response()
    payload = legal_search.LifecycleDraftCreateRequest(
        **VALID_DRAFT, reason="Tạo hồ sơ qua hợp đồng API thử nghiệm."
    )
    created = await legal_search.legal_lifecycle_create_draft(
        payload, _request("admin", "user:editor"), response, "router-create-0001"
    )
    assert response.headers["etag"] == '"1"'
    assert "content" not in created

    with pytest.raises(HTTPException) as invalid_revision:
        await legal_search.legal_lifecycle_update_draft(
            created["id"],
            legal_search.LifecycleDraftUpdateRequest(
                title="Tiêu đề khác", reason="Thử revision header không hợp lệ."
            ),
            _request("admin", "user:editor"),
            Response(),
            "router-update-0001",
            "not-a-revision",
        )
    assert invalid_revision.value.detail["code"] == "lifecycle_if_match_invalid"
