#!/usr/bin/env python3
"""Audit the final Retrieval Release V2 acceptance evidence.

The M5/M6 benchmark runner proves retrieval-stage gates.  This separate
read-only audit adds the production-answer gates that cannot be inferred from
Recall/MRR alone: full-answer latency, document/procedure correctness,
critical-fact coverage, citation support, fallback rate, exact-294 regression,
the sealed production holdout, and pointer preservation.  Missing evidence is
reported as BLOCKED.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_POINTER = ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt"
DEFAULT_OUTPUT = DEFAULT_DIR / "retrieval-release-v2-acceptance.json"


def _load(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else None


def _metric(report: dict[str, Any], *names: str) -> float | None:
    for name in names:
        value = report.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _status_pass(report: dict[str, Any] | None) -> bool:
    return bool(report and str(report.get("status") or "").upper() == "PASS")


def audit(
    *,
    m5_path: Path,
    m6_path: Path,
    full_answer_path: Path,
    exact_294_path: Path,
    holdout_path: Path | None = None,
    pointer_path: Path,
    expected_pointer: str,
    output: Path,
) -> dict[str, Any]:
    blockers: list[str] = []
    checks: dict[str, Any] = {}
    m5 = _load(m5_path)
    m6 = _load(m6_path)
    full_answer = _load(full_answer_path)
    exact_294 = _load(exact_294_path)
    holdout_path = holdout_path or DEFAULT_DIR / "production-holdout-acceptance-v1.json"
    holdout = _load(holdout_path)
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None

    checks["m5"] = {
        "path": str(m5_path.resolve()),
        "exists": m5 is not None,
        "status": (m5 or {}).get("status"),
        "selected_gates": (m5 or {}).get("selected_gates"),
    }
    if not _status_pass(m5) or not all(bool(value) for value in (m5 or {}).get("selected_gates", {}).values()):
        blockers.append("m5_retrieval_gate")

    checks["m6"] = {
        "path": str(m6_path.resolve()),
        "exists": m6 is not None,
        "status": (m6 or {}).get("status"),
        "selected_gates": (m6 or {}).get("selected_gates"),
    }
    if not _status_pass(m6) or not all(bool(value) for value in (m6 or {}).get("selected_gates", {}).values()):
        blockers.append("m6_retrieval_reranking_gate")

    release_fingerprint_keys = (
        "manifest_file_sha256",
        "manifest_sha256",
        "suite_file_sha256",
        "suite_sha256",
        "embedding_model_fingerprint",
    )
    release_fingerprints_match = bool(m5 and m6) and all(
        m5.get(key) == m6.get(key) and m5.get(key) not in (None, "")
        for key in release_fingerprint_keys
    )
    checks["release_fingerprints"] = {
        "pass": release_fingerprints_match,
        "keys": list(release_fingerprint_keys),
        "m5": {key: (m5 or {}).get(key) for key in release_fingerprint_keys},
        "m6": {key: (m6 or {}).get(key) for key in release_fingerprint_keys},
    }
    if _status_pass(m5) and _status_pass(m6) and not release_fingerprints_match:
        blockers.append("m5_m6_release_fingerprint_mismatch")

    answer_metrics = {
        "full_answer_p95_ms": _metric(full_answer or {}, "full_answer_p95_ms", "p95_ms"),
        "correct_document_procedure_rate": _metric(full_answer or {}, "correct_document_procedure_rate"),
        "critical_fact_coverage": _metric(full_answer or {}, "critical_fact_coverage"),
        "unexpected_fallback_rate": _metric(full_answer or {}, "unexpected_fallback_rate"),
        "citation_support_rate": _metric(full_answer or {}, "citation_support_rate", "citation_support"),
        "outside_manifest_count": (full_answer or {}).get("outside_manifest_count"),
        "invalid_temporal_count": (full_answer or {}).get("invalid_temporal_count"),
    }
    checks["full_answer"] = {"path": str(full_answer_path.resolve()), "exists": full_answer is not None, "metrics": answer_metrics}
    if not _status_pass(full_answer):
        blockers.append("full_answer_evidence_missing_or_failed")
    else:
        if answer_metrics["full_answer_p95_ms"] is None or answer_metrics["full_answer_p95_ms"] > 25_000:
            blockers.append("full_answer_p95_gt_25s")
        if answer_metrics["correct_document_procedure_rate"] is None or answer_metrics["correct_document_procedure_rate"] < 0.99:
            blockers.append("correct_document_procedure_lt_99pct")
        if answer_metrics["critical_fact_coverage"] is None or answer_metrics["critical_fact_coverage"] < 0.95:
            blockers.append("critical_fact_coverage_lt_95pct")
        if answer_metrics["unexpected_fallback_rate"] is None or answer_metrics["unexpected_fallback_rate"] > 0.03:
            blockers.append("unexpected_fallback_gt_3pct")
        if answer_metrics["citation_support_rate"] is None or answer_metrics["citation_support_rate"] < 1.0:
            blockers.append("citation_support_lt_100pct")
        if int(answer_metrics["outside_manifest_count"] or 0) != 0:
            blockers.append("outside_manifest_evidence")
        if int(answer_metrics["invalid_temporal_count"] or 0) != 0:
            blockers.append("invalid_temporal_evidence")

    checks["exact_294"] = {"path": str(exact_294_path.resolve()), "exists": exact_294 is not None, "status": (exact_294 or {}).get("status")}
    if not _status_pass(exact_294):
        blockers.append("exact_294_regression_missing_or_failed")

    holdout_gates = (holdout or {}).get("gates") or {}
    checks["production_holdout"] = {
        "path": str(holdout_path.resolve()),
        "exists": holdout is not None,
        "status": (holdout or {}).get("status"),
        "case_count": (holdout or {}).get("holdout_case_count"),
        "holdout_sealed": (holdout or {}).get("holdout_sealed"),
        "gates": holdout_gates,
    }
    if (
        not _status_pass(holdout)
        or int((holdout or {}).get("holdout_case_count") or 0) != 500
        or (holdout or {}).get("holdout_sealed") is not True
        or not holdout_gates
        or not all(bool(value) for value in holdout_gates.values())
    ):
        blockers.append("production_holdout_acceptance_missing_or_failed")

    expected_benchmark_hashes = {
        file_sha256(path)
        for path, report in ((m5_path, m5), (m6_path, m6))
        if report is not None and path.is_file()
    }
    holdout_benchmark_hash = (holdout or {}).get("benchmark_report_sha256")
    holdout_binding_pass = bool(
        holdout
        and holdout_benchmark_hash in expected_benchmark_hashes
        and (not release_fingerprints_match or all(
            holdout.get(key) == m6.get(key)
            for key in ("manifest_file_sha256", "manifest_sha256", "suite_file_sha256", "suite_sha256")
            if holdout.get(key) is not None and m6.get(key) is not None
        ))
    )
    checks["holdout_binding"] = {
        "pass": holdout_binding_pass,
        "benchmark_report_sha256": holdout_benchmark_hash,
        "expected_benchmark_report_sha256": sorted(expected_benchmark_hashes),
        "selected_experiment": (holdout or {}).get("selected_experiment"),
    }
    if _status_pass(holdout) and not holdout_binding_pass:
        blockers.append("production_holdout_fingerprint_mismatch")

    pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
    checks["active_pointer"] = {
        "path": str(pointer_path.resolve()),
        "before": pointer_before,
        "after": pointer_after,
        "expected_baseline": expected_pointer,
        "unchanged": pointer_before == pointer_after,
    }
    if pointer_before != pointer_after or pointer_after != expected_pointer:
        blockers.append("active_pointer_changed_or_unexpected")

    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-release-v2-acceptance-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "checks": checks,
        "mutation": {
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m5", type=Path, default=DEFAULT_DIR / "m5-v2-acceptance.json")
    parser.add_argument("--m6", type=Path, default=DEFAULT_DIR / "m6-v2-acceptance.json")
    parser.add_argument("--full-answer", type=Path, default=DEFAULT_DIR / "full-answer-acceptance.json")
    parser.add_argument("--exact-294", type=Path, default=DEFAULT_DIR / "golden-294-acceptance.json")
    parser.add_argument("--holdout", type=Path, default=DEFAULT_DIR / "production-holdout-acceptance-v1.json")
    parser.add_argument("--pointer", type=Path, default=DEFAULT_POINTER)
    parser.add_argument("--expected-pointer", default="legal_chunks_vnlegal_lal_haiphong_unified_v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = audit(
        m5_path=args.m5.resolve(),
        m6_path=args.m6.resolve(),
        full_answer_path=args.full_answer.resolve(),
        exact_294_path=args.exact_294.resolve(),
        holdout_path=args.holdout.resolve(),
        pointer_path=args.pointer.resolve(),
        expected_pointer=args.expected_pointer,
        output=args.output.resolve(),
    )
    print(json.dumps({"status": report["status"], "blockers": report["blockers"], "output": str(args.output.resolve())}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
