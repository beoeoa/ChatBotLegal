"""Privacy-safe DB-5 old-core/new-serving retrieval comparison."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from typing import Any

import chromadb
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CHROMA_PATH = Path(r"J:\legal-chatbot-data\chroma_store")
GOLDEN_PATH = ROOT / "notebook_data" / "legal-golden-set.json"
ROLE_MATRIX_PATH = (
    ROOT / "tests" / "fixtures" / "feature005_role_matrix.json"
)
DB2_DIR = ROOT / "reports" / "feature005" / "db2-shadow-20260723"
DB3_DIR = ROOT / "reports" / "feature005" / "db3-coverage-20260723"
OLD_CORE = "legal_chunks_vnlegal_lal_haiphong"
SOURCE = "legal_chunks_vnlegal_lal"
PRIMARY = "legal_chunks_lechan_primary_v20260723"
SUPPORT = "legal_chunks_lechan_support_v20260723"


def _configure_runtime() -> None:
    settings = dotenv_values(ROOT / ".env")
    release = str(settings.get("LEGAL_RELEASE_DATABASE_URL") or "").strip()
    if not release:
        raise RuntimeError("LEGAL_RELEASE_DATABASE_URL is required")
    os.environ["LEGAL_DATABASE_URL"] = release.replace(
        "@host.docker.internal:",
        "@127.0.0.1:",
    )
    os.environ["LEGAL_CHROMA_PATH"] = str(CHROMA_PATH)


_configure_runtime()

from api.legal_exact_retrieval import (  # noqa: E402
    normalize_exact_identifier,
    plan_exact_lookup,
)
from api.legal_retrieval_quality import diversify_ranked_candidates  # noqa: E402
from api.legal_section_grounding import (  # noqa: E402
    plan_legal_issues,
    retrieval_domain_slug,
)
from scripts.legal_search_server import (  # noqa: E402
    LegalRetriever,
    SearchRequest,
    _domain_matches,
    _is_current,
    _repair_mojibake_text,
    _rewrite_query,
)


DOMAIN_MAP = {
    "ho_tich": "ho_tich_chung_thuc",
    "ho_tich_chung_thuc": "ho_tich_chung_thuc",
    "cu_tru": "cu_tru_an_ninh",
    "cu_tru_an_ninh": "cu_tru_an_ninh",
    "dat_dai": "dat_dai_xay_dung",
    "dat_dai_xay_dung": "dat_dai_xay_dung",
    "trat_tu_do_thi": "trat_tu_do_thi",
    "xay_dung": "dat_dai_xay_dung",
    "khieu_nai": "noi_vu_hanh_chinh",
    "xu_phat": "noi_vu_hanh_chinh",
    "khieu_nai_to_cao_xu_phat": "noi_vu_hanh_chinh",
    "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
}

# DB-3 matched these rows by legal number, but Step 1 verified that the
# resolved document has a different legal subject.  Treating them as a
# retrieval failure would pressure ranking to surface an unrelated source.
SOURCE_IDENTITY_GAPS: dict[tuple[str, int], dict[str, Any]] = {
    ("ct_002", 104407): {
        "reason_code": "legal_number_resolves_to_unrelated_subject",
        "correct_source_match_count": 0,
    },
    ("golden_urban_003", 55930): {
        "reason_code": "law_number_collision_wrong_legal_subject",
        "correct_source_match_count": 0,
    },
    ("golden_urban_004", 113824): {
        "reason_code": "legal_number_resolves_to_unrelated_subject",
        "correct_source_match_count": 0,
    },
}


def _source_identity_gap(
    *,
    case_id: str,
    document_id: int,
) -> dict[str, Any] | None:
    gap = SOURCE_IDENTITY_GAPS.get((case_id, int(document_id)))
    return dict(gap) if gap else None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(percentile * len(ordered)) - 1),
    )
    return round(float(ordered[index]), 3)


def _ratio_gate(value: float, denominator: int, minimum: float) -> bool:
    """A gap-only slice has no retrievable expected source to score."""
    return denominator == 0 or value >= minimum


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(GOLDEN_PATH.read_text(encoding="utf-8-sig"))
    cases: list[dict[str, Any]] = []
    for row in payload.get("questions") or []:
        query = str(
            row.get("question_citizen")
            or row.get("question")
            or row.get("question_officer")
            or ""
        ).strip()
        if len(query) < 2:
            continue
        repaired_query = _repair_mojibake_text(query)
        selected_domain = DOMAIN_MAP.get(str(row.get("domain") or ""))
        planned = plan_legal_issues(repaired_query, max_issues=6)
        cases.append(
            {
                "case_id": str(row["id"]),
                "query": repaired_query,
                "domain_group": str(row.get("domain") or "unknown"),
                "domain": selected_domain,
                "issues": [
                    {
                        "issue_id": issue.issue_id,
                        "query": issue.query_text,
                        "intent": issue.intent,
                        "planner_domain": issue.domain,
                        "domain": retrieval_domain_slug(
                            issue.domain,
                            selected_domain,
                            issue.query_text,
                        ),
                    }
                    for issue in planned
                ],
            }
        )
    return cases


def _load_role_cases() -> list[dict[str, Any]]:
    """Load the nine role cases without inventing expected legal metadata."""

    payload = json.loads(
        ROLE_MATRIX_PATH.read_text(encoding="utf-8-sig")
    )
    cases: list[dict[str, Any]] = []
    for row in payload.get("cases") or []:
        query = _repair_mojibake_text(str(row.get("question") or "").strip())
        if len(query) < 2:
            continue
        planned = plan_legal_issues(query, max_issues=6)
        cases.append(
            {
                "case_id": str(row["id"]),
                "role": str(row.get("role") or ""),
                "query": query,
                "domain_group": "role_9",
                "domain": None,
                "issues": [
                    {
                        "issue_id": issue.issue_id,
                        "query": issue.query_text,
                        "intent": issue.intent,
                        "planner_domain": issue.domain,
                        "domain": retrieval_domain_slug(
                            issue.domain,
                            None,
                            issue.query_text,
                        ),
                    }
                    for issue in planned
                ],
            }
        )
    return cases


def _expected_sources(
    path: Path | None = None,
    *,
    dataset: str = "golden_167",
) -> dict[str, list[dict[str, Any]]]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    path = path or (DB3_DIR / "expected-sources.jsonl")
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("set") != dataset:
                continue
            if row.get("outcome") not in {
                "AVAILABLE_CORRECTLY_TIERED",
                "VERIFIED_DATA_GAP",
            }:
                continue
            if (
                row.get("outcome") == "AVAILABLE_CORRECTLY_TIERED"
                and row.get("document_id") is not None
                and not row.get("step2_verified")
            ):
                identity_gap = _source_identity_gap(
                    case_id=str(row["case_id"]),
                    document_id=int(row["document_id"]),
                )
                if identity_gap:
                    row = {
                        **row,
                        "outcome": "VERIFIED_DATA_GAP",
                        "expected_law_number": row.get("law_number"),
                        "document_id": None,
                        "corpus_match_count": identity_gap[
                            "correct_source_match_count"
                        ],
                        "reason_code": identity_gap["reason_code"],
                        "resolution": "correct_legal_source_not_in_corpus",
                    }
            by_case[str(row["case_id"])].append(row)
    return by_case


def _expected_units(
    expected_rows: list[dict[str, Any]],
) -> list[tuple[int, str | None]]:
    units: list[tuple[int, str | None]] = []
    for row in expected_rows:
        if row.get("outcome") != "AVAILABLE_CORRECTLY_TIERED":
            continue
        document_id = row.get("document_id")
        if document_id is None:
            continue
        unit = (
            int(document_id),
            str(row.get("provision")) if row.get("kind") == "provision" else None,
        )
        if unit not in units:
            units.append(unit)
    return units


def _expected_rows_for_issue(
    issue: Mapping[str, Any],
    expected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind approved expected sources to the issue that names their metadata."""

    rewritten = _rewrite_query(str(issue.get("query") or ""))
    plan = plan_exact_lookup(rewritten)
    if not plan.law_numbers and not plan.article_numbers:
        return []
    law_numbers = set(plan.law_numbers)
    articles = set(plan.article_numbers)
    matched: list[dict[str, Any]] = []
    for row in expected_rows:
        law = normalize_exact_identifier(
            row.get("law_number") or row.get("expected_law_number")
        )
        if law and law not in law_numbers:
            continue
        provision = str(row.get("provision") or "").strip().casefold()
        if provision and articles and provision not in articles:
            continue
        if law or provision:
            matched.append(row)
    return matched


