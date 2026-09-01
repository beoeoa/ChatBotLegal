from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from api import conversational_orchestrator as orchestrator
from api import unified_chat_service
from api.models import AskRequest, AskResponse
from api.routers import search as search_router


def _message(index: int, role: str, content: str, *, status: str = "complete") -> dict:
    return {
        "id": f"m-{index}",
        "role": role,
        "content": content,
        "status": status,
    }


def test_orchestrator_flag_is_default_off_role_scoped_and_excludes_admin() -> None:
    assert not orchestrator.is_conversational_orchestrator_enabled("citizen", environ={})
    values = {
        "CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED": "true",
        "CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ROLES": "citizen,officer,admin",
    }
    assert orchestrator.is_conversational_orchestrator_enabled("citizen", environ=values)
    assert orchestrator.is_conversational_orchestrator_enabled("officer", environ=values)
    assert not orchestrator.is_conversational_orchestrator_enabled("admin", environ=values)


def test_turn_router_separates_meta_document_legal_and_out_of_scope() -> None:
    active = {"document_id": "154/2024/NĐ-CP", "title": "Nghị định 154/2024/NĐ-CP"}
    assert orchestrator.decide_conversation_turn("Tôi vừa hỏi bạn cái gì?").route == "chat_meta"
    assert orchestrator.decide_conversation_turn("t vừa hỏi cái gì").route == "chat_meta"
    assert orchestrator.decide_conversation_turn(
        "Bạn là chatbot à, bạn giúp được những việc gì?"
    ).route == "chat_meta"
    assert orchestrator.decide_conversation_turn(
        "t đang hỏi thủ tục gì ấy nhỉ"
    ).route == "chat_meta"
    assert orchestrator.decide_conversation_turn(
        "Tóm tắt ngắn ba vấn đề pháp lý gần nhất tôi đang hỏi."
    ).route == "chat_meta"
    assert orchestrator.decide_conversation_turn(
        "Trong toàn bộ phiên này tôi đã chuyển qua những lĩnh vực nào?"
    ).route == "chat_meta"
    assert orchestrator.decide_conversation_turn(
        "Đề xuất 4 câu hỏi tiếp theo hữu ích nhất, không tự trả lời thay tôi."
    ).route == "chat_meta"
    document = orchestrator.decide_conversation_turn(
        "Văn bản trên nói về cái gì?", active_document=active
    )
    assert document.route == "document_followup"
    assert document.active_document_available
    assert orchestrator.decide_conversation_turn(
        "Đăng ký tạm trú cần giấy tờ gì?"
    ).route == "legal_query"
    assert orchestrator.decide_conversation_turn("Hãy viết thơ cho tôi").route == "out_of_scope"
    assert orchestrator.decide_conversation_turn(
        "Viết cho tôi một bài thơ tình 20 câu"
    ).route == "out_of_scope"
    assert orchestrator.decide_conversation_turn(
        "Văn bản vừa nêu nói về nội dung gì?", active_document=active
    ).route == "document_followup"
    assert orchestrator.decide_conversation_turn(
        "Điều luật vừa dẫn quy định nguyên văn thế nào?", active_document=active
    ).route == "document_followup"
    # Không chắc thì fail-open vào legal query để retrieval quyết định.
    assert orchestrator.decide_conversation_turn("Giúp tôi việc này").route == "legal_query"


def test_short_facet_after_multi_topic_turn_requests_clarification_without_retrieval() -> None:
    decision = orchestrator.decide_conversation_turn(
        "Còn lệ phí thì sao?",
        history_messages=[
            _message(
                1,
                "user",
                "Tôi muốn khiếu nại quyết định phạt, đăng ký tạm trú và hỏi trợ cấp cho mẹ.",
            )
        ],
    )

    assert decision.route == "chat_meta"
    assert decision.reason_code == "ambiguous_multi_topic_followup"


