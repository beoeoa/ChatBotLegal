from __future__ import annotations

import asyncio
import hashlib
import json

from api.ask_progress import safe_public_response_payload, stream_ask_progress
from api.models import AskResponse
from api.routers.search import _ask_error_response
from open_notebook.exceptions import ExternalServiceError


def _events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event = "message"
        payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if payload is not None:
            events.append((event, payload))
    return events


def test_external_provider_error_is_safe_and_does_not_expose_provider_body():
    provider_body = "provider response chunk-id=secret trace-id=secret"
    status, detail = _ask_error_response(ExternalServiceError(provider_body))

    assert status == 502
    assert detail["code"] == "AI_SERVICE_ERROR"
    assert detail["message"]
    assert provider_body not in detail["message"]
    assert "chunk-id" not in detail["message"].casefold()
    assert "trace-id" not in detail["message"].casefold()
    assert detail["retryable"] is True


def test_sse_legacy_final_preserves_answer_and_citations_but_hides_trace_for_non_admin():
    async def execute(_progress):
        return AskResponse(
            answer="Nội dung đã được kiểm chứng.",
            question="Câu hỏi kiểm thử",
            citations=[
                {
                    "law_number": "60/2014/QH13",
                    "article_number": "13",
                    "source_url": "https://official.example/law",
                    "chunk_id": "chunk-secret",
                }
            ],
            answer_sections=[],
            rag_trace={"chunk_id": "trace-secret"},
        )

    async def collect(role: str) -> list[tuple[str, dict]]:
        body = "".join(
            [
                chunk
                async for chunk in stream_ask_progress(
                    execute,
                    trace_id=f"step7-{role}",
                    conversation_id=None,
                    idempotency_key=f"step7-idem-{role}",
                    effective_role=role,
                )
            ]
        )
        return _events(body)

    citizen_events = asyncio.run(collect("citizen"))
    admin_events = asyncio.run(collect("admin"))
    citizen_final = next(data["response"] for name, data in citizen_events if name == "final")
    admin_final = next(data["response"] for name, data in admin_events if name == "final")

    assert citizen_final["answer"] == "Nội dung đã được kiểm chứng."
    assert citizen_final["citations"][0]["law_number"] == "60/2014/QH13"
    assert citizen_final["rag_trace"] is None
    assert admin_final["rag_trace"] is not None
    encoded = json.dumps(citizen_final, ensure_ascii=False)
    assert "chunk-secret" not in encoded
    assert "trace-secret" not in encoded


def test_api_public_projection_keeps_forms_and_sections_without_internal_ids():
    projected = safe_public_response_payload(
        {
            "answer": "Đã kiểm chứng.",
            "citations": [
                {
                    "law_number": "60/2014/QH13",
                    "article_number": "13",
                    "source_url": "https://official.example/law",
                    "chunk_id": "chunk-secret",
                }
            ],
            "answer_sections": [
                {
                    "issue_id": "issue-1",
                    "title": "Hồ sơ",
                    "status": "sufficiently_evidenced",
                    "answer": "Chuẩn bị tờ khai.",
                    "facet": "documents",
                    "priority": "critical",
                    "claim_types": ["documents", "next_action"],
                    "citations": [
                        {
                            "law_number": "60/2014/QH13",
                            "article_number": "16",
                            "packet_id": "packet-secret",
                        }
                    ],
                }
            ],
            "recommended_forms": [
                {
                    "procedure_id": "dang_ky_khai_sinh",
                    "review_status": "approved",
                }
            ],
            "rag_trace": {"request_id": "request-secret", "coverage": {"documents": "verified"}},
        },
        include_admin_trace=False,
    )

    encoded = json.dumps(projected, ensure_ascii=False)
    assert projected["answer"] == "Đã kiểm chứng."
    assert projected["citations"][0]["law_number"] == "60/2014/QH13"
    assert projected["answer_sections"][0]["citations"][0]["article_number"] == "16"
    assert projected["answer_sections"][0]["facet"] == "documents"
    assert projected["answer_sections"][0]["priority"] == "critical"
    assert projected["answer_sections"][0]["claim_types"] == ["documents", "next_action"]
    assert projected["recommended_forms"][0]["procedure_id"] == "dang_ky_khai_sinh"
    assert projected["rag_trace"] is None
    assert all(marker not in encoded for marker in ("chunk-secret", "packet-secret", "request-secret"))


def test_public_projection_does_not_downgrade_server_verified_quote_proof():
    quote = "Người đăng ký nộp tờ khai theo quy định."
    citation = {
        "document_title": "Luật kiểm thử",
        "law_number": "01/2026/QH15",
        "article_number": "1",
        "effective_status": "active",
        "source_url": "https://example.gov.vn/law",
        "verification_level": "content_quote",
        "verification_status": "verified",
        "proof": {
            "support_quote": quote,
            "char_start": 10,
            "char_end": 10 + len(quote),
            "text_hash": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        },
    }

    projected = safe_public_response_payload(
        {
            "answer": quote,
            "citations": [citation],
            "answer_sections": [
                {
                    "issue_id": "issue-1",
                    "title": "Kết luận",
                    "status": "sufficiently_evidenced",
                    "answer": quote,
                    "citations": [citation],
                }
            ],
        },
        include_admin_trace=False,
    )

    assert projected["citations"][0]["verification_level"] == "content_quote"
    assert projected["citations"][0]["verification_status"] == "verified"
    assert projected["citations"][0]["proof"]["support_quote"] == quote
    assert (
        projected["answer_sections"][0]["citations"][0]["verification_level"]
        == "content_quote"
    )
