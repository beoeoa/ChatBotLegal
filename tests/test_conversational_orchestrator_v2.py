from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from api import chat_memory_service as memory
from api import conversational_orchestrator as orchestrator
from api.legal_structured_answer import (
    build_direct_markdown_answer_prompt,
    build_qwen_conversational_answer_prompt,
)
from api.models import AskRequest
from api.routers import search as search_router


def _message(index: int, role: str, content: str) -> dict:
    return {
        "id": f"m-{index}",
        "role": role,
        "content": content,
        "status": "complete",
    }


@pytest.mark.parametrize(
    "question",
    [
        "Theo Điều 7 của văn bản 60/2014/QH13, quy định cụ thể liên quan là gì?",
        "Văn bản 60/2014/QH13 tại Điều 7 có nội dung cần áp dụng như thế nào?",
    ],
)
def test_explicit_law_and_article_is_always_an_independent_legal_query(
    question: str,
) -> None:
    assert orchestrator._is_standalone_legal_identifier_query(question)
    decision = orchestrator.deterministic_conversation_intent_v2(
        question,
        role="citizen",
        history_messages=[
            _message(1, "user", "Văn bản trước nói về đất đai."),
            _message(2, "assistant", "Nội dung trước chưa được xác minh."),
        ],
        active_document={
            "document_id": "old-document",
            "law_number": "31/2024/QH15",
        },
    )

    assert decision.route == "legal_query"
    assert decision.active_document_id is None


def test_v2_flags_are_default_off_role_scoped_and_exclude_admin() -> None:
    assert not orchestrator.is_llm_router_v2_enabled("citizen", environ={})
    values = {
        "CHAT_LLM_ROUTER_V2_ENABLED": "true",
        "CHAT_LLM_ROUTER_V2_ROLES": "citizen,officer,admin",
        "CHAT_CONTEXT_COMPACTION_V2_ENABLED": "true",
        "CHAT_CONTEXT_COMPACTION_V2_ROLES": "citizen,officer,admin",
    }
    assert orchestrator.is_llm_router_v2_enabled("citizen", environ=values)
    assert orchestrator.is_llm_router_v2_enabled("officer", environ=values)
    assert not orchestrator.is_llm_router_v2_enabled("admin", environ=values)
    assert orchestrator.is_context_compaction_v2_enabled("citizen", environ=values)
    assert not orchestrator.is_context_compaction_v2_enabled("admin", environ=values)


