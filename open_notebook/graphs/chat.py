import asyncio
import os
import re
import sqlite3
from threading import Lock
from typing import Annotated, Any, Optional

from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from loguru import logger
from typing_extensions import TypedDict

from open_notebook.ai.models import model_manager
from open_notebook.ai.models import Model
from open_notebook.ai.provision import provision_langchain_model as _legacy_provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content

# Compatibility injection point for extensions/tests written before the
# ModelGateway cutover. Normal runtime never calls this alias.
provision_langchain_model = _legacy_provision_langchain_model


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    context: Optional[Any]
    context_config: Optional[dict]
    model_override: Optional[str]
    system_name: Optional[str]
    organization_name: Optional[str]
    system_prompt_addendum: Optional[str]
    chat_behavior_policy: Optional[str]


def _chat_timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("OPEN_NOTEBOOK_CHAT_TIMEOUT_SECONDS", "30")))
    except (TypeError, ValueError):
        return 30.0


def _chat_max_tokens() -> int:
    try:
        return max(
            512,
            min(8_192, int(os.getenv("OPEN_NOTEBOOK_CHAT_MAX_TOKENS", "3072"))),
        )
    except (TypeError, ValueError):
        return 3_072


async def _chat_prompt_data(state: ThreadState) -> dict[str, Any]:
    """Add the current product branding and admin guidance to notebook chat.

    The legal contract remains in the checked-in prompt template. The
    database-backed values are deliberately additive so an admin can tune
    wording without replacing source, citation, or safety rules.
    """

    data = dict(state)
    from api.chat_behavior_policy import render_behavior_policy

    data["chat_behavior_policy"] = render_behavior_policy(
        role="citizen",
        route="chat_meta",
        answer_depth="balanced",
        natural_chat=True,
    )
    try:
        from api.system_settings import active_settings

        settings = await active_settings()
        data.update(
            system_name=(settings.system_name or "Pháp luật Hải Phòng").strip(),
            organization_name=(settings.organization_name or "").strip(),
            system_prompt_addendum=(settings.system_prompt_addendum or "").strip(),
        )
    except Exception as exc:
        logger.warning(
            "Notebook chat settings unavailable; using built-in prompt contract: {}",
            type(exc).__name__,
        )
    return data


async def _render_chat_system_prompt(state: ThreadState) -> str:
    return Prompter(prompt_template="chat/system").render(
        data=await _chat_prompt_data(state)
    )  # type: ignore[arg-type]


def _render_chat_system_prompt_sync(state: ThreadState) -> str:
    async def render() -> str:
        return await _render_chat_system_prompt(state)

    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as executor:
            return executor.submit(lambda: asyncio.run(render())).result()
    except RuntimeError:
        return asyncio.run(render())


async def generate_chat_message(
    state: ThreadState, config: RunnableConfig
):
    """Generate one assistant message on the caller's active event loop."""
    model_id = config.get("configurable", {}).get("model_id") or state.get(
        "model_override"
    )
    primary_model_id = model_id
    if not primary_model_id:
        defaults = await model_manager.get_defaults()
        primary_model_id = defaults.default_chat_model

    if provision_langchain_model is not _legacy_provision_langchain_model:
        try:
            legacy_model = await provision_langchain_model(primary_model_id)
            legacy_result = await asyncio.wait_for(
                legacy_model.ainvoke(state.get("messages", [])),
                timeout=_chat_timeout_seconds(),
            )
            content = extract_text_content(getattr(legacy_result, "content", legacy_result))
            return AIMessage(content=clean_thinking_content(content))
        except Exception as exc:
            logger.warning(
                "Injected notebook model unavailable; returning grounded excerpt: {}",
                type(exc).__name__,
            )
            return AIMessage(content=_extractive_context_fallback(state))

    system_prompt = await _render_chat_system_prompt(state)
    payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])

    from api.model_gateway import default_model_gateway
    try:
        timeout_seconds = _chat_timeout_seconds()
        result = await asyncio.wait_for(
            default_model_gateway.generate(
                str(payload),
                model_id=str(primary_model_id),
                options={
                    "max_tokens": _chat_max_tokens(),
                    "timeout": timeout_seconds,
                },
            ),
            timeout=timeout_seconds,
        )
        ai_message = AIMessage(
            content=result.text,
            response_metadata={
                **result.metadata,
                "canonical_model_id": result.model_id,
                "fallback_used": result.fallback_used,
            },
        )
    except Exception as exc:
        logger.warning(
            "Notebook ModelGateway unavailable; returning grounded excerpt: {}",
            type(exc).__name__,
        )
        ai_message = AIMessage(content=_extractive_context_fallback(state))
    content = extract_text_content(ai_message.content)
    cleaned_content = clean_thinking_content(content)
    return ai_message.model_copy(update={"content": cleaned_content})


