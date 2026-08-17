from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request

from api.routers import legal_search


def _request(role: str = "admin", user_id: str = "user:admin") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/legal/management",
            "headers": [],
        }
    )
    request.state.user_role = role
    request.state.user_id = user_id
    request.state.username = role
    return request


@pytest.mark.asyncio
async def test_management_endpoints_reject_non_admin_before_store_reads(monkeypatch):
    store_read = AsyncMock()
    monkeypatch.setattr(legal_search, "_request", store_read)

    with pytest.raises(HTTPException) as officer_denied:
        await legal_search.legal_management_summary(_request(role="officer"))
    with pytest.raises(HTTPException) as citizen_denied:
        await legal_search.legal_management_document("42", _request(role="citizen"))

    assert officer_denied.value.status_code == 403
    assert citizen_denied.value.status_code == 403
    store_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_management_list_is_metadata_only_and_forwards_bounded_filters(monkeypatch):
    async def internal_request(method, path, **kwargs):
        assert method == "GET"
        assert path == "/management/documents"
        assert kwargs["params"]["q"] == "hộ tịch"
        assert kwargs["params"]["stored_status"] == "active"
        assert kwargs["params"]["limit"] == 25
        assert kwargs["params"]["offset"] == 50
        return {
            "items": [
                {
                    "doc_id": 42,
                    "document_title": "Nghị định thử nghiệm",
                    "law_number": "42/2026/NĐ-CP",
                    "source_url": "https://vbpl.vn/example",
                    "stored_status": "active",
                    "as_of_status": "active",
                    "retrieval_tier": "core",
                    "article_count": 2,
                    "chunk_count": 4,
                    "quality_flags": [],
                    "content": "must-not-leak",
                    "chunks": [{"content": "must-not-leak"}],
                    "raw_exception": "must-not-leak",
                }
            ],
            "total": 1,
            "limit": 25,
            "offset": 50,
            "as_of": "2026-08-09",
        }

    monkeypatch.setattr(legal_search, "_request", internal_request)
    result = await legal_search.legal_management_documents(
        _request(),
        q="hộ tịch",
        document_type=None,
        issuing_agency=None,
        scope=None,
        domain=None,
        stored_status="active",
        validity_status=None,
        tier="all",
        data_quality=None,
        source_presence="all",
        issued_from=None,
        issued_to=None,
        effective_from=None,
        effective_to=None,
        expired_from=None,
        expired_to=None,
        as_of="2026-08-09",
        limit=25,
        offset=50,
        sort_by="effective_date",
        sort_order="desc",
    )

    item = result["items"][0]
    assert item["source_url"] == "https://vbpl.vn/example"
    assert "content" not in item
    assert "chunks" not in item
    assert "raw_exception" not in item


@pytest.mark.asyncio
async def test_management_list_uses_same_expired_projection_as_current_retrieval(
    monkeypatch,
):
    async def internal_request(method, path, **kwargs):
        del method, kwargs
        assert path == "/management/documents"
        return {
            "items": [
                {
                    "doc_id": 31285,
                    "document_title": "VÄƒn báº£n lá»‹ch sá»­",
                    "law_number": "96/2014/TT-BQP",
                    "stored_status": "active",
                    "as_of_status": "active",
                    "source_url": "https://vbpl.vn/van-ban/chi-tiet/example",
                }
            ],
            "total": 1,
            "limit": 30,
            "offset": 0,
            "as_of": "2026-08-10",
        }

    monkeypatch.setattr(legal_search, "_request", internal_request)
    monkeypatch.setattr(
        legal_search.default_snapshot_cache,
        "load",
        lambda: {
            "schema_version": "legal-validity-serving-v1",
            "mode": "protect",
            "document_ids": {"31285": "96/2014/TT-BQP"},
            "documents": {
                "96/2014/TT-BQP": {
                    "document_id": "31285",
                    "normalized_status": "expired",
                    "serving_action": "historical_only",
                    "source_url": "https://vbpl.vn/van-ban/chi-tiet/example",
                    "verified_at": "2026-08-09T11:37:38+00:00",
                    "effective_from": "2014-08-25",
                    "effective_to": "2016-12-20",
                    "affected_provisions": [],
                    "identity_status": "exact",
                    "evidence_status": "sufficient",
                }
            },
        },
    )

    result = await legal_search.legal_management_documents(
        _request(),
        q="96/2014/TT-BQP",
        document_type=None,
        issuing_agency=None,
        scope=None,
        domain=None,
        stored_status=None,
        validity_status=None,
        tier="all",
        data_quality=None,
        source_presence="all",
        issued_from=None,
        issued_to=None,
        effective_from=None,
        effective_to=None,
        expired_from=None,
        expired_to=None,
        as_of="2026-08-10",
        limit=30,
        offset=0,
        sort_by="effective_date",
        sort_order="desc",
    )

    item = result["items"][0]
    assert item["stored_status"] == "active"
    assert item["validity_status"] == "expired"
    assert item["serving_status"] == "historical_only"
    assert item["current_answer_eligible"] is False
    assert item["historical_lookup_allowed"] is True
    assert item["validity_sync"]["display_label"] == (
        "H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
    )


