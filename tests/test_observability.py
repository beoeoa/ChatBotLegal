from api.observability import RuntimeTelemetry


def test_telemetry_keeps_only_route_status_and_duration():
    telemetry = RuntimeTelemetry(max_events=3)
    telemetry.add("/api/search/ask", 200, 135)
    telemetry.add("/api/legal/crawl/scan", 503, 6200)

    summary = telemetry.summary(slow_threshold_ms=5000)

    assert summary["by_category"]["ask"]["count"] == 1
    assert summary["by_category"]["crawler"]["error_count"] == 1
    assert summary["slow_requests"][0]["route"] == "/api/legal/crawl/scan"
    assert "content" not in summary["slow_requests"][0]
