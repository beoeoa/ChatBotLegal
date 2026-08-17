"""Versioned, dataset-aware retrieval quality metrics for legal RAG.

The module is pure and intentionally does not open PostgreSQL, Chroma or a
model.  Benchmark runners provide immutable cases and observed rows; this
module owns denominator policy, domain slicing, multi-source coverage and
source-gap discovery.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping


METRIC_CONTRACT_VERSION = "legal-retrieval-metrics-v2"
SOURCE_GAP_SCHEMA_VERSION = "legal-retrieval-source-gap-v1"

_DOMAIN_ALIASES = {
    "ho_tich_chung_thuc": "ho_tich_chung_thuc",
    "tu_phap_ho_tich": "ho_tich_chung_thuc",
    "ho tich/chung thuc": "ho_tich_chung_thuc",
    "dat_dai_xay_dung": "dat_dai_xay_dung",
    "dat dai/xay dung": "dat_dai_xay_dung",
    "dat dai/xay dung/moi truong": "dat_dai_xay_dung",
    "cu_tru": "cu_tru_an_ninh",
    "cu_tru_an_ninh": "cu_tru_an_ninh",
    "cu tru/an ninh": "cu_tru_an_ninh",
    "cu tru/can cuoc/an ninh": "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat": "khieu_nai_to_cao_xu_phat",
    "khieu nai/to cao/xu phat": "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
    "an sinh/y te/giao duc": "an_sinh_y_te_giao_duc",
}
_ISSUE_REASON_RE = re.compile(r"\bvan de\s+(\d+)\b")
_EXPLICIT_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)"),
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "")).casefold()
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", text.replace("đ", "d")).strip()


def normalize_domain(value: Any) -> str:
    folded = _fold(value).replace(" ", "_")
    direct = _DOMAIN_ALIASES.get(folded)
    if direct:
        return direct
    return _DOMAIN_ALIASES.get(_fold(value), folded or "unknown")


def normalize_law_number(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _fold(value))


def normalize_article(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _fold(value))


def _article_matches(actual: Any, expected: Any) -> bool:
    actual_value = normalize_article(actual)
    alternatives = {
        normalize_article(part)
        for part in re.split(r"[,;/|]+", str(expected or ""))
        if normalize_article(part)
    }
    # A law-level Golden reference intentionally accepts any article from the
    # matched law.  Treating a missing expected article as the empty string
    # made every such reference impossible to hit.
    if not alternatives:
        return not normalize_article(expected)
    return actual_value in alternatives


def source_key(source: Mapping[str, Any]) -> str:
    return f"{normalize_law_number(source.get('law_number'))}:{normalize_article(source.get('article'))}"


def expected_sources_for_case(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = case.get("expected_sources")
    if values is None:
        values = case.get("positive_sources")
    return [dict(item) for item in (values or []) if isinstance(item, Mapping)]


def question_for_case(case: Mapping[str, Any]) -> str:
    questions = case.get("questions")
    if isinstance(questions, Mapping):
        return str(questions.get("citizen") or questions.get("officer") or "").strip()
    return str(case.get("question") or "").strip()


def explicit_query_dates(query: Any) -> list[str]:
    """Return every valid explicit calendar date in stable ISO form."""

    observed: set[date] = set()
    text = str(query or "")
    for index, pattern in enumerate(_EXPLICIT_DATE_PATTERNS):
        for match in pattern.finditer(text):
            values = tuple(int(part) for part in match.groups())
            year, month, day = values if index == 0 else (values[2], values[1], values[0])
            try:
                observed.add(date(year, month, day))
            except ValueError:
                continue
    return [value.isoformat() for value in sorted(observed)]


def validate_case_temporal_alignment(case: Mapping[str, Any]) -> dict[str, Any] | None:
    """Audit the benchmark contract between query dates and ``legal_as_of``.

    A mismatch is a dataset error, not a retrieval miss.  The benchmark must
    not silently change either the query or the approved case clock.
    """

    legal_as_of = str(case.get("legal_as_of") or "").strip()
    dates = explicit_query_dates(question_for_case(case))
    mismatched = [value for value in dates if value != legal_as_of]
    if not mismatched:
        return None
    return {
        "case_id": str(case.get("case_id") or ""),
        "code": "DATASET_LEGAL_AS_OF_QUERY_DATE_MISMATCH",
        "legal_as_of": legal_as_of or None,
        "query_dates": dates,
        "mismatched_query_dates": mismatched,
    }


def expected_source_groups(case: Mapping[str, Any]) -> list[list[dict[str, Any]]]:
    """Group expected sources by explicit ``Vấn đề N`` provenance.

    Datasets without issue provenance remain one issue. This avoids inventing
    a source-to-issue mapping from query text.
    """

    sources = expected_sources_for_case(case)
    if not sources:
        return []
    numbered: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unnumbered: list[dict[str, Any]] = []
    for source in sources:
        match = _ISSUE_REASON_RE.search(_fold(source.get("reason")))
        if match:
            numbered[int(match.group(1))].append(source)
        else:
            unnumbered.append(source)
    if not numbered:
        return [sources]
    groups = [numbered[key] for key in sorted(numbered)]
    if unnumbered:
        groups.append(unnumbered)
    return groups


def match_expected_sources(
    results: Iterable[Mapping[str, Any]],
    case: Mapping[str, Any],
    *,
    top_k: int = 10,
) -> dict[str, Any]:
    bounded = [dict(item) for item in results][:top_k]
    sources = expected_sources_for_case(case)
    matched: set[str] = set()
    first_rank: int | None = None
    for rank, result in enumerate(bounded, start=1):
        for source in sources:
            if normalize_law_number(result.get("law_number")) != normalize_law_number(
                source.get("law_number")
            ):
                continue
            if not _article_matches(result.get("article_number"), source.get("article")):
                continue
            matched.add(source_key(source))
            if first_rank is None:
                first_rank = rank
    groups = expected_source_groups(case)
    issue_hits = [
        any(source_key(source) in matched for source in group) for group in groups
    ]
    expected_keys = {source_key(source) for source in sources}
    coverage = len(matched) / len(expected_keys) if expected_keys else 0.0
    return {
        "hit_at_10": first_rank is not None,
        "hit_rank": first_rank,
        "matched_expected_source_keys": sorted(matched),
        "expected_source_coverage": round(coverage, 6),
        "issue_hits": issue_hits,
    }


def match_expected_source_group_hits(
    case: Mapping[str, Any],
    result_packets: Iterable[Iterable[Mapping[str, Any]]],
    final_results: Iterable[Mapping[str, Any]],
) -> list[bool]:
    """Measure each approved source group across all bounded issue packets.

    The deterministic issue planner may return fewer textual issues than the
    Golden provenance groups. A packet that contains both approved groups must
    count both; positional packet-to-group pairing would create false misses.
    """

    packets = [[dict(item) for item in packet] for packet in result_packets]
    final = [dict(item) for item in final_results]
    return [
        any(
            bool(
                match_expected_sources(
                    packet,
                    {"expected_sources": group},
                    top_k=10,
                )["hit_at_10"]
            )
            for packet in packets
        )
        or bool(
            match_expected_sources(
                final,
                {"expected_sources": group},
                top_k=10,
            )["hit_at_10"]
        )
        for group in expected_source_groups(case)
    ]


def _ratio(numerator: int | float, denominator: int) -> float | None:
    return round(float(numerator) / denominator, 6) if denominator else None


def _slice_metrics(items: list[tuple[Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
    answer_rows = [(case, row) for case, row in items if not bool(case.get("expected_refusal"))]
    refusal_rows = [(case, row) for case, row in items if bool(case.get("expected_refusal"))]
    hits = sum(bool(row.get("hit_at_10")) for _, row in answer_rows)
    reciprocal_rank = sum(
        1.0 / int(row["hit_rank"])
        for _, row in answer_rows
        if row.get("hit_at_10") and int(row.get("hit_rank") or 0) in range(1, 11)
    )
    correct_refusals = sum(bool(row.get("correct_refusal")) for _, row in refusal_rows)
    return {
        "case_count": len(items),
        "answer_required_count": len(answer_rows),
        "expected_refusal_count": len(refusal_rows),
        "recall_at_10": _ratio(hits, len(answer_rows)),
        "mrr_at_10": _ratio(reciprocal_rank, len(answer_rows)),
        "correct_refusal_rate": _ratio(correct_refusals, len(refusal_rows)),
    }


def build_retrieval_metrics(
    cases: Iterable[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    case_list = [dict(case) for case in cases]
    row_by_id = {str(row.get("case_id")): dict(row) for row in rows}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    misses: list[dict[str, Any]] = []
    false_blocked = 0
    issue_count = 0
    issue_hits = 0
    all_source_cases = 0
    all_source_complete = 0
    dataset_errors: list[dict[str, Any]] = []

    for case in case_list:
        case_id = str(case.get("case_id") or "")
        row = row_by_id.get(case_id, {"case_id": case_id, "miss_reason": "evaluation_mismatch"})
        dataset_error = row.get("dataset_error") or validate_case_temporal_alignment(case)
        if dataset_error:
            dataset_errors.append(dict(dataset_error))
            continue
        pairs.append((case, row))
        if bool(case.get("expected_refusal")):
            continue
        error_code = str(row.get("error_code") or "")
        if row.get("response_status") == "clarification_required" and error_code:
            false_blocked += 1
        groups = expected_source_groups(case)
        observed_issue_hits = list(row.get("issue_hits") or [])
        if groups:
            issue_count += len(groups)
            issue_hits += sum(bool(value) for value in observed_issue_hits[: len(groups)])
            all_source_cases += 1
            if float(row.get("expected_source_coverage") or 0.0) >= 1.0:
                all_source_complete += 1
        if not bool(row.get("hit_at_10")):
            reason = str(row.get("miss_reason") or "")
            if not reason:
                if error_code == "TEMPORAL_AS_OF_CONFLICT":
                    reason = "temporal_false_block"
                elif row.get("source_available") is False:
                    reason = "source_absent"
                elif int(row.get("result_count") or 0) == 0:
                    reason = "candidate_miss"
                else:
                    reason = "fusion_rank_loss"
            misses.append(
                {
                    "case_id": case_id,
                    "domain": normalize_domain(case.get("domain")),
                    "reason": reason,
                    "error_code": error_code or None,
                }
            )

    overall = _slice_metrics(pairs)
    per_domain: dict[str, Any] = {}
    per_intent: dict[str, Any] = {}
    per_temporal_scope: dict[str, Any] = {}
    for label, selector, target in (
        ("domain", lambda case, row: normalize_domain(case.get("domain") or row.get("domain")), per_domain),
        ("intent", lambda case, row: str(row.get("intent") or "unknown"), per_intent),
        ("temporal_scope", lambda case, row: str(row.get("temporal_scope") or "unknown"), per_temporal_scope),
    ):
        del label
        buckets: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
        for case, row in pairs:
            buckets[selector(case, row)].append((case, row))
        target.update({key: _slice_metrics(value) for key, value in sorted(buckets.items())})

    return {
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "dataset_case_count": len(case_list),
        "valid_case_count": len(pairs),
        "dataset_error_count": len(dataset_errors),
        "dataset_errors": dataset_errors,
        **overall,
        "mrr": overall["mrr_at_10"],
        "false_blocked_answer_count": false_blocked,
        "issue_count": issue_count,
        "issue_recall_at_10": _ratio(issue_hits, issue_count),
        "all_required_sources_coverage": _ratio(all_source_complete, all_source_cases),
        "per_domain": per_domain,
        "per_intent": per_intent,
        "per_temporal_scope": per_temporal_scope,
        "miss_count": len(misses),
        "misses": misses,
    }


def _dataset_cases(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in (values or []) if isinstance(item, Mapping)]


def build_source_gap_manifest(
    candidate_manifest: Mapping[str, Any],
    datasets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    available_laws = {
        normalize_law_number(document.get("law_number"))
        for document in (candidate_manifest.get("documents") or [])
        if normalize_law_number(document.get("law_number"))
    }
    missing: list[dict[str, Any]] = []
    expected_reference_count = 0
    for dataset_name, payload in sorted(datasets.items()):
        for case in _dataset_cases(payload):
            if bool(case.get("expected_refusal")):
                continue
            for source in expected_sources_for_case(case):
                expected_reference_count += 1
                law_number = str(source.get("law_number") or "").strip()
                if normalize_law_number(law_number) in available_laws:
                    continue
                missing.append(
                    {
                        "dataset": dataset_name,
                        "case_id": str(case.get("case_id") or ""),
                        "domain": normalize_domain(case.get("domain")),
                        "legal_as_of": case.get("legal_as_of"),
                        "law_number": law_number,
                        "article": source.get("article"),
                        "official_source_url": source.get("source_url"),
                        "status": None,
                        "effective_from": None,
                        "effective_to": None,
                        "authority": None,
                        "jurisdiction": None,
                        "relationship": None,
                        "reason_missing": "law_not_in_candidate_manifest",
                        "required_action": "legal_review_required",
                    }
                )
    missing.sort(key=lambda row: (row["dataset"], row["case_id"], normalize_law_number(row["law_number"]), normalize_article(row["article"])))
    payload: dict[str, Any] = {
        "schema_version": SOURCE_GAP_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_collection": candidate_manifest.get("candidate_collection")
        or candidate_manifest.get("collection_name"),
        "candidate_manifest_sha256": candidate_manifest.get("manifest_sha256"),
        "expected_reference_count": expected_reference_count,
        "missing_reference_count": len(missing),
        "missing_law_count": len({normalize_law_number(row["law_number"]) for row in missing}),
        "missing_references": missing,
        "candidate_collection_mutated": False,
        "active_pointer_changed": False,
        "approval_status": "legal_review_required" if missing else "source_coverage_complete",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def summarize_stage_diagnostics(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    values = [dict(row) for row in rows]
    stage_fields = (
        "source_available",
        "article_chunk_available",
        "vector_hit_at_20",
        "vector_hit_at_50",
        "lexical_hit_at_20",
        "lexical_hit_at_50",
        "candidate_hit_at_50",
        "fusion_hit_at_20",
        "fusion_hit_at_50",
        "reranked_hit_at_10",
        "expanded_hit_at_10",
        "final_hit_at_10",
    )
    stage_recall = {
        field.removesuffix("_hit_at_10").removesuffix("_hit_at_20").removesuffix("_hit_at_50")
        + (
            "_recall_at_10"
            if field.endswith("_hit_at_10")
            else "_recall_at_20"
            if field.endswith("_hit_at_20")
            else "_recall_at_50"
            if field.endswith("_hit_at_50")
            else "_rate"
        ): _ratio(sum(bool(row.get(field)) for row in values), len(values))
        for field in stage_fields
    }
    exact_rows = [row for row in values if bool(row.get("exact_expected"))]
    stage_recall["exact_expected_count"] = len(exact_rows)
    stage_recall["exact_recall_at_50"] = _ratio(
        sum(bool(row.get("exact_hit_at_50")) for row in exact_rows),
        len(exact_rows),
    )
    root_causes: dict[str, int] = defaultdict(int)
    diagnosed_rows: list[dict[str, Any]] = []
    for row in values:
        if bool(row.get("final_hit_at_10")):
            cause = "hit"
        elif row.get("temporal_blocked"):
            cause = "temporal_false_block"
        elif row.get("source_available") is False:
            cause = "source_absent"
        elif row.get("article_chunk_available") is False:
            cause = "article_chunk_absent"
        elif row.get("filter_rejected"):
            cause = "filter_rejected"
        elif row.get("exact_expected") and not row.get("exact_hit_at_50"):
            cause = "exact_lookup_failure"
        elif not row.get("candidate_hit_at_50"):
            cause = "candidate_miss"
        elif not row.get("fusion_hit_at_50"):
            cause = "fusion_rank_loss"
        elif row.get("reranker_enabled") and not row.get("reranked_hit_at_10"):
            cause = "reranker_rank_loss"
        elif (
            row.get("expansion_enabled")
            and row.get("reranked_hit_at_10")
            and not row.get("expanded_hit_at_10")
        ):
            cause = "expansion_loss"
        else:
            cause = "fusion_rank_loss"
        if cause != "hit":
            root_causes[cause] += 1
        diagnosed_rows.append({**row, "primary_root_cause": cause})
    per_domain: dict[str, Any] = {}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in diagnosed_rows:
        buckets[normalize_domain(row.get("domain"))].append(row)
    for domain, domain_rows in sorted(buckets.items()):
        per_domain[domain] = {
            "case_count": len(domain_rows),
            "candidate_recall_at_50": _ratio(
                sum(bool(row.get("candidate_hit_at_50")) for row in domain_rows),
                len(domain_rows),
            ),
            "final_recall_at_10": _ratio(
                sum(bool(row.get("final_hit_at_10")) for row in domain_rows),
                len(domain_rows),
            ),
        }
    return {
        "schema_version": "legal-retrieval-stage-diagnostics-v1",
        "case_count": len(values),
        **stage_recall,
        "root_causes": dict(sorted(root_causes.items())),
        "per_domain": per_domain,
        "cases": diagnosed_rows,
    }


def evaluate_candidate_stage_gate(
    summary: Mapping[str, Any],
    source_gap_manifest: Mapping[str, Any] | None = None,
    source_article_integrity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    per_domain = summary.get("per_domain") or {}
    checks = {
        "candidate_recall_at_50": float(summary.get("candidate_recall_at_50") or 0.0) >= 0.99,
        "per_domain_candidate_recall_at_50": bool(per_domain)
        and all(
            float(metrics.get("candidate_recall_at_50") or 0.0) >= 0.98
            for metrics in per_domain.values()
        ),
        "outside_manifest_zero": int(summary.get("outside_manifest_result_count") or 0) == 0,
        "invalid_evidence_zero": int(summary.get("invalid_evidence_result_count") or 0) == 0,
        "active_pointer_unchanged": not bool(summary.get("active_pointer_changed")),
    }
    if source_gap_manifest is not None:
        checks["approved_source_gap_zero"] = (
            int(source_gap_manifest.get("missing_reference_count") or 0) == 0
            and not bool(source_gap_manifest.get("candidate_collection_mutated"))
            and not bool(source_gap_manifest.get("active_pointer_changed"))
        )
    if source_article_integrity is not None:
        checks["approved_source_article_gap_zero"] = (
            int(source_article_integrity.get("law_absent_reference_count") or 0)
            == 0
            and int(
                source_article_integrity.get("article_absent_reference_count")
                or 0
            )
            == 0
            and not bool(source_article_integrity.get("database_mutated"))
            and not bool(
                source_article_integrity.get("candidate_collection_mutated")
            )
            and not bool(source_article_integrity.get("active_pointer_changed"))
        )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_gates": [name for name, passed in checks.items() if not passed],
    }


__all__ = [
    "METRIC_CONTRACT_VERSION",
    "SOURCE_GAP_SCHEMA_VERSION",
    "build_retrieval_metrics",
    "build_source_gap_manifest",
    "expected_source_groups",
    "expected_sources_for_case",
    "match_expected_sources",
    "match_expected_source_group_hits",
    "evaluate_candidate_stage_gate",
    "explicit_query_dates",
    "normalize_domain",
    "normalize_law_number",
    "question_for_case",
    "summarize_stage_diagnostics",
    "validate_case_temporal_alignment",
]
