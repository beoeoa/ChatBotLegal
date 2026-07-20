"""Feature 005 requirements: evidence is bound to one request and issue."""

from unittest.mock import patch

import pytest

from api.legal_section_grounding import LegalIssue, evidence_matches_current_issue
from api.routers.search import _section_retrieval_kwargs, stream_ask_response
from open_notebook.graphs.ask import _legal_search
from scripts.legal_search_server import SearchRequest, bind_request_provenance


def test_retrieval_request_and_results_carry_opaque_issue_provenance():
    request = SearchRequest(
        query="Thủ tục đất đai",
        request_id="request-current",
        issue_id="issue-land",
        issue_domain="dat_dai_moi_truong",
    )

    result = bind_request_provenance({"chunk_id": 42}, request)

    assert result["request_id"] == "request-current"
    assert result["issue_id"] == "issue-land"
    assert result["issue_domain"] == "dat_dai_moi_truong"
    assert request.model_dump(exclude_none=True)["request_id"] == "request-current"


def test_prior_request_and_other_issue_evidence_never_match_current_issue():
    issue = LegalIssue(
        issue_id="issue-land",
        request_id="request-current",
        text="Hồ sơ đất đai",
        domain="dat_dai_moi_truong",
        intent="dossier",
    )
    old_request = {
        "request_id": "request-old",
        "issue_id": "issue-labour",
        "domain_slug": "lao_dong",
    }
    other_issue = {
        "request_id": "request-current",
        "issue_id": "issue-authority",
        "domain_slug": "dat_dai_moi_truong",
    }

    assert evidence_matches_current_issue(old_request, issue) is False
    assert evidence_matches_current_issue(other_issue, issue) is False


def test_router_keeps_history_out_of_retrieval_even_when_section_flag_is_off(
    monkeypatch,
):
    monkeypatch.setenv("LEGAL_SECTION_GROUNDING_ENABLED", "false")
    current_question = "Hồ sơ cấp giấy chứng nhận đất đai?"
    legacy_kwargs = _section_retrieval_kwargs(
        current_question=current_question,
        request_id="request-current",
        selected_domain="dat_dai_xay_dung",
    )

    assert legacy_kwargs == {"retrieval_question": current_question}
    assert "request_id" not in legacy_kwargs
    assert "issue_id" not in legacy_kwargs
    assert "issue_domain" not in legacy_kwargs
    assert "Lịch sử trò chuyện" not in legacy_kwargs["retrieval_question"]

    monkeypatch.setenv("LEGAL_SECTION_GROUNDING_ENABLED", "true")
    kwargs = _section_retrieval_kwargs(
        current_question="Hồ sơ cấp giấy chứng nhận đất đai?",
        request_id="request-current",
        selected_domain="dat_dai_xay_dung",
    )

    assert kwargs["retrieval_question"] == "Hồ sơ cấp giấy chứng nhận đất đai?"
    assert kwargs["request_id"] == "request-current"
    assert kwargs["issue_id"] == "issue-request-current"
    assert "Lịch sử trò chuyện" not in kwargs["retrieval_question"]


@pytest.mark.asyncio
async def test_stream_graph_receives_current_retrieval_query_without_section_provenance(
    monkeypatch,
):
    captured = {}

    class _Graph:
        async def astream(self, *, input, **_kwargs):
            captured.update(input)
            yield {"write_final_answer": {"final_answer": "Đã kiểm tra."}}

    class _Model:
        id = "model-test"

    monkeypatch.setattr("api.routers.search.ask_graph", _Graph())
    current_question = "Tôi cần giấy tờ khai sinh nào?"
    history_prompt = (
        "Lịch sử trò chuyện trước đó:\nCâu hỏi đất đai trước đó\n---\n"
        f"Câu hỏi hiện tại:\n{current_question}"
    )

    events = []
    async for event in stream_ask_response(
        history_prompt,
        "citizen",
        _Model(),
        _Model(),
        _Model(),
        retrieval_question=current_question,
    ):
        events.append(event)

    assert events
    assert captured["question"] == history_prompt
    assert captured["retrieval_question"] == current_question
    assert "request_id" not in captured
    assert "issue_id" not in captured


@pytest.mark.asyncio
async def test_legal_search_sends_only_current_issue_text_and_preserves_provenance():
    class _Client:
        base_url = "http://127.0.0.1:8765"

        def __init__(self):
            self.payloads = []

        async def search(self, payload, **_kwargs):
            self.payloads.append(dict(payload))
            return {
                "results": [
                    {
                        "chunk_id": 42,
                        "request_id": payload.get("request_id"),
                        "issue_id": payload.get("issue_id"),
                        "issue_domain": payload.get("issue_domain"),
                        "law_number": "31/2024/QH15",
                        "document_status": "active",
                        "source_url": "https://vbpl.vn/42",
                    }
                ]
            }

    client = _Client()
    with patch("open_notebook.graphs.ask.get_legal_search_client", return_value=client):
        results = await _legal_search(
            "Hồ sơ cấp giấy chứng nhận đất đai?",
            domain="dat_dai_xay_dung",
            request_id="request-current",
            issue_id="issue-land",
            issue_domain="land",
        )

    assert client.payloads
    assert all(
        payload["query"] == "Hồ sơ cấp giấy chứng nhận đất đai?"
        for payload in client.payloads
    )
    assert results[0]["request_id"] == "request-current"
    assert results[0]["issue_id"] == "issue-land"
