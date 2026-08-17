import asyncio

from api.legal_answer_router import (
    EXACT_ARTICLE,
    GENERAL_LEGAL,
    HISTORICAL,
    PIPELINE_VERSION,
    PROCEDURE_FORM,
    LegalAnswerRoute,
    is_answer_pipeline_v2_enabled,
    route_legal_answer,
)
from api.legal_section_grounding import LegalIssue
from api.models import AskRequest
from api.routers import search


def test_v2_rollout_defaults_to_disabled_and_is_role_scoped():
    assert not is_answer_pipeline_v2_enabled("citizen", environ={})
    environ = {
        "LEGAL_ANSWER_PIPELINE_V2_ENABLED": "true",
        "LEGAL_ANSWER_PIPELINE_V2_ROLES": "citizen",
    }
    assert is_answer_pipeline_v2_enabled("citizen", environ=environ)
    assert not is_answer_pipeline_v2_enabled("officer", environ=environ)
    assert not is_answer_pipeline_v2_enabled("admin", environ=environ)


def test_exact_article_route_keeps_article_as_one_issue():
    result = route_legal_answer(
        "Điều 18a của Luật 88/2025/QH15 quy định điều kiện, thời hạn và thẩm quyền thế nào?"
    )

    assert result.pipeline_version == PIPELINE_VERSION
    assert result.answer_route == EXACT_ARTICLE
    assert len(result.issues) == 1
    assert result.exact_law_number == "88/2025/QH15"
    assert result.exact_article_number.casefold() == "18a"


def test_ordinary_commas_and_conjunctions_do_not_create_extra_issues():
    result = route_legal_answer(
        "Đăng ký khai sinh cần hồ sơ gì, nộp ở đâu và thời hạn bao lâu?"
    )

    assert result.answer_route == PROCEDURE_FORM
    assert len(result.issues) == 1
    assert "hồ sơ" in result.issues[0].query_text.casefold()
    assert "thời hạn" in result.issues[0].query_text.casefold()


def test_explicit_numbered_questions_remain_independent():
    result = route_legal_answer(
        "(1) Điều 18a của Luật 88/2025/QH15 quy định gì? "
        "(2) Điều 37a của Luật 88/2025/QH15 quy định gì?"
    )

    assert len(result.issues) == 2
    assert "18a" in result.issues[0].query_text
    assert "37a" in result.issues[1].query_text


def test_historical_request_has_priority_over_general_route():
    result = route_legal_answer(
        "Tại ngày 01/01/2020 quy định về đăng ký thường trú được áp dụng thế nào?"
    )

    assert result.answer_route == HISTORICAL
    assert len(result.issues) == 1


def test_ambiguous_generic_form_code_fails_closed_with_clarification():
    result = route_legal_answer("Cho tôi tải Mẫu 01")

    assert result.answer_route == PROCEDURE_FORM
    assert result.procedure_id is None
    assert result.clarifying_questions
    assert "thủ tục" in result.clarifying_questions[0].casefold()


def test_general_legal_route_for_non_procedure_question():
    result = route_legal_answer("Quyền khiếu nại của công dân được pháp luật bảo vệ thế nào?")

    assert result.answer_route == GENERAL_LEGAL
    assert len(result.issues) == 1


def test_explicitly_incomplete_question_returns_clarification_without_guessing():
    result = route_legal_answer(
        "Tôi cần hỏi về đất đai nhưng chưa cho biết nơi có tài sản. "
        "Hệ thống có thể kết luận ngay không; nếu không, cần tôi bổ sung chính xác thông tin gì?"
    )

    assert result.clarifying_questions
    assert "nơi có tài sản" in result.clarifying_questions[0]
    assert result.decision_reason == "missing_facts_require_clarification"


def test_structured_v2_disables_ai_planner_and_keeps_facets_in_one_issue(monkeypatch):
    captured = {}

    class EmptyClient:
        async def search_batch(self, payload):
            captured["payload"] = payload
            return {
                "issues": [
                    {"issue_id": issue["issue_id"], "results": []}
                    for issue in payload["issues"]
                ]
            }

    async def planner_must_not_run(*_args, **_kwargs):
        raise AssertionError("V2 must not invoke the AI problem-map planner")

    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")
    monkeypatch.setenv("LEGAL_PROBLEM_MAP_LLM_ENABLED", "true")
    monkeypatch.setattr(search, "get_legal_search_client", lambda: EmptyClient())
    monkeypatch.setattr(search, "provision_langchain_model", planner_must_not_run)

    sections, _aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Đăng ký khai sinh cần hồ sơ gì, nộp ở đâu và thời hạn bao lâu?",
                role="citizen",
            ),
            request_id="request-router-v2-1",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["documents", "submission_place", "processing_time"],
            },
            legal_as_of="2026-08-11",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert len(captured["payload"]["issues"]) == 1
    assert len(sections) == 1
    assert trace["pipeline_version"] == PIPELINE_VERSION
    assert trace["answer_route"] == PROCEDURE_FORM
    assert trace["problem_map"]["planner_mode"] == "deterministic"


