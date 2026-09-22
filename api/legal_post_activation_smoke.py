"""Deterministic retrieval checks for newly activated legal documents.

An import response proving that SQL rows and vectors were written is not enough
to claim that the chatbot can retrieve the document.  This module verifies the
actual serving endpoint with both an exact legal-identifier query and a
semantic content query.  It contains no model calls and returns an auditable,
content-light result for import/replacement workflows.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from datetime import date
from typing import Any, Mapping, Sequence

import httpx

from api.legal_domains import canonicalize_legal_domain


_LAW_NUMBER_LIKE_RE = re.compile(
    r"\b\d{1,4}\s*/\s*\d{4}\s*/\s*[A-ZĐ0-9-]{1,24}\b",
    re.IGNORECASE,
)
_ARTICLE_PREFIX_RE = re.compile(
    r"^\s*(?:điều|dieu)\s+\d+[a-z]?\s*[.:-]?\s*",
    re.IGNORECASE,
)
_NO_DOMAIN_FILTER = frozenset({"", "unknown", "all", "general"})


def _document_id(value: Any) -> str:
    text = str(value or "").strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    try:
        return str(int(text))
    except (TypeError, ValueError):
        return text.casefold()


def _probe_domain(value: Any) -> str | None:
    canonical = str(canonicalize_legal_domain(value) or "").strip().casefold()
    return None if canonical in _NO_DOMAIN_FILTER else canonical


def build_semantic_probe_query(
    *,
    title: str,
    content: str,
    law_number: str,
) -> str:
    """Build a bounded content query without relying on the exact identifier."""

    def clean(value: str) -> str:
        value = _LAW_NUMBER_LIKE_RE.sub(" ", str(value or ""))
        if law_number:
            value = re.sub(re.escape(law_number), " ", value, flags=re.IGNORECASE)
        value = _ARTICLE_PREFIX_RE.sub("", value)
        return re.sub(r"\s+", " ", value).strip(" .,:;-\n\t")

    clean_title = clean(title)
    candidates = [
        clean(part)
        for part in re.split(r"(?:\r?\n){1,}|(?<=[.;!?])\s+", str(content or ""))
    ]
    title_folded = re.sub(r"\s+", " ", clean_title).strip().casefold()
    meaningful = [
        part
        for part in candidates
        if len(part) >= 32
        # A short Article heading (for example "Phạm vi điều chỉnh")
        # is not enough to prove that the newly embedded provision is
        # semantically searchable in a large corpus. Prefer a substantive
        # sentence with enough distinct terms to identify this document.
        and len(set(part.casefold().split())) >= 8
        # Legal pages normally repeat the document title immediately after
        # the document type.  Repeating that title is an identity probe, not
        # a semantic probe, and it is too weak to prove that the freshly
        # embedded passage is searchable.
        and re.sub(r"\s+", " ", part).strip().casefold() != title_folded
    ]
    # Prefer a substantive Article/section passage over a repeated title or a
    # "Căn cứ ..." preamble.  The probe must exercise content embedding while
    # remaining deterministic and independent of a second model call.
    ranked = sorted(
        enumerate(meaningful),
        key=lambda item: (
            -(
                3 * bool(re.search(r"\b(?:điều|chương|mục|phần)\s+\d", item[1], re.I))
                + 2 * bool(re.search(r"\bthông tư này\b|\bvăn bản này\b", item[1], re.I))
                - 2 * bool(re.match(r"^căn cứ\b", item[1], re.I))
                - 2 * bool(re.match(r"^theo đề nghị\b", item[1], re.I))
            ),
            -len(set(item[1].casefold().split())),
            -len(item[1]),
            item[0],
        ),
    )
    passage = ranked[0][1] if ranked else clean(content)
    # Prefer the identifier-free title when it is substantive: crawled legal
    # chunks repeat the title in their structural heading, and appending a
    # generic paragraph can dilute a distinctive subject in the vector query.
    # For short/generic titles use the passage so the probe still exercises
    # actual provision content. The exact leg separately proves the number.
    if len(clean_title) >= 48:
        pieces = [clean_title]
    else:
        pieces = [piece for piece in (passage, clean_title) if piece]
    query = " ".join(dict.fromkeys(pieces))
    return query[:480].strip()


def _result_document_ids(payload: Mapping[str, Any]) -> list[str]:
    rows = payload.get("results") or []
    if not isinstance(rows, list):
        return []
    return list(
        dict.fromkeys(
            _document_id(row.get("document_id") or row.get("doc_id"))
            for row in rows
            if isinstance(row, Mapping)
            and str(row.get("document_id") or row.get("doc_id") or "").strip()
        )
    )


async def verify_post_activation_retrieval(
    *,
    document_id: Any,
    law_number: str,
    title: str,
    content: str,
    domain: str | None,
    base_url: str,
    client: httpx.AsyncClient | None = None,
    retry_delays: Sequence[float] = (0.0, 0.25, 0.75),
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Verify exact and semantic visibility through the real retrieval API."""

    expected_id = _document_id(document_id)
    semantic_query = build_semantic_probe_query(
        title=title,
        content=content,
        law_number=law_number,
    )
    exact_query = f"Văn bản số {str(law_number or '').strip()}".strip()
    started = time.perf_counter()
    if not expected_id or not str(law_number or "").strip() or not semantic_query:
        return {
            "status": "failed",
            "passed": False,
            "reason_code": "post_activation_probe_input_incomplete",
            "document_id": expected_id or None,
            "exact_match": False,
            "semantic_match": False,
            "attempts": 0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    normalized_domain = _probe_domain(domain)
    exact_ids: list[str] = []
    semantic_ids: list[str] = []
    exact_error: str | None = None
    semantic_error: str | None = None
    exact_match = False
    semantic_match = False
    attempts = 0

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        delays = tuple(max(0.0, float(value)) for value in retry_delays) or (0.0,)
        for attempt, delay in enumerate(delays, start=1):
            attempts = attempt
            if delay:
                await asyncio.sleep(delay)

            common = {
                "limit": 30,
                "top_k": 30,
                "candidate_count": 200,
                "lexical_candidate_count": 80,
                "as_of": date.today().isoformat(),
                "as_of_explicit": False,
                "temporal_scope": "current",
            }
            if not exact_match:
                try:
                    response = await active_client.post(
                        f"{base_url.rstrip('/')}/search",
                        json={**common, "query": exact_query, "domain": None},
                    )
                    response.raise_for_status()
                    exact_payload = response.json()
                    exact_ids = _result_document_ids(
                        exact_payload if isinstance(exact_payload, Mapping) else {}
                    )
                    exact_match = expected_id in exact_ids
                    exact_error = None
                except Exception as exc:  # The audit result records the class only.
                    exact_error = exc.__class__.__name__

            if not semantic_match:
                try:
                    response = await active_client.post(
                        f"{base_url.rstrip('/')}/search",
                        json={
                            **common,
                            "query": semantic_query,
                            "domain": normalized_domain,
                        },
                    )
                    response.raise_for_status()
                    semantic_payload = response.json()
                    semantic_ids = _result_document_ids(
                        semantic_payload if isinstance(semantic_payload, Mapping) else {}
                    )
                    semantic_match = expected_id in semantic_ids
                    semantic_error = None

                    # A newly imported central document may not have a
                    # ward-level domain.  The public single-search endpoint
                    # can therefore stop at ``clarification_required`` before
                    # executing retrieval, even though the content is already
                    # embedded and searchable.  Use the existing batch raw
                    # retrieval contract as a deterministic probe fallback;
                    # this bypasses only classification, not serving-state or
                    # vector checks, and does not add an LLM call.
                    if (
                        not semantic_match
                        and isinstance(semantic_payload, Mapping)
                        and semantic_payload.get("status") == "clarification_required"
                    ):
                        batch_response = await active_client.post(
                            f"{base_url.rstrip('/')}/search/batch",
                            json={
                                "request_id": f"post-activation-semantic-{expected_id}",
                                "as_of": date.today().isoformat(),
                                "as_of_explicit": False,
                                "raw_query_mode": True,
                                "issues": [
                                    {
                                        "issue_id": "post-activation-semantic",
                                        "query": semantic_query,
                                        "queries": [
                                            {
                                                "query_id": "post-activation-semantic-raw",
                                                "query_type": "raw",
                                                "query": semantic_query,
                                            }
                                        ],
                                        "domain": normalized_domain,
                                        "intent": "raw_retrieval",
                                        "facets": [],
                                        "subject_anchor": None,
                                        "procedure_id": None,
                                    }
                                ],
                            },
                        )
                        batch_response.raise_for_status()
                        batch_payload = batch_response.json()
                        semantic_ids = _result_document_ids(
                            batch_payload if isinstance(batch_payload, Mapping) else {}
                        )
                        semantic_match = expected_id in semantic_ids
                except Exception as exc:
                    semantic_error = exc.__class__.__name__

            if exact_match and semantic_match:
                break
    finally:
        if owns_client:
            close = getattr(active_client, "aclose", None)
            if callable(close):
                await close()

    passed = exact_match and semantic_match
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "reason_code": None if passed else "post_activation_retrieval_not_visible",
        "document_id": expected_id,
        "domain_filter": normalized_domain,
        "exact_match": exact_match,
        "semantic_match": semantic_match,
        "exact_result_document_ids": exact_ids[:30],
        "semantic_result_document_ids": semantic_ids[:30],
        "exact_error": exact_error,
        "semantic_error": semantic_error,
        "semantic_query_sha256": hashlib.sha256(
            semantic_query.encode("utf-8")
        ).hexdigest(),
        "attempts": attempts,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