def _flatten_context(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        parts: list[str] = []
        for child in value.values():
            parts.extend(_flatten_context(child))
        return parts
    if isinstance(value, (list, tuple)):
        parts = []
        for child in value:
            parts.extend(_flatten_context(child))
        return parts
    return []


def _extractive_context_fallback(state: ThreadState) -> str:
    """Return source-grounded excerpts when every configured model is unavailable."""
    context_text = "\n".join(_flatten_context(state.get("context"))).strip()
    if not context_text:
        return "Hồ sơ chưa có nội dung văn bản để trích dẫn trong câu trả lời này."

    question = ""
    for message in reversed(state.get("messages", [])):
        if getattr(message, "type", "") == "human":
            question = str(getattr(message, "content", ""))
            break
    terms = {
        term
        for term in re.findall(r"[\wÀ-ỹĐđ]+", question.casefold())
        if len(term) >= 3
    }
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n{1,}", context_text)
        if paragraph.strip()
    ]
    ranked = sorted(
        enumerate(paragraphs),
        key=lambda item: (
            -sum(term in item[1].casefold() for term in terms),
            item[0],
        ),
    )
    excerpts: list[str] = []
    total = 0
    for _, paragraph in ranked:
        if paragraph in excerpts:
            continue
        excerpts.append(paragraph)
        total += len(paragraph)
        if total >= 1800 or len(excerpts) >= 8:
            break
    return "Trích nội dung liên quan trực tiếp từ hồ sơ:\n\n" + "\n\n".join(excerpts)


def call_model_with_messages(state: ThreadState, config: RunnableConfig) -> dict:
    try:
        def run_in_new_loop():
            return asyncio.run(generate_chat_message(state, config))
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                ai_message = executor.submit(run_in_new_loop).result()
        except RuntimeError:
            ai_message = asyncio.run(generate_chat_message(state, config))
        return {"messages": ai_message}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


_graph_lock = Lock()
_compiled_graph = None
_checkpoint_connection = None


def _get_compiled_graph():
    """Open the checkpoint database only when notebook chat is actually used.

    Importing the FastAPI application must not create a persistent SQLite file.
    Keeping initialization behind this boundary also lets unrelated source,
    credential and model routes start and be tested without touching chat state.
    """

    global _compiled_graph, _checkpoint_connection
    if _compiled_graph is not None:
        return _compiled_graph
    with _graph_lock:
        if _compiled_graph is not None:
            return _compiled_graph
        _checkpoint_connection = sqlite3.connect(
            LANGGRAPH_CHECKPOINT_FILE,
            check_same_thread=False,
        )
        memory = SqliteSaver(_checkpoint_connection)
        agent_state = StateGraph(ThreadState)
        agent_state.add_node("agent", call_model_with_messages)
        agent_state.add_edge(START, "agent")
        agent_state.add_edge("agent", END)
        _compiled_graph = agent_state.compile(checkpointer=memory)
        return _compiled_graph


class _LazyChatGraph:
    """Preserve the public graph interface while deferring persistent I/O."""

    def __getattr__(self, name):
        return getattr(_get_compiled_graph(), name)


graph = _LazyChatGraph()
