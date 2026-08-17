"""Fail-closed contracts for the Stage E synthetic retrieval evaluation set.

Ragas and DeepEval are candidate generators only.  This module deliberately
keeps legal source provenance deterministic and leaves every generated row in
``pending_final_review`` until an owner imports a signed review decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from api.retrieval_release_contracts import DOMAINS, SPLIT_COUNTS


SPLIT_SIZES: dict[str, int] = dict(SPLIT_COUNTS)

SCENARIO_QUOTA_PER_100: tuple[tuple[str, int], ...] = (
    ("current_answer", 65),
    ("historical_answer", 15),
    ("temporal_refusal", 5),
    ("insufficient_facts_refusal", 5),
    ("out_of_scope_refusal", 10),
)

COVERAGE_MINIMUM_PER_100: dict[str, int] = {
    "exact_law_article": 20,
    "procedure": 20,
    "multi_issue": 15,
    "validity_trap": 20,
}

_ID_PREFIX = {
    "golden-regression": "E-GR",
    "hard-negative": "E-HN",
    "production-holdout": "E-PH",
}


def _scenario_for_offset(offset: int) -> str:
    position = offset % 100
    cursor = 0
    for scenario, count in SCENARIO_QUOTA_PER_100:
        cursor += count
        if position < cursor:
            return scenario
    raise AssertionError("scenario quota does not total 100")


def _coverage_tags_for_offset(offset: int, scenario: str) -> list[str]:
    position = offset % 100
    tags: list[str] = []
    if position < 20:
        tags.append("exact_law_article")
    if 20 <= position < 40:
        tags.append("procedure")
    if 40 <= position < 55:
        tags.append("multi_issue")
    if 55 <= position < 75:
        tags.append("validity_trap")
    if scenario in {"historical_answer", "temporal_refusal"} and "validity_trap" not in tags:
        tags.append("validity_trap")
    return tags


def build_generation_plan() -> list[dict[str, Any]]:
    """Build the deterministic 2,000-slot plan without calling a model."""

    plan: list[dict[str, Any]] = []
    for split, split_size in SPLIT_SIZES.items():
        per_domain = split_size // len(DOMAINS)
        split_counter = 0
        for domain in DOMAINS:
            for offset in range(per_domain):
                split_counter += 1
                scenario = _scenario_for_offset(offset)
                plan.append(
                    {
                        "case_id": f"{_ID_PREFIX[split]}-{split_counter:04d}",
                        "split": split,
                        "domain": domain,
                        "scenario": scenario,
                        "coverage_tags": _coverage_tags_for_offset(offset, scenario),
                        "visibility": "sealed" if split == "production-holdout" else "development",
                        "review_status": "pending_final_review",
                        "reviewer_approval": None,
                    }
                )
    validate_generation_plan(plan)
    return plan


def validate_generation_plan(plan: Sequence[Mapping[str, Any]]) -> None:
    errors: list[str] = []
    if len(plan) != sum(SPLIT_SIZES.values()):
        errors.append(f"expected 2000 slots, found {len(plan)}")

    ids = [str(item.get("case_id", "")) for item in plan]
    if len(ids) != len(set(ids)):
        errors.append("case_id values are not unique")

    actual_splits = Counter(str(item.get("split")) for item in plan)
    if actual_splits != Counter(SPLIT_SIZES):
        errors.append(f"split quota mismatch: {dict(actual_splits)}")

    for split, split_size in SPLIT_SIZES.items():
        per_domain = split_size // len(DOMAINS)
        multiplier = per_domain // 100
        split_rows = [item for item in plan if item.get("split") == split]
        domain_counts = Counter(str(item.get("domain")) for item in split_rows)
        expected_domain_counts = Counter({domain: per_domain for domain in DOMAINS})
        if domain_counts != expected_domain_counts:
            errors.append(f"{split} domain quota mismatch: {dict(domain_counts)}")

        for domain in DOMAINS:
            rows = [item for item in split_rows if item.get("domain") == domain]
            actual_scenarios = Counter(str(item.get("scenario")) for item in rows)
            expected_scenarios = Counter(
                {scenario: count * multiplier for scenario, count in SCENARIO_QUOTA_PER_100}
            )
            if actual_scenarios != expected_scenarios:
                errors.append(
                    f"{split}/{domain} scenario quota mismatch: {dict(actual_scenarios)}"
                )
            tags = Counter(str(tag) for item in rows for tag in item.get("coverage_tags", []))
            for tag, minimum in COVERAGE_MINIMUM_PER_100.items():
                required = minimum * multiplier
                if tags[tag] < required:
                    errors.append(f"{split}/{domain} needs {required} {tag}, found {tags[tag]}")

    if any(item.get("review_status") != "pending_final_review" for item in plan):
        errors.append("all generated slots must remain pending_final_review")
    if any(item.get("reviewer_approval") is not None for item in plan):
        errors.append("generation plan cannot contain reviewer approval")
    if any(
        item.get("visibility") != ("sealed" if item.get("split") == "production-holdout" else "development")
        for item in plan
    ):
        errors.append("split visibility mismatch")

    if errors:
        raise ValueError("; ".join(errors))


def require_local_ollama_url(url: str) -> str:
    normalized = str(url).rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Stage E requires a local Ollama HTTP endpoint")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Stage E requires a plain local Ollama endpoint")
    return normalized


def build_generation_provenance(
    *,
    ragas_version: str,
    deepeval_version: str,
    ollama_version: str,
    generation_model: str,
    generation_model_digest: str,
    embedding_model: str,
    embedding_model_digest: str,
    ollama_url: str,
) -> dict[str, str]:
    endpoint = require_local_ollama_url(ollama_url)
    required = {
        "ragas_version": ragas_version,
        "deepeval_version": deepeval_version,
        "ollama_version": ollama_version,
        "generation_model": generation_model,
        "generation_model_digest": generation_model_digest,
        "embedding_model": embedding_model,
        "embedding_model_digest": embedding_model_digest,
    }
    missing = [key for key, value in required.items() if not str(value).strip()]
    if missing:
        raise ValueError(f"missing generation provenance: {', '.join(missing)}")
    return {
        **{key: str(value).strip() for key, value in required.items()},
        "ollama_url": endpoint,
        "network_policy": "local_only",
        "approval_policy": "candidate_only_owner_final_review",
    }


def _validate_attested_source(source: Mapping[str, Any]) -> None:
    required = (
        "document_id",
        "official_source_url",
        "source_snapshot_sha256",
        "passage_sha256",
    )
    if not source.get("metadata_attested") or any(not str(source.get(key, "")).strip() for key in required):
        raise ValueError("candidate requires an attested official source with reproducible hashes")
    parsed = urlparse(str(source["official_source_url"]))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("candidate requires an attested official source URL")


def _canonical_sha(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_candidate(
    *,
    slot: Mapping[str, Any],
    question: str,
    source: Mapping[str, Any],
    framework: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_attested_source(source)
    clean_question = re.sub(r"\s+", " ", str(question)).strip()
    if len(clean_question) < 10:
        raise ValueError("generated question is too short")
    if framework not in {
        "ragas",
        "deepeval",
        "legacy_approved_seed",
        "deterministic_grounded_transform",
    }:
        raise ValueError("unsupported generation framework")
    if provenance.get("network_policy") != "local_only":
        raise ValueError("candidate generation provenance must be local_only")
    require_local_ollama_url(str(provenance.get("ollama_url", "")))
    if slot.get("review_status") != "pending_final_review" or slot.get("reviewer_approval") is not None:
        raise ValueError("generation slot is not pending final review")

    candidate: dict[str, Any] = {
        **dict(slot),
        "question": clean_question,
        "document_id": str(source["document_id"]),
        "law_number": source.get("law_number"),
        "article": source.get("article"),
        "official_source_url": str(source["official_source_url"]),
        "source_snapshot_sha256": str(source["source_snapshot_sha256"]),
        "passage_sha256": str(source["passage_sha256"]),
        "metadata_attested": True,
        "jurisdiction": source.get("jurisdiction"),
        "validity_from": source.get("validity_from"),
        "validity_to": source.get("validity_to"),
        "metadata_attestation_sha256": source.get("metadata_attestation_sha256"),
        "expected_answer": None,
        "review_status": "pending_final_review",
        "reviewer_approval": None,
        "reviewer_note": None,
        "generation": {
            **dict(provenance),
            "framework": framework,
            "include_expected_output": False,
        },
    }
    candidate["candidate_sha256"] = _canonical_sha(candidate)
    return candidate


def normalize_question(question: str) -> str:
    value = unicodedata.normalize("NFKC", str(question)).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([?.!,;:])", r"\1", value)
    return value


_LEGAL_NUMBER_RE = re.compile(r"\b\d{1,3}/\d{4}/[A-ZĐ0-9-]+\b", re.IGNORECASE)
_OUT_OF_SCOPE_TERMS = (
    "mua điện thoại",
    "thời tiết",
    "lịch thi đấu",
    "nấu ăn",
    "nấu",
    "phần mềm",
    "du lịch",
    "phim",
    "đầu tư",
    "tình cảm",
    "bài toán",
    "chơi game",
)


def _compact_legal_number(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def validate_generated_question(
    question: str,
    *,
    slot: Mapping[str, Any],
    source: Mapping[str, Any],
) -> list[str]:
    """Return deterministic generation defects; an empty list is acceptable."""

    text = re.sub(r"\s+", " ", str(question)).strip()
    folded = unicodedata.normalize("NFKC", text).casefold()
    errors: list[str] = []
    if len(text) < 20:
        errors.append("question_too_short")
    if not text.endswith("?"):
        errors.append("question_mark_missing")
    if "variation_key" in folded or "unique_focus" in folded or text.startswith(("{", "[")):
        errors.append("structured_generation_artifact")

    source_number = _compact_legal_number(source.get("law_number"))
    mentioned_numbers = {_compact_legal_number(item) for item in _LEGAL_NUMBER_RE.findall(text)}
    if any(item != source_number for item in mentioned_numbers):
        errors.append("foreign_legal_number")
    if re.search(r"\b(hiến pháp|bộ luật)\s+(?:năm\s+)?\d{4}\b", folded):
        errors.append("invented_legal_instrument_title")

    tags = set(str(item) for item in slot.get("coverage_tags", []))
    scenario = str(slot.get("scenario") or "")
    if "exact_law_article" in tags:
        if source_number not in _compact_legal_number(text):
            errors.append("exact_law_number_missing")
        article = str(source.get("article") or "").strip()
        if article and not re.search(rf"\bđiều\s+{re.escape(article)}\b", folded):
            errors.append("exact_article_missing")
    if "procedure" in tags and not any(
        term in folded for term in ("thủ tục", "hồ sơ", "giấy tờ", "trình tự", "thẩm quyền", "nộp", "giải quyết")
    ):
        errors.append("procedure_anchor_missing")
    if "multi_issue" in tags and " và " not in f" {folded} ":
        errors.append("multi_issue_anchor_missing")
    if "validity_trap" in tags and not any(
        term in folded
        for term in ("hiệu lực", "thời điểm", "trước ngày", "sau ngày", "năm ", "hiện hành", "thay thế", "chuyển tiếp", "không rõ ngày")
    ):
        errors.append("validity_anchor_missing")

    if scenario == "historical_answer" and not (
        re.search(r"\b(?:19|20)\d{2}\b", folded)
        or any(term in folded for term in ("tại thời điểm", "trước ngày", "sau ngày", "giai đoạn", "trước đây"))
    ):
        errors.append("historical_anchor_missing")
    if scenario == "temporal_refusal" and not any(
        term in folded
        for term in ("không rõ", "không nhớ", "trước hay sau", "mâu thuẫn", "không biết ngày", "chưa xác định thời điểm")
    ):
        errors.append("temporal_ambiguity_missing")
    if scenario == "insufficient_facts_refusal" and not any(
        term in folded for term in ("không rõ", "không biết", "chưa có", "không nhớ", "thiếu", "chưa xác định")
    ):
        errors.append("missing_fact_anchor_missing")
    if scenario == "out_of_scope_refusal":
        if mentioned_numbers or re.search(r"\bđiều\s+\d+\b", folded):
            errors.append("out_of_scope_contains_legal_citation")
        if not any(term in folded for term in _OUT_OF_SCOPE_TERMS):
            errors.append("out_of_scope_anchor_missing")
    return errors


def validate_candidate_pool(
    candidates: Sequence[Mapping[str, Any]], *, require_complete: bool = True
) -> None:
    if require_complete:
        validate_generation_plan(candidates)
    normalized: dict[str, str] = {}
    for candidate in candidates:
        question = normalize_question(str(candidate.get("question", "")))
        if not question:
            raise ValueError(f"empty question for {candidate.get('case_id')}")
        if question in normalized:
            raise ValueError(
                f"duplicate question: {normalized[question]} and {candidate.get('case_id')}"
            )
        normalized[question] = str(candidate.get("case_id"))

    for candidate in candidates:
        if candidate.get("review_status") != "pending_final_review":
            raise ValueError("candidate pool contains non-pending review status")
        if candidate.get("reviewer_approval") is not None:
            raise ValueError("candidate pool contains automatic reviewer approval")
        _validate_attested_source(candidate)
        defects = validate_generated_question(
            str(candidate.get("question") or ""),
            slot=candidate,
            source=candidate,
        )
        if defects:
            raise ValueError(
                f"generated question gate failed for {candidate.get('case_id')}: {','.join(defects)}"
            )


def ensure_external_holdout_path(path: Path | str, repository_root: Path | str) -> Path:
    candidate = Path(path).resolve()
    repo = Path(repository_root).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError:
        return candidate
    raise ValueError("production holdout must be stored outside the repository")


def iter_development_candidates(candidates: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    return (candidate for candidate in candidates if candidate.get("split") != "production-holdout")
