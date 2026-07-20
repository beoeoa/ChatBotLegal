"""Privacy-safe retrieval benchmark for local and fixture-only execution.

The runner intentionally keeps free-text questions in memory only. Persisted
reports contain benchmark configuration, case/source identifiers, quality
scores and timings; they never contain questions, credentials or raw results.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = ROOT / "notebook_data" / "legal-golden-expert-review.json"
DEFAULT_REPORT_PATH = ROOT / "notebook_data" / "quality_runs" / "retrieval-benchmark.json"
DEFAULT_CANDIDATE_GRID: dict[str, tuple[int, ...]] = {
    "core": (80, 120, 180),
    "expanded": (120, 180, 240),
}
DEFAULT_CONCURRENCY = (1, 5, 10, 20)
DEFAULT_MODES = ("cold", "warm")
TOP_K = 6

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_LAW_NUMBER_RE = re.compile(r"\b\d{1,4}/\d{4}/[A-Z0-9.-]+\b")
_TIMING_KEYS = {"embedding", "ann_search", "hydrate_filter_rank", "total"}
_FORBIDDEN_REPORT_KEYS = {
    "answer",
    "authorization",
    "content",
    "cookie",
    "credential",
    "document_title",
    "password",
    "prompt",
    "query",
    "question",
    "secret",
    "source_url",
    "token",
}


@dataclass(frozen=True)
class BenchmarkCase:
    """One evaluated case; ``query`` must never be copied into a report."""

    case_id: str
    query: str
    expected_source_ids: tuple[str, ...]
    critical_authority_source_ids: tuple[str, ...] = ()
    domain: str | None = None
    as_of: str | None = None


@dataclass(frozen=True)
class Scenario:
    retrieval_tier: str
    candidate_count: int
    concurrency: int
    mode: str


class SearchTransport(Protocol):
    def search(
        self,
        case: BenchmarkCase,
        *,
        retrieval_tier: str,
        candidate_count: int,
    ) -> Mapping[str, Any]: ...


class HttpSearchTransport:
    """HTTP transport restricted to a credential-free loopback URL."""

    def __init__(self, search_url: str, *, timeout_seconds: float = 240.0):
        self.search_url = validate_local_search_url(search_url)
        self.client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def search(
        self,
        case: BenchmarkCase,
        *,
        retrieval_tier: str,
        candidate_count: int,
    ) -> Mapping[str, Any]:
        request: dict[str, Any] = {
            "query": case.query,
            "limit": TOP_K,
            "candidate_count": candidate_count,
            "retrieval_tier": retrieval_tier,
            "include_trace": False,
        }
        if case.domain:
            request["domain"] = case.domain
        if case.as_of:
            request["as_of"] = case.as_of
        response = self.client.post(self.search_url, json=request)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, Mapping) else {}


class FixtureSearchTransport:
    """Deterministic transport for local fixture verification."""

    def __init__(self, path: Path):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        responses = payload.get("responses") if isinstance(payload, Mapping) else None
        if not isinstance(responses, Mapping):
            raise ValueError("Fixture benchmark requires a responses object")
        self.responses = responses

    def search(
        self,
        case: BenchmarkCase,
        *,
        retrieval_tier: str,
        candidate_count: int,
    ) -> Mapping[str, Any]:
        response: Any = self.responses.get(case.case_id, {})
        if isinstance(response, Mapping) and retrieval_tier in response:
            response = response[retrieval_tier]
        if isinstance(response, Mapping) and str(candidate_count) in response:
            response = response[str(candidate_count)]
        return response if isinstance(response, Mapping) else {}


def _ascii_fold(value: str) -> str:
    value = value.replace("Đ", "D").replace("đ", "d")
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def _case_id(value: Any) -> str:
    identifier = str(value or "").strip()
    if not _SAFE_IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError("Case IDs must be opaque identifiers, not free text")
    return identifier


def _simple_source_id(prefix: str, value: Any) -> str | None:
    identifier = _ascii_fold(str(value or "").strip()).replace(" ", "")
    if not identifier or not _SAFE_IDENTIFIER_RE.fullmatch(identifier):
        return None
    return f"{prefix}:{identifier}"


def normalize_source_id(value: Any) -> str | None:
    """Normalize only opaque document/chunk IDs or legal document numbers."""

    raw = str(value or "").strip()
    if not raw or len(raw) > 160 or any(marker in raw for marker in ("?", "#", "@", "=", "\\")):
        return None
    folded = _ascii_fold(raw).upper()
    prefix, separator, suffix = folded.partition(":")
    if separator and prefix.lower() in {"law", "doc", "chunk", "source"}:
        if prefix.lower() == "law":
            law_match = _LAW_NUMBER_RE.search(suffix.replace(" ", ""))
            return f"law:{law_match.group(0)}" if law_match else None
        return _simple_source_id(prefix.lower(), suffix)
    law_match = _LAW_NUMBER_RE.search(folded.replace(" ", ""))
    if law_match:
        return f"law:{law_match.group(0)}"
    return None


def _result_source_ids(result: Mapping[str, Any]) -> list[str]:
    identifiers: list[str] = []
    law_id = normalize_source_id(result.get("law_number"))
    if law_id:
        identifiers.append(law_id)
    for key, prefix in (
        ("document_id", "doc"),
        ("doc_id", "doc"),
        ("chunk_id", "chunk"),
        ("id", "chunk"),
        ("source_id", "source"),
    ):
        identifier = _simple_source_id(prefix, result.get(key))
        if identifier and identifier not in identifiers:
            identifiers.append(identifier)
    return identifiers


def _source_ids(values: Any) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    normalized: list[str] = []
    for value in values:
        identifier = normalize_source_id(value)
        if identifier and identifier not in normalized:
            normalized.append(identifier)
    return tuple(normalized)


def load_cases(path: Path, *, approved_only: bool = True) -> list[BenchmarkCase]:
    """Load compact fixtures or approved expert records without logging text."""

    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("Benchmark case file must be a JSON object")
    records = payload.get("cases")
    is_expert_export = records is None
    if records is None:
        records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("Benchmark case file requires cases or records")

    cases: list[BenchmarkCase] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if is_expert_export and approved_only:
            status = str(record.get("expert_review_status") or "").strip().lower()
            if status != "approved":
                continue
        case_id = _case_id(record.get("case_id") or record.get("review_id") or record.get("id"))
        query = str(record.get("query") or record.get("question") or "").strip()
        if len(query) < 2:
            continue
        expected = _source_ids(
            record.get("expected_source_ids") or record.get("expected_documents") or ()
        )
        critical = _source_ids(
            record.get("critical_authority_source_ids")
            or record.get("critical_authority_documents")
            or expected
        )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                query=query,
                expected_source_ids=expected,
                critical_authority_source_ids=critical,
                domain=str(record.get("domain") or "").strip() or None,
                as_of=str(record.get("legal_as_of") or record.get("as_of") or "").strip() or None,
            )
        )
    return cases


def build_scenarios(
    *,
    candidate_grid: Mapping[str, Sequence[int]] = DEFAULT_CANDIDATE_GRID,
    concurrencies: Sequence[int] = DEFAULT_CONCURRENCY,
    modes: Sequence[str] = DEFAULT_MODES,
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for tier in ("core", "expanded"):
        for candidate_count in candidate_grid.get(tier, ()):
            if int(candidate_count) < 20 or int(candidate_count) > 500:
                raise ValueError("Candidate counts must stay within the retrieval contract")
            for concurrency in concurrencies:
                if int(concurrency) < 1:
                    raise ValueError("Concurrency must be positive")
                for mode in modes:
                    if mode not in {"cold", "warm"}:
                        raise ValueError("Benchmark mode must be cold or warm")
                    scenarios.append(
                        Scenario(tier, int(candidate_count), int(concurrency), mode)
                    )
    return scenarios


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(float(ordered[index]), 3)


def _server_timing(payload: Mapping[str, Any]) -> dict[str, float]:
    timing = payload.get("timing_ms")
    if not isinstance(timing, Mapping):
        return {}
    return {
        key: round(float(value), 3)
        for key, value in timing.items()
        if key in _TIMING_KEYS and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _sample(
    case: BenchmarkCase,
    scenario: Scenario,
    transport: SearchTransport,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = transport.search(
            case,
            retrieval_tier=scenario.retrieval_tier,
            candidate_count=scenario.candidate_count,
        )
        status = "ok"
    except Exception:
        # Raw exceptions can contain URLs, credentials or request content.
        payload = {}
        status = "error"
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    raw_results = payload.get("results") if isinstance(payload, Mapping) else None
    results = raw_results[:TOP_K] if isinstance(raw_results, list) else []
    result_aliases: list[list[str]] = [
        _result_source_ids(result)
        for result in results
        if isinstance(result, Mapping)
    ]
    returned_ids = [aliases[0] for aliases in result_aliases if aliases]
    retrieved = {identifier for aliases in result_aliases for identifier in aliases}
    expected = set(case.expected_source_ids)
    critical = set(case.critical_authority_source_ids)
    matched = sorted(expected & retrieved)
    missing_critical = sorted(critical - retrieved)
    recall = round(len(matched) / len(expected), 5) if expected else None

    return {
        "case_id": case.case_id,
        "retrieval_tier": scenario.retrieval_tier,
        "candidate_count": scenario.candidate_count,
        "concurrency": scenario.concurrency,
        "mode": scenario.mode,
        "status": status,
        "expected_source_ids": list(case.expected_source_ids),
        "returned_source_ids": returned_ids,
        "matched_source_ids": matched,
        "missing_critical_source_ids": missing_critical,
        "recall_at_6": recall,
        "critical_authority_retained": not missing_critical,
        "timing_ms": {
            "client_total": elapsed_ms,
            "server": _server_timing(payload),
        },
    }


def _scenario_summary(
    scenario: Scenario,
    samples: Sequence[Mapping[str, Any]],
    *,
    batch_elapsed_ms: float,
    min_recall_at_6: float,
    min_critical_authority_retention: float,
) -> dict[str, Any]:
    recall_values = [
        float(sample["recall_at_6"])
        for sample in samples
        if isinstance(sample.get("recall_at_6"), (int, float))
    ]
    # Count critical IDs from the persisted result without retaining case text.
    critical_expected = 0
    critical_missing = 0
    for sample in samples:
        missing = list(sample.get("missing_critical_source_ids") or [])
        critical_missing += len(missing)
        critical_expected += int(sample.get("_critical_expected_count") or 0)
    if critical_expected:
        critical_retention = round(
            (critical_expected - critical_missing) / critical_expected,
            5,
        )
    else:
        critical_retention = 1.0
    recall_at_6 = round(sum(recall_values) / len(recall_values), 5) if recall_values else None
    durations = [
        float((sample.get("timing_ms") or {}).get("client_total"))
        for sample in samples
        if isinstance((sample.get("timing_ms") or {}).get("client_total"), (int, float))
    ]
    recall_pass = recall_at_6 is not None and recall_at_6 >= min_recall_at_6
    critical_pass = critical_retention >= min_critical_authority_retention
    errors = sum(sample.get("status") != "ok" for sample in samples)
    return {
        "retrieval_tier": scenario.retrieval_tier,
        "candidate_count": scenario.candidate_count,
        "concurrency": scenario.concurrency,
        "mode": scenario.mode,
        "case_count": len(samples),
        "error_count": errors,
        "recall_at_6": recall_at_6,
        "critical_authority_retention": critical_retention,
        "gates": {
            "recall_at_6": recall_pass,
            "critical_authority_retention": critical_pass,
        },
        "timing_ms": {
            "batch_total": round(batch_elapsed_ms, 3),
            "client_p50": _percentile(durations, 0.50),
            "client_p95": _percentile(durations, 0.95),
            "client_max": round(max(durations), 3) if durations else None,
        },
        "pass": bool(samples) and errors == 0 and recall_pass and critical_pass,
    }


def run_benchmark(
    cases: Sequence[BenchmarkCase],
    transport: SearchTransport,
    *,
    candidate_grid: Mapping[str, Sequence[int]] = DEFAULT_CANDIDATE_GRID,
    concurrencies: Sequence[int] = DEFAULT_CONCURRENCY,
    modes: Sequence[str] = DEFAULT_MODES,
    min_recall_at_6: float = 0.95,
    min_critical_authority_retention: float = 1.0,
) -> dict[str, Any]:
    """Execute the matrix. ``cold`` is the first pass, ``warm`` the repeat pass."""

    scenarios = build_scenarios(
        candidate_grid=candidate_grid,
        concurrencies=concurrencies,
        modes=modes,
    )
    all_samples: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for scenario in scenarios:
        batch_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=scenario.concurrency) as executor:
            samples = list(
                executor.map(
                    lambda case: _sample(case, scenario, transport),
                    cases,
                )
            )
        batch_elapsed_ms = (time.perf_counter() - batch_started) * 1000
        for sample, case in zip(samples, cases, strict=True):
            sample["_critical_expected_count"] = len(case.critical_authority_source_ids)
        summary = _scenario_summary(
            scenario,
            samples,
            batch_elapsed_ms=batch_elapsed_ms,
            min_recall_at_6=min_recall_at_6,
            min_critical_authority_retention=min_critical_authority_retention,
        )
        for sample in samples:
            sample.pop("_critical_expected_count", None)
        all_samples.extend(samples)
        summaries.append(summary)

    report = {
        "schema_version": "1.0",
        "mode": "benchmark",
        "generated_at": datetime.now(UTC).isoformat(),
        "top_k": TOP_K,
        "thresholds": {
            "min_recall_at_6": min_recall_at_6,
            "min_critical_authority_retention": min_critical_authority_retention,
        },
        "samples": all_samples,
        "summaries": summaries,
        "pass": bool(summaries) and all(summary["pass"] for summary in summaries),
    }
    assert_report_privacy(report)
    return report


def validate_local_search_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Local benchmark URL must use HTTP or HTTPS")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Live benchmark execution is restricted to loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Benchmark URL must not include credentials or query values")
    return url


def assert_report_privacy(value: Any, *, path: str = "report") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(forbidden in lowered for forbidden in _FORBIDDEN_REPORT_KEYS):
                raise ValueError(f"Forbidden report field at {path}")
            assert_report_privacy(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_report_privacy(item, path=f"{path}[{index}]")
    elif isinstance(value, str) and "://" in value:
        raise ValueError(f"URLs are forbidden in benchmark reports at {path}")


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    assert_report_privacy(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _csv_ints(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected comma-separated integers") from exc
    if not values:
        raise argparse.ArgumentTypeError("At least one integer is required")
    return values


def _dry_run_report(
    cases: Sequence[BenchmarkCase],
    scenarios: Sequence[Scenario],
    *,
    min_recall_at_6: float,
    min_critical_authority_retention: float,
) -> dict[str, Any]:
    report = {
        "schema_version": "1.0",
        "mode": "dry_run",
        "generated_at": datetime.now(UTC).isoformat(),
        "case_ids": [case.case_id for case in cases],
        "scenario_count": len(scenarios),
        "planned_request_count": len(cases) * len(scenarios),
        "candidate_grid": {
            tier: sorted(
                {scenario.candidate_count for scenario in scenarios if scenario.retrieval_tier == tier}
            )
            for tier in ("core", "expanded")
        },
        "concurrency": sorted({scenario.concurrency for scenario in scenarios}),
        "modes": sorted({scenario.mode for scenario in scenarios}),
        "thresholds": {
            "min_recall_at_6": min_recall_at_6,
            "min_critical_authority_retention": min_critical_authority_retention,
        },
    }
    assert_report_privacy(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a privacy-safe legal retrieval benchmark (dry-run by default)"
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--core-candidates", type=_csv_ints, default=DEFAULT_CANDIDATE_GRID["core"])
    parser.add_argument(
        "--expanded-candidates",
        type=_csv_ints,
        default=DEFAULT_CANDIDATE_GRID["expanded"],
    )
    parser.add_argument("--concurrency", type=_csv_ints, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--min-recall-at-6", type=float, default=0.95)
    parser.add_argument("--min-critical-authority-retention", type=float, default=1.0)
    parser.add_argument("--include-unapproved", action="store_true")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--execute-local", action="store_true")
    execution.add_argument("--fixture-results", type=Path)
    parser.add_argument("--search-url", default="http://127.0.0.1:8765/search")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases, approved_only=not args.include_unapproved)
    if args.case_limit is not None:
        if args.case_limit < 1:
            parser.error("--case-limit must be positive")
        cases = cases[: args.case_limit]
    candidate_grid = {
        "core": args.core_candidates,
        "expanded": args.expanded_candidates,
    }
    scenarios = build_scenarios(
        candidate_grid=candidate_grid,
        concurrencies=args.concurrency,
    )

    if not args.execute_local and args.fixture_results is None:
        report = _dry_run_report(
            cases,
            scenarios,
            min_recall_at_6=args.min_recall_at_6,
            min_critical_authority_retention=args.min_critical_authority_retention,
        )
        write_report(args.report, report)
        print(f"Dry-run report written: {args.report.resolve()}")
        return 0

    if not cases:
        parser.error("No eligible benchmark cases; approve records or use a fixture case file")

    transport: SearchTransport
    http_transport: HttpSearchTransport | None = None
    if args.fixture_results is not None:
        transport = FixtureSearchTransport(args.fixture_results)
    else:
        try:
            http_transport = HttpSearchTransport(
                args.search_url,
                timeout_seconds=args.timeout_seconds,
            )
        except ValueError:
            parser.error("Live execution requires a credential-free loopback search URL")
        transport = http_transport

    try:
        report = run_benchmark(
            cases,
            transport,
            candidate_grid=candidate_grid,
            concurrencies=args.concurrency,
            min_recall_at_6=args.min_recall_at_6,
            min_critical_authority_retention=args.min_critical_authority_retention,
        )
    finally:
        if http_transport is not None:
            http_transport.close()
    write_report(args.report, report)
    print(f"Benchmark report written: {args.report.resolve()}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
