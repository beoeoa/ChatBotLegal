"""Retrieval-only evaluation for large legal-question datasets.

The retrieval phase calls only the live retrieval HTTP service.  It never
loads a fixture retriever or a language model.  Model sampling is an explicit,
separate opt-in and records aggregate timing/cost only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_SCHEMA = "retrieval-dataset-v2"
CLASSIFICATIONS = {
    "FOUND_AND_RETRIEVED",
    "FOUND_NOT_RETRIEVED",
    "VERIFIED_DATA_GAP",
}
ALLOWED_REPORT_KEYS = {
    "schema_version",
    "dataset",
    "dataset_sha256",
    "dataset_version",
    "retrieval_version",
    "ranking_strategy",
    "learned_reranker_enabled",
    "generation_version",
    "run_id",
    "legal_as_of",
    "retrieval_url",
    "retrieval_only",
    "live_retrieval",
    "fixture_fallback_count",
    "external_error_count",
    "external_status",
    "status",
    "thresholds",
    "gates",
    "request_count",
    "completed_count",
    "expected_source_observation_count",
    "classification_counts",
    "reason_code_counts",
    "recall_at_10",
    "direct_source_top5",
    "wrong_field_count",
    "expired_selection_count",
    "coverage",
    "latency_ms",
    "cache_hits",
    "cache_misses",
    "sample_counts",
    "selected_model_case_count",
    "model_request_count",
    "model_external_error_count",
    "model_fallback_count",
    "model_latency_ms",
    "fallback_rate",
    "case_pass_rate",
    "available_facet_coverage",
    "grounded_claim_rate",
    "citation_validity_rate",
    "form_gate_rate",
    "broken_internal_marker_count",
    "cost_estimate_usd",
    "cases",
}
FORBIDDEN_REPORT_KEY_PARTS = {
    "question",
    "answer",
    "content",
    "prompt",
    "token",
    "credential",
    "secret",
    "password",
    "attachment",
    "citation_body",
}
DOMAIN_MAP = {
    "ho_tich": "ho_tich_chung_thuc",
    "ho_tich_chung_thuc": "ho_tich_chung_thuc",
    "cu_tru": "cu_tru_an_ninh",
    "cu_tru_an_ninh": "cu_tru_an_ninh",
    "dat_dai": "dat_dai_xay_dung",
    "dat_dai_xay_dung": "dat_dai_xay_dung",
    "xay_dung": "dat_dai_xay_dung",
    "trat_tu_do_thi": "trat_tu_do_thi",
    "khieu_nai": "noi_vu_hanh_chinh",
    "xu_phat": "noi_vu_hanh_chinh",
    "khieu_nai_to_cao_xu_phat": "noi_vu_hanh_chinh",
    "an_sinh_y_te_giao_duc": "an_sinh_y_te_giao_duc",
    "Hộ tịch/chứng thực": "ho_tich_chung_thuc",
    "Đất đai/xây dựng": "dat_dai_xay_dung",
    "Cư trú/an ninh": "cu_tru_an_ninh",
    "Khiếu nại/tố cáo/xử phạt": "noi_vu_hanh_chinh",
    "An sinh/y tế/giáo dục": "an_sinh_y_te_giao_duc",
}
EVALUATED_EXPECTED_OUTCOMES = {
    "AVAILABLE_CORRECTLY_TIERED",
    "VERIFIED_DATA_GAP",
}
DEFAULT_THRESHOLDS = {
    "minimum_case_count": 1,
    "recall_at_10_min": 0.95,
    "direct_source_top5_min": 0.95,
    "wrong_field_count_max": 0,
    "expired_selection_count_max": 0,
    "coverage_min": 0.90,
    "latency_p95_ms_max": 3000,
    "fallback_rate_max": 0.0,
}


def normalize_question(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(text.casefold().split())


def _cache_key(
    question: str,
    legal_as_of: str,
    *,
    dataset_version: str = "",
    retrieval_version: str = "",
) -> str:
    material = (
        f"{normalize_question(question)}\n{legal_as_of}\n"
        f"{dataset_version}\n{retrieval_version}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


_INTERNAL_MARKER_RE = re.compile(
    r"(?:legal:|#ref-source-|(?:chunk|trace|packet)[_-]?id\b|"
    r"\bchunk[_-][a-z0-9_-]{6,})",
    re.IGNORECASE,
)


def _contains_internal_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _INTERNAL_MARKER_RE.search(str(key))
            or _contains_internal_marker(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_internal_marker(item) for item in value)
    return bool(_INTERNAL_MARKER_RE.search(str(value or "")))


def evaluate_model_response(
    payload: Mapping[str, Any],
    *,
    expected_form: Mapping[str, Any] | None = None,
    expected_gap: bool = False,
) -> dict[str, Any]:
    """Evaluate one generated response without persisting its legal content."""

    trace = payload.get("rag_trace") if isinstance(payload.get("rag_trace"), Mapping) else {}
    orchestration = (
        trace.get("section_orchestration")
        if isinstance(trace.get("section_orchestration"), Mapping)
        else {}
    )
    validation = (
        orchestration.get("claim_validation")
        if isinstance(orchestration.get("claim_validation"), Mapping)
        else trace.get("claim_validation")
        if isinstance(trace.get("claim_validation"), Mapping)
        else {}
    )
    metric = (
        orchestration.get("metric")
        if isinstance(orchestration.get("metric"), Mapping)
        else {}
    )
    coverage_ratio = float(validation.get("coverage_ratio") or 0.0)
    displayed_claims = int(validation.get("displayed_legal_claim_count") or 0)
    valid_claims = int(validation.get("displayed_claims_with_valid_evidence") or 0)
    grounding_ratio = (
        float(validation.get("claim_grounding_ratio"))
        if validation.get("claim_grounding_ratio") is not None
        else (valid_claims / displayed_claims if displayed_claims else 1.0)
    )
    quality_gate = validation.get("quality_gate")
    quality_pass = bool(
        quality_gate.get("pass")
        if isinstance(quality_gate, Mapping)
        else validation.get("pass")
    )

    public_sections = [
        section
        for section in (payload.get("answer_sections") or [])
        if isinstance(section, Mapping)
    ]
    public_clarification = bool(payload.get("clarifying_questions")) and bool(
        public_sections
    )
    if not validation and public_sections:
        # Citizen responses intentionally omit the admin-only RAG trace. The
        # public structured contract is sufficient for full-answer evaluation.
        answered_sections = [
            section
            for section in public_sections
            if section.get("status") == "sufficiently_evidenced"
        ]
        clarified_sections = [
            section
            for section in public_sections
            if section.get("status") == "insufficiently_evidenced"
            and section.get("clarifying_question")
        ]
        covered_sections = len(answered_sections) + len(clarified_sections)
        coverage_ratio = covered_sections / len(public_sections)
        displayed_claims = sum(
            max(1, len(section.get("claim_types") or []))
            for section in answered_sections
        )
        valid_claims = sum(
            max(1, len(section.get("claim_types") or []))
            for section in answered_sections
            if section.get("citations")
        )
        grounding_ratio = (
            valid_claims / displayed_claims if displayed_claims else 1.0
        )
        quality_pass = (
            covered_sections == len(public_sections)
            and (
                bool(answered_sections)
                or (
                    public_clarification
                    and len(clarified_sections) == len(public_sections)
                )
            )
            and str(payload.get("grounding_status") or "")
            in {"fully_grounded", "partially_grounded", "insufficient_evidence"}
        )

    citations: list[Mapping[str, Any]] = []
    for citation in payload.get("citations") or []:
        if isinstance(citation, Mapping):
            citations.append(citation)
    for section in payload.get("answer_sections") or []:
        if not isinstance(section, Mapping):
            continue
        for citation in section.get("citations") or []:
            if isinstance(citation, Mapping):
                citations.append(citation)
    citation_fingerprints: set[str] = set()
    unique_citations: list[Mapping[str, Any]] = []
    for citation in citations:
        fingerprint = json.dumps(citation, sort_keys=True, ensure_ascii=False, default=str)
        if fingerprint not in citation_fingerprints:
            citation_fingerprints.add(fingerprint)
            unique_citations.append(citation)
    valid_citations = sum(
        bool(
            str(item.get("source_url") or item.get("url") or "").startswith(
                ("https://", "http://")
            )
        )
        and not _contains_internal_marker(item)
        and str(item.get("verification_status") or "verified") == "verified"
        and str(item.get("verification_level") or "content_quote")
        in {"physical_span", "content_quote"}
        for item in unique_citations
    )
    citation_rate = (
        valid_citations / len(unique_citations) if unique_citations else 1.0
    )

    recommended_forms = [
        item
        for item in payload.get("recommended_forms") or []
        if isinstance(item, Mapping)
    ]
    if expected_form:
        expected_procedure = str(expected_form.get("procedure_id") or "")
        expected_code = str(expected_form.get("form_code") or expected_form.get("code") or "")
        form_gate = any(
            (not expected_procedure or str(item.get("procedure_id") or "") == expected_procedure)
            and (not expected_code or str(item.get("form_code") or item.get("code") or "") == expected_code)
            for item in recommended_forms
        )
    elif expected_gap:
        form_gate = bool(payload.get("forms_unavailable")) and not recommended_forms
    else:
        form_gate = True

    error_category = str(metric.get("error_category") or "none").casefold()
    answer_mode = str(payload.get("answer_mode") or "normal")
    fallback = answer_mode in {"verified_source_condensed", "source_view_only"} or error_category in {
        "timeout",
        "validation_failed",
        "retrieval_unavailable",
        "json_invalid",
        "model_json_invalid",
    }
    internal_marker = _contains_internal_marker(
        {
            "answer_sections": payload.get("answer_sections") or [],
            "citations": payload.get("citations") or [],
            "recommended_forms": recommended_forms,
        }
    )
    case_pass = (
        coverage_ratio >= 0.90
        and grounding_ratio == 1.0
        and valid_claims == displayed_claims
        and quality_pass
        and citation_rate == 1.0
        and form_gate
        and not internal_marker
    )
    return {
        "case_pass": case_pass,
        "coverage_ratio": round(coverage_ratio, 5),
        "displayed_claim_count": displayed_claims,
        "valid_claim_count": valid_claims,
        "grounded_claim_rate": round(grounding_ratio, 5),
        "citation_count": len(unique_citations),
        "valid_citation_count": valid_citations,
        "citation_validity_rate": round(citation_rate, 5),
        "form_case": bool(expected_form) or expected_gap,
        "form_gate": form_gate,
        "fallback": fallback,
        "internal_marker_count": int(internal_marker),
        "error_category": error_category,
    }


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _article(value: Any) -> str:
    return re.sub(r"^\s*(?:điều|dieu)\s*", "", str(value or "").strip().casefold())


def _articles(value: Any) -> set[str]:
    """Normalize a compact Golden provision list such as ``32,33,34``."""

    if value in (None, ""):
        return set()
    return {
        normalized
        for part in re.split(r"[;,]", str(value))
        if (normalized := _article(part))
    }


def _domains_compatible(requested: str | None, selected: str | None) -> bool:
    if not requested or not selected:
        return True
    requested_fold = _fold(requested)
    selected_fold = _fold(selected)
    if requested_fold == selected_fold:
        return True
    selected_parts = tuple(part.strip() for part in selected_fold.split(",") if part.strip())
    aliases = {
        "civil_status": (
            "civil_status", "ho_tich", "ho_tich_chung_thuc", "tu_phap_ho_tich",
        ),
        "ho_tich_chung_thuc": (
            "civil_status", "ho_tich", "ho_tich_chung_thuc", "tu_phap_ho_tich",
        ),
        "land": ("land", "dat_dai", "dat_dai_xay_dung", "dat_dai_moi_truong"),
        "dat_dai_xay_dung": (
            "land", "dat_dai", "dat_dai_xay_dung", "dat_dai_moi_truong",
            "construction", "xay_dung", "xay_dung_do_thi",
        ),
        "construction": (
            "construction", "xay_dung", "dat_dai_xay_dung", "xay_dung_do_thi",
        ),
        "xay_dung": (
            "construction", "xay_dung", "dat_dai_xay_dung", "xay_dung_do_thi",
        ),
        "administrative": (
            "administrative", "noi_vu_hanh_chinh", "khieu_nai_to_cao_xu_phat",
            "hanh_chinh_cong",
        ),
        "noi_vu_hanh_chinh": (
            "administrative", "noi_vu_hanh_chinh", "khieu_nai_to_cao_xu_phat",
            "hanh_chinh_cong",
        ),
        "trat_tu_do_thi": (
            "trat_tu_do_thi", "xay_dung_do_thi", "dat_dai_xay_dung",
            "dat_dai_moi_truong", "khieu_nai_to_cao_xu_phat",
        ),
        "an_sinh_y_te_giao_duc": (
            "an_sinh_y_te_giao_duc", "an_sinh_y_te", "giao_duc_van_hoa",
            "labour", "lao_dong",
        ),
        "labour": ("labour", "lao_dong", "an_sinh_y_te_giao_duc"),
        "lao_dong": ("labour", "lao_dong", "an_sinh_y_te_giao_duc"),
    }
    allowed = aliases.get(requested_fold, (requested_fold,))
    return selected_fold in allowed or any(part in allowed for part in selected_parts)


def _is_current(row: Mapping[str, Any], legal_as_of: str) -> bool:
    status = _fold(row.get("document_status") or row.get("effective_status"))
    if status and status not in {"active", "current", "con_hieu_luc"}:
        return False
    try:
        as_of = date.fromisoformat(legal_as_of)
    except ValueError:
        as_of = date.today()
    for key in ("effective_date", "article_effective_from"):
        value = str(row.get(key) or "").strip()
        if value and value[:10] > as_of.isoformat():
            return False
    for key in ("expired_date", "article_effective_to"):
        value = str(row.get(key) or "").strip()
        if value and value[:10] <= as_of.isoformat():
            return False
    return True


def _safe_source(row: Mapping[str, Any], rank: int, legal_as_of: str, requested_domain: str | None) -> dict[str, Any]:
    current = _is_current(row, legal_as_of)
    row_requested_domain = str(row.get("_requested_domain") or requested_domain or "") or None
    in_domain = _domains_compatible(
        row_requested_domain,
        str(row.get("domain_slug") or row.get("domain") or ""),
    )
    return {
        "rank": rank,
        "issue_rank": row.get("_issue_rank"),
        "document_id": row.get("document_id"),
        "article_number": row.get("article_number"),
        "law_number": row.get("law_number"),
        "source_url": row.get("source_url"),
        "effective_status": row.get("document_status") or row.get("effective_status"),
        "effective_date": row.get("effective_date") or row.get("article_effective_from"),
        "expired_date": row.get("expired_date") or row.get("article_effective_to"),
        "domain": row.get("domain_slug") or row.get("domain"),
        "current": current,
        "in_requested_domain": in_domain,
        "reason_code": (
            "selected_wrong_field"
            if not in_domain
            else "selected_expired_or_not_current"
            if not current
            else "selected_live_source"
        ),
    }


def classify_case(
    *,
    expected_sources: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    requested_domain: str | None,
    legal_as_of: str = "2026-07-23",
) -> dict[str, Any]:
    """Classify one case without inferring a missing legal contract."""

    # A multi-issue question has an independent top-10 contract for every
    # issue. Flattening all issue results and cutting the first ten silently
    # discarded exact sources from issue 2+, even when they ranked first for
    # that issue. Keep only rows that are top-10 inside their own issue.
    issue_top10_rows = [
        row
        for index, row in enumerate(rows, start=1)
        if int(row.get("_issue_rank") or index) <= 10
    ]
    safe_rows = [
        _safe_source(row, rank, legal_as_of, requested_domain)
        for rank, row in enumerate(issue_top10_rows, start=1)
    ]
    wrong_field_count = sum(not item["in_requested_domain"] for item in safe_rows)
    expired_count = sum(not item["current"] for item in safe_rows)
    available = [
        item for item in expected_sources
        if str(item.get("outcome") or "") != "VERIFIED_DATA_GAP"
    ]
    gaps = [
        item for item in expected_sources
        if str(item.get("outcome") or "") == "VERIFIED_DATA_GAP"
    ]
    observations: list[dict[str, Any]] = []
    for expected in expected_sources:
        if str(expected.get("outcome") or "") == "VERIFIED_DATA_GAP":
            observations.append(
                {
                    "document_id": None,
                    "law_number": expected.get("expected_law_number") or expected.get("law_number"),
                    "provision": expected.get("provision"),
                    "rank": None,
                    "in_top5": False,
                    "in_top10": False,
                    "classification": "VERIFIED_DATA_GAP",
                    "reason_code": expected.get("reason_code") or "verified_source_absent",
                }
            )
            continue
        document_id = expected.get("document_id")
        expected_law_number = str(
            expected.get("law_number")
            or expected.get("expected_law_number")
            or ""
        ).strip()
        expected_provision = expected.get("provision") or expected.get("article")
        provisions = _articles(expected_provision)
        matched = next(
            (
                row for row in safe_rows
                if (
                    (
                        document_id is not None
                        and str(row.get("document_id")) == str(document_id)
                    )
                    or (
                        document_id is None
                        and bool(expected_law_number)
                        and _fold(row.get("law_number")) == _fold(expected_law_number)
                    )
                )
                and (
                    not provisions
                    or _article(row.get("article_number")) in provisions
                )
            ),
            None,
        )
        observations.append(
            {
                "document_id": document_id,
                "law_number": expected.get("law_number"),
                "provision": expected_provision,
                "rank": matched["rank"] if matched else None,
                "in_top5": bool(
                    matched
                    and int(matched.get("issue_rank") or matched["rank"]) <= 5
                ),
                "in_top10": bool(matched),
                "classification": (
                    "FOUND_AND_RETRIEVED" if matched else "FOUND_NOT_RETRIEVED"
                ),
                "reason_code": (
                    "expected_source_ranked_top10"
                    if matched
                    else "source_exists_but_not_in_top10"
                ),
            }
        )

    if expected_sources and not available and gaps:
        classification = "VERIFIED_DATA_GAP"
        reason_code = str(gaps[0].get("reason_code") or "verified_source_absent")
    elif any(item["classification"] == "FOUND_NOT_RETRIEVED" for item in observations):
        classification = "FOUND_NOT_RETRIEVED"
        reason_code = "source_exists_but_not_in_top10"
    elif any(item["classification"] == "FOUND_AND_RETRIEVED" for item in observations):
        classification = "FOUND_AND_RETRIEVED"
        reason_code = "expected_source_ranked_top10"
    elif rows:
        classification = "FOUND_AND_RETRIEVED"
        reason_code = "live_result_without_expected_source_contract"
    else:
        classification = "FOUND_NOT_RETRIEVED"
        reason_code = "no_live_result_without_verified_gap"

    return {
        "classification": classification,
        "reason_code": reason_code,
        "expected_sources": observations,
        "selected_sources": safe_rows,
        "top5": [
            row for row in safe_rows
            if int(row.get("issue_rank") or row["rank"]) <= 5
        ],
        "top10": safe_rows,
        "wrong_field_count": wrong_field_count,
        "expired_selection_count": expired_count,
        "uncertain": (
            classification == "FOUND_NOT_RETRIEVED"
            or wrong_field_count > 0
            or expired_count > 0
        ),
    }


def select_model_sample_ids(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample_percent: float = 1.0,
    seed: int = 20260726,
    full_answer: bool = False,
) -> dict[str, set[str]]:
    """Select only permitted model-review groups, deterministically."""

    percent = 100.0 if full_answer else min(5.0, max(1.0, float(sample_percent)))
    selected: dict[str, set[str]] = {str(row["case_id"]): set() for row in rows}
    first_by_domain: dict[str, str] = {}
    for row in sorted(rows, key=lambda item: str(item.get("case_id") or "")):
        domain = str(row.get("domain") or "unknown")
        first_by_domain.setdefault(domain, str(row["case_id"]))
    for case_id in first_by_domain.values():
        selected[case_id].add("representative")
    for row in rows:
        case_id = str(row["case_id"])
        if full_answer:
            selected[case_id].add("all_cases")
        if bool(row.get("uncertain")):
            selected[case_id].add("uncertain")
        if bool(row.get("is_regression")):
            selected[case_id].add("regression")
        digest = hashlib.sha256(f"{seed}:{case_id}".encode()).digest()
        if int.from_bytes(digest[:8], "big") / 2**64 < percent / 100:
            selected[case_id].add("random")
    return selected


def validate_privacy_safe_report(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict) or set(payload) != ALLOWED_REPORT_KEYS:
        return False, "unsupported_report_shape"

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = str(key).casefold()
                if any(part in lowered for part in FORBIDDEN_REPORT_KEY_PARTS):
                    return False
                if not walk(nested):
                    return False
        elif isinstance(value, list):
            return all(walk(item) for item in value)
        return True

    return (True, "privacy_safe") if walk(payload) else (False, "content_bearing_field")


def _load_dataset(path: Path, legal_as_of: str) -> list[dict[str, Any]]:
    from api.legal_answer_router import route_legal_answer
    from api.legal_section_grounding import plan_legal_issues, retrieval_domain_slug

    if path.suffix.casefold() == ".jsonl":
        raw = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("cases"), list)
            and any(
                isinstance(case, Mapping)
                and isinstance(case.get("questions"), Mapping)
                for case in payload["cases"]
            )
        ):
            # Golden v2 stores role-specific questions and compact legal
            # sources.  Project it into the retrieval-only benchmark contract
            # without changing the approved source data on disk.
            raw = []
            for case in payload["cases"]:
                if not isinstance(case, Mapping):
                    continue
                questions = case.get("questions") or {}
                expected_sources = [
                    {
                        **dict(source),
                        "provision": source.get("provision") or source.get("article"),
                        "outcome": str(source.get("outcome") or "AVAILABLE_CORRECTLY_TIERED"),
                    }
                    for source in (case.get("expected_sources") or [])
                    if isinstance(source, Mapping)
                ]
                raw.append(
                    {
                        **dict(case),
                        "question": str(
                            questions.get("citizen")
                            or questions.get("officer")
                            or questions.get("admin")
                            or ""
                        ),
                        "expected_sources": expected_sources,
                    }
                )
        else:
            raw = (
                payload.get("questions", payload.get("cases", payload))
                if isinstance(payload, dict)
                else payload
            )
    if not isinstance(raw, list):
        raise ValueError("dataset_must_be_list_or_questions_object")
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        question = str(item.get("question") or item.get("question_citizen") or "").strip()
        if not question:
            raise ValueError(f"dataset_question_missing_at_index_{index}")
        case_id = str(item.get("case_id") or item.get("id") or f"case-{index + 1}")
        selected_domain = DOMAIN_MAP.get(
            str(item.get("domain") or item.get("domain_group") or ""),
            item.get("domain") or item.get("domain_group"),
        )
        # Benchmark the V2 deterministic router contract. Commas and ordinary
        # conjunctions are coverage inside one issue; only explicit numbered
        # or reviewed quoted lists create siblings.
        route = route_legal_answer(question)
        planned = plan_legal_issues(
            question,
            max_issues=6,
            explicit_only=True,
        )
        expected_sources = list(item.get("expected_sources") or [])
        if (
            not expected_sources
            and str(item.get("expected_scope") or "").strip().casefold()
            == "outside"
        ):
            expected_sources = [
                {
                    "outcome": "VERIFIED_DATA_GAP",
                    "reason_code": "outside_commune_scope",
                }
            ]
        cases.append(
            {
                "case_id": case_id,
                "question": question,
                "legal_as_of": str(item.get("legal_as_of") or legal_as_of),
                "domain": selected_domain,
                "intent": item.get("intent") or "unknown",
                "clarifying_questions": list(route.clarifying_questions),
                "issues": [
                    {
                        "issue_id": issue.issue_id,
                        "query": issue.query_text,
                        "domain": (
                            retrieval_domain_slug(
                            issue.domain,
                            selected_domain,
                            issue.query_text,
                            )
                            or (
                                DOMAIN_MAP.get(str(issue.domain), issue.domain)
                                if str(issue.domain or "").casefold()
                                not in {"", "unknown"}
                                else selected_domain
                            )
                        ),
                        "intent": issue.intent,
                    }
                    for issue in planned
                ],
                "expected_sources": expected_sources,
                "expected_form": (
                    dict(item.get("expected_form"))
                    if isinstance(item.get("expected_form"), Mapping)
                    else None
                ),
                "expected_gap": bool(item.get("expected_gap")),
                "source_gap": (
                    dict(item.get("source_gap"))
                    if isinstance(item.get("source_gap"), Mapping)
                    else None
                ),
                "is_regression": bool(item.get("is_regression") or item.get("regression")),
            }
        )
    return cases


def _load_expected_sources(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    rows: list[dict[str, Any]] = []
    if path.suffix.casefold() == ".jsonl":
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = payload.get("expected_sources", payload) if isinstance(payload, dict) else payload
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("case_id"):
            continue
        if str(row.get("outcome") or "") not in EVALUATED_EXPECTED_OUTCOMES:
            continue
        grouped.setdefault(str(row["case_id"]), []).append(dict(row))
    return grouped


def _load_verified_retrieval_report(
    path: Path,
    *,
    dataset_path: Path,
    cases: Sequence[Mapping[str, Any]],
    dataset_version: str,
    retrieval_version: str,
    ranking_strategy: str,
    learned_reranker_enabled: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Reuse only a matching, live PASS retrieval report for full-answer QA."""

    report = json.loads(path.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    checks = {
        "schema": report.get("schema_version") == REPORT_SCHEMA,
        "status": report.get("status") == "PASS",
        "live": report.get("live_retrieval") is True,
        "dataset_hash": report.get("dataset_sha256") == expected_hash,
        "dataset_version": report.get("dataset_version") == dataset_version,
        "retrieval_version": report.get("retrieval_version") == retrieval_version,
        "ranking_strategy": report.get("ranking_strategy") == ranking_strategy,
        "learned_reranker": bool(report.get("learned_reranker_enabled"))
        is learned_reranker_enabled,
        "case_count": int(report.get("request_count") or 0) == len(cases),
        "completed": int(report.get("completed_count") or 0) == len(cases),
        "no_external_errors": int(report.get("external_error_count") or 0) == 0,
        "has_live_misses": int(report.get("cache_misses") or 0) > 0,
    }
    failed = sorted(key for key, passed in checks.items() if not passed)
    if failed:
        raise ValueError(
            "retrieval_report_not_reusable:" + ",".join(failed)
        )
    rows = [dict(item) for item in report.get("cases") or []]
    expected_ids = {str(case["case_id"]) for case in cases}
    actual_ids = {str(item.get("case_id") or "") for item in rows}
    if expected_ids != actual_ids:
        raise ValueError("retrieval_report_not_reusable:case_id_set")
    for item in rows:
        item["selected_sources"] = list(item.get("top10") or [])
    return rows, {
        "hits": int(report.get("cache_hits") or 0),
        "misses": int(report.get("cache_misses") or 0),
        "errors": int(report.get("external_error_count") or 0),
    }


def _evaluation_expected_sources(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("outcome") or "") in EVALUATED_EXPECTED_OUTCOMES
    ]