def test_short_facet_after_single_topic_turn_remains_a_legal_query() -> None:
    decision = orchestrator.decide_conversation_turn(
        "Còn lệ phí thì sao?",
        history_messages=[_message(1, "user", "Đăng ký lại khai sinh cần hồ sơ gì?")],
    )

    assert decision.route == "legal_query"


def test_deterministic_followup_rewrite_uses_only_previous_user_turn() -> None:
    rewritten = orchestrator.rewrite_legal_followup_deterministic(
        "Còn thời hạn thì sao?",
        [
            _message(1, "user", "Đăng ký tạm trú tại nhà thuê cần giấy tờ gì?"),
            _message(2, "assistant", "NỘI DUNG AI KHÔNG ĐƯỢC DÙNG LÀM SỰ THẬT"),
        ],
    )

    assert rewritten.rewrite_applied
    assert "Đăng ký tạm trú tại nhà thuê" in rewritten.standalone_query
    assert "NỘI DUNG AI" not in rewritten.standalone_query
    assert rewritten.inherited_turn_ids == ("m-1",)


def test_deterministic_followup_rewrite_does_not_change_complete_question() -> None:
    question = "Đăng ký lại khai sinh cần nộp hồ sơ ở đâu?"
    rewritten = orchestrator.rewrite_legal_followup_deterministic(
        question,
        [_message(1, "user", "Trước đó tôi hỏi tách thửa đất.")],
    )

    assert not rewritten.rewrite_applied
    assert rewritten.standalone_query == question


def test_explicit_no_search_summary_is_meta_without_case_specific_router() -> None:
    decision = orchestrator.decide_conversation_turn(
        "Không tra cứu, chỉ tóm tắt nội dung đã hỏi giúp tôi.",
        history_messages=[_message(1, "user", "Tạm trú cần giấy tờ gì?")],
    )

    assert decision.route == "chat_meta"


def test_provider_fallback_summarizes_session_instead_of_returning_identity_copy() -> None:
    history = [
        _message(1, "user", "Tôi hỏi đăng ký lại khai sinh."),
        _message(2, "user", "Sau đó tôi hỏi tách thửa đất."),
        _message(3, "user", "Tôi cũng hỏi đăng ký tạm trú."),
    ]
    answer, _ = search_router._conversation_fallback_answer(
        "Trong toàn bộ phiên này tôi đã chuyển qua những lĩnh vực nào?",
        route="chat_meta",
        history_messages=history,
    )

    assert "hộ tịch" in answer
    assert "đất đai" in answer
    assert "cư trú" in answer


def test_short_conversation_keeps_full_order_and_does_not_duplicate_current_question() -> None:
    messages = [
        _message(1, "user", "Đăng ký tạm trú cần gì?"),
        _message(2, "assistant", "Cần tờ khai và giấy tờ chỗ ở hợp pháp."),
        _message(3, "user", "Còn thời hạn thì sao?"),
    ]
    packet = orchestrator.build_conversation_context_packet(
        messages,
        current_question="Còn thời hạn thì sao?",
        state={"revision": 2},
    )

    assert packet.history_mode == "full"
    assert packet.included_message_ids == ("m-1", "m-2")
    assert packet.prompt_block.index("Đăng ký tạm trú") < packet.prompt_block.index("Cần tờ khai")
    assert "Còn thời hạn thì sao?" not in packet.prompt_block


