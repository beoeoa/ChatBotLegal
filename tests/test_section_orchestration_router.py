import asyncio

from api.models import AskRequest
from api.routers import search
from api.routers.search import (
    _append_request_exact_identifiers,
    _build_section_graph_input,
)
from api.legal_exact_retrieval import plan_exact_lookup


def test_section_graph_input_uses_only_the_current_issue_for_retrieval():
    graph_input = _build_section_graph_input(
        issue_id="issue-2",
        issue_query="Lệ phí của thủ tục này là bao nhiêu?",
        issue_domain="administrative",
        request_id="request-opaque-1",
        role="citizen",
        selected_domain="land",
        question_type="procedure",
        required_sections=["fee"],
        legal_as_of="2026-07-18",
    )

    assert graph_input["question"] == "Lệ phí của thủ tục này là bao nhiêu?"
    assert graph_input["retrieval_question"] == "Lệ phí của thủ tục này là bao nhiêu?"
    assert graph_input["issue_id"] == "issue-2"
    assert graph_input["request_id"] == "request-opaque-1"
    assert graph_input["domain"] == "administrative"


def test_split_issue_keeps_exact_document_identity_from_original_question():
    query = _append_request_exact_identifiers(
        "thẩm quyền và nơi nộp; khu đất tại xã có diện tích bao nhiêu",
        request_exact_plan=plan_exact_lookup(
            "Theo Nghị quyết 22/2025/NQ-HĐND, khu đất tại xã có diện tích bao nhiêu?"
        ),
    )

    assert plan_exact_lookup(query).law_number == "22/2025/NQ-HDND"


def test_orchestration_keeps_supported_issue_when_another_issue_has_no_evidence(monkeypatch):
    class FakeGraph:
        async def astream(self, *, input, config, stream_mode):
            del config, stream_mode
            if input["issue_id"] == "issue-1":
                source = {
                    "id": "legal:source-1",
                    "request_id": input["request_id"],
                    "issue_id": input["issue_id"],
                    "domain": "land",
                    "effective_status": "active",
                    "official": True,
                    "scope": "central",
                    "source_url": "https://official.example/land",
                    "document_title": "Luật Đất đai",
                    "law_number": "31/2024/QH15",
                    "article_number": "137",
                }
                yield {"provide_answer": {"evidence": [source]}}
                yield {"write_final_answer": {"final_answer": "Theo 31/2024/QH15, Điều 137."}}
            else:
                yield {"provide_answer": {"evidence": []}}
                yield {"write_final_answer": {"final_answer": "Không có căn cứ."}}

    monkeypatch.setattr(search, "ask_graph", FakeGraph())
    sections, aggregate, _, _ = asyncio.run(
        search._run_section_orchestration(
            ask_request=AskRequest(question="Tranh chấp thửa đất giải quyết thế nào; lệ phí bao nhiêu?", role="citizen"),
            request_id="request-opaque-2",
            question_policy={"question_type": "procedure", "required_sections": []},
            legal_as_of="2026-07-18",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert sections[1].status == "insufficiently_evidenced"
    assert aggregate["grounding_status"] == "partially_grounded"
    assert "31/2024/QH15" in aggregate["answer"]