def enqueue_detected_source_gaps(
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    store_path: Path,
) -> dict[str, int]:
    """Queue only explicit source gaps; retrieval misses remain regressions."""

    from api.source_gap_jobs import create_source_gap_job, enqueue_source_gap_job

    result_by_id = {str(item.get("case_id")): item for item in results}
    created = 0
    skipped = 0
    for case in cases:
        gap = case.get("source_gap")
        if not isinstance(gap, Mapping):
            continue
        gap_type = str(gap.get("gap_type") or "").upper()
        result = result_by_id.get(str(case.get("case_id")), {})
        if (
            gap_type == "FOUND_NOT_RETRIEVED"
            or result.get("classification") == "FOUND_NOT_RETRIEVED"
        ):
            skipped += 1
            continue
        job = create_source_gap_job(
            case_id=str(case.get("case_id") or ""),
            gap_type=gap_type,
            source_pages=list(gap.get("source_pages") or []),
            legal_as_of=str(case.get("legal_as_of") or ""),
            procedure_id=str(gap.get("procedure_id") or "") or None,
            expected_name=str(gap.get("expected_name") or "") or None,
            expected_code=str(gap.get("expected_code") or "") or None,
        )
        enqueue_source_gap_job(job, store_path=store_path)
        created += 1
    return {"created": created, "skipped": skipped}


