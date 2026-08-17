"""Tests for api/observability — bounded queues, privacy invariants, issue aggregation."""
import pytest
from api.observability import RuntimeTelemetry


class TestRuntimeTelemetry:
    """Sanitize timing data and enforce privacy constraints."""

    def test_add_categorizes_routes(self):
        t = RuntimeTelemetry()
        t.add("/api/search/ask", 200, 120)
        t.add("/api/legal/docs/abc123", 200, 80)
        t.add("/api/legal/docs/abc123/download.pdf", 200, 250)
        t.add("/api/faq", 200, 45)
        t.add("/api/conversations", 200, 60)
        t.add("/api/live-support/events", 200, 15)
        t.add("/api/legal/crawl/candidates/cand1/extract", 200, 300)
        t.add("/api/legal/crawl/sources", 200, 50)
        t.add("/api/legal/import/candidates", 200, 100)
        t.add("/api/procedures/download/form/1", 200, 70)
        summary = t.summary()
        cats = summary["by_category"]
        assert cats["ask"]["count"] == 1
        assert cats["document_view"]["count"] == 1
        assert cats["pdf_stream_export"]["count"] == 1
        assert cats["faq_load"]["count"] == 1
        assert cats["conversation_load"]["count"] == 1
        assert cats["support_delivery"]["count"] == 1
        assert cats["ocr"]["count"] == 1
        assert cats["crawler"]["count"] == 1
        assert cats["import_job"]["count"] == 1
        assert cats["form_download"]["count"] == 1

    def test_bounded_queue_discards_old(self):
        t = RuntimeTelemetry(max_events=3)
        t.add("/api/a", 200, 10)
        t.add("/api/b", 200, 20)
        t.add("/api/c", 200, 30)
        t.add("/api/d", 200, 40)  # should evict /api/a
        summary = t.summary()
        assert summary["event_count"] == 3
        routes = [e["route"] for e in summary["slow_requests"]]
        assert "/api/a" not in routes

    def test_safe_metadata_filters_keys(self):
        t = RuntimeTelemetry()
        t.record_operation(
            category="test",
            duration_ms=50,
            metadata={
                "status": 200,
                "query_string": "secret=abc",
                "user_id": "u123",
                "question": "hỏi về khai sinh",
                "long_value": "x" * 100,
                "cached": True,
            },
        )
        events = list(t._events)
        meta = events[0]
        assert "query_string" not in meta
        assert "user_id" not in meta
        assert "question" not in meta
        assert "long_value" not in meta
        assert meta.get("cached") is True

    def test_record_issue_stores_sanitized(self):
        t = RuntimeTelemetry()
        t.record_issue("citation_dead", category="document_view", status_code=404, error_class="NotFoundError")
        issues = t._issues
        assert len(issues) == 1
        assert issues[0]["issue_type"] == "citation_dead"
        assert issues[0]["category"] == "document_view"
        assert issues[0]["status_code"] == 404
        assert issues[0]["error_class"] == "NotFoundError"

    def test_summary_excludes_sensitive_data(self):
        t = RuntimeTelemetry()
        t.add("/api/search/ask", 200, 100)
        t.record_issue("unanswered_question", category="ask", error_class="NoAnswer")
        summary = t.summary()
        text = str(summary).lower()
        # Issue labels and the static privacy notice may contain generic words
        # such as "question"; verify that actual sensitive values never leak.
        for sensitive_value in ["nguyen van a", "secret=abc", "bearer abc", "cookie=", "matkhau123"]:
            assert sensitive_value not in text, f"Sensitive value '{sensitive_value}' leaked into summary"

    def test_slow_requests_filter(self):
        t = RuntimeTelemetry()
        t.add("/api/fast", 200, 100)
        t.add("/api/slow", 500, 6000)
        summary = t.summary(slow_threshold_ms=5000)
        assert len(summary["slow_requests"]) == 1
        assert summary["slow_requests"][0]["route"] == "/api/slow"

    def test_outcome_normalization(self):
        t = RuntimeTelemetry()
        t.record_operation(category="test", duration_ms=10, outcome="bad_value")
        event = t._events[0]
        assert event["outcome"] == "error"

    def test_non_api_paths_ignored(self):
        t = RuntimeTelemetry()
        t.add("/health", 200, 5)
        t.add("/", 200, 3)
        assert t._events.maxlen == 1000  # unchanged
        assert len(list(t._events)) == 0  # filtered out

    def test_summary_includes_nearest_rank_p50_and_p95(self):
        t = RuntimeTelemetry()
        for duration_ms in (10, 20, 30, 40, 50):
            t.record_operation(category="ask.retrieval", duration_ms=duration_ms)

        row = t.summary()["by_category"]["ask.retrieval"]

        assert row["p50_ms"] == 30
        assert row["p95_ms"] == 50

    def test_ask_stage_categories_are_allowlisted(self):
        t = RuntimeTelemetry()

        for stage in (
            "queue",
            "retrieval",
            "generation",
            "repair",
            "validation",
            "persistence",
            "total",
        ):
            t.record_ask_stage(stage, duration_ms=1)

        with pytest.raises(ValueError, match="Unsupported Ask stage"):
            t.record_ask_stage("question-text", duration_ms=1)

        assert set(t.summary()["by_category"]) == {
            "ask.queue",
            "ask.retrieval",
            "ask.generation",
            "ask.repair",
            "ask.validation",
            "ask.persistence",
            "ask.total",
        }

    def test_legal_orchestration_records_only_bounded_counts(self):
        t = RuntimeTelemetry()

        t.record_legal_orchestration(
            duration_ms=321,
            planner_mode="deterministic_fallback",
            issue_count=8,
            query_count=16,
            candidate_count=24,
            rejected_count=2,
            missing_coverage_count=3,
            supplemental_round=2,
            removed_noise_count=5,
            prompt_chars=11_500,
        )

        event = list(t._events)[0]
        assert event["category"] == "ask.legal_orchestration"
        assert event["issue_count"] == 8
        assert event["query_count"] == 16
        assert event["prompt_chars"] == 11_500
        assert "question" not in event
        assert "content" not in event

    def test_repair_and_audit_outcome_counters_are_fixed_and_sanitized(self):
        t = RuntimeTelemetry()
        t.record_ask_outcome("repair", "skipped")
        t.record_ask_outcome("repair", "success")
        t.record_ask_outcome("audit", "success")
        t.record_ask_outcome("audit", "failed")

        with pytest.raises(ValueError, match="Unsupported Ask outcome"):
            t.record_ask_outcome("audit", "question=private")
        with pytest.raises(ValueError, match="Unsupported Ask outcome kind"):
            t.record_ask_outcome("user-123", "success")

        counters = t.summary()["ask_outcomes"]
        assert counters["repair"] == {
            "attempted": 0,
            "skipped": 1,
            "success": 1,
            "failed": 0,
        }
        assert counters["audit"] == {"success": 1, "failed": 1}
