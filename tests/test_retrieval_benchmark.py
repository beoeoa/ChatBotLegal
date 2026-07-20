import json
import threading
import time

import pytest

from scripts.benchmark_legal_retrieval import (
    BenchmarkCase,
    DEFAULT_CANDIDATE_GRID,
    DEFAULT_CONCURRENCY,
    build_scenarios,
    load_cases,
    main,
    run_benchmark,
    validate_local_search_url,
)


class FixtureTransport:
    def __init__(self, results_by_case):
        self.results_by_case = results_by_case

    def search(self, case, *, retrieval_tier, candidate_count):
        return {
            "results": self.results_by_case[case.case_id],
            "timing_ms": {"total": 12.5, "embedding": 4.0},
        }


def _case(case_id, expected, critical=()):
    return BenchmarkCase(
        case_id=case_id,
        query=f"synthetic query for {case_id}",
        expected_source_ids=tuple(expected),
        critical_authority_source_ids=tuple(critical),
    )


def test_default_matrix_covers_required_candidate_and_concurrency_grid():
    assert DEFAULT_CANDIDATE_GRID == {
        "core": (80, 120, 180),
        "expanded": (120, 180, 240),
    }
    assert DEFAULT_CONCURRENCY == (1, 5, 10, 20)

    scenarios = build_scenarios()

    assert len(scenarios) == 48
    assert {scenario.mode for scenario in scenarios} == {"cold", "warm"}
    assert {
        (scenario.retrieval_tier, scenario.candidate_count)
        for scenario in scenarios
    } == {
        ("core", 80),
        ("core", 120),
        ("core", 180),
        ("expanded", 120),
        ("expanded", 180),
        ("expanded", 240),
    }


def test_benchmark_computes_recall_and_critical_authority_gate_without_content():
    cases = [
        _case("case-pass", ["law:60/2014/QH13"], ["law:60/2014/QH13"]),
        _case(
            "case-fail",
            ["law:123/2015/ND-CP", "law:60/2014/QH13"],
            ["law:60/2014/QH13"],
        ),
    ]
    transport = FixtureTransport(
        {
            "case-pass": [
                {
                    "chunk_id": "1",
                    "law_number": "60/2014/QH13",
                    "content": "raw source text must not be persisted",
                    "source_url": "https://example.test/private",
                },
            ],
            "case-fail": [
                {"chunk_id": "2", "law_number": "123/2015/ND-CP"},
            ],
        }
    )

    report = run_benchmark(
        cases,
        transport,
        candidate_grid={"core": (80,)},
        concurrencies=(1,),
        modes=("cold",),
        min_recall_at_6=0.95,
        min_critical_authority_retention=1.0,
    )

    assert report["pass"] is False
    assert report["summaries"][0]["recall_at_6"] == 0.75
    assert report["summaries"][0]["critical_authority_retention"] == 0.5
    samples = {sample["case_id"]: sample for sample in report["samples"]}
    assert samples["case-pass"]["recall_at_6"] == 1.0
    assert samples["case-pass"]["critical_authority_retained"] is True
    assert samples["case-fail"]["recall_at_6"] == 0.5
    assert samples["case-fail"]["critical_authority_retained"] is False
    assert samples["case-fail"]["matched_source_ids"] == ["law:123/2015/ND-CP"]

    serialized = json.dumps(report, ensure_ascii=False).lower()
    assert "synthetic query" not in serialized
    assert "question" not in serialized
    assert "password" not in serialized
    assert "authorization" not in serialized
    assert "content" not in serialized


def test_concurrency_runner_actually_overlaps_fixture_requests():
    class ConcurrentTransport:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def search(self, case, *, retrieval_tier, candidate_count):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.01)
            with self.lock:
                self.active -= 1
            return {"results": [{"law_number": "60/2014/QH13"}]}

    transport = ConcurrentTransport()
    cases = [
        _case(f"case-{index}", ["law:60/2014/QH13"])
        for index in range(8)
    ]

    report = run_benchmark(
        cases,
        transport,
        candidate_grid={"core": (80,)},
        concurrencies=(5,),
        modes=("cold",),
    )

    assert report["pass"] is True
    assert transport.max_active >= 2
    assert report["summaries"][0]["concurrency"] == 5


def test_raw_transport_exception_is_never_persisted():
    class FailingTransport:
        def search(self, case, *, retrieval_tier, candidate_count):
            raise RuntimeError("token=fixture-secret; query=private text")

    report = run_benchmark(
        [_case("case-error", ["law:60/2014/QH13"])],
        FailingTransport(),
        candidate_grid={"core": (80,)},
        concurrencies=(1,),
        modes=("cold",),
    )
    serialized = json.dumps(report, ensure_ascii=False).lower()

    assert report["samples"][0]["status"] == "error"
    assert "fixture-secret" not in serialized
    assert "private text" not in serialized
    assert "runtimeerror" not in serialized


def test_case_loader_uses_approved_source_ids_but_report_never_needs_question(tmp_path):
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "review_id": "approved-1",
                        "question": "private free text",
                        "expected_documents": ["60/2014/QH13"],
                        "critical_authority_source_ids": ["law:60/2014/QH13"],
                        "expert_review_status": "approved",
                    },
                    {
                        "review_id": "pending-1",
                        "question": "must not run",
                        "expected_documents": ["123/2015/ND-CP"],
                        "expert_review_status": "pending",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    cases = load_cases(case_file)

    assert [case.case_id for case in cases] == ["approved-1"]
    assert cases[0].expected_source_ids == ("law:60/2014/QH13",)
    assert cases[0].critical_authority_source_ids == ("law:60/2014/QH13",)


@pytest.mark.parametrize(
    "url",
    [
        "https://retrieval.example/search",
        "http://user:secret@127.0.0.1:8765/search",
        "http://127.0.0.1:8765/search?token=secret",
    ],
)
def test_live_runner_rejects_non_local_or_credential_bearing_urls(url):
    with pytest.raises(ValueError):
        validate_local_search_url(url)


def test_cli_defaults_to_dry_run_and_writes_only_a_sanitized_plan(tmp_path):
    case_file = tmp_path / "cases.json"
    report_file = tmp_path / "report.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "dry-1",
                        "query": "free text that must not be persisted",
                        "password": "fixture-secret",
                        "expected_source_ids": ["law:60/2014/QH13"],
                        "critical_authority_source_ids": ["law:60/2014/QH13"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["--cases", str(case_file), "--report", str(report_file)]) == 0
    report_text = report_file.read_text(encoding="utf-8")
    payload = json.loads(report_text)

    assert payload["mode"] == "dry_run"
    assert payload["case_ids"] == ["dry-1"]
    assert payload["planned_request_count"] == 48
    assert "free text" not in report_text
    assert "fixture-secret" not in report_text
    assert "query" not in report_text.lower()