def _percentile(values: Sequence[float], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(float(item) for item in values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index])


async def evaluate_retrieval(
    *,
    cases: Sequence[Mapping[str, Any]],
    expected_by_case: Mapping[str, Sequence[Mapping[str, Any]]],
    retrieval_url: str,
    concurrency: int = 8,
    cache_path: Path | None = None,
    request_timeout_seconds: float = 60.0,
    dataset_version: str = "",
    retrieval_version: str = "",
    ranking_strategy: str = "legacy_stack",
    enable_learned_reranker: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cache: dict[str, Any] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8")).get("entries", {})
        except (OSError, json.JSONDecodeError):
            cache = {}
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    cache_hits = 0
    cache_misses = 0
    external_errors = 0

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(request_timeout_seconds, connect=10.0)
    ) as client:
        async def one(case: Mapping[str, Any]) -> dict[str, Any]:
            nonlocal cache_hits, cache_misses
            case_id = str(case["case_id"])
            legal_as_of = str(case["legal_as_of"])
            if case.get("clarifying_questions"):
                return {
                    "case_id": case_id,
                    "domain": case.get("domain"),
                    "legal_as_of": legal_as_of,
                    "classification": "CLARIFICATION_REQUIRED",
                    "reason_code": "deterministic_clarification_required",
                    "expected_sources": [],
                    "selected_sources": [],
                    "top5": [],
                    "top10": [],
                    "wrong_field_count": 0,
                    "expired_selection_count": 0,
                    "uncertain": False,
                    "cache_hit": False,
                    "timing_ms": 0,
                }
            key = _cache_key(
                str(case["question"]),
                legal_as_of,
                dataset_version=dataset_version,
                retrieval_version=retrieval_version,
            )
            async with lock:
                cached = cache.get(key)
            if cached is not None:
                cache_hits += 1
                cached_rows = list(
                    cached.get("selected_sources")
                    or cached.get("top10")
                    or []
                )
                # Cache stores privacy-safe retrieval observations, not a
                # permanently frozen score. Re-apply the current approved
                # source contract so evaluator fixes do not require another
                # expensive retrieval run.
                reclassified = classify_case(
                    expected_sources=_evaluation_expected_sources(
                        expected_by_case.get(case_id, case.get("expected_sources") or [])
                    ),
                    rows=cached_rows,
                    requested_domain=str(case.get("domain") or "") or None,
                    legal_as_of=legal_as_of,
                )
                return {
                    **dict(cached),
                    **reclassified,
                    "case_id": case_id,
                    "cache_hit": True,
                    "timing_ms": 0,
                }
            cache_misses += 1
            payload = {
                "request_id": f"retrieval-benchmark-{key[:16]}",
                "as_of": legal_as_of,
                "issues": list(case.get("issues") or [{
                    "issue_id": "issue-1",
                    "query": str(case["question"]),
                    "domain": case.get("domain"),
                    "intent": case.get("intent") or "unknown",
                }]),
                "retrieval_tier": "core",
                "ranking_strategy": ranking_strategy,
                "enable_learned_reranker": enable_learned_reranker,
            }
            retrieval_seconds = 0.0
            async with semaphore:
                request_started = time.perf_counter()
                response = await client.post(
                    f"{retrieval_url.rstrip('/')}/search/batch",
                    json=payload,
                )
                response.raise_for_status()
                retrieval_seconds += time.perf_counter() - request_started
            body = response.json()
            live_rows: list[dict[str, Any]] = []
            seen_live: set[str] = set()
            issue_domains = {
                str(issue.get("issue_id")): str(issue.get("domain") or "") or None
                for issue in case.get("issues") or []
            }
            for issue_result in body.get("issues") or []:
                issue_id = str(issue_result.get("issue_id") or "")
                requested_issue_domain = issue_domains.get(issue_id)
                for issue_rank, item in enumerate(issue_result.get("results") or [], start=1):
                    item = {
                        **dict(item),
                        "_issue_id": issue_id,
                        "_requested_domain": requested_issue_domain,
                        "_issue_rank": issue_rank,
                    }
                    identity_value = str(item.get("chunk_id") or item.get("source_id") or "")
                    identity = f"{issue_id}:{identity_value}" if identity_value else ""
                    if identity and identity in seen_live:
                        continue
                    if identity:
                        seen_live.add(identity)
                    live_rows.append(item)
            provisional = classify_case(
                expected_sources=_evaluation_expected_sources(
                    expected_by_case.get(case_id, case.get("expected_sources") or [])
                ),
                rows=live_rows,
                requested_domain=str(case.get("domain") or "") or None,
                legal_as_of=legal_as_of,
            )
            expanded_used = False
            if provisional["uncertain"]:
                expanded_used = True
                expanded_payload = {**payload, "retrieval_tier": "expanded"}
                async with semaphore:
                    request_started = time.perf_counter()
                    expanded_response = await client.post(
                        f"{retrieval_url.rstrip('/')}/search/batch",
                        json=expanded_payload,
                    )
                    expanded_response.raise_for_status()
                    retrieval_seconds += time.perf_counter() - request_started
                expanded_body = expanded_response.json()
                seen = {
                    f"{str(item.get('_issue_id') or '')}:"
                    f"{str(item.get('chunk_id') or item.get('source_id') or '')}"
                    for item in live_rows
                }
                for expanded_issue in expanded_body.get("issues") or []:
                    issue_id = str(expanded_issue.get("issue_id") or "")
                    requested_issue_domain = issue_domains.get(issue_id)
                    for issue_rank, item in enumerate(expanded_issue.get("results") or [], start=1):
                        item = {
                            **dict(item),
                            "_issue_id": issue_id,
                            "_requested_domain": requested_issue_domain,
                            "_issue_rank": issue_rank,
                        }
                        identity_value = str(item.get("chunk_id") or item.get("source_id") or "")
                        identity = f"{issue_id}:{identity_value}" if identity_value else ""
                        if identity and identity in seen:
                            continue
                        if identity:
                            seen.add(identity)
                        live_rows.append(item)
            elapsed = round(retrieval_seconds * 1000, 3)
            result = classify_case(
                expected_sources=_evaluation_expected_sources(
                    expected_by_case.get(case_id, case.get("expected_sources") or [])
                ),
                rows=live_rows,
                requested_domain=str(case.get("domain") or "") or None,
                legal_as_of=legal_as_of,
            )
            safe_result = {
                **result,
                "case_id": case_id,
                "domain": case.get("domain"),
                "legal_as_of": legal_as_of,
                "cache_hit": False,
                "timing_ms": elapsed,
                "expanded_used": expanded_used,
            }
            async with lock:
                cache[key] = {
                    key_name: value
                    for key_name, value in safe_result.items()
                    if key_name not in {"case_id", "cache_hit", "timing_ms"}
                }
            return safe_result

        gathered = await asyncio.gather(
            *(one(case) for case in cases),
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        for case, result in zip(cases, gathered):
            if not isinstance(result, Exception):
                results.append(result)
                continue
            external_errors += 1
            expected_rows = _evaluation_expected_sources(
                expected_by_case.get(str(case["case_id"]), case.get("expected_sources") or [])
            )
            fallback = classify_case(
                expected_sources=expected_rows,
                rows=[],
                requested_domain=str(case.get("domain") or "") or None,
                legal_as_of=str(case["legal_as_of"]),
            )
            results.append(
                {
                    **fallback,
                    "case_id": str(case["case_id"]),
                    "domain": case.get("domain"),
                    "legal_as_of": str(case["legal_as_of"]),
                    "cache_hit": False,
                    "timing_ms": 0,
                    "expanded_used": False,
                    "reason_code": "live_retrieval_timeout"
                    if isinstance(result, httpx.TimeoutException)
                    else "live_retrieval_request_failed",
                    "uncertain": True,
                }
            )
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": "retrieval-cache-v2",
                    "dataset_version": dataset_version,
                    "retrieval_version": retrieval_version,
                    "entries": cache,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return list(results), {
        "hits": cache_hits,
        "misses": cache_misses,
        "errors": external_errors,
    }


async def _sample_models(
    *,
    cases_by_id: Mapping[str, Mapping[str, Any]],
    selected: Mapping[str, set[str]],
    model_url: str,
    token: str,
    concurrency: int = 2,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    request_count = 0
    fallback_count = 0
    external_error_count = 0
    durations: list[float] = []
    estimated_input_tokens = 0
    evaluations: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0), headers=headers) as client:
        async def one(case_id: str, reasons: set[str]) -> None:
            nonlocal request_count, fallback_count, external_error_count, estimated_input_tokens
            case = cases_by_id[case_id]
            estimated_input_tokens += max(1, len(str(case["question"])) // 4)
            started = time.perf_counter()
            async with semaphore:
                try:
                    response = await client.post(
                        f"{model_url.rstrip('/')}/api/search/ask/simple",
                        json={
                            "question": case["question"],
                            "role": "citizen",
                            "legal_as_of": case["legal_as_of"],
                            "idempotency_key": f"model-benchmark-{_cache_key(case['question'], case['legal_as_of'])[:24]}",
                        },
                    )
                    response.raise_for_status()
                    body = response.json()
                    evaluation = evaluate_model_response(
                        body,
                        expected_form=case.get("expected_form"),
                        expected_gap=bool(case.get("expected_gap")),
                    )
                    evaluations.append(evaluation)
                    if evaluation["fallback"]:
                        fallback_count += 1
                except Exception:
                    external_error_count += 1
                    fallback_count += 1
                finally:
                    request_count += 1
                    durations.append((time.perf_counter() - started) * 1000)

        await asyncio.gather(*(one(case_id, reasons) for case_id, reasons in selected.items() if reasons))
    displayed_claim_count = sum(item["displayed_claim_count"] for item in evaluations)
    valid_claim_count = sum(item["valid_claim_count"] for item in evaluations)
    citation_count = sum(item["citation_count"] for item in evaluations)
    valid_citation_count = sum(item["valid_citation_count"] for item in evaluations)
    form_case_count = sum(bool(item["form_case"]) for item in evaluations)
    return {
        "request_count": request_count,
        "external_error_count": external_error_count,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / request_count, 5) if request_count else 0.0,
        "latency_ms": {
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
        },
        "estimated_input_tokens": estimated_input_tokens,
        "passed_case_count": sum(bool(item["case_pass"]) for item in evaluations),
        "case_pass_rate": (
            round(sum(bool(item["case_pass"]) for item in evaluations) / request_count, 5)
            if request_count
            else 0.0
        ),
        "available_facet_coverage": (
            round(sum(float(item["coverage_ratio"]) for item in evaluations) / request_count, 5)
            if request_count
            else 0.0
        ),
        "displayed_claim_count": displayed_claim_count,
        "valid_claim_count": valid_claim_count,
        "grounded_claim_rate": (
            round(valid_claim_count / displayed_claim_count, 5)
            if displayed_claim_count
            else (1.0 if evaluations else 0.0)
        ),
        "citation_count": citation_count,
        "valid_citation_count": valid_citation_count,
        "citation_validity_rate": (
            round(valid_citation_count / citation_count, 5)
            if citation_count
            else (1.0 if evaluations else 0.0)
        ),
        "form_case_count": form_case_count,
        "form_gate_rate": (
            round(
                sum(
                    bool(item["form_gate"])
                    for item in evaluations
                    if item["form_case"]
                )
                / form_case_count,
                5,
            )
            if form_case_count
            else 1.0
        ),
        "internal_marker_count": sum(item["internal_marker_count"] for item in evaluations),
    }


