from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

import open_notebook.graphs.chat as chat_graph_module


@pytest.mark.asyncio
async def test_generate_chat_message_uses_async_model_on_active_loop(monkeypatch):
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="  Câu trả lời  ")
    provision = AsyncMock(return_value=model)
    monkeypatch.setattr(chat_graph_module, "provision_langchain_model", provision)
    monkeypatch.setattr(
        chat_graph_module.model_manager,
        "get_defaults",
        AsyncMock(return_value=SimpleNamespace(default_chat_model="model:primary")),
    )

    result = await chat_graph_module.generate_chat_message(
        {
            "messages": [HumanMessage(content="Câu hỏi")],
            "notebook": None,
            "context": {"sources": [], "notes": []},
            "model_override": None,
        },
        RunnableConfig(configurable={"thread_id": "chat_session:test"}),
    )

    assert result.content == "  Câu trả lời  "
    provision.assert_awaited_once()
    model.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_chat_message_uses_configured_model_fallback(monkeypatch):
    primary = AsyncMock()
    primary.ainvoke.side_effect = RuntimeError("rate limited")
    fallback = AsyncMock()
    fallback.ainvoke.return_value = AIMessage(content="Đã trả lời bằng mô hình dự phòng")
    provision = AsyncMock(side_effect=[primary, fallback])
    monkeypatch.setattr(chat_graph_module, "provision_langchain_model", provision)
    monkeypatch.setattr(
        chat_graph_module.model_manager,
        "get_defaults",
        AsyncMock(return_value=SimpleNamespace(default_chat_model="model:primary")),
    )
    monkeypatch.setattr(
        chat_graph_module.Model,
        "get_models_by_type",
        AsyncMock(
            return_value=[
                SimpleNamespace(id="model:primary", provider="openrouter"),
                SimpleNamespace(id="model:fallback", provider="deepseek"),
            ]
        ),
    )

    result = await chat_graph_module.generate_chat_message(
        {
            "messages": [HumanMessage(content="Câu hỏi")],
            "notebook": None,
            "context": {"sources": ["Nội dung"]},
            "model_override": None,
        },
        RunnableConfig(configurable={"thread_id": "chat_session:test"}),
    )

    assert result.content == "Đã trả lời bằng mô hình dự phòng"
    assert provision.await_count == 2


@pytest.mark.asyncio
async def test_generate_chat_message_returns_grounded_excerpt_if_models_fail(monkeypatch):
    primary = AsyncMock()
    primary.ainvoke.side_effect = RuntimeError("rate limited")
    monkeypatch.setattr(
        chat_graph_module,
        "provision_langchain_model",
        AsyncMock(return_value=primary),
    )
    monkeypatch.setattr(
        chat_graph_module.model_manager,
        "get_defaults",
        AsyncMock(return_value=SimpleNamespace(default_chat_model="model:primary")),
    )
    monkeypatch.setattr(
        chat_graph_module.Model,
        "get_models_by_type",
        AsyncMock(return_value=[SimpleNamespace(id="model:primary", provider="openrouter")]),
    )

    result = await chat_graph_module.generate_chat_message(
        {
            "messages": [HumanMessage(content="Văn bản là nghị định số bao nhiêu?")],
            "notebook": None,
            "context": {
                "sources": [
                    "CHÍNH PHỦ\nNGHỊ ĐỊNH SỐ 310/2026/NĐ-CP\nQuy định chi tiết"
                ]
            },
            "model_override": None,
        },
        RunnableConfig(configurable={"thread_id": "chat_session:test"}),
    )

    assert "310/2026/NĐ-CP" in result.content
    assert "Trích nội dung liên quan" in result.content
