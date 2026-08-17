import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
import httpx

from api.legal_section_grounding import LegalIssue
from api.legal_exact_retrieval import plan_exact_lookup
from api.legal_provider_circuit import StructuredProviderCircuit
from api.models import AskRequest
from api.routers import search


@pytest.fixture(autouse=True)
def _isolated_provider_circuit(monkeypatch):
    monkeypatch.setattr(
        search,
        "_STRUCTURED_PROVIDER_CIRCUIT",
        StructuredProviderCircuit(
            failure_threshold=1,
            cooldown_seconds=120,
        ),
    )


def _issue(request_id: str = "request-opaque-3") -> LegalIssue:
    return LegalIssue(
        request_id=request_id,
        issue_id="issue-1",
        title="Thời hạn",
        query_text="Thời hạn giải quyết là bao lâu?",
        intent="deadline",
        domain="civil_status",
        split_confidence="high",
    )


def test_exact_article_context_is_restored_when_primary_child_is_filtered():
    assembled = "1. Quy định chung.\n2. Nội dung Điều luật."
    projected = [
        {
            "chunk_id": 101,
            "exact_article_order": 0,
            "exact_article_packet_ref": "document:1:article:2",
            "exact_article_packet_status": "complete",
            "exact_article_assembled_content": assembled,
            "exact_article_full_article_character_count": len(assembled),
        },
        {
            "chunk_id": 102,
            "exact_article_order": 1,
            "exact_article_packet_ref": "document:1:article:2",
            "exact_article_packet_status": "complete",
            "exact_article_assembled_content": None,
        },
    ]

    retained = search._restore_exact_article_context_after_relevance(
        [projected[1]], projected
    )

    assert retained[0]["exact_article_context_restored"] is True
    assert retained[0]["exact_article_assembled_content"] == assembled
    assert retained[0]["parent_context"] == assembled


def test_multi_issue_local_article_inherits_one_request_law():
    request_plan = plan_exact_lookup(
        "Luật 73/2025/QH15 Điều 11; Điều 13"
    )
    local = search._issue_local_exact_plan(
        "Điều 13 cần điều kiện gì?",
        request_exact_plan=request_plan,
        issue_count=2,
    )
    assert local.law_number == "73/2025/QH15"
    assert local.article_number == "13"
    assert local.article_law_pairs == (("73/2025/QH15", "13"),)


def test_reviewed_natural_language_aliases_preserve_multiple_law_article_pairs():
    plan = plan_exact_lookup(
        "Quyền của công dân về cư trú (phần 8) và các hành vi bị nghiêm cấm "
        "(phần 7 về căn cước)."
    )
    assert set(plan.article_law_pairs) == {
        ("68/2020/QH14", "8"),
        ("26/2023/QH15", "7"),
    }


def test_reviewed_aliases_bind_articles_for_natural_language_multi_issue_request():
    plan = plan_exact_lookup(
        "Những việc không được làm của nhà giáo theo Điều 11 và "
        "thời điểm thẻ bảo hiểm y tế có giá trị sử dụng theo Điều 13."
    )
    assert set(plan.article_law_pairs) == {
        ("73/2025/QH15", "11"),
        ("188/2025/ND-CP", "13"),
    }


def test_alias_issue_query_gets_literal_identity_for_exact_retrieval():
    request_plan = plan_exact_lookup(
        "Quyền của công dân về cư trú (phần 8) và các hành vi bị nghiêm cấm "
        "(phần 7 về căn cước)."
    )
    query = search._append_request_exact_identifiers(
        "Quyền của công dân về cư trú (phần 8)",
        request_exact_plan=request_plan,
    )
    assert "68/2020/QH14 Điều 8" in query


def test_land_responsibility_alias_uses_verified_article_seven():
    plan = plan_exact_lookup(
        "Người chịu trách nhiệm trước Nhà nước đối với đất được giao"
    )
    assert plan.article_law_pairs == (("31/2024/QH15", "7"),)


