from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from api.legal_query_understanding import (
    M4_INTENTS,
    M4_QUERY_SCHEMA_VERSION,
    classify_legal_query,
    validate_m4_query_classification,
)
import scripts.legal_search_server as legal_search


@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("Thủ tục đăng ký khai sinh thế nào?", "PROCEDURE"),
        ("Điều kiện đăng ký kết hôn là gì?", "ELIGIBILITY"),
        ("Đăng ký khai sinh cần giấy tờ gì?", "REQUIRED_DOCUMENTS"),
        ("Cơ quan nào có thẩm quyền chứng thực?", "AUTHORITY"),
        ("Các bước giải quyết hồ sơ ra sao?", "PROCESS"),
        ("Thời hạn giải quyết là bao lâu?", "DEADLINE"),
        ("Lệ phí chứng thực bao nhiêu?", "FEE"),
        ("Tải mẫu tờ khai đăng ký khai sinh", "FORM"),
        ("Căn cứ pháp lý của thủ tục này?", "LEGAL_BASIS"),
        ("Văn bản này còn hiệu lực không?", "VALIDITY"),
        ("Điều 16 Luật Hộ tịch quy định gì?", "SPECIFIC_DOCUMENT"),
        ("Quy định tại ngày 01/06/2020 là gì?", "HISTORICAL"),
        ("So sánh thường trú và tạm trú", "COMPARISON"),
        ("Tôi muốn khiếu nại quyết định hành chính", "COMPLAINT"),
        ("Tư vấn thủ tục đăng kiểm ô tô", "OUT_OF_SCOPE"),
        ("Tôi cần hỏi một việc", "UNKNOWN"),
    ],
)
def test_classifier_covers_required_m4_intents(query: str, intent: str):
    result = classify_legal_query(query, today=date(2026, 8, 15))

    assert result["intent"] == intent
    assert validate_m4_query_classification(result)["status"] == "pass"


def test_m4_contract_is_structured_and_normalizes_query():
    result = classify_legal_query(
        "  ĐĂNG   KÝ khai sinh ở UBND phường cần giấy tờ gì?  ",
        today=date(2026, 8, 15),
    )

    assert result["schema_version"] == M4_QUERY_SCHEMA_VERSION
    assert result["normalized_query"] == (
        "đăng ký khai sinh ở ubnd phường cần giấy tờ gì?"
    )
    assert result["domain"] == "ho_tich"
    assert result["intent"] == "REQUIRED_DOCUMENTS"
    assert result["scope"] == "commune"
    assert result["temporal_scope"] == "current"
    assert result["answer_type"] == "instructional"
    assert set(result["secondary_intents"]).issubset(M4_INTENTS)


def test_exact_historical_date_is_safe_retrieval_boundary():
    result = classify_legal_query(
        "Ngày 01/06/2020, đăng ký khai sinh cần hồ sơ gì?",
        today=date(2026, 8, 15),
    )

    assert result["temporal_scope"] == "historical"
    assert result["temporal_reference"] == "2020-06-01"
    assert result["temporal_precision"] == "day"
    assert result["retrieval_allowed"] is True
    assert result["retrieval_as_of"] == "2020-06-01"


@pytest.mark.parametrize(
    "query",
    [
        "Văn bản đang có hiệu lực tại ngày 11/08/2026 là văn bản nào?",
        "Quy định còn hiệu lực tại ngày 11/08/2026 về đăng ký khai sinh?",
    ],
)
def test_date_scoped_effectivity_is_not_an_absolute_current_marker(query: str):
    result = classify_legal_query(
        query,
        as_of=date(2026, 8, 11),
        as_of_explicit=True,
        today=date(2026, 8, 15),
    )

    assert result["temporal_scope"] == "historical"
    assert result["retrieval_allowed"] is True
    assert result["retrieval_as_of"] == "2026-08-11"
    assert result["temporal_error_code"] is None
    assert result["signals"]["current_marker"] is False


