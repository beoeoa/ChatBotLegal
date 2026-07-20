import httpx
import pytest

from api.legal_search_client import LegalSearchClient


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