def test_natural_law_title_binds_explicit_articles_without_a_law_number():
    plan = plan_exact_lookup(
        "Theo Luật Hộ tịch, Điều 47 và Điều 48 quy định gì?"
    )
    assert plan.law_numbers == ("60/2014/QH13",)
    assert set(plan.article_law_pairs) == {
        ("60/2014/QH13", "47"),
        ("60/2014/QH13", "48"),
    }


class _Client:
    async def search_batch(self, payload):
        issue = payload["issues"][0]
        return {
            "issues": [{
                "issue_id": issue["issue_id"],
                "results": [{
                    "source_id": "source-1",
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
                }],
            }],
        }


class _MultiClient:
    async def search_batch(self, payload):
        rows = []
        for issue in payload["issues"]:
            content = (
                "Thời hạn giải quyết là 03 ngày làm việc."
                if issue["intent"] == "deadline"
                else "Lệ phí đăng ký là 20.000 đồng."
            )
            rows.append({
                "issue_id": issue["issue_id"],
                "results": [{
                    "source_id": f"source-{issue['issue_id']}",
                    "request_id": payload["request_id"],
                    "issue_id": issue["issue_id"],
                    "domain": "civil_status",
                    "effective_status": "active",
                    "official": True,
                    "scope": "central",
                    "source_url": "https://example.gov.vn/source",
                    "content": content,
                    "document_title": "Nghị định kiểm thử",
                    "law_number": "01/2026/NĐ-CP",
                    "article_number": "12",
                }],
            })
        return {"issues": rows}


class _EmptyClient:
    async def search_batch(self, payload):
        return {
            "issues": [
                {"issue_id": issue["issue_id"], "results": []}
                for issue in payload["issues"]
            ]
        }


class _SlowClient:
    def __init__(self) -> None:
        self.calls = 0

    async def search_batch(self, payload):
        del payload
        self.calls += 1
        await asyncio.sleep(1.0)
        return {"issues": []}


def test_optimized_profile_uses_fully_grounded_preflight_without_provider(
    monkeypatch,
):
    calls = {"provision": 0, "invoke": 0}

    async def unexpected_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        raise AssertionError("fully grounded preflight should skip provisioning")

    async def unexpected_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        raise AssertionError("fully grounded preflight should skip invocation")

    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ROLES", "citizen")
    monkeypatch.delenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED", raising=False)
    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _Client())
    monkeypatch.setattr(search, "provision_langchain_model", unexpected_provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", unexpected_invoke)
    search._STRUCTURED_MODEL_CACHE.clear()

    _sections, aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id="request-preflight",
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

    assert calls == {"provision": 0, "invoke": 0}
    assert trace["preflight_deterministic"] is True
    assert trace["answer_mode"] == "normal"
    assert trace["provider_error_code"] is None
    assert trace["metric"]["error_category"] == "none"
    assert aggregate["grounding_status"] == "fully_grounded"


@pytest.mark.parametrize(
    ("mode", "expected_error"),
    [
        ("invalid_json", "invalid_output"),
        ("timeout", "provider_timeout"),
        ("rate_limit", "provider_rate_limit"),
    ],
)
def test_orchestration_fails_closed_without_a_second_model_repair(
    monkeypatch, mode, expected_error
):
    calls = {"provision": 0, "invoke": 0}

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def fake_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        if mode == "timeout":
            raise asyncio.TimeoutError
        if mode == "rate_limit":
            response = httpx.Response(
                429,
                request=httpx.Request("POST", "https://provider.example/v1/chat"),
            )
            raise httpx.HTTPStatusError(
                "rate limited",
                request=response.request,
                response=response,
            )
        return SimpleNamespace(content="not valid json")

    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _Client())
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", fake_invoke)
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, _aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id="request-opaque-3",
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

    assert sections[0].citations
    assert calls == {"provision": 1, "invoke": 1}
    assert trace["metric"]["repair_count"] == 0
    assert trace["metric"]["error_category"] == expected_error
    assert trace["answer_mode"] == "verified_source_condensed"
    assert trace["provider_error_code"] == expected_error
    assert trace["claim_validation"]["claim_grounding_ratio"] == 1.0
    assert trace["claim_validation"]["coverage_ratio"] == 1.0
    assert trace["claim_validation"]["issues"][0]["issue_id"] == "issue-1"
    assert (
        trace["claim_validation"]["issues"][0]["coverage_matrix"][0]["status"]
        == "covered"
    )


