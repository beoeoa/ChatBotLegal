"""Public-safe feature-016 answer projection and structured QA log records."""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from typing import Any, Mapping
from urllib.parse import urlsplit


_DOM_MARKERS = (
    'region "ask response"',
    'textbox "enter your question',
    'button "save to notebooks"',
    'region "notifications',
)


def _norm(value: Any) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))
    ).strip()


def _answer_mode(value: Any) -> str:
    mode = _norm(value).casefold()
    if mode in {"verified_source_condensed", "source_view_only"}:
        return mode
    return "grounded_answer"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _public_label(value: Any, fallback: str) -> str:
    raw = _norm(value)
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-.")
    return raw[:80] or fallback


def _provider_disclosure_enabled() -> bool:
    return str(
        os.getenv("LEGAL_PROVIDER_DISCLOSURE_ENABLED") or "true"
    ).strip().casefold() in {"1", "true", "yes", "on"}


def build_public_trust_projection(
    *,
    trace: Mapping[str, Any] | None,
    citations: list[Mapping[str, Any]] | None,
    answer_mode: str | None,
    provider_label: str | None,
    provider_mode: str | None = None,
    model_label: str | None = None,
    redaction_applied: bool = False,
) -> dict[str, Any]:
    safe_trace = _mapping(trace)
    levels = {
        "physical_span": 0,
        "content_quote": 0,
        "metadata_only": 0,
        "rejected": 0,
    }
    for citation in citations or []:
        level = _norm(
            citation.get("verification_level") or citation.get("citation_level")
        ).casefold()
        if level not in levels:
            level = "metadata_only"
        levels[level] += 1
    intent = _mapping(
        safe_trace.get("intent") or safe_trace.get("legal_intent")
    )
    validity = _mapping(safe_trace.get("validity_decision"))
    safe_provider = _public_label(provider_label, "unknown")
    safe_model = _public_label(model_label, "unknown") if model_label else None
    normalized_mode = _norm(provider_mode).casefold()
    if normalized_mode not in {"local", "cloud"}:
        normalized_mode = "local" if safe_provider.casefold() == "local" else "cloud"
    if not _provider_disclosure_enabled():
        safe_provider = "configured-provider"
        safe_model = None
    return {
        "generation_provenance": {
            "mode": normalized_mode,
            "provider_label": safe_provider,
            "model_label": safe_model,
            "redaction_applied": bool(redaction_applied),
        },
        "citation_verification_summary": levels,
        "retrieval_decision_summary": {
            "intent_confidence": intent.get("confidence"),
            "strict_validity": True,
            "fallback_reason": safe_trace.get("fallback_reason"),
            "answer_mode": _answer_mode(answer_mode),
        },
        "intent": intent,
        "validity_decision": validity,
    }


def build_structured_qa_run(
    *,
    run_id: str,
    case: Mapping[str, Any],
    role: str,
    response: Mapping[str, Any],
    latency_stages: Mapping[str, Any],
    versions: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    questions = _mapping(case.get("questions"))
    question = _norm(response.get("question") or questions.get(role) or questions.get("citizen"))
    answer = str(response.get("answer") or "").strip()
    lowered_answer = answer.casefold()
    if any(marker in lowered_answer for marker in _DOM_MARKERS):
        raise ValueError("answer contains DOM/accessibility tree rather than answer text")
    trace_id = _norm(response.get("trace_id"))
    if not trace_id:
        raise ValueError("structured QA run requires trace_id")
    citations = [
        dict(item)
        for item in (response.get("citations") or [])
        if isinstance(item, Mapping)
    ]
    validity_result = _mapping(response.get("validity_decision"))
    if not validity_result:
        validity_result = {
            "legal_as_of": _norm(
                response.get("legal_as_of") or case.get("legal_as_of")
            ),
            "states": sorted(
                {
                    _norm(
                        item.get("effective_status")
                        or item.get("validity_status")
                    )
                    for item in citations
                    if item.get("effective_status") or item.get("validity_status")
                }
            ),
        }
    completeness = response.get("answer_completeness")
    claim_validation = response.get("claim_validation") or []
    return {
        "run_id": _norm(run_id),
        "case_id": _norm(case.get("case_id")),
        "question_hash": hashlib.sha256(
            question.casefold().encode("utf-8")
        ).hexdigest(),
        "role": role,
        "domain": _norm(case.get("domain")),
        "legal_as_of": _norm(
            response.get("legal_as_of") or case.get("legal_as_of")
        ),
        "question": question,
        "answer": answer,
        "answer_mode": _answer_mode(response.get("answer_mode")),
        "grounding_status": _norm(response.get("grounding_status")) or "unknown",
        "completeness": completeness if isinstance(completeness, Mapping) else None,
        "citations": citations,
        "validity_result": validity_result,
        "claim_validation_summary": {
            "total": len(claim_validation),
            "verified": sum(
                _norm(item.get("status")).casefold() == "verified"
                for item in claim_validation
                if isinstance(item, Mapping)
            ),
        },
        "fallback_reason": _mapping(
            response.get("retrieval_decision_summary")
        ).get("fallback_reason"),
        "error": _mapping(response.get("error")) or None,
        "latency_stages": dict(latency_stages),
        "trace_id": trace_id,
        "versions": dict(versions),
        "provider_label": _mapping(response.get("generation_provenance")).get(
            "provider_label"
        ),
        "evaluation": dict(evaluation),
    }
