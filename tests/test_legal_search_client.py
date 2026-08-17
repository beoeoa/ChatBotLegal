from datetime import datetime, timezone

import httpx
import pytest

from api.legal_search_client import LegalSearchClient


def _validity_snapshot():
    verified = datetime(2026, 8, 8, tzinfo=timezone.utc).isoformat()
    return {
        "schema_version": "legal-validity-serving-v1",
        "generated_at": verified,
        "last_success_at": verified,
        "mode": "protect",
        "coverage": {"eligible": 2, "observed": 2, "fresh": 2},
        "document_ids": {},
        "documents": {
            "31/2024/QH15": {
                "document_id": "1",
                "normalized_status": "active",
                "source_url": "https://vbpl.vn/active",
                "verified_at": verified,
                "effective_from": "2024-08-01",
                "effective_to": None,
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            },
            "10/2020/NĐ-CP": {
                "document_id": "2",
                "normalized_status": "expired",
                "source_url": "https://vbpl.vn/expired",
                "verified_at": verified,
                "effective_from": "2020-01-01",
                "effective_to": "2026-08-01",
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            },
        },
    }


@pytest.mark.asyncio
async def test_reuses_one_http_client_for_core_and_expanded_requests():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [], "trace": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(base_url="http://retrieval", client=http)
        await client.search({"query": "q", "retrieval_tier": "core"})
        await client.search({"query": "q", "retrieval_tier": "expanded"})

    assert len(requests) == 2
    assert requests[0].url == requests[1].url


@pytest.mark.asyncio
async def test_round_robins_across_configured_retrieval_replicas():
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(
            base_urls=("http://retrieval-a:8765", "http://retrieval-b:8765"),
            client=http,
        )
        for index in range(4):
            await client.search({"query": f"q-{index}"})

    assert hosts == ["retrieval-a", "retrieval-b", "retrieval-a", "retrieval-b"]


@pytest.mark.asyncio
async def test_read_only_search_fails_over_to_next_retrieval_replica():
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(str(request.url.host))
        if request.url.host == "retrieval-a":
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json={"results": [{"document_id": "1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(
            base_urls=("http://retrieval-a:8765", "http://retrieval-b:8765"),
            client=http,
            validity_snapshot_loader=lambda: None,
        )
        result = await client.search({"query": "q"})

    assert hosts == ["retrieval-a", "retrieval-b"]
    assert result["results"][0]["document_id"] == "1"


def test_reads_retrieval_replica_urls_from_environment(monkeypatch):
    monkeypatch.setenv(
        "LEGAL_SEARCH_URLS",
        "http://retrieval-a:8765, http://retrieval-b:8765",
    )
    client = LegalSearchClient()

    assert client.base_urls == (
        "http://retrieval-a:8765",
        "http://retrieval-b:8765",
    )


@pytest.mark.asyncio
async def test_legacy_422_retry_removes_only_optional_fields():
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(422, json={"detail": "old contract"})
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(base_url="http://retrieval", client=http)
        result = await client.search(
            {
                "query": "q",
                "domain": "ho_tich_chung_thuc",
                "scope_filter": None,
                "retrieval_tier": "core",
            }
        )

    assert result == {"results": []}
    assert payloads[1] == {"query": "q", "domain": "ho_tich_chung_thuc"}


@pytest.mark.asyncio
async def test_search_applies_validity_overlay_before_returning_results():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"law_number": "31/2024/QH15", "document_id": "1"},
                    {"law_number": "10/2020/NĐ-CP", "document_id": "2"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(
            base_url="http://retrieval",
            client=http,
            validity_snapshot_loader=_validity_snapshot,
        )
        result = await client.search({"query": "q", "as_of": "2026-08-08"})

    assert [item["law_number"] for item in result["results"]] == ["31/2024/QH15"]
    assert result["results"][0]["validity_sync"]["status"] == "active"
    assert result["validity_sync"]["filtered_reasons"] == {"expired": 1}


@pytest.mark.asyncio
async def test_search_batch_filters_issue_and_context_results_consistently():
    def handler(request: httpx.Request) -> httpx.Response:
        expired = {"law_number": "10/2020/NĐ-CP", "document_id": "2"}
        active = {"law_number": "31/2024/QH15", "document_id": "1"}
        return httpx.Response(
            200,
            json={
                "issues": [{"issue_id": "a", "results": [active, expired]}],
                "context_results": [active, expired],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = LegalSearchClient(
            base_url="http://retrieval",
            client=http,
            validity_snapshot_loader=_validity_snapshot,
        )
        result = await client.search_batch(
            {"as_of": "2026-08-08", "issues": [{"issue_id": "a", "query": "q"}]}
        )

    assert len(result["issues"][0]["results"]) == 1
    assert len(result["context_results"]) == 1
    assert result["context_results"][0]["law_number"] == "31/2024/QH15"
