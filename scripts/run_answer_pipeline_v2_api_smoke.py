#!/usr/bin/env python3
"""Run the two approved non-browser citizen API checks and save evidence."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

QUESTIONS = (
    "Điều 18a của Luật 88/2025/QH15 quy định gì?",
    (
        "(1) Điều 18a của Luật 88/2025/QH15 quy định gì? "
        "(2) Điều 37a của Luật 88/2025/QH15 quy định gì?"
    ),
)


def _public_result(question: str, response: httpx.Response, elapsed: float) -> dict[str, Any]:
    payload = response.json()
    sections = payload.get("answer_sections") or []
    citations = payload.get("citations") or []
    citation_levels = {
        str(item.get("verification_level") or "")
        for item in citations
        if isinstance(item, dict)
    }
    citation_statuses = {
        str(item.get("verification_status") or "")
        for item in citations
        if isinstance(item, dict)
    }
    return {
        "question": question,
        "http_status": response.status_code,
        "elapsed_seconds": round(elapsed, 3),
        "answer": payload.get("answer"),
        "answer_mode": payload.get("answer_mode"),
        "grounding_status": payload.get("grounding_status"),
        "quality_flags": payload.get("quality_flags") or [],
        "trace_id": payload.get("trace_id"),
        "citations": citations,
        "answer_sections": sections,
        "checks": {
            "answer_nonempty": bool(str(payload.get("answer") or "").strip()),
            "has_citation": bool(citations),
            "citations_are_verified_proof": bool(citations)
            and citation_levels.issubset({"content_quote", "physical_span"})
            and citation_statuses == {"verified"},
            "requested_issue_count": 2 if question.startswith("(1)") else 1,
            "returned_section_count": len(sections),
            "no_silent_issue_omission": len(sections)
            == (2 if question.startswith("(1)") else 1),
        },
    }


def execute(*, base_url: str, output: Path, identifier: str, password: str) -> dict[str, Any]:
    if not password:
        raise RuntimeError("FEATURE016_CITIZEN_PASSWORD is required")
    with httpx.Client(base_url=base_url, timeout=180) as client:
        login = client.post(
            "/api/auth/login",
            json={"identifier": identifier, "password": password, "role": "citizen"},
        )
        login.raise_for_status()
        token = str(login.json().get("token") or "")
        if not token:
            raise RuntimeError("Citizen login returned no token")
        headers = {"Authorization": f"Bearer {token}", "X-User-Role": "citizen"}
        rows: list[dict[str, Any]] = []
        for question in QUESTIONS:
            started = time.perf_counter()
            response = client.post(
                "/api/search/ask/simple",
                headers=headers,
                json={
                    "question": question,
                    "role": "citizen",
                    "legal_as_of": "2026-08-11",
                },
            )
            elapsed = time.perf_counter() - started
            response.raise_for_status()
            rows.append(_public_result(question, response, elapsed))
    report = {
        "schema_version": "answer-pipeline-v2-api-smoke-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "role": "citizen",
        "browser_uat": False,
        "credentials_recorded": False,
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "http_success_count": sum(row["http_status"] == 200 for row in rows),
            "grounded_count": sum(
                row["grounding_status"] in {"fully_grounded", "partially_grounded"}
                for row in rows
            ),
            "no_silent_issue_omission": all(
                row["checks"]["no_silent_issue_omission"] for row in rows
            ),
            "all_citations_are_verified_proof": all(
                row["checks"]["citations_are_verified_proof"] for row in rows
            ),
            "max_elapsed_seconds": max(row["elapsed_seconds"] for row in rows),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identifier", default="citizen01")
    args = parser.parse_args()
    report = execute(
        base_url=args.base_url.rstrip("/"),
        output=args.output.resolve(),
        identifier=args.identifier,
        password=os.getenv("FEATURE016_CITIZEN_PASSWORD", ""),
    )
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