def build_report(
    *,
    dataset: str,
    legal_as_of: str,
    retrieval_url: str,
    cases: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    cache_stats: Mapping[str, int],
    sample_reasons: Mapping[str, set[str]],
    model_stats: Mapping[str, Any] | None = None,
    input_cost_per_million: float = 0.0,
    output_cost_per_million: float = 0.0,
    thresholds: Mapping[str, float | int] | None = None,
    dataset_sha256: str = "",
    dataset_version: str = "",
    retrieval_version: str = "",
    ranking_strategy: str = "legacy_stack",
    learned_reranker_enabled: bool = True,
    generation_version: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    classifications = Counter(str(item["classification"]) for item in results)
    reasons = Counter(str(item["reason_code"]) for item in results)
    available = [item for item in results if item["classification"] != "VERIFIED_DATA_GAP"]
    observations = [
        expected
        for item in results
        for expected in item.get("expected_sources") or []
        if expected.get("classification") != "VERIFIED_DATA_GAP"
    ]
    hits10 = sum(bool(item.get("in_top10")) for item in observations)
    docs = [item for item in observations if item.get("provision") in (None, "")]
    direct_observations = docs or observations
    hits5 = sum(bool(item.get("in_top5")) for item in direct_observations)
    timings = [float(item.get("timing_ms") or 0) for item in results if not item.get("cache_hit")]
    wrong = sum(int(item.get("wrong_field_count") or 0) for item in results)
    expired = sum(int(item.get("expired_selection_count") or 0) for item in results)
    model = model_stats or {}
    input_tokens = int(model.get("estimated_input_tokens") or 0)
    output_tokens = int(model.get("estimated_output_tokens") or 0)
    cost = (
        input_tokens * input_cost_per_million / 1_000_000
        + output_tokens * output_cost_per_million / 1_000_000
    )
    sample_counts = Counter(reason for values in sample_reasons.values() for reason in values)
    selected_model_case_count = sum(bool(values) for values in sample_reasons.values())
    applied_thresholds = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    recall_at_10 = round(hits10 / len(observations), 5) if observations else 0.0
    direct_source_top5 = (
        round(hits5 / len(direct_observations), 5)
        if direct_observations
        else 0.0
    )
    coverage = (
        round(
            sum(
                bool(item.get("selected_sources"))
                or item.get("classification")
                in {"VERIFIED_DATA_GAP", "CLARIFICATION_REQUIRED"}
                for item in results
            )
            / len(results),
            5,
        )
        if results
        else 0.0
    )
    latency = {
        "p50": _percentile(timings, 0.5),
        "p95": _percentile(timings, 0.95),
    }
    fallback_rate = float(model.get("fallback_rate") or 0.0)
    model_request_count = int(model.get("request_count") or 0)
    model_external_error_count = int(model.get("external_error_count") or 0)
    model_latency_ms = dict(model.get("latency_ms") or {"p50": 0, "p95": 0})
    model_requested = model_stats is not None
    case_pass_rate = float(model.get("case_pass_rate") or 0.0)
    available_facet_coverage = float(model.get("available_facet_coverage") or 0.0)
    grounded_claim_rate = float(model.get("grounded_claim_rate") or 0.0)
    citation_validity_rate = float(model.get("citation_validity_rate") or 0.0)
    form_gate_rate = float(model.get("form_gate_rate") if model.get("form_gate_rate") is not None else 0.0)
    broken_internal_marker_count = int(model.get("internal_marker_count") or 0)
    gates = {
        "minimum_dataset_size": (
            len(cases) >= int(applied_thresholds["minimum_case_count"])
        ),
        "live_retrieval_without_fixture": (
            int(cache_stats.get("misses") or 0) > 0
            and int(cache_stats.get("errors") or 0) == 0
        ),
        "expected_source_contract_present": len(observations) > 0,
        "recall_at_10": recall_at_10 >= float(applied_thresholds["recall_at_10_min"]),
        "direct_source_top5": (
            bool(direct_observations)
            and direct_source_top5
            >= float(applied_thresholds["direct_source_top5_min"])
        ),
        "wrong_field": wrong <= int(applied_thresholds["wrong_field_count_max"]),
        "expired_selection": expired <= int(applied_thresholds["expired_selection_count_max"]),
        "coverage": coverage >= float(applied_thresholds["coverage_min"]),
        "latency_p95": latency["p95"] <= int(applied_thresholds["latency_p95_ms_max"]),
        "model_sample_policy": model_request_count <= selected_model_case_count,
        "fallback_rate": fallback_rate <= float(applied_thresholds["fallback_rate_max"]),
        "model_case_pass_rate": (
            not model_requested or case_pass_rate >= 0.90
        ),
        "available_facet_coverage": (
            not model_requested or available_facet_coverage >= 0.90
        ),
        "grounded_claim_rate": (
            not model_requested or grounded_claim_rate == 1.0
        ),
        "citation_validity_rate": (
            not model_requested or citation_validity_rate == 1.0
        ),
        "form_gate_rate": not model_requested or form_gate_rate == 1.0,
        "no_internal_markers": broken_internal_marker_count == 0,
    }
    external_error_count = int(cache_stats.get("errors") or 0)
    provider_failure_is_mass = bool(
        model_request_count
        and model_external_error_count / model_request_count >= 0.5
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "dataset": dataset,
        "dataset_sha256": dataset_sha256,
        "dataset_version": dataset_version,
        "retrieval_version": retrieval_version,
        "ranking_strategy": ranking_strategy,
        "learned_reranker_enabled": learned_reranker_enabled,
        "generation_version": generation_version,
        "run_id": run_id,
        "legal_as_of": legal_as_of,
        "retrieval_url": retrieval_url,
        "retrieval_only": not model_requested,
        "live_retrieval": True,
        "fixture_fallback_count": 0,
        "status": (
            "BLOCKED_EXTERNAL"
            if external_error_count or provider_failure_is_mass
            else "PASS"
            if all(gates.values())
            else "FAIL"
        ),
        "thresholds": applied_thresholds,
        "gates": gates,
        "request_count": len(results),
        "completed_count": len(results),
        "expected_source_observation_count": len(observations),
        "external_error_count": external_error_count,
        "external_status": (
            "BLOCKED_EXTERNAL"
            if external_error_count or provider_failure_is_mass
            else "READY"
        ),
        "classification_counts": dict(sorted(classifications.items())),
        "reason_code_counts": dict(sorted(reasons.items())),
        "recall_at_10": recall_at_10,
        "direct_source_top5": direct_source_top5,
        "wrong_field_count": wrong,
        "expired_selection_count": expired,
        "coverage": coverage,
        "latency_ms": latency,
        "cache_hits": int(cache_stats.get("hits") or 0),
        "cache_misses": int(cache_stats.get("misses") or 0),
        "sample_counts": dict(sorted(sample_counts.items())),
        "selected_model_case_count": selected_model_case_count,
        "model_request_count": model_request_count,
        "model_external_error_count": model_external_error_count,
        "model_fallback_count": int(model.get("fallback_count") or 0),
        "model_latency_ms": model_latency_ms,
        "fallback_rate": fallback_rate,
        "case_pass_rate": case_pass_rate,
        "available_facet_coverage": available_facet_coverage,
        "grounded_claim_rate": grounded_claim_rate,
        "citation_validity_rate": citation_validity_rate,
        "form_gate_rate": form_gate_rate,
        "broken_internal_marker_count": broken_internal_marker_count,
        "cost_estimate_usd": round(cost, 8),
        "cases": [
            {
                "case_id": item["case_id"],
                "domain": item.get("domain"),
                "legal_as_of": item.get("legal_as_of"),
                "classification": item["classification"],
                "reason_code": item["reason_code"],
                "uncertain": item.get("uncertain"),
                "timing_ms": item.get("timing_ms"),
                "cache_hit": item.get("cache_hit"),
                "wrong_field_count": item.get("wrong_field_count"),
                "expired_selection_count": item.get("expired_selection_count"),
                "expected_sources": item.get("expected_sources"),
                "top5": item.get("top5"),
                "top10": item.get("top10"),
            }
            for item in results
        ],
    }
    valid, reason = validate_privacy_safe_report(report)
    if not valid:
        raise ValueError(f"refusing_to_write_non_private_report:{reason}")
    return report


async def run(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_dataset(args.dataset, args.legal_as_of)
    if args.limit:
        cases = cases[: args.limit]
    expected = _load_expected_sources(args.expected_sources)
    if args.retrieval_report:
        results, cache_stats = _load_verified_retrieval_report(
            args.retrieval_report,
            dataset_path=args.dataset,
            cases=cases,
            dataset_version=args.dataset_version,
            retrieval_version=args.retrieval_version,
            ranking_strategy=args.ranking_strategy,
            learned_reranker_enabled=args.enable_learned_reranker,
        )
    else:
        results, cache_stats = await evaluate_retrieval(
            cases=cases,
            expected_by_case=expected,
            retrieval_url=args.retrieval_url,
            concurrency=args.concurrency,
            cache_path=args.cache,
            request_timeout_seconds=args.request_timeout_seconds,
            dataset_version=args.dataset_version,
            retrieval_version=args.retrieval_version,
            ranking_strategy=args.ranking_strategy,
            enable_learned_reranker=args.enable_learned_reranker,
        )
    sample_rows = [
        {
            "case_id": item["case_id"],
            "domain": item.get("domain"),
            "classification": item["classification"],
            "uncertain": item["uncertain"],
            "is_regression": next(
                bool(case.get("is_regression"))
                for case in cases
                if case["case_id"] == item["case_id"]
            ),
        }
        for item in results
    ]
    sample_reasons = select_model_sample_ids(
        sample_rows,
        sample_percent=args.sample_percent,
        seed=args.seed,
        full_answer=args.full_answer,
    )
    if getattr(args, "source_gap_store", None):
        enqueue_detected_source_gaps(
            cases,
            results,
            store_path=args.source_gap_store,
        )
    model_stats = None
    if args.model_url:
        token = os.getenv(args.model_token_env, "")
        model_stats = await _sample_models(
            cases_by_id={str(case["case_id"]): case for case in cases},
            selected=sample_reasons,
            model_url=args.model_url,
            token=token,
            concurrency=args.model_concurrency,
        )
    return build_report(
        dataset=str(args.dataset),
        legal_as_of=args.legal_as_of,
        retrieval_url=args.retrieval_url,
        cases=cases,
        results=results,
        cache_stats=cache_stats,
        sample_reasons=sample_reasons,
        model_stats=model_stats,
        input_cost_per_million=args.input_cost_per_million,
        output_cost_per_million=args.output_cost_per_million,
        thresholds={
            "minimum_case_count": args.minimum_case_count,
            "recall_at_10_min": args.recall_at_10_min,
            "direct_source_top5_min": args.direct_source_top5_min,
            "wrong_field_count_max": args.wrong_field_count_max,
            "expired_selection_count_max": args.expired_selection_count_max,
            "coverage_min": args.coverage_min,
            "latency_p95_ms_max": args.latency_p95_ms_max,
            "fallback_rate_max": args.fallback_rate_max,
        },
        dataset_sha256=hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        dataset_version=args.dataset_version,
        retrieval_version=args.retrieval_version,
        ranking_strategy=args.ranking_strategy,
        learned_reranker_enabled=args.enable_learned_reranker,
        generation_version=args.generation_version,
        run_id=args.run_id or f"eval-{int(time.time())}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a large dataset with live retrieval only.")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--expected-sources", type=Path)
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    parser.add_argument("--legal-as-of", default="2026-07-23")
    parser.add_argument("--dataset-version", default="")
    parser.add_argument("--retrieval-version", default="")
    parser.add_argument(
        "--ranking-strategy",
        choices=("legacy_stack", "rrf_v2"),
        default="legacy_stack",
    )
    parser.add_argument(
        "--enable-learned-reranker",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--generation-version", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--request-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cache", type=Path)
    parser.add_argument(
        "--retrieval-report",
        type=Path,
        help="Reuse a matching live PASS retrieval report and run only final-answer evaluation.",
    )
    parser.add_argument("--source-gap-store", type=Path)
    parser.add_argument("--sample-percent", type=float, default=1.0)
    parser.add_argument(
        "--full-answer",
        action="store_true",
        help="Evaluate final API answers for every case; explicit opt-in avoids accidental provider cost.",
    )
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--model-url")
    parser.add_argument("--model-token-env", default="LEGAL_BENCHMARK_TOKEN")
    parser.add_argument("--model-concurrency", type=int, default=2)
    parser.add_argument("--input-cost-per-million", type=float, default=0.0)
    parser.add_argument("--output-cost-per-million", type=float, default=0.0)
    parser.add_argument("--minimum-case-count", type=int, default=1)
    parser.add_argument("--recall-at-10-min", type=float, default=0.95)
    parser.add_argument("--direct-source-top5-min", type=float, default=0.95)
    parser.add_argument("--wrong-field-count-max", type=int, default=0)
    parser.add_argument("--expired-selection-count-max", type=int, default=0)
    parser.add_argument("--coverage-min", type=float, default=0.90)
    parser.add_argument("--latency-p95-ms-max", type=int, default=3000)
    parser.add_argument("--fallback-rate-max", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.sample_percent <= (100 if args.full_answer else 5):
        raise SystemExit(
            "--sample-percent must be between 1 and 5 unless --full-answer is set"
        )
    if args.minimum_case_count < 1:
        raise SystemExit("--minimum-case-count must be positive")
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "request_count": report["request_count"],
        "model_request_count": report["model_request_count"],
        "recall_at_10": report["recall_at_10"],
        "coverage": report["coverage"],
        "latency_p95_ms": report["latency_ms"]["p95"],
    }))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
