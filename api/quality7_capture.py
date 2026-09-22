"""Opt-in local evidence capture for explicitly selected synthetic test users."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


def capture_enabled(username: str) -> bool:
    allowed = {v.strip() for v in os.getenv("QUALITY7_CAPTURE_USERS", "").split(",") if v.strip()}
    return bool(os.getenv("QUALITY7_CAPTURE_DIR", "").strip() and username and username in allowed)


def capture_turn(*, username: str, request_id: str, question: str, plan, result, response, generation=None) -> None:
    directory = os.getenv("QUALITY7_CAPTURE_DIR", "").strip()
    if not capture_enabled(username):
        return
    try:
        target = Path(directory).resolve()
        expected = (Path(__file__).resolve().parents[1] / "artifacts" / "quality7").resolve()
        if not target.is_relative_to(expected):
            raise ValueError("capture_outside_quality7_artifacts")
        target.mkdir(parents=True, exist_ok=True)
        payload = {"version":"quality7-capture-v1", "observed_at":datetime.now(timezone.utc).isoformat(),
            "request_id":request_id, "question":question,
            "items":plan.public_items() if plan else [],
            "resolved_issues":list(plan.legal_issues) if plan else [],
            "evidence":list(result.evidence_by_id.values()), "trace":dict(result.trace),
            "response":response.model_dump(mode="json"), "legal_review":"pending",
            "generation": generation or {"status": "not_called_or_not_captured"}}
        filename = hashlib.sha256(request_id.encode()).hexdigest() + ".json"
        with (target / filename).open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.getLogger(__name__).warning("quality7_capture_failed type=%s", type(exc).__name__)
