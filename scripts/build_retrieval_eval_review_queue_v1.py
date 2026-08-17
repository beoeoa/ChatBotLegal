#!/usr/bin/env python3
"""Build a non-promotable legal-review queue from legacy evaluation inputs.

The repository contains a 1,000-case Golden input and a 100-case
Hard-negative input in older contracts.  This command preserves those inputs
and proposes read-only inventory matches for their law numbers.  It never
claims that a database URL is an official source, never infers effectivity or
jurisdiction, and never creates a retrieval-eval-suite-v1 dataset.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.reconcile_retrieval_source_gaps_v2 import normalize_law


DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "retrieval-eval-review-queue-v1.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(value) for value in values or [] if isinstance(value, dict)]


def _sources(case: dict[str, Any], *, positive: bool) -> list[dict[str, Any]]:
    if positive:
        values = case.get("expected_sources") or case.get("positive_sources") or []
    else:
        values = case.get("forbidden_sources") or case.get("hard_negatives") or []
    return [dict(value) for value in values if isinstance(value, dict)]


def _inventory_index(inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for row in inventory.get("documents") or []:
        law_key = normalize_law(row.get("law_number"))
        if not law_key:
            continue
        index.setdefault(law_key, []).append({
            "document_id": row.get("document_id"),
            "law_number": row.get("law_number"),
            "status_observed": row.get("status_observed"),
            "serving_state_observed": row.get("serving_state"),
            "source_url_observed": row.get("source_url"),
            "effective_date_observed": row.get("effective_date"),
            "expired_date_observed": row.get("expired_date"),
            "article_count_observed": row.get("article_count"),
            "chunk_count_observed": row.get("chunk_count"),
            "legal_review_required": True,
        })
    return index


def propose_source(source: dict[str, Any], inventory_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Return a transparent source mapping proposal, never an approval."""

    law_number = str(source.get("law_number") or "").strip()
    matches = list(inventory_index.get(normalize_law(law_number), []))
    if not matches:
        status = "unmatched_in_inventory"
    elif len(matches) == 1:
        status = "matched_unique_observation"
    else:
        status = "matched_multiple_observations"
    return {
        "requested": {
            "law_number": law_number or None,
            "article": source.get("article"),
            "paragraph": source.get("paragraph") or source.get("clause"),
            "point": source.get("point"),
        },
        "mapping_status": status,
        "inventory_observations": matches,
        "official_url": None,
        "jurisdiction": None,
        "validity_from": None,
        "validity_to": None,
        "review_reasons": [
            "official_source_and_checksum_required",
            "jurisdiction_requires_legal_review",
            "validity_interval_requires_legal_review",
            *(["inventory_match_is_ambiguous"] if len(matches) > 1 else []),
            *(["law_number_not_found_in_inventory"] if not matches else []),
        ],
    }


def _case_question(case: dict[str, Any]) -> str | None:
    question = case.get("question")
    if question:
        return str(question)
    questions = case.get("questions") or {}
    value = questions.get("citizen") or questions.get("officer")
    return str(value) if value else None


def _review_reasons(case: dict[str, Any], positive: list[dict[str, Any]], negative: list[dict[str, Any]], proposals: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if not _case_question(case):
        reasons.append("question_missing")
    if not positive:
        reasons.append("positive_source_group_missing")
    if not case.get("legal_as_of"):
        reasons.append("legal_as_of_missing")
    if not case.get("reviewer_approval"):
        reasons.append("retrieval_suite_reviewer_approval_missing")
    if any(item["mapping_status"] == "unmatched_in_inventory" for item in proposals):
        reasons.append("source_not_found_in_inventory")
    if any(item["mapping_status"] == "matched_multiple_observations" for item in proposals):
        reasons.append("source_match_requires_manual_disambiguation")
    if any(item["mapping_status"] != "matched_unique_observation" for item in proposals):
        reasons.append("source_mapping_not_unique")
    if positive and not negative and case.get("split") == "hard-negative":
        reasons.append("hard_negative_source_missing")
    return sorted(set(reasons))


def _build_case(case: dict[str, Any], *, split: str, inventory_index: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    positive = _sources(case, positive=True)
    negative = _sources(case, positive=False)
    proposals = [propose_source(source, inventory_index) for source in [*positive, *negative]]
    positive_proposals = proposals[: len(positive)]
    negative_proposals = proposals[len(positive):]
    proposed_refusal = not bool(positive)
    return {
        "case_id": case.get("case_id"),
        "split_observed": split,
        "domain_observed": case.get("domain"),
        "question_observed": _case_question(case),
        "legal_as_of_observed": case.get("legal_as_of"),
        "legacy_review_status": case.get("review_status"),
        "legacy_expected_answer_mode": case.get("expected_answer_mode"),
        "proposed_answer_required": not proposed_refusal,
        "proposed_expected_refusal": proposed_refusal,
        "proposed_refusal_category": "insufficient_facts" if proposed_refusal else "none",
        "positive_source_proposals": positive_proposals,
        "hard_negative_source_proposals": negative_proposals,
        "legacy_tags": sorted(set(str(value) for value in (case.get("risk_tags") or []))),
        "review_reasons": _review_reasons(case, positive, negative, proposals),
        "promotable_to_retrieval_eval_suite_v1": False,
    }


def build(*, golden: Path, hard_negative: Path, inventory: Path, output: Path) -> dict[str, Any]:
    golden_payload = _load(golden)
    hard_payload = _load(hard_negative)
    inventory_payload = _load(inventory)
    index = _inventory_index(inventory_payload)
    golden_cases = [_build_case(case, split="golden-regression", inventory_index=index) for case in _cases(golden_payload)]
    hard_cases = [_build_case(case, split="hard-negative", inventory_index=index) for case in _cases(hard_payload)]
    cases = [*golden_cases, *hard_cases]
    source_proposals = [
        proposal
        for case in cases
        for proposal in [*(case["positive_source_proposals"] or []), *(case["hard_negative_source_proposals"] or [])]
    ]
    mapping_counts = Counter(str(item.get("mapping_status")) for item in source_proposals)
    unresolved_cases = sum(bool(case.get("review_reasons")) for case in cases)
    payload: dict[str, Any] = {
        "schema_version": "retrieval-eval-review-queue-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "LEGAL_REVIEW_REQUIRED",
        "policy": {
            "canonical_suite_created": False,
            "do_not_fabricate_legal_fields": True,
            "inventory_matches_are_observations_only": True,
            "holdout_included": False,
        },
        "input_checksums": {
            "golden": file_sha256(golden),
            "hard_negative": file_sha256(hard_negative),
            "inventory": file_sha256(inventory),
        },
        "observed_counts": {
            "golden_cases": len(golden_cases),
            "hard_negative_cases": len(hard_cases),
            "total_cases": len(cases),
            "source_proposals": len(source_proposals),
            "cases_requiring_review": unresolved_cases,
            "mapping_status": dict(sorted(mapping_counts.items())),
        },
        "cases": cases,
        "mutation": {
            "database_mutated": False,
            "vector_collections_mutated": False,
            "datasets_mutated": False,
            "active_pointer_changed": False,
        },
    }
    payload["queue_sha256"] = canonical_sha256(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    payload = build(
        golden=args.golden.resolve(),
        hard_negative=args.hard_negative.resolve(),
        inventory=args.inventory.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps({
        "status": payload["status"],
        "observed_counts": payload["observed_counts"],
        "queue_sha256": payload["queue_sha256"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
