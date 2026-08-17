import asyncio
import re
import sqlite3
from typing import Annotated, Any, Optional

from ai_prompter import Prompter
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from loguru import logger
from typing_extensions import TypedDict

from open_notebook.ai.models import Model, model_manager
from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import OpenNotebookError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content


class ThreadState(TypedDict):
    messages: Annotated[list, add_messages]
    notebook: Optional[Notebook]
    context: Optional[Any]
    context_config: Optional[dict]
    model_override: Optional[str]


async def generate_chat_message(
    state: ThreadState, config: RunnableConfig
):
    """Generate one assistant message on the caller's active event loop."""
    system_prompt = Prompter(prompt_template="chat/system").render(data=state)  # type: ignore[arg-type]
    payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
    model_id = config.get("configurable", {}).get("model_id") or state.get(
        "model_override"
    )
    primary_model_id = model_id
    if not primary_model_id:
        defaults = await model_manager.get_defaults()
        primary_model_id = defaults.default_chat_model

    errors: list[Exception] = []
    try:
        model = await provision_langchain_model(
            str(payload), model_id, "chat", max_tokens=8192
        )
        ai_message = await model.ainvoke(payload)
    except Exception as exc:
        errors.append(exc)
        logger.warning(
            "Default notebook chat model failed; trying configured alternatives: {}",
            type(exc).__name__,
        )
        ai_message = None

    if ai_message is None:
        candidates = await Model.get_models_by_type("language")
        # Prefer a different provider before another model on the same shared
        # pool, then use stable identifiers for deterministic ordering.
        candidates.sort(
            key=lambda item: (
                item.provider == next(
                    (
                        candidate.provider
                        for candidate in candidates
                        if str(candidate.id) == str(primary_model_id)
                    ),
                    "",
                ),
                str(item.id),
            )
        )
        for candidate in candidates:
            if str(candidate.id) == str(primary_model_id):
                continue
            try:
                fallback_model = await provision_langchain_model(
                    str(payload), str(candidate.id), "chat", max_tokens=8192
                )
                ai_message = await fallback_model.ainvoke(payload)
                logger.info(
                    "Notebook chat recovered with configured fallback provider={}",
                    candidate.provider,
                )
                break
            except Exception as exc:
                errors.append(exc)
                logger.warning(
                    "Notebook chat fallback provider={} failed: {}",
                    candidate.provider,
                    type(exc).__name__,
                )

    if ai_message is None:
        ai_message = AIMessage(
            content=_extractive_context_fallback(state)
        )
        logger.warning(
            "All {} configured notebook chat attempts failed; returned deterministic context excerpt",
            len(errors),
        )
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
        system_prompt = Prompter(prompt_template="chat/system").render(data=state)  # type: ignore[arg-type]
        payload = [SystemMessage(content=system_prompt)] + state.get("messages", [])
        model_id = config.get("configurable", {}).get("model_id") or state.get(
            "model_override"
        )

        # Handle async model provisioning from sync context
        def run_in_new_loop():
            """Run the async function in a new event loop"""
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(
                    provision_langchain_model(
                        str(payload), model_id, "chat", max_tokens=8192
                    )
                )
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)

        try:
            # Try to get the current event loop
            asyncio.get_running_loop()
            # If we're in an event loop, run in a thread with a new loop
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_new_loop)
                model = future.result()
        except RuntimeError:
            # No event loop running, safe to use asyncio.run()
            model = asyncio.run(
                provision_langchain_model(
                    str(payload),
                    model_id,
                    "chat",
                    max_tokens=8192,
                )
            )

        ai_message = model.invoke(payload)

        # Clean thinking content from AI response (e.g., <think>...</think> tags)
        content = extract_text_content(ai_message.content)
        cleaned_content = clean_thinking_content(content)
        cleaned_message = ai_message.model_copy(update={"content": cleaned_content})

        return {"messages": cleaned_message}
    except OpenNotebookError:
        raise
    except Exception as e:
        error_class, user_message = classify_error(e)
        raise error_class(user_message) from e


conn = sqlite3.connect(
    LANGGRAPH_CHECKPOINT_FILE,
    check_same_thread=False,
)
memory = SqliteSaver(conn)

agent_state = StateGraph(ThreadState)
agent_state.add_node("agent", call_model_with_messages)
agent_state.add_edge(START, "agent")
agent_state.add_edge("agent", END)
graph = agent_state.compile(checkpointer=memory)
