from __future__ import annotations

from pathlib import Path

import pytest

from api.routers import search as search_router


@pytest.mark.asyncio
async def test_api_search_helper_uses_shared_validity_guarded_client(monkeypatch):
    class GuardedClient:
        def __init__(self):
            self.calls = []

        async def search(self, payload):
            self.calls.append(payload)
            return {
                "results": [
                    {
                        "chunk_id": "active-1",
                        "document_id": "1",
                        "law_number": "31/2024/QH15",
                        "document_title": "Luật Đất đai",
                        "document_status": "active",
                        "validity_sync": {
                            "status": "active",
                            "serving_action": "allow",
                        },
                    }
                ],
                "validity_sync": {
                    "filtered_count": 1,
                    "filtered_reasons": {"expired": 1},
                },
            }

    guarded = GuardedClient()
    monkeypatch.setattr(search_router, "get_legal_search_client", lambda: guarded)

    results = await search_router._search_legal_documents(
        query="đăng ký đất đai",
        limit=5,
        domain=None,
    )

    assert len(guarded.calls) == 1
    assert guarded.calls[0]["query"] == "đăng ký đất đai"
    assert results[0]["law_number"] == "31/2024/QH15"
    assert results[0]["validity_sync"]["status"] == "active"


def test_existing_api_lifecycle_owns_one_validity_scheduler_task():
    source = (Path(__file__).resolve().parents[1] / "api" / "main.py").read_text(
        encoding="utf-8"
    )

    assert source.count("asyncio.create_task(legal_effectivity_scheduler_loop())") == 1
    assert "effectivity_task.cancel()" in source
    assert "await effectivity_task" in source