def test_absolute_current_marker_still_conflicts_with_a_past_as_of():
    result = classify_legal_query(
        "Hiện nay đăng ký khai sinh cần giấy tờ gì?",
        as_of=date(2026, 8, 11),
        as_of_explicit=True,
        today=date(2026, 8, 15),
    )

    assert result["retrieval_allowed"] is False
    assert result["temporal_error_code"] == "TEMPORAL_AS_OF_CONFLICT"


def test_explicit_insufficient_facts_request_stops_before_retrieval():
    result = classify_legal_query(
        "Tôi cần làm thủ tục nhưng chưa nêu thủ tục cụ thể. Hệ thống có thể "
        "kết luận ngay không; nếu không, cần tôi bổ sung chính xác thông tin gì?",
        today=date(2026, 8, 15),
    )

    assert result["intent"] == "UNKNOWN"
    assert result["answer_type"] == "clarification"
    assert result["retrieval_allowed"] is False
    assert result["retrieval_as_of"] is None
    assert result["temporal_error_code"] == "QUERY_FACTS_INSUFFICIENT"
    assert result["signals"]["insufficient_facts_marker"] is True


def test_year_only_history_accepts_matching_explicit_as_of():
    result = classify_legal_query(
        "Năm 2020 đăng ký khai sinh cần hồ sơ gì?",
        as_of=date(2020, 6, 1),
        as_of_explicit=True,
        today=date(2026, 8, 15),
    )

    assert result["temporal_scope"] == "historical"
    assert result["temporal_precision"] == "day"
    assert result["retrieval_allowed"] is True
    assert result["retrieval_as_of"] == "2020-06-01"


def test_computed_current_date_is_not_treated_as_user_explicit_date():
    computed_default = classify_legal_query(
        "Ngày 01/06/2020 đăng ký khai sinh cần gì?",
        as_of=date(2026, 8, 15),
        as_of_explicit=False,
        today=date(2026, 8, 15),
    )
    conflicting_explicit = classify_legal_query(
        "Ngày 01/06/2020 đăng ký khai sinh cần gì?",
        as_of=date(2026, 8, 15),
        as_of_explicit=True,
        today=date(2026, 8, 15),
    )

    assert computed_default["retrieval_as_of"] == "2020-06-01"
    assert computed_default["retrieval_allowed"] is True
    assert conflicting_explicit["temporal_error_code"] == "TEMPORAL_AS_OF_CONFLICT"
    assert conflicting_explicit["retrieval_allowed"] is False


def test_invalid_or_future_date_fails_closed():
    invalid = classify_legal_query(
        "Quy định ngày 31/02/2020 là gì?", today=date(2026, 8, 15)
    )
    future = classify_legal_query(
        "Quy định ngày 01/01/2030 là gì?", today=date(2026, 8, 15)
    )

    assert invalid["temporal_scope"] == "unknown"
    assert invalid["temporal_error_code"] == "TEMPORAL_DATE_INVALID"
    assert invalid["retrieval_allowed"] is False
    assert future["temporal_scope"] == "unknown"
    assert future["temporal_error_code"] == "TEMPORAL_DATE_IN_FUTURE"


@pytest.mark.parametrize(
    ("query", "scope", "error_code"),
    [
        ("Năm 2020 đăng ký khai sinh cần gì?", "historical", "HISTORICAL_AS_OF_REQUIRED"),
        ("Trước đây đăng ký khai sinh cần gì?", "unknown", "TEMPORAL_SCOPE_CLARIFICATION_REQUIRED"),
    ],
)
def test_ambiguous_past_queries_fail_closed(query: str, scope: str, error_code: str):
    result = classify_legal_query(query, today=date(2026, 8, 15))

    assert result["temporal_scope"] == scope
    assert result["retrieval_allowed"] is False
    assert result["temporal_error_code"] == error_code
    assert result["retrieval_as_of"] is None