def _article_matches(value: Any, expected: Any) -> bool:
    actual = str(value or "").strip().casefold()
    wanted = str(expected or "").strip().casefold()
    return actual in {wanted, f"điều {wanted}", f"dieu {wanted}"}


def _expected_source_observations(
    expected_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify approved expected sources without guessing missing contracts."""

    observations: list[dict[str, Any]] = []
    top10 = rows[:10]
    for expected in expected_rows:
        outcome = str(expected.get("outcome") or "")
        checks = expected.get("checks") or expected.get("document_checks") or {}
        if outcome == "VERIFIED_DATA_GAP":
            observations.append(
                {
                    "kind": expected.get("kind"),
                    "document_id": None,
                    "law_number": expected.get("expected_law_number"),
                    "provision": expected.get("provision"),
                    "classification": "VERIFIED_DATA_GAP",
                    "source_exists_in_corpus": False,
                    "corpus_match_count": int(
                        expected.get("corpus_match_count") or 0
                    ),
                    "rank": None,
                    "in_top_5": False,
                    "in_top_10": False,
                    "effectivity_verified": False,
                    "scope_verified": False,
                    "hierarchy_verified": False,
                    "tier": None,
                    "selection_reason": None,
                    "exclusion_reason": expected.get("reason_code")
                    or "verified_source_absent_from_corpus",
                    "reference_sha256": expected.get("reference_sha256"),
                }
            )
            continue
        if outcome != "AVAILABLE_CORRECTLY_TIERED":
            continue
        document_id = int(expected["document_id"])
        provision = (
            str(expected.get("provision"))
            if expected.get("kind") == "provision"
            else None
        )
        rank: int | None = None
        matched_row: dict[str, Any] | None = None
        for index, row in enumerate(top10, start=1):
            if int(row.get("document_id") or 0) != document_id:
                continue
            if provision is not None and not _article_matches(
                row.get("article_number"),
                provision,
            ):
                continue
            rank = index
            matched_row = row
            break
        retrieved = rank is not None
        observations.append(
            {
                "kind": expected.get("kind"),
                "document_id": document_id,
                "law_number": expected.get("law_number")
                or (matched_row or {}).get("law_number"),
                "provision": provision,
                "classification": (
                    "FOUND_AND_RETRIEVED"
                    if retrieved
                    else "FOUND_NOT_RETRIEVED"
                ),
                "source_exists_in_corpus": True,
                "rank": rank,
                "in_top_5": bool(rank is not None and rank <= 5),
                "in_top_10": bool(rank is not None and rank <= 10),
                "effectivity_verified": bool(checks.get("effectivity")),
                "scope_verified": bool(checks.get("scope")),
                "hierarchy_verified": bool(checks.get("hierarchy")),
                "tier": checks.get("tier"),
                "retrieved_domain": (matched_row or {}).get("domain_slug"),
                "retrieved_current": (
                    _is_current(matched_row, date(2026, 7, 23))
                    if matched_row
                    else None
                ),
                "selection_reason": (
                    "expected_source_ranked_top_10" if retrieved else None
                ),
                "exclusion_reason": (
                    None
                    if retrieved
                    else (
                        "source_exists_but_provision_not_ranked_top_10"
                        if provision is not None
                        else "source_exists_but_document_not_ranked_top_10"
                    )
                ),
                "reference_sha256": expected.get("reference_sha256"),
            }
        )
    return observations


def _classify_issue(
    observations: list[dict[str, Any]],
    *,
    has_live_results: bool,
) -> str:
    classifications = {
        str(observation.get("classification") or "")
        for observation in observations
    }
    if "FOUND_NOT_RETRIEVED" in classifications:
        return "FOUND_NOT_RETRIEVED"
    if "FOUND_AND_RETRIEVED" in classifications:
        return "FOUND_AND_RETRIEVED"
    if classifications == {"VERIFIED_DATA_GAP"}:
        return "VERIFIED_DATA_GAP"
    # The expanded safety set intentionally has no approved direct-source
    # contract. Do not claim a retrieval defect unless an approved expected
    # source is known to exist in the corpus.
    return (
        "FOUND_AND_RETRIEVED"
        if has_live_results
        else "EXPECTED_CONTRACT_GAP"
    )


def _collection_chunk_ids(collection: Any) -> set[int]:
    """Use the immutable shadow collection membership as the serving contract.

    DB-2 is the initial build ledger. Step 2 is allowed to append verified
    official documents, so a frozen DB-2 ledger would incorrectly hide those
    incremental vectors during the live gate.
    """

    result: set[int] = set()
    count = int(collection.count())
    for offset in range(0, count, 10_000):
        identifiers = (
            collection.get(limit=10_000, offset=offset, include=[]).get("ids") or []
        )
        for identifier in identifiers:
            match = re.fullmatch(r"chunk-(\d+)", str(identifier))
            if not match:
                raise RuntimeError(f"invalid shadow vector ID: {identifier}")
            result.add(int(match.group(1)))
    if len(result) != count:
        raise RuntimeError(
            f"shadow collection membership mismatch: count={count}, ids={len(result)}"
        )
    return result


def _allowed_ids(client: Any) -> dict[str, set[int]]:
    return {
        "core": _collection_chunk_ids(client.get_collection(PRIMARY)),
        "expanded": _collection_chunk_ids(client.get_collection(SUPPORT)),
    }


def _needs_support(
    query: str,
    rows: list[dict[str, Any]],
    expected_rows: list[dict[str, Any]],
) -> bool:
    if not rows:
        return True
    if any(
        observation["classification"] == "FOUND_NOT_RETRIEVED"
        for observation in _expected_source_observations(expected_rows, rows)
    ):
        return True
    plan = plan_exact_lookup(query)
    if plan.law_number and not any(
        str(row.get("law_number") or "").replace(" ", "").upper()
        == plan.law_number
        for row in rows
    ):
        return True
    return False


def _retrieve(
    *,
    retriever: LegalRetriever,
    issue: dict[str, Any],
    expected_rows: list[dict[str, Any]],
    allow_support: bool,
) -> tuple[list[dict[str, Any]], float, bool, dict[str, Any]]:
    started = perf_counter()
    try:
        primary = retriever.search(
            SearchRequest(
                query=issue["query"],
                limit=10,
                candidate_count=150,
                lexical_candidate_count=60,
                as_of=date(2026, 7, 23),
                domain=issue["domain"],
                retrieval_tier="core",
                include_trace=True,
                allow_broad_fallback=False,
                request_id=f"step1-{issue['case_id']}",
                issue_id=issue["issue_id"],
                issue_domain=issue["planner_domain"],
            )
        )
    except Exception as exc:
        raise RuntimeError(
            f"live primary retrieval failed for {issue['case_id']}/"
            f"{issue['issue_id']}: {type(exc).__name__}"
        ) from exc
    rows = [dict(row) for row in primary.get("results") or []]
    primary_trace = primary.get("trace") or {}
    filtered_counts = dict(
        (primary_trace.get("pipeline_counts") or {}).get(
            "filtered_by_reason"
        )
        or {}
    )
    diagnostics: dict[str, Any] = {
        "filtered_by_reason": filtered_counts,
        "primary_timing_ms": primary.get("timing_ms") or {},
        "primary_pipeline_counts": primary_trace.get("pipeline_counts") or {},
        "filtered_candidates": primary_trace.get("filtered_candidates") or [],
    }
    support_called = False
    if allow_support and _needs_support(issue["query"], rows, expected_rows):
        support_called = True
        try:
            support = retriever.search(
                SearchRequest(
                    query=issue["query"],
                    limit=10,
                    candidate_count=100,
                    lexical_candidate_count=40,
                    as_of=date(2026, 7, 23),
                    domain=issue["domain"],
                    retrieval_tier="expanded",
                    include_trace=True,
                    allow_broad_fallback=False,
                    request_id=f"step1-{issue['case_id']}",
                    issue_id=issue["issue_id"],
                    issue_domain=issue["planner_domain"],
                )
            )
        except Exception as exc:
            raise RuntimeError(
                f"live support retrieval failed for {issue['case_id']}/"
                f"{issue['issue_id']}: {type(exc).__name__}"
            ) from exc
        rows = diversify_ranked_candidates(
            rows + [dict(row) for row in support.get("results") or []],
            limit=10,
            max_per_article=2,
            max_per_document=3,
        )
        support_trace = support.get("trace") or {}
        support_filtered = dict(
            (support_trace.get("pipeline_counts") or {}).get(
                "filtered_by_reason"
            )
            or {}
        )
        for reason, count in support_filtered.items():
            filtered_counts[reason] = filtered_counts.get(reason, 0) + int(count)
        diagnostics["support_timing_ms"] = support.get("timing_ms") or {}
        diagnostics["support_pipeline_counts"] = (
            support_trace.get("pipeline_counts") or {}
        )
        diagnostics["filtered_candidates"] = (
            diagnostics["filtered_candidates"]
            + (support_trace.get("filtered_candidates") or [])
        )[:60]
    return (
        rows,
        round((perf_counter() - started) * 1000, 3),
        support_called,
        diagnostics,
    )


def _evaluate_run(
    *,
    name: str,
    retriever: LegalRetriever,
    cases: list[dict[str, Any]],
    expected: dict[str, list[tuple[int, str | None]]],
    allow_support: bool,
) -> dict[str, Any]:
    unit_total = 0
    unit_hits = 0
    document_total = 0
    document_top5_hits = 0
    wrong_field_or_expired = 0
    covered = 0
    timings: list[float] = []
    support_calls = 0
    samples: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        rows, elapsed, support_called = _retrieve(
            retriever=retriever,
            case=case,
            allow_support=allow_support,
        )
        timings.append(elapsed)
        support_calls += int(support_called)
        covered += int(bool(rows))
        top10 = [
            (
                int(row.get("document_id") or 0),
                str(row.get("article_number") or ""),
            )
            for row in rows[:10]
        ]
        top5_docs = {document_id for document_id, _ in top10[:5]}
        units = expected.get(case["case_id"], [])
        matched = 0
        for document_id, provision in units:
            unit_total += 1
            if provision is None:
                document_total += 1
                hit = any(item[0] == document_id for item in top10)
                if document_id in top5_docs:
                    document_top5_hits += 1
            else:
                hit = any(
                    item[0] == document_id
                    and item[1].strip().casefold()
                    in {provision.casefold(), f"điều {provision}".casefold()}
                    for item in top10
                )
            unit_hits += int(hit)
            matched += int(hit)
        for row in rows:
            if not _domain_matches(case["domain"], row.get("domain_slug")):
                wrong_field_or_expired += 1
            elif not _is_current(row, date(2026, 7, 23)):
                wrong_field_or_expired += 1
        samples.append(
            {
                "case_id": case["case_id"],
                "result_count": len(rows),
                "expected_units": len(units),
                "matched_units": matched,
                "top_document_ids": [item[0] for item in top10],
                "top_articles": [
                    [item[0], item[1]]
                    for item in top10
                ],
                "timing_ms": elapsed,
                "support_called": support_called,
            }
        )
        if index % 20 == 0:
            print(
                json.dumps(
                    {
                        "run": name,
                        "processed": index,
                        "total": len(cases),
                    }
                ),
                flush=True,
            )
    return {
        "name": name,
        "case_count": len(cases),
        "expected_unit_count": unit_total,
        "recall_at_10": round(unit_hits / unit_total, 5) if unit_total else 0.0,
        "direct_document_count": document_total,
        "direct_source_top5": (
            round(document_top5_hits / document_total, 5)
            if document_total
            else 0.0
        ),
        "wrong_field_or_expired": wrong_field_or_expired,
        "coverage": round(covered / len(cases), 5) if cases else 0.0,
        "p95_retrieval_ms": _percentile(timings, 0.95),
        "support_calls": support_calls,
        "samples": samples,
    }


def _evaluate_step1_run(
    *,
    name: str,
    retriever: LegalRetriever,
    cases: list[dict[str, Any]],
    expected: dict[str, list[dict[str, Any]]],
    allow_support: bool,
) -> dict[str, Any]:
    unit_results: dict[tuple[str, int, str | None], dict[str, bool]] = {}
    wrong_field_or_expired = 0
    covered = 0
    timings: list[float] = []
    support_calls = 0
    samples: list[dict[str, Any]] = []
    classification_counts: dict[str, int] = defaultdict(int)
    found_not_retrieved: list[dict[str, str]] = []
    issue_count = 0

    for index, case in enumerate(cases, start=1):
        expected_rows = expected.get(case["case_id"], [])
        case_issues: list[dict[str, Any]] = []
        for planned_issue in case["issues"]:
            issue_count += 1
            issue = {**planned_issue, "case_id": case["case_id"]}
            issue_expected_rows = _expected_rows_for_issue(
                issue,
                expected.get(case["case_id"], []),
            )
            rows, elapsed, support_called, retrieval_diagnostics = _retrieve(
                retriever=retriever,
                issue=issue,
                expected_rows=issue_expected_rows,
                allow_support=allow_support,
            )
            timings.append(elapsed)
            support_calls += int(support_called)
            covered += int(bool(rows))
            top10 = [
                (
                    int(row.get("document_id") or 0),
                    str(row.get("article_number") or ""),
                )
                for row in rows[:10]
            ]
            observations = _expected_source_observations(
                issue_expected_rows,
                rows,
            )
            classification = _classify_issue(
                observations,
                has_live_results=bool(rows),
            )
            classification_counts[classification] += 1
            if classification == "FOUND_NOT_RETRIEVED":
                found_not_retrieved.append(
                    {
                        "case_id": case["case_id"],
                        "issue_id": issue["issue_id"],
                        "domain_group": case["domain_group"],
                    }
                )

            top5_docs = {document_id for document_id, _ in top10[:5]}
            units = _expected_units(issue_expected_rows)
            matched = 0
            for document_id, provision in units:
                key = (case["case_id"], document_id, provision)
                record = unit_results.setdefault(
                    key,
                    {"hit": False, "top5": False, "document": provision is None},
                )
                if provision is None:
                    hit = any(item[0] == document_id for item in top10)
                    record["top5"] = record["top5"] or document_id in top5_docs
                else:
                    hit = any(
                        item[0] == document_id
                        and _article_matches(item[1], provision)
                        for item in top10
                    )
                record["hit"] = record["hit"] or hit
                matched += int(hit)

            selected_wrong = 0
            for row in rows:
                if not _domain_matches(issue["domain"], row.get("domain_slug")):
                    selected_wrong += 1
                elif not _is_current(row, date(2026, 7, 23)):
                    selected_wrong += 1
            wrong_field_or_expired += selected_wrong

            case_issues.append(
                {
                    "issue_id": issue["issue_id"],
                    "intent": issue["intent"],
                    "planner_domain": issue["planner_domain"],
                    "retrieval_domain": issue["domain"],
                    "classification": classification,
                    "classification_reason": (
                        "approved_expected_source_assessment"
                        if observations
                        else (
                            "eligible_live_results_no_approved_expected_source"
                            if rows
                            else "no_eligible_live_result"
                        )
                    ),
                    "result_count": len(rows),
                    "expected_source_count": len(observations),
                    "matched_expected_units": matched,
                    "expected_sources": observations,
                    "top_document_ids": [item[0] for item in top10],
                    "top_articles": [[item[0], item[1]] for item in top10],
                    "selected_source_domains": [
                        str(row.get("domain_slug") or "") for row in rows[:10]
                    ],
                    "all_selected_current": all(
                        _is_current(row, date(2026, 7, 23)) for row in rows
                    ),
                    "all_selected_in_domain": all(
                        _domain_matches(issue["domain"], row.get("domain_slug"))
                        for row in rows
                    ),
                    "retrieval_diagnostics": retrieval_diagnostics,
                    "timing_ms": elapsed,
                    "support_called": support_called,
                }
            )

        samples.append(
            {
                "case_id": case["case_id"],
                "domain_group": case["domain_group"],
                "issues": case_issues,
            }
        )
        if index % 20 == 0:
            print(
                json.dumps(
                    {"run": name, "processed": index, "total": len(cases)}
                ),
                flush=True,
            )

    unit_total = len(unit_results)
    unit_hits = sum(1 for item in unit_results.values() if item["hit"])
    document_units = [
        item for item in unit_results.values() if item["document"]
    ]
    document_total = len(document_units)
    document_top5_hits = sum(1 for item in document_units if item["top5"])
    return {
        "name": name,
        "case_count": len(cases),
        "issue_count": issue_count,
        "classified_issue_count": sum(classification_counts.values()),
        "classification_counts": dict(sorted(classification_counts.items())),
        "found_not_retrieved": found_not_retrieved,
        "expected_unit_count": unit_total,
        "recall_at_10": round(unit_hits / unit_total, 5) if unit_total else 0.0,
        "direct_document_count": document_total,
        "direct_source_top5": (
            round(document_top5_hits / document_total, 5)
            if document_total
            else 0.0
        ),
        "wrong_field_or_expired": wrong_field_or_expired,
        "coverage": round(covered / issue_count, 5) if issue_count else 0.0,
        "p95_retrieval_ms": _percentile(timings, 0.95),
        "support_calls": support_calls,
        "samples": samples,
    }


def _verified_data_gap_manifest(
    expected: dict[str, list[dict[str, Any]]],
    source_manifest: Path | None = None,
) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    for case_id, rows in sorted(expected.items()):
        for row in rows:
            if row.get("outcome") != "VERIFIED_DATA_GAP":
                continue
            gaps.append(
                {
                    "case_id": case_id,
                    "kind": row.get("kind"),
                    "expected_law_number": row.get("expected_law_number"),
                    "provision": row.get("provision"),
                    "classification": "VERIFIED_DATA_GAP",
                    "corpus_match_count": int(
                        row.get("corpus_match_count") or 0
                    ),
                    "reason_code": row.get("reason_code"),
                    "resolution": row.get("resolution"),
                    "reference_sha256": row.get("reference_sha256"),
                }
            )
    return {
        "schema_version": "feature005-step1-data-gaps-v1",
        "legal_as_of": "2026-07-23",
        "source_manifest": str(
            source_manifest or (DB3_DIR / "expected-sources.jsonl")
        ),
        "gap_count": len(gaps),
        "gaps": gaps,
    }


def main() -> int:
    import argparse

    global PRIMARY, SUPPORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gaps-output", type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--domain")
    parser.add_argument("--expected-sources", type=Path)
    parser.add_argument(
        "--db4-manifest",
        type=Path,
        default=(
            ROOT
            / "reports"
            / "feature005"
            / "db4-serving-20260723"
            / "manifest.json"
        ),
    )
    parser.add_argument("--primary", default=PRIMARY)
    parser.add_argument("--support", default=SUPPORT)
    args = parser.parse_args()
    PRIMARY = str(args.primary)
    SUPPORT = str(args.support)
    db4 = json.loads(args.db4_manifest.resolve().read_text(encoding="utf-8"))
    if db4.get("status") != "verified":
        raise RuntimeError("DB-4 must be verified")
    if db4.get("primary_collection") != PRIMARY:
        raise RuntimeError("DB-4 primary collection does not match requested collection")
    if db4.get("support_collection") != SUPPORT:
        raise RuntimeError("DB-4 support collection does not match requested collection")
    cases = _load_cases()
    role_cases = _load_role_cases()
    if args.domain:
        cases = [
            case for case in cases if case["domain_group"] == args.domain
        ]
    if args.limit:
        cases = cases[: args.limit]
    expected_manifest = (
        args.expected_sources.resolve()
        if args.expected_sources
        else DB3_DIR / "expected-sources.jsonl"
    )
    expected = _expected_sources(expected_manifest)
    role_expected = _expected_sources(
        expected_manifest,
        dataset="role_9",
    )
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    names = {collection.name for collection in client.list_collections()}
    required = {OLD_CORE, SOURCE, PRIMARY, SUPPORT}
    if not required <= names:
        raise RuntimeError(f"missing collections: {sorted(required - names)}")
    pointer_path = CHROMA_PATH / "active_core_collection.txt"
    pointer_before = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.exists()
        else None
    )

    retriever = LegalRetriever()
    retriever._collection = client.get_collection(OLD_CORE)
    retriever._source_collection = client.get_collection(SOURCE)
    # Warm and cache exact query embeddings once; per-search timing still
    # includes the real cache lookup and complete ANN/SQL/hydration/ranking.
    issue_queries = [
        issue["query"]
        for case in [*cases, *role_cases]
        for issue in case["issues"]
    ]
    retriever.encode_queries(issue_queries)
    print(
        json.dumps(
            {"query_embedding_warm": len(issue_queries), "total": len(cases)}
        ),
        flush=True,
    )

    retriever._shadow_allowed_chunk_ids = None
    old_run = _evaluate_step1_run(
        name="old_core",
        retriever=retriever,
        cases=cases,
        expected=expected,
        allow_support=False,
    )
    old_role_run = _evaluate_step1_run(
        name="old_core_role_9",
        retriever=retriever,
        cases=role_cases,
        expected=role_expected,
        allow_support=False,
    )

    retriever._collection = client.get_collection(PRIMARY)
    retriever._source_collection = client.get_collection(SUPPORT)
    retriever._shadow_allowed_chunk_ids = _allowed_ids(client)
    new_run = _evaluate_step1_run(
        name="new_serving",
        retriever=retriever,
        cases=cases,
        expected=expected,
        allow_support=True,
    )
    new_role_run = _evaluate_step1_run(
        name="new_serving_role_9",
        retriever=retriever,
        cases=role_cases,
        expected=role_expected,
        allow_support=True,
    )

    pointer_after = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.exists()
        else None
    )
    gates = {
        "recall_at_10": _ratio_gate(
            new_run["recall_at_10"],
            new_run["expected_unit_count"],
            0.95,
        ),
        "direct_source_top5": _ratio_gate(
            new_run["direct_source_top5"],
            new_run["direct_document_count"],
            0.95,
        ),
        "wrong_field_or_expired": new_run["wrong_field_or_expired"] == 0,
        "retrieval_p95": new_run["p95_retrieval_ms"] <= 3000,
        "coverage_non_decreasing": new_run["coverage"] >= old_run["coverage"],
        "active_pointer_unchanged": pointer_before == pointer_after,
        "all_issues_classified": (
            new_run["classified_issue_count"] == new_run["issue_count"]
        ),
        "no_unresolved_found_not_retrieved": (
            not new_run["found_not_retrieved"]
        ),
        "role_recall_at_10": _ratio_gate(
            new_role_run["recall_at_10"],
            new_role_run["expected_unit_count"],
            0.95,
        ),
        "role_direct_source_top5": _ratio_gate(
            new_role_run["direct_source_top5"],
            new_role_run["direct_document_count"],
            0.95,
        ),
        "role_wrong_field_or_expired": (
            new_role_run["wrong_field_or_expired"] == 0
        ),
        "role_retrieval_p95": (
            new_role_run["p95_retrieval_ms"] <= 3000
        ),
        "role_coverage_non_decreasing": (
            new_role_run["coverage"] >= old_role_run["coverage"]
        ),
        "role_all_issues_classified": (
            new_role_run["classified_issue_count"]
            == new_role_run["issue_count"]
        ),
        "role_no_unresolved_found_not_retrieved": (
            not new_role_run["found_not_retrieved"]
        ),
    }
    report = {
        "schema_version": "feature005-step1-retrieval-v1",
        "status": "PASS" if all(gates.values()) else "GATE_FAILED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": "2026-07-23",
        "configuration": {
            "primary": {"vector": 150, "lexical": 60, "rerank": 40},
            "support": {"vector": 100, "lexical": 40, "max_calls": 1},
            "diversity": {"per_article": 2, "per_document": 3},
            "retrieval_mode": "live_retrieval_only_no_fixture_fallback",
            "query_embedding_mode": "warm_cached_retrieval_encoder",
            "generation_model_calls": 0,
            "corpus_reembedded": False,
            "db4_manifest": str(args.db4_manifest.resolve()),
            "expected_sources_manifest": str(expected_manifest),
            "datasets": {
                "golden_167": len(cases),
                "role_9": len(role_cases),
            },
        },
        "old_core": old_run,
        "new_serving": new_run,
        "old_core_role_9": old_role_run,
        "new_serving_role_9": new_role_run,
        "gates": gates,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    gaps_output = args.gaps_output or (
        args.output.parent / "verified-data-gaps.json"
    )
    gaps_output.write_text(
        json.dumps(
            _verified_data_gap_manifest(expected, expected_manifest),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "old_core": {
                    key: value
                    for key, value in old_run.items()
                    if key != "samples"
                },
                "new_serving": {
                    key: value
                    for key, value in new_run.items()
                    if key != "samples"
                },
                "old_core_role_9": {
                    key: value
                    for key, value in old_role_run.items()
                    if key != "samples"
                },
                "new_serving_role_9": {
                    key: value
                    for key, value in new_role_run.items()
                    if key != "samples"
                },
                "gates": gates,
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
