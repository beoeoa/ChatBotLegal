"""Build a privacy-safe queue for candidate answer/citation failures.

This is a read-only diagnostic.  It joins the answer Golden report with the
approved Golden source packet and the retrieval Golden report.  It does not
call an API, mutate a database/vector collection, or persist question/answer
bodies.  The queue deliberately distinguishes intentional ``explicit_fallback``
cases from grounded cases that were found by retrieval but were rendered as
``source_view_only``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def rows(payload: dict[str, Any], key: str = "cases") -> list[dict[str, Any]]:
    value = payload.get(key)
    return [item for item in value or [] if isinstance(item, dict)]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_projection(item: dict[str, Any]) -> dict[str, Any]:
    """Keep only citation identity/validity fields; never source bodies."""

    return {
        "law_number": item.get("law_number"),
        "article": item.get("article") or item.get("article_number"),
        "clause": item.get("clause") or item.get("clause_number"),
        "point": item.get("point") or item.get("point_number"),
        "document_id": item.get("document_id"),
        "source_url": item.get("source_url"),
        "effective_status": item.get("effective_status"),
        "current": item.get("current"),
        "rank": item.get("rank"),
        "reason_code": item.get("reason_code"),
    }


def expected_source_projection(item: dict[str, Any]) -> dict[str, Any]:
    proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
    return {
        "law_number": item.get("law_number"),
        "article": item.get("article") or item.get("provision"),
        "clause": item.get("clause"),
        "point": item.get("point"),
        "document_id": item.get("document_id"),
        "source_url": proof.get("official_url") or item.get("source_url"),
        "reason": item.get("reason"),
    }


def classify_failure(
    answer: dict[str, Any],
    golden: dict[str, Any],
    retrieval: dict[str, Any] | None,
) -> tuple[str, str, str, bool]:
    expected_mode = str(golden.get("expected_answer_mode") or "")
    retrieval_class = str((retrieval or {}).get("classification") or "unknown")
    retrieval_reason = str((retrieval or {}).get("reason_code") or "not_recorded")

    # These cases are intentionally refusal/source-only cases.  Counting them
    # as missing citation coverage makes the activation gate semantically wrong.
    if expected_mode == "explicit_fallback":
        return (
            "J",
            "expected_explicit_fallback_not_citation_failure",
            "answer_contract_expected_refusal",
            True,
        )

    # Retrieval found the expected live source, while the answer benchmark
    # still produced source_view_only/insufficient_evidence.  This is an
    # answer-layer fallback/quality-gate failure, not a corpus recall failure.
    if expected_mode == "grounded_answer" and retrieval_class == "FOUND_AND_RETRIEVED":
        return (
            "I",
            "retrieved_expected_source_but_quality_gate_or_fallback_blocked_answer",
            "answer_quality_gate_or_provider_fallback",
            False,
        )

    if retrieval_class in {"NOT_FOUND", "WRONG_SOURCE", "UNKNOWN"}:
        return (
            "A",
            "expected_source_not_retrieved_or_retrieval_trace_missing",
            "retrieval",
            False,
        )

    return (
        "J",
        "unconfirmed_requires_end_to_end_trace",
        "unknown",
        False,
    )


def build_queue(
    answer_report: dict[str, Any],
    golden_report: dict[str, Any],
    retrieval_report: dict[str, Any],
    source_report: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    golden_by_id = {str(row.get("case_id")): row for row in rows(golden_report)}
    retrieval_by_id = {str(row.get("case_id")): row for row in rows(retrieval_report)}
    source_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in rows(source_report, "sources"):
        source_by_id.setdefault(str(item.get("case_id")), []).append(item)

    failures = [
        row
        for row in rows(answer_report.get("candidate") or {})
        if row.get("citation_coverage_verified") is not True
    ]
    queue: list[dict[str, Any]] = []
    for answer in failures:
        case_id = str(answer.get("case_id") or "")
        golden = golden_by_id.get(case_id, {})
        retrieval = retrieval_by_id.get(case_id, {})
        category, root_cause, failure_stage, confirmed = classify_failure(
            answer, golden, retrieval
        )
        expected = [
            expected_source_projection(item)
            for item in golden.get("expected_sources") or []
            if isinstance(item, dict)
        ]
        retrieved = [
            source_projection(item)
            for item in (retrieval.get("top10") or [])
            if isinstance(item, dict)
        ]
        # ``source-evidence`` is an independent physical-proof packet.  It is
        # retained only as metadata; it is not treated as proof that the
        # packet matches the expected source for this case.
        proof_sources = [
            source_projection(item)
            for item in source_by_id.get(case_id, [])
            if isinstance(item, dict)
        ]
        queue.append(
            {
                "case_id": case_id,
                "question_sha256": answer.get("question_sha256"),
                "domain": answer.get("domain"),
                "intent": golden.get("procedure_family"),
                "expected_answer_mode": golden.get("expected_answer_mode"),
                "expected_source": expected,
                "retrieved_sources": retrieved,
                "independent_source_packet_metadata": proof_sources,
                "answer_status": {
                    "http_status": answer.get("http_status"),
                    "answer_present": answer.get("answer_present"),
                    "answer_mode": answer.get("answer_mode"),
                    "fallback_tier": answer.get("fallback_tier"),
                    "fallback_or_blocked": answer.get("fallback_or_blocked"),
                },
                "citation_status": {
                    "verified": answer.get("citation_coverage_verified"),
                    "citation_count": answer.get("citation_count"),
                    "verified_claim_count": answer.get("verified_claim_count"),
                    "claim_count": answer.get("claim_count"),
                    "quality_flags": answer.get("quality_flags") or [],
                },
                "grounding_status": answer.get("grounding_status"),
                "source_gap": bool(answer.get("source_gap")),
                "fallback_reason": answer.get("fallback_tier"),
                "failure_stage": failure_stage,
                "retrieval_classification": retrieval.get("classification"),
                "retrieval_reason_code": retrieval.get("reason_code"),
                "retrieval_expected_source_in_top10": any(
                    item.get("in_top10") is True
                    for item in retrieval.get("expected_sources") or []
                    if isinstance(item, dict)
                ),
                "root_cause_category": category,
                "root_cause": root_cause,
                "root_cause_confirmed_from_artifacts": confirmed,
                "root_cause_confidence": (
                    "contract_confirmed"
                    if category == "J"
                    else "failure_class_confirmed_exact_mechanism_pending_trace"
                    if category == "I"
                    else "unconfirmed"
                ),
                "remediation_action": (
                    "Exclude from citation numerator; preserve expected_refusal/source_view_only contract."
                    if category == "J" and "explicit_fallback" in root_cause
                    else "Trace structured eligibility/quality fallback and preserve evidence IDs through answer projection."
                    if category == "I"
                    else "Trace retrieval and source metadata before changing corpus or validators."
                ),
            }
        )

    summary = {
        "schema_version": "legal-candidate-answer-failure-queue-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_answer_report": answer_report.get("schema_version"),
        "case_count": len(failures),
        "root_cause_category_counts": dict(Counter(item["root_cause_category"] for item in queue)),
        "root_cause_counts": dict(Counter(item["root_cause"] for item in queue)),
        "failure_stage_counts": dict(Counter(item["failure_stage"] for item in queue)),
        "domain_counts": dict(Counter(item["domain"] for item in queue)),
        "expected_answer_mode_counts": dict(
            Counter(item["expected_answer_mode"] for item in queue)
        ),
        "retrieval_classification_counts": dict(
            Counter(item["retrieval_classification"] for item in queue)
        ),
        "confirmed_from_artifacts": sum(
            bool(item["root_cause_confirmed_from_artifacts"]) for item in queue
        ),
        "unconfirmed_requires_trace": sum(
            not bool(item["root_cause_confirmed_from_artifacts"]) for item in queue
        ),
        "privacy": {
            "question_bodies_recorded": False,
            "answer_bodies_recorded": False,
            "quotes_recorded": False,
            "credentials_recorded": False,
            "question_hash_algorithm": "sha256",
        },
    }
    return queue, summary


def write_csv(path: Path, queue: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "question_sha256",
        "domain",
        "intent",
        "expected_answer_mode",
        "answer_status",
        "citation_status",
        "grounding_status",
        "source_gap",
        "fallback_reason",
        "failure_stage",
        "retrieval_classification",
        "retrieval_reason_code",
        "retrieval_expected_source_in_top10",
        "root_cause_category",
        "root_cause",
        "root_cause_confirmed_from_artifacts",
        "remediation_action",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in queue:
            row = dict(item)
            for key in ("answer_status", "citation_status"):
                row[key] = json.dumps(row[key], ensure_ascii=False, separators=(",", ":"))
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer-report", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--retrieval-report", type=Path, required=True)
    parser.add_argument("--source-evidence", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()

    queue, summary = build_queue(
        load(args.answer_report.resolve()),
        load(args.golden.resolve()),
        load(args.retrieval_report.resolve()),
        load(args.source_evidence.resolve()),
    )
    if len(queue) != 165:
        raise SystemExit(f"expected_165_failures:{len(queue)}")
    for path in (args.json_output, args.csv_output, args.summary_output):
        path.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.json_output.resolve().write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.summary_output.resolve().write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(args.csv_output.resolve(), queue)
    print(json.dumps({"case_count": len(queue), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