def test_long_conversation_compacts_without_slicing_messages_or_exceeding_budget() -> None:
    messages: list[dict] = []
    for index in range(50):
        role = "user" if index % 2 == 0 else "assistant"
        topic = "đăng ký tạm trú chủ nhà" if index == 4 else f"chủ đề {index}"
        messages.append(_message(index, role, f"{topic} " + ("nội dung đầy đủ " * 90)))
    messages.append(_message(50, "user", "Chủ nhà không đi cùng thì sao?"))
    environ = {
        "CHAT_CONTEXT_INPUT_TOKEN_BUDGET": "8000",
        "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE": "4000",
        "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE": "2000",
    }
    packet = orchestrator.build_conversation_context_packet(
        messages,
        current_question="Chủ nhà không đi cùng thì sao?",
        state={
            "revision": 9,
            "canonical_domain": "cu_tru_an_ninh",
            "procedure": {"name": "Đăng ký tạm trú"},
            "active_document": {
                "title": "Nghị định 154/2024/NĐ-CP",
                "article_refs": ["Điều 5"],
            },
        },
        environ=environ,
    )

    assert packet.history_mode == "compacted"
    assert packet.history_tokens <= packet.history_token_budget
    assert packet.messages_considered == 50
    assert "TÓM TẮT DETERMINISTIC PHẦN CŨ" in packet.prompt_block
    assert "MỤC NÉN HỘI THOẠI V1" in packet.prompt_block
    assert packet.compaction_item_checksum
    assert "Văn bản đang trao đổi: Nghị định 154/2024/NĐ-CP (Điều 5)" in packet.prompt_block
    for message_id in packet.included_message_ids:
        original = next(item["content"] for item in messages if item["id"] == message_id)
        assert " ".join(original.split()) in packet.prompt_block


def test_compacted_packet_labels_langgraph_llm_summary_as_non_legal_context() -> None:
    messages = [
        _message(
            index,
            "user" if index % 2 == 0 else "assistant",
            f"Lượt {index}: " + ("nội dung hội thoại dài " * 120),
        )
        for index in range(30)
    ]
    packet = orchestrator.build_conversation_context_packet(
        messages,
        current_question="Tôi đang hỏi tiếp việc gì?",
        state={
            "conversation_digest_v2": {
                "topic_summary": "Người dùng đang trao đổi về cư trú.",
                "current_goal": "Theo dõi việc đang hỏi.",
            },
            "compaction_summary_mode": "langgraph_llm",
        },
        environ={
            "CHAT_CONTEXT_INPUT_TOKEN_BUDGET": "8000",
            "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE": "4000",
            "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE": "2000",
        },
    )

    assert packet.history_mode == "compacted"
    assert "TÓM TẮT LLM DO LANGGRAPH QUẢN LÝ" in packet.prompt_block
    assert "KHÔNG PHẢI CĂN CỨ PHÁP LUẬT" in packet.prompt_block


def test_local_large_context_budget_keeps_the_entire_fifty_turn_session() -> None:
    messages = [
        _message(
            index,
            "user" if index % 2 == 0 else "assistant",
            f"Lượt {index}: " + ("nội dung hội thoại pháp luật " * 35),
        )
        for index in range(100)
    ]
    packet = orchestrator.build_conversation_context_packet(
        messages,
        current_question="Tóm tắt toàn bộ phiên này.",
        environ={
            "CHAT_CONTEXT_INPUT_TOKEN_BUDGET": "65536",
            "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE": "12000",
            "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE": "4096",
        },
    )

    assert packet.history_mode == "full"
    assert packet.messages_considered == 100
    assert packet.messages_included == 100
    assert packet.older_messages_compacted == 0


def test_conversational_prompts_separate_history_from_legal_evidence_and_hide_reasoning() -> None:
    packet = orchestrator.build_conversation_context_packet(
        [_message(1, "user", "Xin chào")],
        current_question="Bạn là ai?",
    )
    prompt = orchestrator.build_conversational_prompt(
        question="Bạn là ai?",
        role="officer",
        route="chat_meta",
        context_packet=packet,
    )
    assert "LỊCH SỬ HỘI THOẠI — KHÔNG PHẢI CĂN CỨ PHÁP LUẬT" in prompt
    assert "không xuất chuỗi suy luận" in prompt
    assert "Thưa anh/chị cán bộ" in prompt