def test_multi_issue_question_uses_one_model_call_and_passes_step3_gate(monkeypatch):
    calls = {"provision": 0, "invoke": 0}
    issues = [
        _issue(),
        LegalIssue(
            request_id="request-opaque-3",
            issue_id="issue-2",
            title="Lệ phí",
            query_text="Lệ phí bao nhiêu?",
            intent="fee",
            domain="civil_status",
            split_confidence="high",
        ),
    ]

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def fake_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        return SimpleNamespace(content="""{"issues":[
          {"issue_id":"issue-1","claims":[{
            "claim_text":"Thời hạn giải quyết là 03 ngày làm việc.",
            "claim_type":"deadline","evidence_id":"evidence-1",
            "support_quote":"Thời hạn giải quyết là 03 ngày làm việc"
          }]},
          {"issue_id":"issue-2","claims":[{
            "claim_text":"Lệ phí đăng ký là 20.000 đồng.",
            "claim_type":"fee","evidence_id":"evidence-2",
            "support_quote":"Lệ phí đăng ký là 20.000 đồng"
          }]}
        ]}""")

    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: issues)
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _MultiClient())
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", fake_invoke)
    search._STRUCTURED_MODEL_CACHE.clear()

    _sections, _aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn và lệ phí là bao nhiêu?", role="citizen"
            ),
            request_id="request-opaque-3",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time", "fee"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert calls == {"provision": 1, "invoke": 1}
    assert trace["metric"]["repair_count"] == 0
    assert trace["claim_validation"]["claim_grounding_ratio"] == 1.0
    assert trace["claim_validation"]["coverage_ratio"] == 1.0
    assert trace["claim_validation"]["quality_gate"]["pass"] is True
    assert trace["context_stats"]["character_count"] <= 6000
    assert trace["context_stats"]["maximum_characters"] == 6000


def test_retrieval_unavailable_records_zero_provisioning_without_server_error(
    monkeypatch,
):
    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _EmptyClient())

    sections, _aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id="request-opaque-3",
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

    assert sections[0].status == "insufficiently_evidenced"
    assert trace["metric"]["stage_timings_ms"]["provisioning"] == 0
    assert trace["metric"]["error_category"] == "retrieval_unavailable"


def test_provider_slot_limit_is_bounded_and_configurable(monkeypatch):
    monkeypatch.setenv("LEGAL_STRUCTURED_PROVIDER_MAX_CONCURRENCY", "6")
    search._STRUCTURED_MODEL_INVOCATION_SLOTS_BY_LOOP.clear()

    async def read_slots():
        return search._structured_model_invocation_slots()

    slots = asyncio.run(read_slots())

    assert slots._value == 6