def test_context_budget_profiles_are_bounded_per_generation_model() -> None:
    oversized = {
        "CHAT_CONTEXT_INPUT_TOKEN_BUDGET": "65536",
        "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE": "12000",
        "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE": "4096",
    }

    qwen25 = orchestrator.context_budget_environ_v2(
        model_name="qwen2.5:3b", environ=oversized
    )
    qwen35 = orchestrator.context_budget_environ_v2(
        model_name="qwen3.5:4b", environ=oversized
    )
    deepseek = orchestrator.context_budget_environ_v2(
        model_name="deepseek-v4-flash", environ=oversized
    )

    assert qwen25["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] == "10000"
    assert qwen25["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] == "1536"
    assert qwen35["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] == "12000"
    assert qwen35["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] == "2048"
    assert deepseek["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] == "20000"
    assert deepseek["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] == "4096"
    assert oversized["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] == "65536"


def test_v3_routing_flag_is_default_off_and_role_scoped() -> None:
    assert not orchestrator.is_routing_memory_v3_enabled("citizen", environ={})
    values = {
        "CHAT_ROUTING_MEMORY_V3_ENABLED": "true",
        "CHAT_ROUTING_MEMORY_V3_ROLES": "citizen,officer,admin",
    }
    assert orchestrator.is_routing_memory_v3_enabled("citizen", environ=values)
    assert orchestrator.is_routing_memory_v3_enabled("officer", environ=values)
    assert not orchestrator.is_routing_memory_v3_enabled("admin", environ=values)


def test_llm_router_parses_multi_issue_and_enforces_officer_domain() -> None:
    raw = json.dumps(
        {
            "route": "legal_query",
            "confidence": 0.98,
            "reason_code": "multi_issue",
            "issues": [
                {
                    "standalone_query": "Khiếu nại quyết định xử phạt của Chủ tịch UBND phường",
                    "domain_candidate": "khieu_nai_to_cao_xu_phat",
                    "required_facets": ["authority", "next_action"],
                    "actor_anchors": ["người khiếu nại"],
                    "authority_anchors": ["Chủ tịch UBND phường"],
                },
                {
                    "standalone_query": "Đăng ký tạm trú cần hồ sơ gì",
                    "domain_candidate": "cu_tru_an_ninh",
                    "required_facets": ["documents"],
                },
            ],
            "referenced_turn_ids": ["m-1", "owner-khac"],
        },
        ensure_ascii=False,
    )

    citizen = orchestrator.parse_conversation_intent_v2(
        raw,
        question="Tôi hỏi cả hai việc trên",
        role="citizen",
        history_messages=[_message(1, "user", "Câu cũ")],
    )
    assert citizen.route == "legal_query"
    assert [issue.domain_candidate for issue in citizen.issues] == [
        "khieu_nai_to_cao_xu_phat",
        "cu_tru_an_ninh",
    ]
    assert citizen.referenced_turn_ids == ("m-1",)

    officer = orchestrator.parse_conversation_intent_v2(
        raw,
        question="Tôi hỏi cả hai việc trên",
        role="officer",
        allowed_domains=["khieu_nai_to_cao_xu_phat"],
    )
    assert {issue.domain_candidate for issue in officer.issues} == {
        "khieu_nai_to_cao_xu_phat"
    }


def test_router_invalid_json_and_low_confidence_fail_open_to_current_legal_query() -> None:
    for raw in (
        "không phải json",
        '{"route":"chat_meta","confidence":0.2,"issues":[]}',
    ):
        decision = orchestrator.parse_conversation_intent_v2(
            raw,
            question="còn thời hạn thì sao?",
            role="citizen",
        )
        assert decision.route == "legal_query"
        assert decision.router_fallback
        assert decision.issues[0].standalone_query == "còn thời hạn thì sao?"


def test_router_fallback_keeps_obvious_greeting_out_of_retrieval() -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        "không phải json",
        question="Xin chào, tôi sẽ hỏi nhiều thủ tục nhé.",
        role="citizen",
    )

    assert decision.route == "chat_meta"
    assert decision.router_fallback
    assert decision.issues == ()


def test_document_id_and_turn_ids_must_come_from_backend_context() -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        json.dumps(
            {
                "route": "document_followup",
                "confidence": 0.96,
                "issues": [{"standalone_query": "Văn bản trên còn hiệu lực không?"}],
                "active_document_id": "invented",
                "referenced_turn_ids": ["invented-turn"],
            }
        ),
        question="Văn bản trên còn hiệu lực không?",
        role="citizen",
        recent_source_refs=[{"document_id": "doc-1", "title": "Văn bản 1"}],
        history_messages=[_message(1, "user", "Văn bản 1")],
    )
    assert decision.active_document_id is None
    assert decision.referenced_turn_ids == ()


@pytest.mark.parametrize(
    "question",
    [
        "Văn bản địa phương vừa nêu còn hiệu lực không?",
        "Nguồn vừa dùng đã bị thay thế chưa?",
        "Điều vừa nêu quy định nội dung gì?",
    ],
)
def test_explicit_document_followup_overrides_valid_but_wrong_llm_route(
    question: str,
) -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        json.dumps(
            {
                "route": "legal_query",
                "confidence": 0.99,
                "reason_code": "wrong_legal_route",
                "issues": [{"standalone_query": question}],
            },
            ensure_ascii=False,
        ),
        question=question,
        role="citizen",
        recent_source_refs=[{"document_id": "doc-1", "title": "Văn bản 1"}],
        active_document={"document_id": "doc-1", "title": "Văn bản 1"},
        environ={
            "CHAT_ROUTING_MEMORY_V3_ENABLED": "true",
            "CHAT_ROUTING_MEMORY_V3_ROLES": "citizen",
        },
    )

    assert decision.route == "document_followup"
    assert decision.active_document_id == "doc-1"
    assert decision.reason_code == "deterministic_explicit_intent"


def test_explicit_no_retrieval_recall_overrides_valid_but_wrong_llm_route() -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        json.dumps(
            {
                "route": "legal_query",
                "confidence": 0.99,
                "reason_code": "wrong_legal_route",
                "issues": [{"standalone_query": "Câu ngay trước là gì?"}],
            },
            ensure_ascii=False,
        ),
        question="Câu ngay trước tôi hỏi là gì? Chỉ nhắc lại, không tra cứu.",
        role="citizen",
        environ={
            "CHAT_ROUTING_MEMORY_V3_ENABLED": "true",
            "CHAT_ROUTING_MEMORY_V3_ROLES": "citizen",
        },
    )

    assert decision.route == "chat_meta"
    assert decision.issues == ()
    assert decision.reason_code == "deterministic_explicit_intent"


@pytest.mark.parametrize(
    ("question", "expected_route"),
    [
        ("Tôi đang nói đến hai việc đất đai nào gần nhất?", "chat_meta"),
        ("Người hưởng khoản này có được cấp thẻ BHYT không?", "legal_query"),
        ("Văn bản vừa được nhắc đến còn hiệu lực không?", "document_followup"),
    ],
)
def test_generic_state_aware_routes_do_not_confuse_benefit_with_legal_clause(
    question: str,
    expected_route: str,
) -> None:
    assert orchestrator._explicit_conversation_route(question) == expected_route


def test_compaction_prefers_router_referenced_turn_and_uses_digest_v2() -> None:
    messages = [
        _message(
            index,
            "user" if index % 2 == 0 else "assistant",
            ("lượt được tham chiếu " if index == 2 else f"lượt {index} ")
            + ("nội dung dài " * 180),
        )
        for index in range(40)
    ]
    packet = orchestrator.build_conversation_context_packet(
        messages,
        current_question="Nhắc lại mục tiêu trước đó",
        state={
            "conversation_digest_v2": {
                "topic_summary": "Người dùng đang theo dõi một thủ tục cư trú.",
                "current_goal": "Hoàn thiện hồ sơ.",
                "topics": ["tạm trú"],
                "user_facts": [],
                "open_questions": [],
            },
            "digest_revision": 3,
            "digest_through_message_id": "m-20",
        },
        referenced_turn_ids=["m-2"],
        environ={
            "CHAT_CONTEXT_INPUT_TOKEN_BUDGET": "8000",
            "CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE": "4000",
            "CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE": "2000",
        },
    )
    assert packet.history_mode == "compacted"
    assert "m-2" in packet.included_message_ids
    assert "Tóm tắt chủ đề: Người dùng đang theo dõi" in packet.prompt_block
    assert packet.digest_revision == 3
    assert packet.digest_through_message_id == "m-20"
    assert packet.history_tokens <= packet.history_token_budget


def test_answer_envelope_v2_keeps_answer_but_rejects_legal_memory_patch() -> None:
    raw = json.dumps(
        {
            "answer_markdown": "Nội dung trả lời vẫn hiển thị.",
            "suggested_questions": [],
            "conversation_patch": {
                "topic_summary": "Theo Điều 5, thời hạn là 7 ngày.",
                "current_goal": "Chuẩn bị hồ sơ",
                "topics": ["tạm trú"],
                "user_facts": [],
                "open_questions": [],
                "referenced_turn_ids": ["m-1"],
            },
        },
        ensure_ascii=False,
    )
    answer, _, patch, parsed = memory.parse_answer_envelope_v2(
        raw,
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents"],
        allowed_message_ids=["m-1"],
    )
    assert parsed
    assert answer == "Nội dung trả lời vẫn hiển thị."
    assert patch is None


def test_answer_envelope_v2_accepts_user_stated_fact_with_owned_message_id() -> None:
    raw = json.dumps(
        {
            "answer_markdown": "Tôi đã hiểu câu hỏi nối tiếp.",
            "suggested_questions": [],
            "conversation_patch": {
                "topic_summary": "Người dùng đang chuẩn bị hồ sơ tạm trú.",
                "current_goal": "Xác định giấy tờ cần chuẩn bị",
                "topics": ["tạm trú"],
                "user_facts": [
                    {
                        "text": "Người dùng đang thuê căn hộ tại Hải Phòng",
                        "source_message_id": "m-1",
                        "status": "user_stated",
                    }
                ],
                "open_questions": ["Cần chuẩn bị giấy tờ nào"],
                "referenced_turn_ids": ["m-1"],
            },
        },
        ensure_ascii=False,
    )
    _, _, patch, _ = memory.parse_answer_envelope_v2(
        raw,
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents"],
        allowed_message_ids=["m-1"],
    )
    assert patch is not None
    assert patch["user_facts"][0]["source_message_id"] == "m-1"


def test_qwen_envelope_accepts_exact_transport_contract() -> None:
    raw = json.dumps(
        {
            "answer_markdown": "**Kết luận:** Anh/chị có thể tiếp tục chuẩn bị hồ sơ.",
            "suggested_questions": [
                {
                    "text": "Hồ sơ gồm những gì?",
                    "issue_id": "issue-1",
                    "facet": "documents",
                },
                {
                    "text": "Nộp tại đâu?",
                    "issue_id": "issue-1",
                    "facet": "authority",
                },
            ],
            "conversation_patch": {
                "topic_summary": "Người dùng đang hỏi về thủ tục cư trú.",
                "current_goal": "Chuẩn bị hồ sơ",
                "topics": ["cư trú"],
                "user_facts": [],
                "open_questions": ["Hồ sơ gồm những gì"],
                "referenced_turn_ids": ["m-1"],
            },
        },
        ensure_ascii=False,
    )

    answer, suggestions, patch = memory.parse_qwen_answer_envelope_v1(
        raw,
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents", "authority"],
        allowed_message_ids=["m-1"],
    )

    assert answer.startswith("**Kết luận:**")
    assert len(suggestions) == 2
    assert patch is not None


def test_qwen_transport_accepts_plain_markdown_and_hides_stale_json_artifacts() -> None:
    answer, suggestions, patch = memory.parse_qwen_answer_envelope_v1(
        "Câu trả lời Markdown không có envelope",
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents"],
        allowed_message_ids=["m-1"],
    )
    assert answer == "Câu trả lời Markdown không có envelope"
    assert suggestions == []
    assert patch is None

    for raw in ("{}", "[]", '{"answer_markdown":'):
        answer, suggestions, patch = memory.parse_qwen_answer_envelope_v1(
            raw,
            allowed_issue_ids=["issue-1"],
            allowed_facets=["documents"],
            allowed_message_ids=["m-1"],
        )
        assert answer == ""
        assert suggestions == []
        assert patch is None


def test_qwen_envelope_keeps_answer_when_optional_parts_are_invalid() -> None:
    raw = json.dumps(
        {
            "answer_markdown": "**Kết luận:** Anh/chị có thể chuẩn bị hồ sơ.",
            "suggested_questions": [
                {"text": "Quá ngắn?", "issue_id": "wrong-issue", "facet": "unknown"}
            ],
            "conversation_patch": {"topic_summary": "Theo Điều 5 phải nộp hồ sơ"},
            "extra": "Qwen đôi khi sinh thêm trường",
        },
        ensure_ascii=False,
    )

    answer, suggestions, patch = memory.parse_qwen_answer_envelope_v1(
        raw,
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents"],
        allowed_message_ids=["m-1"],
    )

    assert answer.startswith("**Kết luận:**")
    assert suggestions == []
    assert patch is None


def test_qwen_envelope_unwraps_json_fence_without_leaking_transport() -> None:
    raw = '```json\n{"answer_markdown":"Nội dung trả lời"}\n```'

    answer, suggestions, patch = memory.parse_qwen_answer_envelope_v1(
        raw,
        allowed_issue_ids=["issue-1"],
        allowed_facets=["documents"],
        allowed_message_ids=["m-1"],
    )

    assert answer == "Nội dung trả lời"
    assert suggestions == []
    assert patch is None


def test_direct_prompt_v2_requests_plain_markdown_for_every_route() -> None:
    prompt = build_direct_markdown_answer_prompt(
        question="Còn giấy tờ thì sao?",
        role="citizen",
        context="",
        suggestion_envelope=True,
        conversation_patch_envelope=True,
        conversation_context="[m-1] Người dùng: Tôi đang thuê căn hộ.",
    )
    assert "Chỉ trả về nội dung trả lời bằng Markdown" in prompt
    assert "conversation_patch" not in prompt
    assert '"answer_markdown"' not in prompt


def test_deepseek_and_qwen_use_byte_identical_application_prompt() -> None:
    shared_prompt = build_direct_markdown_answer_prompt(
        question="Tôi cần đổi tên cho con thì làm thế nào?",
        role="citizen",
        context="Nguồn pháp luật đã truy xuất.",
        suggestion_envelope=True,
        conversation_patch_envelope=True,
    )

    assert build_qwen_conversational_answer_prompt(shared_prompt) == shared_prompt
    assert "RÀNG BUỘC OUTPUT RIÊNG CHO QWEN" not in shared_prompt
    assert "không HTML, code fence, JSON" in shared_prompt
    assert "Chỉ trả về nội dung trả lời bằng Markdown" in shared_prompt


def test_llm_router_standalone_query_replaces_legacy_rewriter_packet() -> None:
    packet = search_router._router_query_packet_from_decision(
        {
            "route": "legal_query",
            "router_fallback": False,
            "issues": [
                {
                    "standalone_query": "Hồ sơ đăng ký tạm trú khi thuê nhà tại Hải Phòng",
                }
            ],
        },
        question="Còn HS tạm trú thì sao?",
        model_id="qwen2.5:3b",
    )

    assert packet is not None
    assert packet.rewrite_applied
    assert packet.standalone_query == (
        "Hồ sơ đăng ký tạm trú khi thuê nhà tại Hải Phòng"
    )
    assert packet.variants[0] == "Còn HS tạm trú thì sao?"


@pytest.mark.asyncio
async def test_runtime_context_uses_llm_router_v2_before_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ENABLED", "true")
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ROLES", "citizen")
    monkeypatch.setattr(search_router, "get_request_role", lambda _request: "citizen")
    monkeypatch.setattr(search_router, "get_request_user_id", lambda _request: "user-1")
    monkeypatch.setattr(
        search_router.conv_svc,
        "get_conversation",
        AsyncMock(
            return_value={
                "messages": [_message(1, "user", "Tôi thuê nhà tại Hải Phòng")]
            }
        ),
    )
    monkeypatch.setattr(
        search_router.chat_memory,
        "get_conversation_state",
        AsyncMock(return_value={"revision": 1, "recent_source_refs": []}),
    )
    router_call = AsyncMock(
        return_value=json.dumps(
            {
                "route": "legal_query",
                "confidence": 0.96,
                "reason_code": "procedure_followup",
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "standalone_query": "Tôi thuê nhà tại Hải Phòng. Câu hỏi tiếp theo: Còn hồ sơ thì sao?",
                        "domain_candidate": "cu_tru_an_ninh",
                        "required_facets": ["documents"],
                        "actor_anchors": ["người thuê nhà"],
                        "authority_anchors": [],
                        "legal_object_anchors": ["đăng ký tạm trú"],
                    }
                ],
                "active_document_id": None,
                "needs_clarification": False,
                "referenced_turn_ids": ["m-1"],
            }
        )
    )
    monkeypatch.setattr(search_router, "_call_ollama", router_call)

    decision, _state, _history, latency_ms = (
        await search_router._conversation_turn_runtime_context(
            AskRequest(
                question="Còn hồ sơ thì sao?",
                role="citizen",
                conversation_id="conv-1",
            ),
            Request({"type": "http", "headers": []}),
        )
    )

    router_call.assert_awaited_once()
    assert isinstance(decision, orchestrator.ConversationIntentDecisionV2)
    assert decision.reason_code == "procedure_followup"
    assert decision.issues[0].standalone_query == (
        "Tôi thuê nhà tại Hải Phòng. Câu hỏi tiếp theo: Còn hồ sơ thì sao?"
    )
    assert decision.issues[0].domain_candidate == "cu_tru_an_ninh"
    assert decision.referenced_turn_ids == ("m-1",)
    assert latency_ms >= 0


@pytest.mark.asyncio
async def test_runtime_context_skips_llm_router_for_clear_independent_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ENABLED", "true")
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ROLES", "citizen")
    monkeypatch.setattr(search_router, "get_request_role", lambda _request: "citizen")
    monkeypatch.setattr(search_router, "get_request_user_id", lambda _request: "user-1")
    monkeypatch.setattr(
        search_router.conv_svc,
        "get_conversation",
        AsyncMock(return_value={"messages": []}),
    )
    monkeypatch.setattr(
        search_router.chat_memory,
        "get_conversation_state",
        AsyncMock(return_value={"revision": 1, "recent_source_refs": []}),
    )
    router_call = AsyncMock(side_effect=AssertionError("clear question called the LLM router"))
    monkeypatch.setattr(search_router, "_call_ollama", router_call)

    decision, _state, _history, latency_ms = (
        await search_router._conversation_turn_runtime_context(
            AskRequest(
                question="Đăng ký khai sinh cần hồ sơ và nộp ở đâu?",
                role="citizen",
                conversation_id="conv-clear-1",
            ),
            Request({"type": "http", "headers": []}),
        )
    )

    assert isinstance(decision, orchestrator.ConversationIntentDecisionV2)
    assert decision.route == "legal_query"
    assert decision.reason_code == "deterministic_clear_legal_query"
    assert not decision.router_fallback
    assert latency_ms < 1000
    router_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_context_skips_llm_router_for_explicit_history_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ENABLED", "true")
    monkeypatch.setenv("CHAT_LLM_ROUTER_V2_ROLES", "citizen")
    monkeypatch.setattr(search_router, "get_request_role", lambda _request: "citizen")
    monkeypatch.setattr(search_router, "get_request_user_id", lambda _request: "user-1")
    router_call = AsyncMock(side_effect=AssertionError("history recall called the LLM router"))
    monkeypatch.setattr(search_router, "_call_ollama", router_call)

    decision, _state, _history, latency_ms = (
        await search_router._conversation_turn_runtime_context(
            AskRequest(
                question="Nhắc lại đúng hai yêu cầu ngay trước của tôi.",
                role="citizen",
            ),
            Request({"type": "http", "headers": []}),
        )
    )

    assert decision.route == "chat_meta"
    assert decision.reason_code == "deterministic_explicit_intent"
    assert latency_ms < 1000
    router_call.assert_not_awaited()


def test_notebook_style_decision_keeps_meta_out_of_retrieval() -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        "Tôi vừa hỏi câu gì? Chỉ nhắc lại, không tra cứu.",
        role="citizen",
        history_messages=[_message(1, "user", "Đăng ký tạm trú cần gì?")],
    )

    assert decision.route == "chat_meta"
    assert decision.issues == ()
    assert not decision.router_fallback


