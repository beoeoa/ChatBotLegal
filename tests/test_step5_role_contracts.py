from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from api import conversation_service as conversation
from api.ask_progress import _safe_sources, stream_ask_progress
from api.legal_section_grounding import LegalIssue
from api.models import AskRequest, AskResponse
from api.routers import search


@pytest.fixture(autouse=True)
def _reset_structured_provider_circuit():
    """Keep provider-circuit state from leaking between contract tests."""

    search._STRUCTURED_PROVIDER_CIRCUIT.clear()
    yield
    search._STRUCTURED_PROVIDER_CIRCUIT.clear()


def test_structured_source_gap_exposes_missing_facets_without_trace_ids():
    gaps = search._structured_source_gap(
        {
            "claim_validation": {
                "issues": [
                    {
                        "missing_facets": ["documents"],
                        "unavailable_facets": ["form", "deadline"],
                        "issue_id": "issue-secret",
                    },
                    {
                        "missing_facets": ["documents"],
                        "unavailable_facets": [],
                    },
                ]
            }
        }
    )

    assert gaps == ["documents", "form", "deadline"]
    assert all("issue" not in item for item in gaps)


def _run(coro):
    return asyncio.run(coro)


def _events(body: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data: dict | None = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if data is not None:
            parsed.append((event, data))
    return parsed


def test_sse_sources_expose_only_public_citation_metadata():
    payload = _safe_sources(
        {
            "citations": [
                {
                    "chunk_id": "chunk-secret",
                    "document_id": "document-secret",
                    "trace_id": "trace-secret",
                    "packet_id": "packet-secret",
                    "law_number": "60/2014/QH13",
                    "document_title": "Luật Hộ tịch",
                    "article_number": "16",
                    "effective_status": "active",
                    "source_url": "https://official.example/law",
                }
            ]
        }
    )

    encoded = json.dumps(payload)
    assert payload["citations"] == [
        {
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch",
            "article_number": "16",
            "effective_status": "active",
            "source_url": "https://official.example/law",
        }
    ]
    assert not any(
        marker in encoded
        for marker in ("chunk-secret", "document-secret", "trace-secret", "packet-secret")
    )


def test_sse_role_isolation_keeps_trace_admin_only():
    async def execute(_progress):
        return AskResponse(
            answer="Nội dung đã kiểm chứng.",
            question="Câu hỏi fixture",
            rag_trace={"evidence_coverage": {"authority": {"status": "verified"}}},
            grounding_status="fully_grounded",
        )

    async def collect(role: str) -> list[tuple[str, dict]]:
        body = "".join(
            [
                chunk
                async for chunk in stream_ask_progress(
                    execute,
                    trace_id=f"transport-{role}",
                    conversation_id=None,
                    idempotency_key=f"idempotency-{role}",
                    effective_role=role,
                )
            ]
        )
        return _events(body)

    citizen = _run(collect("citizen"))
    officer = _run(collect("officer"))
    admin = _run(collect("admin"))

    citizen_final = next(data["response"] for name, data in citizen if name == "final")
    officer_final = next(data["response"] for name, data in officer if name == "final")
    admin_final = next(data["response"] for name, data in admin if name == "final")
    assert citizen_final["rag_trace"] is None
    assert officer_final["rag_trace"] is None
    assert admin_final["rag_trace"]["evidence_coverage"]["authority"]["status"] == "verified"


def test_history_round_trip_preserves_sections_and_forms_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        conversation, "JSON_FALLBACK_DIR", str(tmp_path / "conversations")
    )
    monkeypatch.setattr(
        conversation, "_use_surreal", lambda: asyncio.sleep(0, result=False)
    )
    owner = "user:step5-citizen"
    created = _run(
        conversation.create_conversation(
            owner_key=owner,
            role_context="citizen",
            title="Step 5 history",
        )
    )
    sections = [
        {
            "issue_id": "issue-1",
            "title": "Biểu mẫu",
            "status": "insufficiently_evidenced",
            "answer": None,
            "guidance": None,
            "limitation": "Chưa có biểu mẫu chính thức đã duyệt.",
            "citations": [],
            "clarifying_question": None,
        }
    ]
    _run(
        conversation.add_message(
            created["id"],
            owner_key=owner,
            role="assistant",
            role_context="citizen",
            content="Chưa có biểu mẫu chính thức đã duyệt.",
            status="complete",
            answer_sections=sections,
            forms_unavailable=True,
            rag_trace=None,
        )
    )

    detail = _run(
        conversation.get_conversation(
            created["id"],
            owner_key=owner,
            role_context="citizen",
        )
    )
    assistant = detail["messages"][0]
    assert assistant["answer_sections"] == sections
    assert assistant["forms_unavailable"] is True
    assert assistant["rag_trace"] is None