def test_incomplete_exact_article_packet_never_calls_answer_model(monkeypatch):
    calls = {"search": 0, "provision": 0, "invoke": 0}
    exact_issue = LegalIssue(
        request_id="request-exact-incomplete",
        issue_id="issue-1",
        title="Nội dung Điều 73",
        query_text="Điều 73 của văn bản 60/2014/QH13 quy định gì?",
        intent="rule",
        domain="dat_dai_xay_dung",
        split_confidence="high",
    )

    class IncompleteExactClient:
        async def search_batch(self, payload):
            calls["search"] += 1
            issue_id = payload["issues"][0]["issue_id"]
            return {
                "issues": [
                    {
                        "issue_id": issue_id,
                        "results": [],
                        "exact_article_packets": [
                            {
                                "status": "incomplete",
                                "packet_ref": "document:60:article:73",
                                "law_number": "60/2014/QH13",
                                "article_number": "73",
                                "reason_codes": ["missing_chunk_indexes"],
                                "missing_chunk_indexes": [2],
                                "missing_structural_units": [
                                    "Khoản 2 > Điểm b"
                                ],
                            }
                        ],
                    }
                ]
            }

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def fake_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        return object()

    monkeypatch.setenv("LEGAL_PROBLEM_MAP_LLM_ENABLED", "false")
    monkeypatch.setattr(
        search, "plan_legal_issues", lambda *args, **kwargs: [exact_issue]
    )
    monkeypatch.setattr(
        search, "get_legal_search_client", lambda: IncompleteExactClient()
    )
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(
        search, "invoke_blocking_model_with_deadline", fake_invoke
    )
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question=exact_issue.query_text,
                role="citizen",
            ),
            request_id=exact_issue.request_id,
            question_policy={
                "question_type": "legal_lookup",
                "required_sections": ["applicable_rule", "legal_basis_links"],
            },
            legal_as_of="2026-08-10",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert calls == {"search": 2, "provision": 0, "invoke": 0}
    assert rows == []
    assert sections[0].status == "insufficiently_evidenced"
    assert "không dùng một chunk riêng lẻ" in aggregate["answer"]
    assert trace["exact_article_gate"]["ready"] is False
    assert trace["exact_article_gate"]["issues"]["issue-1"][
        "missing_chunk_indexes"
    ] == [2]


def test_expired_exact_article_is_labeled_instead_of_reported_as_missing(monkeypatch):
    calls = {"search": 0, "provision": 0, "invoke": 0}
    exact_issue = LegalIssue(
        request_id="request-expired-exact",
        issue_id="issue-1",
        title="Nội dung Điều 26",
        query_text="Điều 26 của văn bản 96/2014/TT-BQP hiện còn được dùng không?",
        intent="rule",
        domain="an_sinh_y_te_giao_duc",
        split_confidence="high",
    )

    class ExpiredExactClient:
        async def search_batch(self, payload):
            calls["search"] += 1
            issue_id = payload["issues"][0]["issue_id"]
            validity = {
                "mode": "protect",
                "filtered_count": 3,
                "filtered_reasons": {"expired": 3},
                "warning_count": 0,
            }
            return {
                "issues": [
                    {
                        "issue_id": issue_id,
                        "results": [],
                        "validity_sync": validity,
                    }
                ],
                "validity_sync": validity,
            }

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def fake_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        return object()

    monkeypatch.setenv("LEGAL_PROBLEM_MAP_LLM_ENABLED", "false")
    monkeypatch.setattr(
        search, "plan_legal_issues", lambda *args, **kwargs: [exact_issue]
    )
    monkeypatch.setattr(
        search, "get_legal_search_client", lambda: ExpiredExactClient()
    )
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(
        search, "invoke_blocking_model_with_deadline", fake_invoke
    )
    search._STRUCTURED_MODEL_CACHE.clear()

    sections, aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question=exact_issue.query_text,
                role="citizen",
            ),
            request_id=exact_issue.request_id,
            question_policy={
                "question_type": "legal_lookup",
                "required_sections": ["applicable_rule", "legal_basis_links"],
            },
            legal_as_of="2026-08-10",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert calls == {"search": 2, "provision": 0, "invoke": 0}
    assert rows == []
    assert sections[0].status == "insufficiently_evidenced"
    assert (
        "96/2014/TT-BQP đã hết hiệu lực – không dùng để trả lời hiện hành"
        in aggregate["answer"]
    )
    assert trace["expired_source_blocked"] is True
    assert trace["validity_filtered_reasons"] == ["expired"]


