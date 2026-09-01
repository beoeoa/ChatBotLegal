"""Unified router v1: rule-first serving decision (no answer LLM)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from api import conversational_orchestrator as orchestrator
from api import unified_router
from api.legal_answer_router import route_legal_answer
from api.models import AskRequest, AskResponse
from api.routers import search as search_router


def test_smalltalk_is_out_of_scope() -> None:
    decision = unified_router.decide_router("Kể chuyện cười đi", role="citizen")
    assert decision.conversation_route == "out_of_scope"
    assert decision.source == "rule"
    assert decision.legal_route is None


def test_gratitude_is_chat_meta() -> None:
    decision = unified_router.decide_router("cảm ơn nhiều nhé", role="citizen")
    assert decision.conversation_route == "chat_meta"
    assert decision.source == "rule"


def test_fail_open_help_request_stays_legal_query() -> None:
    decision = unified_router.decide_router("Giúp tôi việc này", role="citizen")
    assert decision.conversation_route == "legal_query"
    assert decision.source == "fail_open"


def test_exact_article_is_independent_of_active_document() -> None:
    active = {"document_id": "154/2024/NĐ-CP", "title": "Nghị định 154/2024/NĐ-CP"}
    decision = unified_router.decide_router(
        "Điều 13 Luật 60/2014/QH13 quy định thế nào?",
        role="citizen",
        active_document=active,
    )
    assert decision.conversation_route == "legal_query"
    assert decision.legal_route == "exact_article"
    assert decision.source == "rule"


def test_officer_land_question_keeps_dat_dai_domain() -> None:
    decision = unified_router.decide_router(
        "Tôi mua nhà đất đã có Giấy chứng nhận tại Hải Phòng, cần làm các bước sang tên nào?",
        role="officer",
        allowed_domains=["ho_tich_chung_thuc"],
    )
    assert decision.conversation_route == "legal_query"
    # Must not be rewritten to the officer account/allow-list domain.
    assert decision.canonical_domain != "ho_tich_chung_thuc"
    assert decision.canonical_domain in {"dat_dai_xay_dung", "unknown"}
    if decision.canonical_domain == "dat_dai_xay_dung":
        allowed, message = unified_router.check_officer_domain_acl(
            decision, ["ho_tich_chung_thuc"]
        )
        assert allowed is False
        assert message


def test_document_followup_without_active_doc_skips_legal_planner(monkeypatch) -> None:
    called = {"n": 0}
    original = route_legal_answer

    def wrapped(*args, **kwargs):
        called["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr("api.unified_router.route_legal_answer", wrapped)
    decision = unified_router.decide_router(
        "Văn bản trên nói về cái gì?",
        role="citizen",
        active_document=None,
    )
    assert decision.conversation_route == "document_followup"
    assert not decision.active_document_available
    assert called["n"] == 0


def _request() -> Request:
    return Request({"type": "http", "headers": []})


@pytest.mark.asyncio
async def test_meta_skips_retrieval_when_unified_on_and_orchestrator_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_UNIFIED_ROUTER_V1_ENABLED", "true")
    monkeypatch.setenv("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED", "false")
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ENABLED", "false")
    turn = orchestrator.decide_conversation_turn("Tôi vừa hỏi bạn cái gì?")
    monkeypatch.setattr(
        search_router,
        "_conversation_turn_runtime_context",
        AsyncMock(return_value=(turn, None, [], 0)),
    )
    monkeypatch.setattr(search_router, "get_request_role", lambda _request: "citizen")
    monkeypatch.setattr(search_router, "get_request_user_id", lambda _request: "user-1")
    conversation_call = AsyncMock(
        return_value=AskResponse(
            question="Tôi vừa hỏi bạn cái gì?",
            answer="Câu hỏi trước đó.",
            conversation_route="chat_meta",
        )
    )
    monkeypatch.setattr(
        search_router, "_execute_nonlegal_conversation_turn", conversation_call
    )
    legal_call = AsyncMock(side_effect=AssertionError("meta must not retrieve"))
    monkeypatch.setattr(
        "api.unified_chat_service.run_legal_answer_pipeline_v3", legal_call
    )
    persist = AsyncMock()
    monkeypatch.setattr(search_router, "_persist_conversation_answer", persist)
    monkeypatch.setattr(
        search_router,
        "_public_model_snapshot",
        AsyncMock(return_value={}),
    )

    response = await search_router._execute_ask_simple(
        AskRequest(question="Tôi vừa hỏi bạn cái gì?", role="citizen"),
        _request(),
    )

    assert response.conversation_route == "chat_meta"
    conversation_call.assert_awaited_once()
    legal_call.assert_not_awaited()

@pytest.mark.parametrize(
    "question",
    [
        "31/2024/QH15 quy định những gì",
        "Giải thích 123/2015/NĐ-CP",
        "Nội dung 20/2021/NĐ-CP là gì",
        "20/2021/NĐ-CP quy định những gì",
        "Điều khoản chính trong 20/2021/NĐ-CP",
        "Tóm tắt luật đất đai 31/2024/QH15",
        "176/2025/NĐ-CP quy định những gì",
        "Giải thích 176/2025/NĐ-CP",
    ],
)
def test_law_number_is_rule_legal_query_before_deixis_and_llm(question: str) -> None:
    assert orchestrator._has_vietnamese_law_number(question)
    assert orchestrator._is_standalone_legal_identifier_query(question)
    assert orchestrator._explicit_conversation_route(question) == "legal_query"
    decision = unified_router.decide_router(
        question,
        role="citizen",
        active_document={"document_id": "old-doc", "title": "Văn bản trên"},
    )
    assert decision.conversation_route == "legal_query"
    assert decision.source == "rule"
    assert decision.reason == "standalone_legal_identifier"
    assert not unified_router.should_invoke_router_llm(question, decision)


def test_short_active_document_followup_is_not_a_new_legal_query() -> None:
    active = {"document_id": "31/2024/QH15", "title": "Luật Đất đai 2024"}
    decision = unified_router.decide_router(
        "cần giấy tờ gì",
        role="citizen",
        active_document=active,
    )
    assert decision.conversation_route == "document_followup"
    assert decision.source == "rule"
    assert decision.active_document_required