def test_generic_administrative_issue_is_bound_to_detected_serving_domain(
    monkeypatch,
):
    issue = LegalIssue(
        request_id="step5-request",
        issue_id="issue-1",
        title="Thẩm quyền",
        query_text="Cấp giấy phép xây dựng nhà ở riêng lẻ tại Hải Phòng",
        intent="authority",
        domain="administrative",
        split_confidence="high",
    )

    class Client:
        async def search_batch(self, payload):
            runtime_issue = payload["issues"][0]
            return {
                "issues": [
                    {
                        "issue_id": runtime_issue["issue_id"],
                        "results": [
                            {
                                "source_id": "source-1",
                                "request_id": payload["request_id"],
                                "issue_id": runtime_issue["issue_id"],
                                "domain": "dat_dai_xay_dung",
                                "effective_status": "active",
                                "official": True,
                                "scope": "central",
                                "source_url": "https://official.example/building",
                                "content": "Ủy ban nhân dân có thẩm quyền cấp giấy phép xây dựng.",
                                "document_title": "Luật Xây dựng",
                                "law_number": "50/2014/QH13",
                                "article_number": "103",
                            }
                        ],
                    }
                ]
            }

    async def provision(*_args, **_kwargs):
        return object()

    async def invoke(*_args, **_kwargs):
        return SimpleNamespace(
            content=json.dumps(
                {
                    "issues": [
                        {
                            "issue_id": "issue-1",
                            "claims": [
                                {
                                    "claim_text": "Ủy ban nhân dân có thẩm quyền cấp giấy phép xây dựng.",
                                    "claim_type": "authority",
                                    "evidence_id": "evidence-1",
                                    "support_quote": "Ủy ban nhân dân có thẩm quyền cấp giấy phép xây dựng.",
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(search, "plan_legal_issues", lambda *_args, **_kwargs: [issue])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: Client())
    monkeypatch.setattr(search, "provision_langchain_model", provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", invoke)
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, _aggregate, _rows, trace = _run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Cấp giấy phép xây dựng nhà ở riêng lẻ tại Hải Phòng",
                role="officer",
            ),
            request_id="step5-request",
            question_policy={
                "question_type": "land_construction",
                "required_sections": ["authority"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert trace["claim_validation"]["quality_gate"]["pass"] is True


def test_timeout_fallback_covers_available_facet_with_exact_source_text(
    monkeypatch,
):
    issue = LegalIssue(
        request_id="step5-timeout",
        issue_id="issue-1",
        title="Thời hạn",
        query_text="Thời hạn giải quyết là bao lâu?",
        intent="deadline",
        domain="civil_status",
        split_confidence="high",
    )

    class Client:
        async def search_batch(self, payload):
            return {
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "results": [
                            {
                                "source_id": "source-1",
                                "request_id": payload["request_id"],
                                "issue_id": "issue-1",
                                "domain": "ho_tich_chung_thuc",
                                "effective_status": "active",
                                "official": True,
                                "scope": "central",
                                "source_url": "https://official.example/deadline",
                                "content": "Thời hạn giải quyết là 03 ngày làm việc.",
                                "document_title": "Nghị định kiểm thử",
                                "law_number": "01/2026/NĐ-CP",
                                "article_number": "12",
                            }
                        ],
                    }
                ]
            }

    async def provision(*_args, **_kwargs):
        return object()

    async def timeout(*_args, **_kwargs):
        raise asyncio.TimeoutError

    monkeypatch.setattr(search, "plan_legal_issues", lambda *_args, **_kwargs: [issue])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: Client())
    monkeypatch.setattr(search, "provision_langchain_model", provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", timeout)
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, _aggregate, _rows, trace = _run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?",
                role="citizen",
            ),
            request_id="step5-timeout",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert sections[0].answer == (
        "- **Mốc thời gian/thời hạn:** Thời hạn giải quyết là 03 ngày làm việc."
    )
    assert trace["metric"]["repair_count"] == 0
    assert trace["metric"]["error_category"] == "provider_timeout"
    assert trace["claim_validation"]["coverage_ratio"] == 1.0
    assert trace["claim_validation"]["quality_gate"]["pass"] is True


def test_valid_but_empty_model_output_uses_same_extractive_validator_path(
    monkeypatch,
):
    issue = LegalIssue(
        request_id="step5-empty",
        issue_id="issue-1",
        title="Thời hạn",
        query_text="Thời hạn giải quyết là bao lâu?",
        intent="deadline",
        domain="civil_status",
        split_confidence="high",
    )

    class Client:
        async def search_batch(self, payload):
            return {
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "results": [
                            {
                                "source_id": "source-1",
                                "request_id": payload["request_id"],
                                "issue_id": "issue-1",
                                "domain": "ho_tich_chung_thuc",
                                "effective_status": "active",
                                "official": True,
                                "scope": "central",
                                "source_url": "https://official.example/deadline",
                                "content": "Thời hạn giải quyết là 03 ngày làm việc.",
                                "document_title": "Nghị định kiểm thử",
                                "law_number": "01/2026/NĐ-CP",
                                "article_number": "12",
                            }
                        ],
                    }
                ]
            }

    async def provision(*_args, **_kwargs):
        return object()

    async def invoke(*_args, **_kwargs):
        return SimpleNamespace(
            content=json.dumps(
                {"issues": [{"issue_id": "issue-1", "claims": []}]},
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(search, "plan_legal_issues", lambda *_args, **_kwargs: [issue])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: Client())
    monkeypatch.setattr(search, "provision_langchain_model", provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", invoke)
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, _aggregate, _rows, trace = _run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?",
                role="citizen",
            ),
            request_id="step5-empty",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert trace["metric"]["repair_count"] == 0
    assert trace["claim_validation"]["quality_gate"]["pass"] is True
    assert trace["claim_validation"]["fallback_reason"] == "invalid_output"