def test_form_code_question_is_independent_after_an_unrelated_legal_turn() -> None:
    history = [
        _message(1, "user", "Hai người tạm trú có đăng ký kết hôn tại phường không?"),
        _message(2, "assistant", "Nội dung trả lời hộ tịch trước đó."),
    ]
    question = "Mẫu CT01 dùng để làm thủ tục gì và nộp ở đâu?"

    decision = orchestrator.deterministic_conversation_intent_v2(
        question,
        role="citizen",
        history_messages=history,
        state={"canonical_domain": "ho_tich_chung_thuc"},
    )

    assert decision.route == "legal_query"
    assert not decision.needs_clarification
    assert orchestrator.is_clear_independent_legal_query(
        question,
        history_messages=history,
        state={"canonical_domain": "ho_tich_chung_thuc"},
    )


def test_legal_generation_history_never_carries_prior_assistant_prose() -> None:
    history = [
        {"id": "user-1", "role": "user", "content": "Sự kiện người dùng nêu."},
        {"id": "assistant-1", "role": "assistant", "content": "Kết luận cũ chưa xác minh."},
    ]

    assert search_router._legal_generation_history(
        history,
        {"route": "legal_query", "referenced_turn_ids": []},
    ) == []
    assert search_router._legal_generation_history(
        history,
        {"route": "legal_query", "referenced_turn_ids": ["user-1"]},
    ) == [history[0]]


