# -*- coding: utf-8 -*-
"""Collect resumable live answers for all 334 expert-review cases."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "notebook_data" / "legal-golden-expert-review.json"
OUTPUT = ROOT / "notebook_data" / "quality_runs" / "live-334-latest.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _login(client: httpx.Client, role: str) -> None:
    token = str(
        os.getenv(f"PILOT_{role.upper()}_TOKEN")
        or os.getenv(f"OPEN_NOTEBOOK_{role.upper()}_TOKEN")
        or ""
    ).strip()
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
        return
    password = str(
        os.getenv(f"OPEN_NOTEBOOK_{role.upper()}_PASSWORD")
        or os.getenv("OPEN_NOTEBOOK_PASSWORD")
        or ""
    ).strip()
    if not password:
        raise RuntimeError(
            f"Set PILOT_{role.upper()}_TOKEN or an explicit {role} password "
            "environment variable before collecting live answers"
        )
    response = client.post("/api/auth/login", json={"password": password, "role": role}, timeout=30)
    response.raise_for_status()
    token = response.json().get("token")
    if not token:
        raise RuntimeError("Đăng nhập thành công nhưng không có token")
    client.headers["Authorization"] = f"Bearer {token}"


def collect(*, api_url: str, output: Path, limit: int | None, resume: bool) -> dict[str, Any]:
    case_payload = _read(CASES, {"records": []})
    records = list(case_payload.get("records") or [])
    previous = _read(output, {"results": []}) if resume else {"results": []}
    completed = {str(item.get("review_id")): item for item in previous.get("results") or [] if item.get("status") == "completed"}
    selected = [item for item in records if str(item.get("review_id")) not in completed]
    if limit is not None:
        selected = selected[:limit]
    result_map = dict(completed)
    run = {
        "run_id": previous.get("run_id") or uuid.uuid4().hex,
        "started_at": previous.get("started_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target_count": len(records),
        "results": list(result_map.values()),
    }
    with httpx.Client(base_url=api_url, timeout=180, follow_redirects=True) as client:
        active_role = ""
        for index, case in enumerate(selected, start=1):
            review_id = str(case.get("review_id"))
            started = time.perf_counter()
            try:
                requested_role = str(case.get("role") or "citizen")
                if requested_role != active_role:
                    _login(client, requested_role)
                    active_role = requested_role
                response = client.post("/api/search/ask/simple", json={
                    "question": case.get("question"),
                    "role": requested_role,
                    "strategy_model": "",
                    "answer_model": "",
                    "final_answer_model": "",
                    "domain": case.get("domain"),
                    "legal_as_of": case.get("legal_as_of"),
                    "event_date": case.get("event_date"),
                    "idempotency_key": f"quality-{run['run_id']}-{review_id}",
                    "show_rag_trace": True,
                })
                response.raise_for_status()
                data = response.json()
                item = {
                    "review_id": review_id,
                    "case_id": case.get("case_id"),
                    "domain": case.get("domain"),
                    "role": case.get("role"),
                    "question": case.get("question"),
                    "status": "completed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "answer": data.get("answer"),
                    "citations": data.get("citations") or [],
                    "recommended_forms": data.get("recommended_forms") or [],
                    "evidence_coverage": data.get("evidence_coverage") or {},
                    "claim_validation": data.get("claim_validation") or [],
                    "quality_flags": data.get("quality_flags") or [],
                    "answer_score_preview": data.get("answer_score_preview"),
                    "grounding_status": data.get("grounding_status"),
                    "trace_id": data.get("trace_id"),
                    "latency_ms": data.get("latency_ms"),
                }
            except Exception as exc:  # noqa: BLE001
                item = {
                    "review_id": review_id,
                    "case_id": case.get("case_id"),
                    "domain": case.get("domain"),
                    "role": case.get("role"),
                    "question": case.get("question"),
                    "status": "failed",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            result_map[review_id] = item
            run["results"] = list(result_map.values())
            run["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write(output, run)
            print(f"[{index}/{len(selected)}] {review_id}: {item['status']}")
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect all 334 live legal answers")
    parser.add_argument("--api-url", default=os.getenv("CHATBOT_API_URL", "http://127.0.0.1:5055"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    run = collect(api_url=args.api_url, output=args.output, limit=args.limit, resume=not args.no_resume)
    completed = sum(1 for item in run["results"] if item.get("status") == "completed")
    print(f"Completed {completed}/{run['target_count']} records. Output: {args.output}")
    return 0 if completed == run["target_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