def test_complete_exact_article_packet_calls_model_once_with_full_context(monkeypatch):
    calls = {"search": 0, "provision": 0, "invoke": 0}
    exact_issue = LegalIssue(
        request_id="request-exact-complete",
        issue_id="issue-1",
        title="Nội dung Điều 73",
        query_text="Điều 73 của văn bản 60/2014/QH13 quy định gì?",
        intent="rule",
        domain="unknown",
        split_confidence="high",
    )
    full_article = "1. Nghĩa vụ thứ nhất.\n2. Nghĩa vụ thứ hai."

    class CompleteExactClient:
        async def search_batch(self, payload):
            calls["search"] += 1
            issue_id = payload["issues"][0]["issue_id"]
            rows = [
                {
                    "source_id": "chunk-1",
                    "chunk_id": 1,
                    "article_id": 73,
                    "document_id": 60,
                    "request_id": payload["request_id"],
                    "issue_id": issue_id,
                    "domain": "ho_tich_chung_thuc",
                    "domain_slug": "ho_tich_chung_thuc",
                    "effective_status": "active",
                    "document_status": "active",
                    "official": True,
                    "scope": "central",
                    "source_url": "https://example.gov.vn/60-2014",
                    "document_title": "Luật Hộ tịch",
                    "law_number": "60/2014/QH13",
                    "article_number": "73",
                    "content": "1. Nghĩa vụ thứ nhất.",
                    "parent_context": full_article,
                    "parent_context_primary": True,
                    "parent_context_truncated": False,
                    "exact_article_packet_ref": "document:60:article:73",
                    "exact_article_packet_status": "complete",
                    "exact_article_assembled_content": full_article,
                    "exact_article_loaded_chunk_count": 2,
                    "exact_article_expected_chunk_count": 2,
                },
                {
                    "source_id": "chunk-2",
                    "chunk_id": 2,
                    "article_id": 73,
                    "document_id": 60,
                    "request_id": payload["request_id"],
                    "issue_id": issue_id,
                    "domain": "ho_tich_chung_thuc",
                    "domain_slug": "ho_tich_chung_thuc",
                    "effective_status": "active",
                    "document_status": "active",
                    "official": True,
                    "scope": "central",
                    "source_url": "https://example.gov.vn/60-2014",
                    "document_title": "Luật Hộ tịch",
                    "law_number": "60/2014/QH13",
                    "article_number": "73",
                    "content": "2. Nghĩa vụ thứ hai.",
                    "parent_context": None,
                    "parent_context_primary": False,
                    "parent_context_truncated": False,
                    "exact_article_packet_ref": "document:60:article:73",
                    "exact_article_packet_status": "complete",
                    "exact_article_assembled_content": None,
                    "exact_article_loaded_chunk_count": 2,
                    "exact_article_expected_chunk_count": 2,
                },
            ]
            return {
                "issues": [
                    {
                        "issue_id": issue_id,
                        "results": rows,
                        "exact_article_packets": [
                            {
                                "status": "complete",
                                "packet_ref": "document:60:article:73",
                                "law_number": "60/2014/QH13",
                                "article_number": "73",
                                "loaded_chunk_count": 2,
                                "expected_chunk_count": 2,
                                "reason_codes": [],
                            }
                        ],
                    }
                ]
            }

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def fake_invoke(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        return SimpleNamespace(
            content=(
                '{"issues":[{"issue_id":"issue-1","claims":[{'
                '"claim_text":"Điều 73 quy định nghĩa vụ thứ nhất.",'
                '"claim_type":"rule","evidence_id":"evidence-1",'
                '"support_quote":"1. Nghĩa vụ thứ nhất."}]}]}'
            )
        )

    monkeypatch.setenv("LEGAL_PROBLEM_MAP_LLM_ENABLED", "false")
    monkeypatch.setattr(
        search, "plan_legal_issues", lambda *args, **kwargs: [exact_issue]
    )
    monkeypatch.setattr(
        search, "get_legal_search_client", lambda: CompleteExactClient()
    )
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(
        search, "invoke_blocking_model_with_deadline", fake_invoke
    )
    search._STRUCTURED_MODEL_CACHE.clear()

    _sections, _aggregate, rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(question=exact_issue.query_text, role="citizen"),
            request_id=exact_issue.request_id,
            question_policy={
                "question_type": "legal_lookup",
                "required_sections": ["applicable_rule", "legal_basis_links"],
            },
            legal_as_of="2026-08-10",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert calls == {"search": 1, "provision": 1, "invoke": 1}
    assert len(rows) == 2
    assert trace["exact_article_gate"]["ready"] is True
    assert trace["context_stats"]["evidence_count"] == 1
    assert trace["context_stats"]["character_count"] >= len(full_article)


def test_unknown_policy_domain_is_not_sent_as_an_index_filter(monkeypatch):
    payloads = []

    class CapturingEmptyClient:
        async def search_batch(self, payload):
            payloads.append(payload)
            return {
                "issues": [
                    {"issue_id": issue["issue_id"], "results": []}
                    for issue in payload["issues"]
                ]
            }

    exact_issue = LegalIssue(
        request_id="request-exact-law",
        issue_id="issue-1",
        title="Hiệu lực",
        query_text="Nghị quyết 22/2025/NQ-HĐND có hiệu lực từ ngày nào?",
        intent="rule",
        domain="unknown",
        split_confidence="high",
    )
    monkeypatch.setattr(
        search,
        "plan_legal_issues",
        lambda *args, **kwargs: [exact_issue],
    )
    monkeypatch.setattr(
        search,
        "get_legal_search_client",
        lambda: CapturingEmptyClient(),
    )

    asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question=exact_issue.query_text,
                role="admin",
            ),
            request_id=exact_issue.request_id,
            question_policy={
                "question_type": "legal_lookup",
                "required_sections": ["conclusion", "citations"],
            },
            legal_as_of="2026-08-09",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )

    assert payloads
    assert all(
        issue["domain"] is None
        for payload in payloads
        for issue in payload["issues"]
    )