@pytest.mark.parametrize(
    "question",
    [
        "Nhắc lại ba vướng mắc đất đai/xây dựng gần nhất mà tôi đã nêu.",
        "Tôi đã hỏi những nhóm đối tượng an sinh nào trong đoạn vừa rồi?",
    ],
)
def test_topic_words_do_not_override_conversation_recall(question: str) -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        question,
        role="citizen",
        history_messages=[_message(1, "user", "Một câu hỏi pháp luật trước đó")],
    )

    assert decision.route == "chat_meta"
    assert decision.issues == ()


def test_procedure_facet_followup_is_not_misread_as_document_followup() -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        "Thời hạn giải quyết hai khoản đó có giống nhau không?",
        role="citizen",
        history_messages=[
            _message(
                1,
                "user",
                "Tôi đang hỏi hai khoản trợ cấp người cao tuổi và trợ cấp xã hội.",
            )
        ],
        active_document={"document_id": "doc-1", "title": "Nghị định liên quan"},
        state={
            "canonical_domain": "an_sinh_y_te_giao_duc",
            "legal_objects": ["trợ cấp người cao tuổi", "trợ cấp xã hội"],
        },
    )

    assert decision.route == "legal_query"
    assert decision.issues[0].domain_candidate == "an_sinh_y_te_giao_duc"
    assert "Câu hỏi tiếp theo" in decision.issues[0].standalone_query