def test_ambiguous_form_route_returns_clarification_without_retrieval(monkeypatch):
    class ForbiddenClient:
        async def search_batch(self, _payload):
            raise AssertionError("ambiguous form must stop before retrieval")

    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")
    monkeypatch.setattr(search, "get_legal_search_client", lambda: ForbiddenClient())

    sections, aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(question="Cho tôi tải Mẫu 01", role="citizen"),
            request_id="request-router-v2-2",
            question_policy={"question_type": "procedure", "required_sections": ["form"]},
            legal_as_of="2026-08-11",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert rows == []
    assert sections[0].status == "insufficiently_evidenced"
    assert sections[0].clarifying_question
    assert aggregate["grounding_status"] == "insufficient_evidence"
    assert trace["claim_validation"]["fallback_reason"] == "clarification_required"


def test_v2_local_uses_same_evidence_and_single_validator_without_cloud_repair(monkeypatch):
    calls = {"ollama": 0}

    class Client:
        async def search_batch(self, payload):
            issue = payload["issues"][0]
            return {
                "issues": [
                    {
                        "issue_id": issue["issue_id"],
                        "results": [
                            {
                                "source_id": "source-local-v2",
                                "request_id": payload["request_id"],
                                "issue_id": issue["issue_id"],
                                "domain": "civil_status",
                                "effective_status": "active",
                                "official": True,
                                "scope": "central",
                                "source_url": "https://example.gov.vn/source",
                                "content": "Thời hạn giải quyết là 03 ngày làm việc.",
                                "document_title": "Nghị định kiểm thử",
                                "law_number": "01/2026/NĐ-CP",
                                "article_number": "12",
                            }
                        ],
                    }
                ],
                "retrieval_decision": {
                    "ranking_strategy": payload["ranking_strategy"],
                    "learned_reranker_enabled": payload["enable_learned_reranker"],
                },
            }

    async def ollama(_model, prompt):
        calls["ollama"] += 1
        assert "EVIDENCE" in prompt
        return (
            '{"issues":[{"issue_id":"issue-1","claims":[{'
            '"claim_text":"Thời hạn giải quyết là 03 ngày làm việc.",'
            '"claim_type":"deadline","evidence_id":"evidence-1",'
            '"support_quote":"Thời hạn giải quyết là 03 ngày làm việc."}]}]}'
        )

    def forbidden_repair(*_args, **_kwargs):
        raise AssertionError("V2 must not run the legacy claim repair layer")

    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")
    monkeypatch.setattr(search, "get_legal_search_client", lambda: Client())
    monkeypatch.setattr(search, "_call_ollama", ollama)
    monkeypatch.setattr(search, "enforce_explicit_facet_claims", forbidden_repair)
    monkeypatch.setattr(search, "supplement_rule_source_diversity", forbidden_repair)

    sections, _aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn đăng ký khai sinh là bao lâu?",
                role="citizen",
                offline_mode=True,
                offline_model="qwen2.5:3b",
            ),
            request_id="request-local-v2-1",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-08-11",
            strategy_model_id="ollama:qwen2.5:3b",
            answer_model_id="ollama:qwen2.5:3b",
            final_answer_model_id="ollama:qwen2.5:3b",
        )
    )

    assert calls["ollama"] == 1
    assert rows
    assert sections[0].status == "sufficiently_evidenced"
    assert trace["pipeline_version"] == PIPELINE_VERSION
    assert trace["retrieval_decision"]["ranking_strategy"] == "rrf_v2"
    assert trace["retrieval_decision"]["learned_reranker_enabled"] is False


