"""Phase C shadow eval: real qrels only, no fabricated Recall/MRR.

Uses artifacts/kaggle-retrieval-r28-g2000-v1 dataset when present. Probes
LEGAL_SEARCH_URL and the independent shadow collection. If the search service
is down, writes a blocker JSON and still measures corpus exact-law coverage
from the read-only lexical sqlite.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

from api.retrieval_vnext_shadow import (
    BUILD_SCRIPT_SHADOW_COLLECTION,
    DEFAULT_SHADOW_COLLECTION,
    LIVE_POINTER_COLLECTION,
    MAX_EVIDENCE_UNITS,
    RERANK_WINDOW,
    read_live_pointer,
    refuse_live_collection,
    shadow_collection_name,
    vnext_shadow_enabled,
)
from scripts.kaggle_retrieval_v2_benchmark_common import (
    compact_summary,
    evaluate_rows,
    normalize_exact,
    score_case,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / (
    "artifacts/kaggle-retrieval-r28-g2000-v1/dataset/"
    "retrieval-eval-suite-v1-final-golden2000.json"
)
DEFAULT_LEXICAL = ROOT / (
    "reports/retrieval-release-v2/legal-retrieval-v2-exact-lexical-v6r1.sqlite3"
)
DEFAULT_CHROMA = ROOT / "release-data/legal/chroma_store/chroma.sqlite3"
DEFAULT_OUT = ROOT / "artifacts/retrieval-vnext-shadow/phase-c-eval-20260901.json"

DEFAULT_ITER100 = ROOT / "tests/fixtures/retrieval_vnext_iter100.json"
DEFAULT_GO_GOLDEN1000 = ROOT / (
    "artifacts/kaggle-retrieval-r28-g2000-v1/dataset/"
    "retrieval-eval-suite-v1-golden1000.json"
)
DEFAULT_GO_HARDNEG500 = ROOT / (
    "artifacts/kaggle-retrieval-r28-g2000-v1/dataset/"
    "retrieval-eval-suite-v1-hardneg500.json"
)
DEFAULT_OUT_ITER100 = ROOT / "artifacts/retrieval-vnext-shadow/phase-c-iter100.json"
DEFAULT_OUT_FULL_GO = ROOT / "artifacts/retrieval-vnext-shadow/phase-c-go-1000-500.json"


def load_iter100(path: Path | None = None) -> dict[str, Any]:
    """Frozen 100-question iteration slice. Never the 2000-file."""
    target = path or DEFAULT_ITER100
    if not target.is_file():
        return {
            "path": str(target),
            "missing": True,
            "go_set_missing": False,
            "cases": [],
            "blockers": [f"iter100_missing:{target}"],
            "case_count": 0,
        }
    payload = json.loads(target.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        cases = []
    return {
        "path": str(target),
        "missing": False,
        "go_set_missing": False,
        "schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
        "dataset_version": payload.get("dataset_version") if isinstance(payload, dict) else None,
        "suite_sha256": payload.get("source_suite_sha256") if isinstance(payload, dict) else None,
        "case_count": len(cases),
        "cases": cases,
        "blockers": [],
        "case_ids": payload.get("case_ids") if isinstance(payload, dict) else [c.get("case_id") for c in cases],
    }


def load_go_1000_500(path: Path | None = None) -> dict[str, Any]:
    """Official GO: Golden 1000 reviewed + 500 hard negatives.

    Refuses the 2000-file. If the 1000 and 500 files are absent, returns
    go_set_missing=True and empty cases (caller must not evaluate).
    """
    if path is not None:
        name = path.name.lower()
        if "golden2000" in name or "golden-v4-2000" in name or "final-golden-v4-2000" in name:
            return {
                "path": str(path),
                "missing": True,
                "go_set_missing": True,
                "cases": [],
                "case_count": 0,
                "blockers": ["full_go_refuses_golden2000"],
            }
        loaded = load_golden(path)
        loaded["go_set_missing"] = bool(loaded.get("missing"))
        loaded["blockers"] = [] if not loaded.get("missing") else [f"go_override_missing:{path}"]
        return loaded

    missing_paths = [p for p in (DEFAULT_GO_GOLDEN1000, DEFAULT_GO_HARDNEG500) if not p.is_file()]
    if missing_paths:
        return {
            "path": f"{DEFAULT_GO_GOLDEN1000} + {DEFAULT_GO_HARDNEG500}",
            "missing": True,
            "go_set_missing": True,
            "cases": [],
            "case_count": 0,
            "blockers": [f"official_go_file_missing:{p}" for p in missing_paths],
            "schema_version": None,
            "dataset_version": None,
            "suite_sha256": None,
        }

    golden = load_golden(DEFAULT_GO_GOLDEN1000)
    hardneg = load_golden(DEFAULT_GO_HARDNEG500)
    cases = list(golden.get("cases") or []) + list(hardneg.get("cases") or [])
    return {
        "path": f"{DEFAULT_GO_GOLDEN1000} + {DEFAULT_GO_HARDNEG500}",
        "missing": False,
        "go_set_missing": False,
        "schema_version": golden.get("schema_version"),
        "dataset_version": golden.get("dataset_version"),
        "suite_sha256": golden.get("suite_sha256"),
        "case_count": len(cases),
        "cases": cases,
        "blockers": [],
    }

GATES = {
    "candidate_recall_at_50": 0.99,
    "recall_at_10": 0.97,
    "recall_at_10_per_domain": 0.95,
    "mrr_at_10": 0.92,
    "exact_law_article_in_corpus": 1.0,
    "multi_issue_all_required_sources_coverage": 0.95,
    "correct_refusal_rate": 0.99,
    "invalid_forbidden_source": 0,
    "warm_p95_ms": 3000,
    "cold_p95_ms": 8000,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_golden(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_GOLDEN
    if not target.is_file():
        return {"path": str(target), "missing": True, "cases": []}
    payload = json.loads(target.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        cases = []
    return {
        "path": str(target),
        "missing": False,
        "schema_version": payload.get("schema_version") if isinstance(payload, dict) else None,
        "dataset_version": payload.get("dataset_version") if isinstance(payload, dict) else None,
        "suite_sha256": payload.get("suite_sha256") if isinstance(payload, dict) else None,
        "case_count": len(cases),
        "cases": cases,
    }


def probe_search(url: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    base = (url or os.getenv("LEGAL_SEARCH_URL") or "http://127.0.0.1:8766").rstrip("/")
    result: dict[str, Any] = {"url": base, "up": False, "status": None, "body": None, "error": None}
    try:
        with urlopen(Request(base + "/health"), timeout=timeout) as response:
            result["status"] = int(response.status)
            raw = response.read(8000).decode("utf-8", errors="replace")
            try:
                result["body"] = json.loads(raw)
            except json.JSONDecodeError:
                result["body"] = raw[:500]
            result["up"] = result["status"] == 200
    except (URLError, OSError, TimeoutError, ValueError) as exc:
        result["error"] = str(exc)
        result["up"] = False
    return result


def list_chroma_collections(path: Path | None = None) -> list[str]:
    db = path or DEFAULT_CHROMA
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name FROM collections ORDER BY name").fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _source_key(source: Mapping[str, Any]) -> str:
    law = normalize_exact(source.get("law_number"))
    article = normalize_exact(source.get("article") or source.get("article_number"))
    if article:
        return f"{law}|{article}"
    return law


def measure_corpus_exact_coverage(
    cases: Sequence[Mapping[str, Any]],
    lexical_path: Path | None = None,
) -> dict[str, Any]:
    """Exact law/article already in the shadow lexical corpus (not Recall)."""

    db = lexical_path or Path(
        os.getenv("LEGAL_RETRIEVAL_EVAL_LEXICAL") or str(DEFAULT_LEXICAL)
    )
    if not db.is_file():
        return {
            "measured": False,
            "reason": "lexical_sqlite_missing",
            "path": str(db),
        }
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        law_keys = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT normalized_key FROM exact_lookup WHERE key_kind='law_number'"
            )
        }
        article_keys = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT normalized_key FROM exact_lookup WHERE key_kind='law_article'"
            )
        }
    finally:
        conn.close()

    answer = [case for case in cases if case.get("answer_required")]
    present = 0
    missing_cases: list[dict[str, Any]] = []
    source_total = 0
    source_present = 0
    for case in answer:
        groups = list(case.get("positive_source_groups") or [])
        all_found = True
        missing_sources: list[str] = []
        for group in groups:
            for source in group.get("sources") or []:
                source_total += 1
                key = _source_key(source)
                found = key in article_keys or key in law_keys
                if "|" not in key:
                    found = key in law_keys
                if found:
                    source_present += 1
                else:
                    all_found = False
                    missing_sources.append(key)
        if all_found:
            present += 1
        elif len(missing_cases) < 50:
            missing_cases.append(
                {
                    "case_id": case.get("case_id"),
                    "missing_sources": missing_sources[:8],
                }
            )
    rate = present / len(answer) if answer else 0.0
    return {
        "measured": True,
        "path": str(db),
        "answer_required_count": len(answer),
        "cases_with_all_sources_in_corpus": present,
        "exact_law_article_in_corpus": rate,
        "source_total": source_total,
        "source_present": source_present,
        "source_present_rate": source_present / source_total if source_total else 0.0,
        "missing_sample": missing_cases,
    }


def _hard_negative_hit(
    rows: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
) -> int:
    forbidden = list(case.get("hard_negative_sources") or [])
    if not forbidden:
        return 0
    count = 0
    for item in rows:
        for source in forbidden:
            if normalize_exact(item.get("law_number")) == normalize_exact(
                source.get("law_number")
            ):
                article = normalize_exact(source.get("article") or source.get("article_number"))
                if not article or article == normalize_exact(
                    item.get("article_number") or item.get("article")
                ):
                    count += 1
                    break
    return count


def score_vnext_case(
    case: Mapping[str, Any],
    *,
    final: Sequence[Mapping[str, Any]],
    candidate_top50: Sequence[Mapping[str, Any]],
    exact_candidates: Sequence[Mapping[str, Any]] = (),
    latency_ms: float,
    packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tagged = dict(case)
    tags = set(tagged.get("tags") or [])
    if "exact_article" in tags:
        tags.add("exact_law_article")
        tagged["tags"] = sorted(tags)
    row = score_case(
        tagged,
        final,
        candidate_top50,
        exact_candidates=exact_candidates,
        latency_ms=latency_ms,
    )
    if case.get("expected_refusal"):
        status = str((packet or {}).get("status") or "")
        cannot_verify = bool((packet or {}).get("cannot_verify"))
        row["correct_refusal"] = status in {"insufficient", "partial"} or cannot_verify
        if not final and case.get("expected_refusal"):
            row["correct_refusal"] = True
    row["invalid_forbidden_source_count"] = _hard_negative_hit(final, case)
    return row


def gate_table(
    *,
    summary: Mapping[str, Any] | None,
    corpus: Mapping[str, Any],
    search: Mapping[str, Any],
    latency_kind: str | None = None,
) -> list[dict[str, Any]]:
    def cell(name: str, threshold: float, value: Any, *, measured: bool, cmp: str = "gte") -> dict[str, Any]:
        passed = None
        if measured and value is not None:
            number = float(value)
            passed = number >= threshold if cmp == "gte" else number <= threshold
        return {
            "gate": name,
            "threshold": threshold,
            "value": value,
            "measured": measured,
            "passed": passed,
        }

    measured_search = bool(search.get("up")) and summary is not None
    per_domain = (summary or {}).get("per_domain") or {}
    domain_floor = None
    if per_domain:
        domain_floor = min(
            float(item.get("recall_at_10") or 0.0)
            for item in per_domain.values()
            if isinstance(item, Mapping)
        )
    rows = [
        cell(
            "candidate_recall_at_50",
            GATES["candidate_recall_at_50"],
            (summary or {}).get("candidate_recall_at_50") if measured_search else None,
            measured=measured_search,
        ),
        cell(
            "final_recall_at_10",
            GATES["recall_at_10"],
            (summary or {}).get("recall_at_10") if measured_search else None,
            measured=measured_search,
        ),
        cell(
            "final_recall_at_10_per_domain",
            GATES["recall_at_10_per_domain"],
            domain_floor if measured_search else None,
            measured=measured_search,
        ),
        cell(
            "mrr_at_10",
            GATES["mrr_at_10"],
            (summary or {}).get("mrr_at_10") if measured_search else None,
            measured=measured_search,
        ),
        cell(
            "exact_law_article_in_corpus",
            GATES["exact_law_article_in_corpus"],
            corpus.get("exact_law_article_in_corpus") if corpus.get("measured") else None,
            measured=bool(corpus.get("measured")),
        ),
        cell(
            "multi_issue_full_source_coverage",
            GATES["multi_issue_all_required_sources_coverage"],
            (summary or {}).get("multi_issue_all_required_sources_coverage")
            if measured_search
            else None,
            measured=measured_search,
        ),
        cell(
            "correct_refusal",
            GATES["correct_refusal_rate"],
            (summary or {}).get("correct_refusal_rate") if measured_search else None,
            measured=measured_search,
        ),
        cell(
            "invalid_forbidden_source",
            GATES["invalid_forbidden_source"],
            (summary or {}).get("invalid_forbidden_source_count") if measured_search else None,
            measured=measured_search,
            cmp="lte",
        ),
        cell(
            "warm_p95_ms",
            GATES["warm_p95_ms"],
            ((summary or {}).get("latency_ms") or {}).get("p95")
            if measured_search and latency_kind != "cold"
            else None,
            measured=measured_search and latency_kind != "cold",
            cmp="lte",
        ),
        cell(
            "cold_p95_ms",
            GATES["cold_p95_ms"],
            None,
            measured=False,
            cmp="lte",
        ),
    ]
    return rows


def audit_environment() -> dict[str, Any]:
    collections = list_chroma_collections()
    live = read_live_pointer(ROOT)
    # An operator-selected R28 canary collection is still required to be
    # side-by-side and non-live; the old default remains for compatibility.
    shadow = str(os.getenv("LEGAL_RETRIEVAL_EVAL_COLLECTION") or shadow_collection_name())
    search = probe_search()
    selected_present = shadow in collections
    return {
        "generated_at": utc_now(),
        "vnext_flag": vnext_shadow_enabled(),
        "legal_search_url": os.getenv("LEGAL_SEARCH_URL") or "http://127.0.0.1:8766",
        "search": search,
        "live_pointer_file": live,
        "live_pointer_collection": LIVE_POINTER_COLLECTION,
        "env_chroma_collection": os.getenv("LEGAL_CHROMA_COLLECTION"),
        "env_chroma_source_collection": os.getenv("LEGAL_CHROMA_SOURCE_COLLECTION"),
        "env_chroma_temporal_collection": os.getenv("LEGAL_CHROMA_TEMPORAL_COLLECTION"),
        "env_retrieval_v2_mode": os.getenv("LEGAL_RETRIEVAL_V2_MODE"),
        "shadow_collection_selected": shadow,
        "build_script_shadow_collection": BUILD_SCRIPT_SHADOW_COLLECTION,
        "build_script_shadow_present": selected_present or BUILD_SCRIPT_SHADOW_COLLECTION in collections,
        "kaggle_shadow_present": selected_present or DEFAULT_SHADOW_COLLECTION in collections,
        "chroma_collections": collections,
        "refuse_live": refuse_live_collection(shadow, root=ROOT),
        "rerank_window": RERANK_WINDOW,
        "max_units": MAX_EVIDENCE_UNITS,
        "live_pointer_written": False,
    }


def run_eval(
    *,
    golden_path: Path | None = None,
    out_path: Path | None = None,
    max_cases: int | None = None,
) -> dict[str, Any]:
    golden = load_golden(golden_path)
    audit = audit_environment()
    cases = list(golden.get("cases") or [])
    if max_cases is not None:
        cases = cases[: max(0, int(max_cases))]
    corpus = measure_corpus_exact_coverage(cases)
    blockers: list[str] = []
    if golden.get("missing"):
        blockers.append(f"golden_missing:{golden.get('path')}")
    if not cases:
        blockers.append("no_qrels")
    if not audit["search"]["up"]:
        blockers.append("legal_search_url_down:" + str(audit["search"].get("error") or "connect_failed"))
    if not audit["kaggle_shadow_present"] and not audit["build_script_shadow_present"]:
        blockers.append("shadow_collection_missing")
    if audit["refuse_live"]:
        blockers.append("selected_shadow_is_live_pointer")

    summary = None
    records: list[dict[str, Any]] = []
    # Live search eval is only attempted when the service is up. Never invent Recall.
    if not blockers and audit["search"]["up"]:
        blockers.append("search_up_but_in_process_http_eval_not_wired_for_full_2000_without_operator_start")
        # The HTTP eval loop lives in scripts/eval_retrieval_vnext_shadow.py so this
        # library stays importable in unit tests without network.
    report = {
        "schema_version": "phase-c-retrieval-vnext-eval-v1",
        "generated_at": utc_now(),
        "timezone_note": "timestamps are UTC; user zone is Asia/Bangkok (UTC+7)",
        "audit": audit,
        "golden": {
            "path": golden.get("path"),
            "missing": golden.get("missing"),
            "schema_version": golden.get("schema_version"),
            "dataset_version": golden.get("dataset_version"),
            "suite_sha256": golden.get("suite_sha256"),
            "case_count": golden.get("case_count"),
            "evaluated_case_count": len(cases),
        },
        "corpus_exact_coverage": corpus,
        "summary": compact_summary(summary) if summary else None,
        "gates": gate_table(summary=summary, corpus=corpus, search=audit["search"]),
        "blockers": blockers,
        "live_pointer_written": False,
        "activation_performed": False,
        "phase_d_e_started": False,
        "records_included": False,
        "note": (
            "Recall/MRR are null unless a real shadow search eval ran. "
            "Corpus exact coverage is measured from the read-only lexical sqlite."
        ),
    }
    report["gates_passed"] = all(
        row.get("passed") is True for row in report["gates"] if row.get("measured")
    ) and not blockers
    target = out_path or DEFAULT_OUT
    write_json(target, report)
    report["metrics_path"] = str(target)
    return report


__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_OUT",
    "GATES",
    "audit_environment",
    "gate_table",
    "load_golden",
    "measure_corpus_exact_coverage",
    "probe_search",
    "run_eval",
    "score_vnext_case",
]