def test_compacted_followup_uses_verified_state_when_prior_turn_is_unavailable() -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        "Còn thời hạn thì sao?",
        role="citizen",
        history_messages=[],
        state={
            "canonical_domain": "cu_tru_an_ninh",
            "procedure": {"id": "1.004194", "name": "Đăng ký tạm trú"},
            "actors": ["người thuê nhà"],
            "legal_objects": ["đăng ký tạm trú"],
        },
    )

    assert decision.route == "legal_query"
    assert decision.issues[0].domain_candidate == "cu_tru_an_ninh"
    assert "Đăng ký tạm trú" in decision.issues[0].standalone_query
    assert "Còn thời hạn thì sao?" in decision.issues[0].standalone_query


@pytest.mark.parametrize(
    ("question", "expected_anchor"),
    [
        (
            "Hai người đang tạm trú có đăng ký kết hôn tại phường được không?",
            "60/2014/QH13 Điều 17",
        ),
        (
            "Trẻ dưới 16 tuổi không có nguồn nuôi dưỡng được trợ cấp thế nào?",
            "20/2021/NĐ-CP khoản 1 Điều 5",
        ),
        (
            "Người đơn thân thuộc hộ nghèo đang nuôi con nhỏ được trợ cấp thế nào?",
            "20/2021/NĐ-CP khoản 4 Điều 5",
        ),
        (
            "Người cao tuổi 75 tuổi xin trợ cấp cần mẫu tờ khai nào?",
            "176/2025/NĐ-CP",
        ),
    ],
)
def test_known_indexed_source_groups_receive_exact_hydration_anchor(
    question: str,
    expected_anchor: str,
) -> None:
    hydrated = search_router._hydrate_retrieval_source_anchors_v3(question)

    assert question in hydrated
    assert expected_anchor in hydrated


