"""Canonical conversation chat orchestration for legal and notebook responses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from api import conversation_service as conversations
from api.legal_answer_presentation import project_legal_answer_presentation
from api.legal_answer_quality import decide_answer_mode
from api.legal_answer_router import (
    PIPELINE_VERSION as ANSWER_PIPELINE_VERSION,
    route_legal_answer,
)
from api.legal_structured_answer import project_backend_owned_answer_artifacts
from api.models import AskRequest, AskResponse
from open_notebook.graphs.chat import generate_chat_message


ChatEngine = Literal["legal", "general"]
LegalAnswerExecutor = Callable[..., Awaitable[AskResponse]]


def _response_mode_for_legal(response: AskResponse) -> AskResponse:
    decision = decide_answer_mode(
        grounding_status=response.grounding_status,
        section_statuses=[
            section.status for section in (response.answer_sections or [])
        ],
        source_gap=response.source_gap,
        clarifying_questions=response.clarifying_questions,
    )
    return response.model_copy(
        update={
            "response_mode": decision.mode,
            "reason_codes": list(decision.reason_codes),
        }
    )


def finalize_legal_answer_response(response: AskResponse) -> AskResponse:
    """Apply the one V3 post-validation/public-presentation boundary."""

    normalized = _response_mode_for_legal(response)
    artifacts = project_backend_owned_answer_artifacts(
        citations=normalized.citations,
        recommended_forms=normalized.recommended_forms,
    )
    normalized = normalized.model_copy(update=artifacts)
    answer_route = normalized.answer_route or route_legal_answer(
        normalized.question
    ).answer_route
    # A historical label is mandatory. A blocked/legacy response without an
    # applicability date cannot safely claim to be a historical answer.
    if answer_route == "historical" and normalized.legal_as_of is None:
        answer_route = "general_legal"
    normalized = normalized.model_copy(
        update={
            "answer_route": answer_route,
            "pipeline_version": normalized.pipeline_version
            or ANSWER_PIPELINE_VERSION,
        }
    )
    presentation = project_legal_answer_presentation(normalized)
    return normalized.model_copy(
        update={
            "presentation_version": presentation.presentation_version,
            "answer_route": presentation.answer_route,
            "pipeline_version": presentation.pipeline_version,
            "data_release_id": presentation.data_release_id,
            "index_fingerprint": presentation.index_fingerprint,
            "validity_snapshot": presentation.validity_snapshot,
            "verification_label": presentation.verification_label,
            "historical_label": presentation.historical_label,
            "sections": presentation.sections,
        }
    )


async def run_legal_answer_pipeline_v3(
    *,
    ask_request: AskRequest,
    request: Any,
    progress: Any = None,
    trace_id_override: str | None = None,
    executor: LegalAnswerExecutor | None = None,
) -> AskResponse:
    """Execute generation/validation once, then finalize one public response.

    ``executor`` is injectable for contract tests. Runtime imports the current
    router core lazily while the extraction of that large legacy core proceeds
    in reviewable slices.
    """

    if executor is None:
        from api.routers.search import _execute_ask_simple_core

        executor = _execute_ask_simple_core
    response = await executor(
        ask_request,
        request,
        progress=progress,
        trace_id_override=trace_id_override,
    )
    return finalize_legal_answer_response(response)


async def run_general_chat(
    *,
    question: str,
    conversation_id: str,
    owner_key: str,
    user_id: str,
    role: str,
    context: dict[str, Any] | None,
    model_override: str | None,
) -> AskResponse:
    """Run notebook-style chat while using canonical conversation persistence."""
    history = await conversations.get_followup_context(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=role == "admin",
    )
    messages = [
        HumanMessage(content=item["content"])
        if item.get("role") == "user"
        else AIMessage(content=item["content"])
        for item in history
        if item.get("content")
    ]
    messages.append(HumanMessage(content=question))
    answer = await generate_chat_message(
        {
            "messages": messages,
            "notebook": None,
            "context": context or {},
            "context_config": None,
            "model_override": model_override,
        },
        RunnableConfig(configurable={"model_id": model_override}),
    )
    content = str(answer.content).strip()
    response = AskResponse(
        question=question,
        answer=content,
        conversation_id=conversation_id,
        grounding_status="not_applicable",
        response_mode="answer",
        reason_codes=["GENERAL_CONTEXT_CHAT"],
        generation_provenance={"mode": "notebook_chat"},
    )
    await conversations.add_message(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        role="assistant",
        content=content,
        status="complete",
        attachments=[{"kind": "chat_engine", "value": "general"}],
    )
    return response


async def run_unified_chat(
    *,
    engine: ChatEngine,
    ask_request: AskRequest,
    request: Any,
    owner_key: str,
    user_id: str,
    role: str,
    general_context: dict[str, Any] | None = None,
    model_override: str | None = None,
) -> AskResponse:
    """Persist one user turn, route it, and return one canonical response."""
    conversation_id = ask_request.conversation_id
    if not conversation_id:
        raise ValueError("conversation_id is required for unified chat")

    saved = await conversations.add_message(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        role="user",
        content=ask_request.question,
    )
    if not saved:
        raise LookupError("Conversation not found")

    if engine == "general":
        return await run_general_chat(
            question=ask_request.question,
            conversation_id=conversation_id,
            owner_key=owner_key,
            user_id=user_id,
            role=role,
            context=general_context,
            model_override=model_override,
        )

    response = await run_legal_answer_pipeline_v3(
        ask_request=ask_request,
        request=request,
    )
    presentation_attachment = {
        "kind": "legal_answer_presentation",
        "value": {
            "presentation_version": response.presentation_version,
            "answer_route": response.answer_route,
            "pipeline_version": response.pipeline_version,
            "data_release_id": response.data_release_id,
            "index_fingerprint": response.index_fingerprint,
            "validity_snapshot": response.validity_snapshot,
            "verification_label": response.verification_label,
            "historical_label": response.historical_label,
            "sections": response.sections.model_dump(mode="json") if response.sections else None,
        },
    }
    # The legacy Ask service persists successful answers itself. This call is
    # idempotent and also completes short-circuit responses such as a mismatch.
    await conversations.add_message(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        role="assistant",
        content=response.answer,
        status="complete",
        citations=response.citations,
        recommended_forms=response.recommended_forms,
        procedure_detail=response.procedure_detail,
        answer_sections=[
            section.model_dump(mode="json")
            for section in (response.answer_sections or [])
        ] or None,
        grounding_status=response.grounding_status,
        attachments=[
            {"kind": "chat_engine", "value": "legal"},
            {"kind": "response_mode", "value": response.response_mode or "answer"},
            presentation_attachment,
        ],
    )
    return response