def test_feature017_resolves_procedure_directly_and_enters_answer_evidence(monkeypatch):
    long_question = (
        "Tôi cần làm thủ tục điều chỉnh "
        + "ranh giới, vị trí, diện tích, " * 14
        + "giữa bản đồ quy hoạch và hồ sơ địa chính; hồ sơ và biểu mẫu gồm những gì?"
    )
    issue = LegalIssue(
        request_id="request-feature017-ask",
        issue_id="issue-feature017-form",
        title="Biểu mẫu thường trú",
        query_text="Tôi cần biểu mẫu làm thường trú",
        intent="form",
        domain="cu_tru_an_ninh",
        split_confidence="high",
    )
    route = LegalAnswerRoute(
        pipeline_version=PIPELINE_VERSION,
        answer_route=PROCEDURE_FORM,
        # A long official procedure name may contain commas that the generic
        # issue splitter mistakes for separate legal disputes. The active
        # release identity must still be allowed to collapse it safely.
        issues=(
            issue,
            LegalIssue(
                request_id="request-feature017-ask",
                issue_id="issue-split-artifact",
                title="split artifact",
                query_text="split artifact",
                intent="dispute",
                domain="cu_tru_an_ninh",
            ),
        ),
        procedure_id=None,
    )

    class EmptyClient:
        async def search_batch(self, payload):
            return {"issues": [{"issue_id": payload["issues"][0]["issue_id"], "results": []}]}

    def resolve_feature017(**_kwargs):
        return {
            "router_mode": "active",
            "status": "resolved",
            "procedure_id": "dang_ky_cu_tru",
            "identity_confirmation": {
                "confirmed": True,
                "procedure_id": "dang_ky_cu_tru",
            },
            "evidence_packet": {
                "release_id": "forms-test-release",
                "identity_confirmation": {
                    "confirmed": True,
                    "procedure_id": "dang_ky_cu_tru",
                },
            },
            "recommended_forms": [{
                "form_id": "ct01",
                "procedure_id": "dang_ky_cu_tru",
                "name": "Tờ khai thay đổi thông tin cư trú",
                "display_name": "Tờ khai thay đổi thông tin cư trú",
                "required_or_conditional": "required",
                "download_url": "https://vbpl.vn/ct01.pdf",
                "source_url": "https://vbpl.vn/ct01.pdf",
                "source_checksum": "a" * 64,
                "effective_from": "2026-08-11",
                "effective_to": None,
                "review_status": "approved",
                "asset_kind": "file",
                "has_official_file": True,
                "has_official_resource": True,
            }],
            "rejected_forms": [],
            "data_gap_reasons": [],
        }

    async def ollama(_model, prompt):
        assert "Tờ khai thay đổi thông tin cư trú" in prompt
        return (
            '{"issues":[{"issue_id":"issue-1","claims":[{'
            '"claim_text":"Biểu mẫu bắt buộc đã duyệt: Tờ khai thay đổi thông tin cư trú.",'
            '"claim_type":"form","evidence_id":"evidence-1",'
            '"support_quote":"Biểu mẫu bắt buộc đã duyệt: Tờ khai thay đổi thông tin cư trú."}]}]}'
        )

    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")
    monkeypatch.setattr(search, "route_legal_answer", lambda _question: route)
    monkeypatch.setattr(search, "get_legal_search_client", lambda: EmptyClient())
    monkeypatch.setattr("api.form_router_v3.resolve_from_configured_release", resolve_feature017)
    official_call = {}

    def build_official(**kwargs):
        official_call.update(kwargs)
        return [], {
            "adapter": "official_procedure_evidence_v1",
            "row_count": 0,
            "matched_procedure_codes": [],
        }

    monkeypatch.setattr(search, "build_official_procedure_evidence", build_official)
    monkeypatch.setattr(search, "_call_ollama", ollama)

    sections, _aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question=long_question,
                role="citizen",
                offline_mode=True,
                offline_model="qwen2.5:3b",
            ),
            request_id="request-feature017-ask",
            question_policy={"question_type": "procedure", "required_sections": ["form"]},
            legal_as_of="2026-08-11",
            strategy_model_id="ollama:qwen2.5:3b",
            answer_model_id="ollama:qwen2.5:3b",
            final_answer_model_id="ollama:qwen2.5:3b",
        )
    )
    assert any(row.get("form_id") == "ct01" for row in rows)
    assert sections[0].status == "sufficiently_evidenced", trace["claim_validation"]
    assert trace["form_router"]["procedure_id"] == "dang_ky_cu_tru"
    assert trace["form_router"]["recommended_forms"][0]["form_id"] == "ct01"
    assert official_call["procedure_id"] == "dang_ky_cu_tru"
    assert official_call["required_facets_by_issue"] == {"issue-1": ["form"]}
    assert len(official_call["issues"]) == 1
    assert len(official_call["issues"][0].title) <= 300
