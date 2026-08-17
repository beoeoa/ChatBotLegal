import pytest

from scripts.benchmark_section_grounding import (
    _extract_runtime_signals,
    _extract_stage_timings,
    _stage_summaries,
)


def test_benchmark_extracts_only_privacy_safe_stage_timings():
    payload = {
        "rag_trace": {
            "section_orchestration": {
                "metric": {
                    "stage_timings_ms": {
                        "retrieval": 120.4,
                        "provisioning": 40.1,
                        "generation": 800.2,
                        "validation": 15.8,
                        "end_to_end": 950.0,
                        "raw_question": 999,
                    }
                }
            }
        }
    }
    assert _extract_stage_timings(payload) == {
        "retrieval": 120.4,
        "provisioning": 40.1,
        "generation": 800.2,
        "validation": 15.8,
        "end_to_end": 950.0,
    }


def test_benchmark_extracts_runtime_error_repair_and_quality_gate_without_content():
    payload = {
        "rag_trace": {
            "section_orchestration": {
                "metric": {
                    "repair_count": 0,
                    "error_category": "timeout",
                },
                "claim_validation": {
                    "quality_gate": {"pass": False},
                },
            }
        }
    }

    assert _extract_runtime_signals(payload) == {
        "repair_count": 0,
        "error_category": "timeout",
        "quality_gate_status": "failed",
    }

@pytest.mark.asyncio
async def test_warm_benchmark_prewarms_each_unique_case_before_measurement(
    monkeypatch,
):
    from scripts import benchmark_section_grounding as benchmark

    calls: list[str] = []

    async def fake_one_request(client, *, endpoint, case_id, case):
        del client, endpoint, case
        calls.append(case_id)
        return {
            "case_id": case_id,
            "status": "completed",
            "http_status": 200,
            "duration_ms": 1,
            "section_statuses": [],
            "repair_count": 0,
            "error_category": "none",
            "quality_gate_status": "passed",
            "stage_timings_ms": {},
        }

    monkeypatch.setattr(benchmark, "_one_request", fake_one_request)
    result = await benchmark.run_benchmark(
        base_url="http://127.0.0.1:5055",
        token="opaque",
        concurrency=1,
        mode="warm",
        case_ids=["simple_supported", "simple_supported"],
    )

    assert calls == [
        "simple_supported",
        "simple_supported",
        "simple_supported",
    ]
    assert result["request_count"] == 2


def test_benchmark_reports_p50_and_p95_for_each_available_stage():
    rows = [
        {"stage_timings_ms": {"retrieval": 100, "generation": 900}},
        {"stage_timings_ms": {"retrieval": 200, "generation": 1100}},
    ]

    assert _stage_summaries(rows) == {
        "retrieval": {"p50": 100, "p95": 200},
        "generation": {"p50": 900, "p95": 1100},
    }