def test_document_followup_hydrates_active_document_identity() -> None:
    hydrated = search_router._hydrate_retrieval_source_anchors_v3(
        "Văn bản vừa nêu còn hiệu lực không?",
        active_document={
            "law_number": "11/2025/QĐ-UBND",
            "title": "Quy định diện tích tối thiểu tách thửa tại Hải Phòng",
        },
        document_followup=True,
    )

    assert "11/2025/QĐ-UBND" in hydrated
    assert "hiệu lực sửa đổi bổ sung thay thế" in hydrated


@pytest.mark.parametrize(
    "question",
    [
        "m giúp dc gì cho t",
        "con mẹ m béo",
    ],
)
def test_natural_smalltalk_does_not_enter_legal_retrieval(question: str) -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        question,
        role="citizen",
    )

    assert decision.route == "chat_meta"
    assert decision.issues == ()


def test_notebook_style_decision_preserves_officer_account_domain() -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        "Còn thời hạn thì sao?",
        role="officer",
        allowed_domains=["khieu_nai_to_cao_xu_phat"],
        history_messages=[
            _message(
                1,
                "user",
                "Khiếu nại quyết định của Chủ tịch UBND phường gửi ai?",
            )
        ],
    )

    assert decision.route == "legal_query"
    assert decision.issues[0].domain_candidate == "khieu_nai_to_cao_xu_phat"