def test_related_documents_are_backend_projected_deduplicated_and_bounded() -> None:
    citations = [
        {
            "document_id": str(index),
            "document_title": f"Văn bản {index}",
            "law_number": f"{index}/2026/NĐ-CP",
            "article_number": str(index),
            "source_url": f"https://example.test/{index}",
        }
        for index in range(1, 5)
    ]
    citations.insert(1, dict(citations[0]))
    projected = orchestrator.project_related_documents(
        citations,
        active_document_id="1",
    )
    assert len(projected) == 3
    assert projected[0]["active"] is True
    assert projected[0]["article_refs"] == ["Điều 1"]
    assert len({item["document_id"] for item in projected}) == 3
    explicit = orchestrator.resolve_explicit_document_reference(
        "Tôi muốn hỏi 2/2026/NĐ-CP quy định gì?",
        projected,
    )
    assert explicit is not None and explicit["document_id"] == "2"
    assert orchestrator.matches_document_reference(
        {"document_id": 22, "law_number": "2/2026/NĐ-CP"},
        projected[1],
    )
    assert not orchestrator.matches_document_reference(
        {"document_id": 99, "law_number": "99/2026/NĐ-CP"},
        projected[1],
    )


def _request() -> Request:
    return Request({"type": "http", "headers": []})


@pytest.mark.asyncio
async def test_chat_meta_skips_legal_retrieval_pipeline_and_persists_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED", "true")
    monkeypatch.setenv("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ROLES", "citizen")
    decision = orchestrator.decide_conversation_turn("Tôi vừa hỏi bạn cái gì?")
    monkeypatch.setattr(
        search_router,
        "_conversation_turn_runtime_context",
        AsyncMock(return_value=(decision, None, [])),
    )
    conversation_call = AsyncMock(
        return_value=AskResponse(
            question="Tôi vừa hỏi bạn cái gì?",
            answer="Câu hỏi trước đó.",
            conversation_route="chat_meta",
        )
    )
    monkeypatch.setattr(search_router, "_execute_nonlegal_conversation_turn", conversation_call)
    legal_call = AsyncMock(side_effect=AssertionError("chat_meta must not enter legal retrieval"))
    monkeypatch.setattr(unified_chat_service, "run_legal_answer_pipeline_v3", legal_call)
    persist = AsyncMock()
    monkeypatch.setattr(search_router, "_persist_conversation_answer", persist)
    monkeypatch.setattr(
        search_router, "_public_model_snapshot", AsyncMock(return_value={})
    )

    response = await search_router._execute_ask_simple(
        AskRequest(question="Tôi vừa hỏi bạn cái gì?", role="citizen"),
        _request(),
    )

    assert response.conversation_route == "chat_meta"
    conversation_call.assert_awaited_once()
    legal_call.assert_not_awaited()
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_legal_route_enters_canonical_pipeline_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ENABLED", "true")
    monkeypatch.setenv("CHAT_CONVERSATIONAL_ORCHESTRATOR_V1_ROLES", "citizen,officer")
    question = "Hồ sơ đăng ký tạm trú cần kiểm tra gì?"
    decision = orchestrator.decide_conversation_turn(question)
    monkeypatch.setattr(
        search_router,
        "_conversation_turn_runtime_context",
        AsyncMock(return_value=(decision, None, [])),
    )
    legal_call = AsyncMock(
        return_value=AskResponse(
            question=question,
            answer="Nội dung nghiệp vụ.",
            citations=[],
        )
    )
    monkeypatch.setattr(unified_chat_service, "run_legal_answer_pipeline_v3", legal_call)
    persist = AsyncMock()
    monkeypatch.setattr(search_router, "_persist_conversation_answer", persist)
    monkeypatch.setattr(
        search_router, "_public_model_snapshot", AsyncMock(return_value={})
    )

    response = await search_router._execute_ask_simple(
        AskRequest(question=question, role="officer"),
        _request(),
    )

    assert response.conversation_route == "legal_query"
    legal_call.assert_awaited_once()
    persist.assert_awaited_once()