@pytest.mark.asyncio
async def test_management_required_read_does_not_leak_internal_error(monkeypatch):
    monkeypatch.setattr(
        legal_search,
        "_request",
        AsyncMock(side_effect=HTTPException(status_code=502, detail="secret upstream trace")),
    )

    with pytest.raises(HTTPException) as failed:
        await legal_search.legal_management_documents(
            _request(),
            q="",
            document_type=None,
            issuing_agency=None,
            scope=None,
            domain=None,
            stored_status=None,
            validity_status=None,
            tier="all",
            data_quality=None,
            source_presence="all",
            issued_from=None,
            issued_to=None,
            effective_from=None,
            effective_to=None,
            expired_from=None,
            expired_to=None,
            as_of="2026-08-09",
            limit=30,
            offset=0,
            sort_by="effective_date",
            sort_order="desc",
        )

    assert failed.value.status_code == 503
    assert "secret upstream trace" not in str(failed.value.detail)


@pytest.mark.asyncio
async def test_management_summary_keeps_optional_failure_explicit(monkeypatch):
    monkeypatch.setattr(
        legal_search,
        "_request",
        AsyncMock(
            return_value={
                "observed_at": "2026-08-09T00:00:00+00:00",
                "documents": {"total": 1, "active": 1},
                "structure": {"articles": 2, "chunks": 2},
                "tiers": {"core": 1, "expanded": 0},
                "vectors": {
                    "status": "unavailable",
                    "reason_code": "vector_store_not_ready",
                    "message": "Kho vector chưa sẵn sàng.",
                },
            }
        ),
    )

    async def failed_operations():
        raise RuntimeError("secret database exception")

    class ValidityService:
        async def status(self):
            return {"status": "healthy", "counts": {"active": 1}, "coverage": {}}

    monkeypatch.setattr(legal_search.LegalCrawlService, "summary", failed_operations)
    monkeypatch.setattr(
        legal_search, "get_legal_validity_sync_service", lambda: ValidityService()
    )

    result = await legal_search.legal_management_summary(_request())

    assert result["documents"]["total"] == 1
    assert result["operations"]["status"] == "unavailable"
    assert result["operations"]["reason_code"] == "operations_store_unavailable"
    assert "secret database exception" not in str(result)
    assert result["vectors"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_management_detail_rejects_identifier_tampering_and_redacts_audit(monkeypatch):
    internal_request = AsyncMock(
        return_value={
            "document": {
                "doc_id": 42,
                "document_title": "Nghị định thử nghiệm",
                "source_url": "https://vbpl.vn/example",
                "stored_status": "active",
            },
            "structure": {"article_count": 2, "chunk_count": 4},
            "relationships": {"status": "unavailable", "reason_code": "table_missing"},
            "vectors": {"status": "available", "expected": 4},
            "versions": {"status": "unavailable", "reason_code": "schema_not_approved"},
            "internal_token": "must-not-leak",
        }
    )

    class Registry:
        async def document_timeline(self, document_id):
            assert document_id == "42"
            return {"document_id": "42", "observations": [], "events": [], "decisions": []}

    async def audits(**kwargs):
        assert kwargs["resource_type"] == "legal_document"
        assert kwargs["resource_id"] == "42"
        return [
            {
                "id": "audit:1",
                "actor_user": "user:admin",
                "actor_role": "admin",
                "action": "admin.legal_validity.vector_cleanup",
                "entity_type": "legal_document",
                "entity_id": "42",
                "details": {"reason": "Đã kiểm tra bằng chứng", "token": "must-not-leak"},
                "ip_address": "127.0.0.1",
                "user_agent": "secret-agent",
                "created": "2026-08-09T00:00:00Z",
            }
        ]

    monkeypatch.setattr(legal_search, "_request", internal_request)
    monkeypatch.setattr(legal_search, "default_registry", Registry())
    monkeypatch.setattr(legal_search, "list_audit_logs", audits)

    result = await legal_search.legal_management_document("42", _request())

    assert result["document"]["source_url"] == "https://vbpl.vn/example"
    assert "internal_token" not in result
    assert result["audit"]["items"][0]["reason"] == "Đã kiểm tra bằng chứng"
    assert "ip_address" not in result["audit"]["items"][0]
    assert "user_agent" not in result["audit"]["items"][0]
    assert "token" not in result["audit"]["items"][0]

    with pytest.raises(HTTPException) as tampered:
        await legal_search.legal_management_document(
            "42; DELETE legal_documents", _request()
        )
    assert tampered.value.status_code == 400
    internal_request.assert_awaited_once()


def test_management_projection_helpers_are_deterministic():
    from scripts.legal_search_server import (
        _management_as_of_status,
        _management_quality_flags,
        _vector_membership_projection,
    )

    assert (
        _management_as_of_status(
            {
                "stored_status": "active",
                "effective_date": date(2026, 9, 1),
                "expired_date": None,
            },
            date(2026, 8, 9),
        )
        == "not_yet_effective"
    )
    assert (
        _management_as_of_status(
            {
                "stored_status": "active",
                "effective_date": date(2025, 1, 1),
                "expired_date": date(2026, 1, 1),
            },
            date(2026, 8, 9),
        )
        == "expired"
    )
    assert _management_quality_flags(
        {
            "document_title": "",
            "law_number": None,
            "issuing_agency": None,
            "source_url": None,
            "chunk_count": 0,
            "stored_status": "legacy-mystery",
        }
    ) == [
        "missing_title",
        "missing_law_number",
        "missing_issuing_agency",
        "missing_source_url",
        "zero_chunks",
        "unknown_status",
    ]
    assert _management_quality_flags(
        {
            "document_title": "Chỉ thị thử nghiệm",
            "law_number": "6-TS/CT",
            "issuing_agency": "Bộ Thuỷ sản",
            "source_url": "https://vbpl.vn/example",
            "chunk_count": 4,
            "stored_status": "active",
            "issued_date": date(1984, 5, 9),
            "effective_date": date(2984, 5, 24),
        }
    ) == ["implausible_effective_date_gap"]

    class Collection:
        def __init__(self, ids):
            self.ids = ids

        def get(self, *, ids, include):
            assert include == []
            return {"ids": [item for item in ids if item in self.ids]}

    projection = _vector_membership_projection(
        {"fast": Collection({"chunk-1"}), "expanded": Collection({"chunk-1", "chunk-2"})},
        [1, 2],
    )
    assert projection["status"] == "available"
    assert projection["collections"]["fast"] == {"present": 1, "missing": 1}
    assert projection["collections"]["expanded"] == {"present": 2, "missing": 0}


def test_management_routes_are_get_only():
    management_routes = [
        route
        for route in legal_search.router.routes
        if route.path.startswith("/legal/management")
    ]
    assert management_routes
    assert all(route.methods == {"GET"} for route in management_routes)