@pytest.mark.asyncio
async def test_model_can_change_after_a_previous_message_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(search_router, "get_request_role", lambda _request: "citizen")
    monkeypatch.setattr(search_router, "get_request_user_id", lambda _request: "user-1")
    monkeypatch.setattr(
        search_router.conv_svc,
        "get_conversation",
        AsyncMock(
            return_value={
                "messages": [
                    {
                        **_message(1, "assistant", "Câu trả lời"),
                        "model_option_id": "qwen-local",
                        "model_display_name": "Qwen",
                        "model_locked": True,
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(
        search_router.chat_memory,
        "get_conversation_state",
        AsyncMock(return_value={}),
    )

    ask_request = AskRequest(
        question="Còn hồ sơ thì sao?",
        role="citizen",
        conversation_id="conv-1",
        model_option_id="deepseek-default",
    )
    decision, _state, _history, _latency_ms = (
        await search_router._conversation_turn_runtime_context(
            ask_request,
            Request({"type": "http", "headers": []}),
        )
    )

    assert decision.route == "legal_query"
    assert ask_request.model_option_id == "deepseek-default"


@pytest.mark.asyncio
async def test_explicit_disabled_model_option_never_falls_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search_router,
        "resolve_model_option",
        AsyncMock(side_effect=PermissionError("CHAT_MODEL_NOT_ALLOWED")),
    )

    with pytest.raises(Exception) as caught:
        await search_router._resolve_ask_model_ids(
            None,
            None,
            None,
            role="citizen",
            model_option_id="qwen-disabled",
        )

    assert getattr(caught.value, "status_code", None) == 403
    assert caught.value.detail["code"] == "CHAT_MODEL_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_ollama_qwen_request_uses_strict_schema_and_bounded_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": '{"answer_markdown":"ok"}'}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, json: dict):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(search_router.httpx, "AsyncClient", FakeClient)
    schema = {"type": "object", "required": ["answer_markdown"]}

    await search_router._call_ollama(
        "qwen3:8b",
        "prompt",
        format_schema=schema,
        num_ctx=16384,
        num_predict=2048,
    )

    assert captured["payload"]["format"] == schema
    assert captured["payload"]["think"] is False
    assert captured["payload"]["options"]["temperature"] == 0.0
    assert captured["payload"]["options"]["num_ctx"] == 16384
    assert captured["payload"]["options"]["num_predict"] == 2048
    assert 0 < captured["client_kwargs"]["timeout"].read <= 120.0


@pytest.mark.asyncio
async def test_ollama_stream_collects_ttft_and_provider_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            yield json.dumps({"response": "Xin ", "done": False})
            yield json.dumps(
                {
                    "response": "chào",
                    "done": True,
                    "total_duration": 12_000_000,
                    "load_duration": 2_000_000,
                    "prompt_eval_count": 19,
                    "prompt_eval_duration": 3_000_000,
                    "eval_count": 2,
                    "eval_duration": 7_000_000,
                }
            )

    class StreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        def stream(self, method: str, url: str, *, json: dict):
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = json
            return StreamContext()

    monkeypatch.setattr(search_router.httpx, "AsyncClient", FakeClient)
    token = search_router._OLLAMA_METRICS.set({"calls": []})
    try:
        answer = await search_router._call_ollama(
            "qwen2.5:3b",
            "prompt",
            num_ctx=4096,
            num_predict=32,
            timeout_seconds=5,
        )
        metrics = search_router._OLLAMA_METRICS.get()
    finally:
        search_router._OLLAMA_METRICS.reset(token)

    assert answer == "Xin chào"
    assert captured["payload"]["stream"] is True
    assert metrics and len(metrics["calls"]) == 1
    call = metrics["calls"][0]
    assert call["time_to_first_token_ms"] is not None
    assert call["total_duration_ms"] == 12.0
    assert call["load_duration_ms"] == 2.0
    assert call["prompt_eval_count"] == 19
    assert call["prompt_eval_duration_ms"] == 3.0
    assert call["eval_count"] == 2
    assert call["eval_duration_ms"] == 7.0


def test_qwen_ui_flag_requires_gate_and_role() -> None:
    assert not search_router._qwen_ui_serving_enabled("citizen", environ={})
    values = {
        "CHAT_QWEN_UI_V1_ENABLED": "true",
        "CHAT_QWEN_GATE_PASSED": "true",
        "CHAT_QWEN_UI_V1_ROLES": "citizen",
    }
    assert search_router._qwen_ui_serving_enabled("citizen", environ=values)
    assert not search_router._qwen_ui_serving_enabled("officer", environ=values)