def test_core_retrieval_timeout_is_bounded_and_skips_expanded_round(monkeypatch):
    client = _SlowClient()
    monkeypatch.setenv("LEGAL_STRUCTURED_RETRIEVAL_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: client)

    started = time.perf_counter()
    sections, _aggregate, _rows, trace = asyncio.run(
        search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id="request-retrieval-timeout",
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-08-09",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert client.calls == 1
    assert sections[0].status == "insufficiently_evidenced"
    assert trace["retrieval_timed_out"] is True
    assert trace["expanded_retrieval_used"] is False
    assert trace["metric"]["error_category"] == "retrieval_unavailable"


def test_provider_circuit_skips_second_call_after_timeout(monkeypatch):
    calls = {"provision": 0, "invoke": 0}

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        calls["provision"] += 1
        return object()

    async def timeout(*args, **kwargs):
        del args, kwargs
        calls["invoke"] += 1
        raise asyncio.TimeoutError

    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _Client())
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", timeout)
    search._STRUCTURED_MODEL_CACHE.clear()

    async def run_one(request_id: str):
        return await search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id=request_id,
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )

    first = asyncio.run(run_one("request-circuit-1"))
    second = asyncio.run(run_one("request-circuit-2"))

    assert calls == {"provision": 1, "invoke": 1}
    assert first[3]["metric"]["error_category"] == "provider_timeout"
    assert second[3]["metric"]["error_category"] == "provider_circuit_open"
    assert second[3]["claim_validation"]["fallback_reason"] == "provider_circuit_open"
    assert second[3]["provider_error_code"] == "provider_circuit_open"
    assert second[3]["answer_mode"] == "verified_source_condensed"