def test_historical_guard_stops_before_vector_and_sql(monkeypatch):
    retriever = legal_search.LegalRetriever()
    encode = MagicMock(side_effect=AssertionError("vector must not run"))
    exact = MagicMock(side_effect=AssertionError("SQL must not run"))
    monkeypatch.setattr(retriever, "encode_query", encode)
    monkeypatch.setattr(retriever, "_fetch_exact_chunks", exact)

    response = retriever.search(
        legal_search.SearchRequest(
            query="Năm 2020 đăng ký khai sinh cần hồ sơ gì?",
            include_trace=True,
        )
    )

    assert response["status"] == "clarification_required"
    assert response["error_code"] == "HISTORICAL_AS_OF_REQUIRED"
    assert response["results"] == []
    assert response["query_classification"]["temporal_scope"] == "historical"
    assert response["trace"]["query_classification"]["retrieval_allowed"] is False
    encode.assert_not_called()
    exact.assert_not_called()


def test_batch_temporal_guard_stops_before_vector_prefetch(monkeypatch):
    prefetch = MagicMock(side_effect=AssertionError("batch vector must not run"))
    monkeypatch.setattr(legal_search.retriever, "prefetch_batch_vectors", prefetch)

    response = legal_search.search_batch(
        legal_search.BatchSearchRequest(
            request_id="m4-batch-block",
            as_of=date(2026, 8, 15),
            as_of_explicit=False,
            issues=[
                legal_search.BatchSearchIssue(
                    issue_id="issue-1",
                    query="Năm 2020 đăng ký khai sinh cần hồ sơ gì?",
                )
            ],
            include_trace=True,
            enable_learned_reranker=False,
        )
    )

    query = response["issues"][0]["queries"][0]
    assert query["status"] == "clarification_required"
    assert query["error_code"] == "HISTORICAL_AS_OF_REQUIRED"
    assert query["result_count"] == 0
    assert response["effective_as_of_values"] == []
    prefetch.assert_not_called()


def test_batch_worker_propagates_benchmark_clock_to_inner_search(monkeypatch):
    benchmark_clock = date(2026, 8, 11)
    captured: list[legal_search.SearchRequest] = []

    monkeypatch.setenv("LEGAL_BENCHMARK_MODE", "1")
    monkeypatch.setattr(legal_search.retriever, "_require_serving_scope", lambda: None)
    monkeypatch.setattr(
        legal_search.retriever,
        "prefetch_batch_vectors",
        lambda queries, **kwargs: [None for _ in queries],
    )

    def fake_search(request: legal_search.SearchRequest):
        captured.append(request)
        classification = classify_legal_query(
            request.query,
            requested_domain=request.domain,
            as_of=request.as_of,
            as_of_explicit=bool(request.as_of_explicit),
            today=request.benchmark_today,
        )
        return {
            "status": "ok",
            "results": [],
            "query_classification": classification,
            "timing_ms": {"total": 0.0},
        }

    monkeypatch.setattr(legal_search.retriever, "search", fake_search)

    legal_search.search_batch(
        legal_search.BatchSearchRequest(
            request_id="m4-batch-benchmark-clock",
            as_of=benchmark_clock,
            as_of_explicit=True,
            benchmark_today=benchmark_clock,
            issues=[
                legal_search.BatchSearchIssue(
                    issue_id="issue-clock",
                    query="Hiện hành đăng ký khai sinh cần giấy tờ gì?",
                )
            ],
            enable_learned_reranker=False,
        )
    )

    assert captured
    assert all(request.benchmark_today == benchmark_clock for request in captured)
    assert all(
        classify_legal_query(
            request.query,
            as_of=request.as_of,
            as_of_explicit=True,
            today=request.benchmark_today,
        )["retrieval_allowed"]
        for request in captured
    )


def test_ask_pipeline_uses_exact_query_date_for_answer_validity():
    from api.models import AskRequest
    from api.routers.search import _apply_m4_temporal_scope

    request = AskRequest(
        question="Ngày 01/06/2020 đăng ký khai sinh cần hồ sơ gì?"
    )
    classification = _apply_m4_temporal_scope(request)

    assert classification["temporal_scope"] == "historical"
    assert classification["retrieval_as_of"] == "2020-06-01"
    assert request.legal_as_of == date(2020, 6, 1)
