"""Standalone helpers shared by the private Kaggle M5/M6 workers.

The module intentionally imports only Python's standard library.  It is copied
into private Kaggle kernel bundles together with the stage-specific worker, so
the benchmark cannot depend on mutable application code or a live database.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


DOMAINS = (
    "Hộ tịch/chứng thực",
    "Đất đai/xây dựng/môi trường",
    "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục",
)
DEVELOPMENT_SPLITS = ("golden-regression", "hard-negative")
LAW_RE = re.compile(
    r"\b\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\b",
    re.IGNORECASE,
)
ARTICLE_RE = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    digest = file_sha256(path)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="utf-8"
    )


def normalize_exact(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = text.replace("Đ", "D").replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"[^A-Z0-9]+", " ", text.upper())
    return " ".join(text.split())


def safe_fts_query(query: str) -> str:
    stop = {
        "cua", "cho", "theo", "toi", "can", "ve", "va", "la", "co", "duoc",
        "nhung", "nay", "trong", "mot", "cac", "xin", "hay", "neu", "thi",
        "quy", "dinh", "van", "ban", "dieu", "phan", "noi", "dung",
    }
    tokens = re.findall(r"[\wÀ-ỹĐđ]+", str(query or ""), flags=re.UNICODE)
    selected: list[str] = []
    for token in tokens:
        normalized = normalize_exact(token).casefold()
        if len(token) <= 1 or normalized in stop:
            continue
        if token.casefold() not in {item.casefold() for item in selected}:
            selected.append(token)
        if len(selected) >= 24:
            break
    return " OR ".join('"' + token.replace('"', " ") + '"' for token in selected)


def source_matches(candidate: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    if normalize_exact(candidate.get("law_number")) != normalize_exact(source.get("law_number")):
        return False
    article = normalize_exact(source.get("article"))
    if not article:
        return True
    requested = set(re.findall(r"[0-9]+[A-Z]?", article))
    candidate_numbers = set(
        re.findall(r"[0-9]+[A-Z]?", normalize_exact(candidate.get("article_number")))
    )
    if requested & candidate_numbers:
        return True
    path = normalize_exact(candidate.get("structural_path"))
    return any(f"DIEU {number}" in path for number in requested)


def matches_any(
    candidates: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
) -> bool:
    return any(
        source_matches(candidate, source)
        for candidate in candidates
        for source in sources
    )


def case_sources(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        source
        for group in case.get("positive_source_groups") or []
        for source in group.get("sources") or []
    ]


def merge_candidates(*branches: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for branch_name, branch in branches:
        for rank, raw in enumerate(branch, start=1):
            item = dict(raw)
            identifier = str(item.get("chunk_revision_id") or "")
            if not identifier:
                continue
            current = merged.setdefault(identifier, item)
            current.setdefault("retrieval_sources", [])
            if branch_name not in current["retrieval_sources"]:
                current["retrieval_sources"].append(branch_name)
            current.setdefault("branch_ranks", {})[branch_name] = rank
            current.setdefault("branch_scores", {})[branch_name] = float(
                item.get("score") or 0.0
            )
    return merged


def fuse(
    exact: Sequence[Mapping[str, Any]],
    vector: Sequence[Mapping[str, Any]],
    lexical: Sequence[Mapping[str, Any]],
    *,
    strategy: str,
    vector_weight: float = 0.6,
    lexical_weight: float = 0.4,
) -> list[dict[str, Any]]:
    merged = merge_candidates(("exact", exact), ("vector", vector), ("lexical", lexical))
    if strategy == "legacy_stack":
        order: list[str] = []
        for branch in (exact, vector, lexical):
            for item in branch:
                identifier = str(item.get("chunk_revision_id") or "")
                if identifier and identifier not in order:
                    order.append(identifier)
        for rank, identifier in enumerate(order, start=1):
            merged[identifier]["score"] = 1.0 / rank
    elif strategy == "rrf":
        for item in merged.values():
            ranks = item.get("branch_ranks") or {}
            item["score"] = sum(1.0 / (60.0 + int(rank)) for rank in ranks.values())
            if "exact" in ranks:
                item["score"] += 1.0
    elif strategy == "weighted":
        vector_scores = [float(item.get("score") or 0.0) for item in vector]
        low = min(vector_scores) if vector_scores else 0.0
        high = max(vector_scores) if vector_scores else 1.0
        scale = max(high - low, 1e-9)
        for item in merged.values():
            ranks = item.get("branch_ranks") or {}
            scores = item.get("branch_scores") or {}
            vector_score = (float(scores.get("vector") or low) - low) / scale if "vector" in ranks else 0.0
            lexical_score = 1.0 / float(ranks["lexical"]) if "lexical" in ranks else 0.0
            exact_bonus = 2.0 if "exact" in ranks else 0.0
            item["score"] = exact_bonus + vector_weight * vector_score + lexical_weight * lexical_score
    else:
        raise ValueError(f"unknown_fusion_strategy:{strategy}")
    return sorted(
        merged.values(),
        key=lambda item: (-float(item.get("score") or 0.0), str(item["chunk_revision_id"])),
    )


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def evaluate_rows(records: list[dict[str, Any]]) -> dict[str, Any]:
    answer = [row for row in records if row["answer_required"]]
    refusal = [row for row in records if row["expected_refusal"]]
    per_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in answer:
        per_domain[str(row["domain"])].append(row)
    multi = [row for row in answer if row.get("multi_issue_case")]
    exact = [row for row in answer if row.get("exact_law_article_case")]
    latency = [float(row.get("latency_ms") or 0.0) for row in records]
    return {
        "metric_contract_version": "legal-retrieval-metrics-v2",
        "case_count": len(records),
        "answer_required_count": len(answer),
        "expected_refusal_count": len(refusal),
        "recall_at_10": sum(bool(row.get("hit_at_10")) for row in answer) / len(answer) if answer else 0.0,
        "candidate_recall_at_50": sum(bool(row.get("candidate_hit_at_50")) for row in answer) / len(answer) if answer else 0.0,
        "mrr_at_10": sum(float(row.get("reciprocal_rank_at_10") or 0.0) for row in answer) / len(answer) if answer else 0.0,
        "top5_rate": sum(bool(row.get("top5_hit")) for row in answer) / len(answer) if answer else 0.0,
        "correct_refusal_rate": sum(bool(row.get("correct_refusal")) for row in refusal) / len(refusal) if refusal else None,
        "issue_recall_at_10": sum(float(row.get("issue_recall_at_10") or 0.0) for row in answer) / len(answer) if answer else 0.0,
        "all_required_sources_coverage": sum(float(row.get("all_required_sources_coverage") or 0.0) for row in answer) / len(answer) if answer else 0.0,
        "multi_issue_case_count": len(multi),
        "multi_issue_all_required_sources_coverage": sum(float(row.get("all_required_sources_coverage") or 0.0) for row in multi) / len(multi) if multi else 0.0,
        "exact_law_article_case_count": len(exact),
        "exact_law_article_lookup_recall_at_50": sum(bool(row.get("exact_hit")) for row in exact) / len(exact) if exact else 0.0,
        "exact_law_article_final_recall_at_10": sum(bool(row.get("hit_at_10")) for row in exact) / len(exact) if exact else 0.0,
        "per_domain": {
            domain: {
                "answer_required_count": len(rows),
                "recall_at_10": sum(bool(row.get("hit_at_10")) for row in rows) / len(rows) if rows else 0.0,
                "mrr_at_10": sum(float(row.get("reciprocal_rank_at_10") or 0.0) for row in rows) / len(rows) if rows else 0.0,
            }
            for domain, rows in sorted(per_domain.items())
        },
        "per_domain_candidate_recall_at_50": {
            domain: sum(bool(row.get("candidate_hit_at_50")) for row in rows) / len(rows) if rows else 0.0
            for domain, rows in sorted(per_domain.items())
        },
        "latency_ms": {
            "p50": statistics.median(latency) if latency else 0.0,
            "p95": percentile(latency, 0.95),
        },
        "reranker_latency_ms": {
            "p50": statistics.median([float(row.get("reranker_latency_ms") or 0.0) for row in records]) if records else 0.0,
            "p95": percentile([float(row.get("reranker_latency_ms") or 0.0) for row in records], 0.95),
        },
        "errors": sum(bool(row.get("error")) for row in records),
        "outside_manifest_count": sum(int(row.get("outside_manifest_count") or 0) for row in records),
        "invalid_forbidden_source_count": sum(
            int(row.get("invalid_forbidden_source_count") or 0) for row in records
        ),
        "invalid_temporal_count": sum(int(row.get("invalid_temporal_count") or 0) for row in records),
        "timeout_count": sum(bool(row.get("timeout")) for row in records),
        "oom_count": sum(bool(row.get("oom")) for row in records),
        "unexpected_fallback_rate": sum(bool(row.get("unexpected_fallback")) for row in records) / len(records) if records else 0.0,
        "records": records,
    }


def score_case(
    case: Mapping[str, Any],
    final: Sequence[Mapping[str, Any]],
    candidate_top50: Sequence[Mapping[str, Any]],
    *,
    exact_candidates: Sequence[Mapping[str, Any]] = (),
    latency_ms: float,
    reranker_latency_ms: float = 0.0,
) -> dict[str, Any]:
    expected_refusal = bool(case.get("expected_refusal"))
    answer_required = bool(case.get("answer_required"))
    sources = case_sources(case)
    top10 = list(final[:10])
    ranks = [index + 1 for index, item in enumerate(top10) if matches_any([item], sources)]
    groups = list(case.get("positive_source_groups") or [])
    group_hits = {
        str(group.get("group_id")): matches_any(top10, group.get("sources") or [])
        for group in groups
    }
    issue_groups = list(case.get("issue_groups") or [])
    required_ids = {
        str(identifier)
        for issue in issue_groups
        for identifier in issue.get("required_source_group_ids") or []
    }
    if not required_ids:
        required_ids = set(group_hits)
    coverage = (
        sum(bool(group_hits.get(identifier)) for identifier in required_ids) / len(required_ids)
        if required_ids
        else 1.0
    )
    expected_reason = str(case.get("refusal_category") or "").casefold()
    correct_refusal = expected_refusal and expected_reason in {
        "temporal_unknown", "temporal_conflict", "insufficient_facts", "out_of_scope"
    }
    reciprocal = 1.0 / ranks[0] if ranks else 0.0
    return {
        "case_id": case.get("case_id"),
        "split": case.get("split"),
        "domain": case.get("domain"),
        "answer_required": answer_required,
        "expected_refusal": expected_refusal,
        "hit_at_10": bool(ranks),
        "top5_hit": bool(ranks and ranks[0] <= 5),
        "candidate_hit_at_50": matches_any(candidate_top50, sources) if answer_required else False,
        "reciprocal_rank_at_10": reciprocal,
        "correct_refusal": correct_refusal,
        "issue_recall_at_10": coverage,
        "all_required_sources_coverage": coverage,
        "multi_issue_case": "multi_issue" in set(case.get("tags") or []) or len(issue_groups) > 1,
        "exact_law_article_case": "exact_law_article" in set(case.get("tags") or []),
        "exact_hit": matches_any(exact_candidates, sources) if answer_required else False,
        "latency_ms": float(latency_ms),
        "reranker_latency_ms": float(reranker_latency_ms),
        "outside_manifest_count": 0,
        "invalid_temporal_count": 0,
        "unexpected_fallback": False,
        "error": None,
    }


def compact_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    output = {key: value for key, value in summary.items() if key != "records"}
    output["misses"] = [
        {
            "case_id": row.get("case_id"),
            "domain": row.get("domain"),
            "candidate_hit_at_50": row.get("candidate_hit_at_50"),
            "hit_at_10": row.get("hit_at_10"),
            "reciprocal_rank_at_10": row.get("reciprocal_rank_at_10"),
        }
        for row in summary.get("records") or []
        if row.get("answer_required") and not row.get("hit_at_10")
    ][:250]
    return output


def split_summaries(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        split: compact_summary(evaluate_rows([dict(row) for row in records if row.get("split") == split]))
        for split in DEVELOPMENT_SPLITS
    }


def safety_pass(splits: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(
        int(summary.get("errors") or 0) == 0
        and int(summary.get("outside_manifest_count") or 0) == 0
        and int(summary.get("invalid_temporal_count") or 0) == 0
        and int(summary.get("timeout_count") or 0) == 0
        and int(summary.get("oom_count") or 0) == 0
        for summary in splits.values()
    )