@pytest.mark.asyncio
async def test_concurrent_timeout_fallback_does_not_block_other_deadlines(
    monkeypatch,
):
    # Isolate worker-thread overlap: with the production threshold of one,
    # either concurrent request may legitimately open the circuit before the
    # other reaches generation, turning this into a scheduler-dependent test.
    monkeypatch.setattr(
        search,
        "_STRUCTURED_PROVIDER_CIRCUIT",
        StructuredProviderCircuit(
            failure_threshold=3,
            cooldown_seconds=120,
        ),
    )

    async def fake_provision(*args, **kwargs):
        del args, kwargs
        return object()

    async def timeout(*args, **kwargs):
        del args, kwargs
        raise asyncio.TimeoutError

    original = search.build_and_render_extractive_answer
    active_fallbacks = 0
    maximum_active_fallbacks = 0
    active_lock = threading.Lock()

    def slow_fallback(**kwargs):
        nonlocal active_fallbacks, maximum_active_fallbacks
        with active_lock:
            active_fallbacks += 1
            maximum_active_fallbacks = max(
                maximum_active_fallbacks, active_fallbacks
            )
        try:
            time.sleep(0.25)
            return original(**kwargs)
        finally:
            with active_lock:
                active_fallbacks -= 1

    monkeypatch.setattr(search, "plan_legal_issues", lambda *args, **kwargs: [_issue()])
    monkeypatch.setattr(search, "get_legal_search_client", lambda: _Client())
    monkeypatch.setattr(search, "provision_langchain_model", fake_provision)
    monkeypatch.setattr(search, "invoke_blocking_model_with_deadline", timeout)
    monkeypatch.setattr(search, "build_and_render_extractive_answer", slow_fallback)
    search._STRUCTURED_MODEL_CACHE.clear()

    async def run_one(request_id: str):
        return await search._run_structured_section_orchestration(
            ask_request=AskRequest(
                question="Thời hạn giải quyết là bao lâu?", role="citizen"
            ),
            request_id=request_id,
            question_policy={
                "question_type": "procedure",
                "required_sections": ["processing_time"],
            },
            legal_as_of="2026-07-24",
            strategy_model_id="strategy",
            answer_model_id="answer",
            final_answer_model_id="final",
        )

    started = time.perf_counter()
    results = await asyncio.gather(run_one("request-a"), run_one("request-b"))
    elapsed = time.perf_counter() - started

    # Assert overlap directly instead of relying only on a host-speed threshold:
    # both fallback workers must be active at the same time.
    assert maximum_active_fallbacks == 2
    assert elapsed < 0.75
    assert {
        result[3]["metric"]["error_category"] for result in results
    } == {"provider_timeout"}


@pytest.mark.asyncio
async def test_structured_model_capacity_queues_within_same_deadline(
    monkeypatch,
):
    calls = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_invoke(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 2:
            both_started.set()
        await release.wait()
        return object()

    monkeypatch.setattr(
        search, "invoke_blocking_model_with_deadline", blocked_invoke
    )

    first = asyncio.create_task(
        search._invoke_structured_model_with_capacity(
            object(), "one", timeout=24
        )
    )
    second = asyncio.create_task(
        search._invoke_structured_model_with_capacity(
            object(), "two", timeout=24
        )
    )
    await both_started.wait()

    third = asyncio.create_task(
        search._invoke_structured_model_with_capacity(
            object(), "three", timeout=24
        )
    )
    await asyncio.sleep(0)
    assert third.done() is False
    assert calls == 2

    release.set()
    await asyncio.gather(first, second, third)
    assert calls == 3


@pytest.mark.asyncio
async def test_structured_model_capacity_wait_never_renews_deadline(
    monkeypatch,
):
    release = asyncio.Event()

    async def blocked_invoke(*_args, **_kwargs):
        await release.wait()
        return object()

    monkeypatch.setattr(
        search, "invoke_blocking_model_with_deadline", blocked_invoke
    )
    first = asyncio.create_task(
        search._invoke_structured_model_with_capacity(
            object(), "one", timeout=1
        )
    )
    second = asyncio.create_task(
        search._invoke_structured_model_with_capacity(
            object(), "two", timeout=1
        )
    )
    await asyncio.sleep(0)

    with pytest.raises(asyncio.TimeoutError):
        await search._invoke_structured_model_with_capacity(
            object(), "three", timeout=0.01
        )

    release.set()
    await asyncio.gather(first, second)
