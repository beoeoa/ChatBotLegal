import json
from types import SimpleNamespace

import pytest

from api import conversational_orchestrator as orchestrator
from api.models import AskRequest
from api.routers import search as search_router


def test_router_contract_is_bounded_and_documents_backend_ownership() -> None:
    schema = search_router.CONVERSATION_ROUTER_SCHEMA
    assert schema["properties"]["issues"]["maxItems"] == orchestrator.ROUTER_MAX_ISSUES
    prompt = orchestrator.build_conversation_router_prompt_v2(
        question="Còn hồ sơ thì sao?",
        role="officer",
        context_packet=orchestrator.build_conversation_context_packet([], current_question="Còn hồ sơ thì sao?"),
        state={},
        allowed_domains=["cu_tru_an_ninh"],
    )
    assert "tối đa 3" in prompt
    assert "Không tự chọn phòng ban" in prompt
    router_budget = orchestrator.context_budget_environ_v2(router=True)
    assert router_budget["CHAT_CONTEXT_INPUT_TOKEN_BUDGET"] == "1024"
    assert router_budget["CHAT_CONTEXT_EVIDENCE_TOKEN_RESERVE"] == "0"
    assert router_budget["CHAT_CONTEXT_OUTPUT_TOKEN_RESERVE"] == "64"


def test_invalid_router_route_falls_back_to_deterministic_policy() -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        json.dumps(
            {
                "route": "department_lookup",
                "confidence": 0.99,
                "issues": [
                    {
                        "standalone_query": "Tôi cần tra cứu pháp luật",
                        "domain_candidate": "cu_tru_an_ninh",
                    }
                ],
            }
        ),
        question="Xin chào",
        role="citizen",
    )
    assert decision.route == "chat_meta"
    assert decision.router_fallback
    assert decision.reason_code == "router_invalid_route"


@pytest.mark.parametrize(
    "question",
    [
        "Ngày mai Hải Phòng có mưa không?",
        "Tính giúp tôi lợi nhuận cổ phiếu này",
        "Tỷ giá hôm nay bao nhiêu?",
    ],
)
def test_non_legal_topics_do_not_enter_legal_retrieval(question: str) -> None:
    decision = orchestrator.deterministic_conversation_intent_v2(
        question,
        role="citizen",
    )
    assert decision.route == "out_of_scope"
    assert decision.issues == ()


def test_officer_out_of_scope_issue_is_dropped_not_relabelled() -> None:
    decision = orchestrator.parse_conversation_intent_v2(
        json.dumps(
            {
                "route": "legal_query",
                "confidence": 0.99,
                "issues": [
                    {
                        "standalone_query": "Hỏi đất đai",
                        "domain_candidate": "dat_dai_xay_dung",
                    },
                    {
                        "standalone_query": "Hỏi hộ tịch",
                        "domain_candidate": "ho_tich_chung_thuc",
                    },
                ],
            }
        ),
        question="Tôi hỏi hai việc",
        role="officer",
        allowed_domains=["ho_tich_chung_thuc"],
    )
    assert [item.domain_candidate for item in decision.issues] == [
        "ho_tich_chung_thuc"
    ]
    assert all(item.domain_candidate != "dat_dai_xay_dung" for item in decision.issues)


@pytest.mark.asyncio
async def test_router_uses_dedicated_local_model_without_answer_model_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAT_LLM_ROUTER_MODEL_ID", raising=False)
    monkeypatch.delenv("CHAT_LLM_ROUTER_MODEL", raising=False)
    model = await search_router._resolve_conversation_router_model(
        AskRequest(question="Câu hỏi mơ hồ", role="citizen"),
        role="citizen",
    )
    assert model.provider == "ollama"
    assert model.name == "qwen2.5:0.5b"


@pytest.mark.asyncio
async def test_router_scope_uses_current_organization_scope_in_unit_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        search_router,
        "get_user_profile",
        lambda _user_id: _async_result({"organization_unit_id": "unit-civil", "allowed_domains": []}),
    )
    monkeypatch.setattr(
        search_router,
        "active_settings",
        lambda: _async_result(SimpleNamespace(organization_routing_mode="unit_primary")),
    )
    monkeypatch.setattr(
        search_router,
        "active_organization_units",
        lambda _settings: _async_result([SimpleNamespace(id="unit-civil")]),
    )
    monkeypatch.setattr(
        search_router,
        "resolve_officer_scope",
        lambda **_kwargs: _async_result(
            SimpleNamespace(domains=("ho_tich", "cu_tru_an_ninh"))
        ),
    )

    domains = await search_router._resolve_router_allowed_domains(
        role="officer",
        user_id="officer-1",
    )
    assert domains == ["ho_tich_chung_thuc", "cu_tru_an_ninh"]


@pytest.mark.asyncio
async def test_router_scope_legacy_projection_is_canonicalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search_router,
        "get_user_profile",
        lambda _user_id: _async_result({"allowed_domains": ["ho_tich"]}),
    )
    monkeypatch.setattr(
        search_router,
        "active_settings",
        lambda: _async_result(SimpleNamespace(organization_routing_mode="legacy")),
    )
    domains = await search_router._resolve_router_allowed_domains(
        role="officer",
        user_id="officer-1",
    )
    assert domains == ["ho_tich_chung_thuc"]


def _async_result(value):
    async def inner():
        return value

    return inner()
