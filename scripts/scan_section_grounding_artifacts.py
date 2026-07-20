"""Reject Feature 005 artifacts that exceed the approved privacy-safe shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALLOWED_TOP_LEVEL = {
    "schema_version",
    "mode",
    "base_url",
    "concurrency",
    "request_count",
    "completed_count",
    "status_counts",
    "section_status_counts",
    "error_category_counts",
    "repair_count",
    "latency_ms",
    "stage_latency_ms",
}
FORBIDDEN_KEY_PARTS = {
    "question", "answer", "citation", "attachment", "credential", "exception",
    "content", "prompt", "token", "secret", "password", "trace", "chunk", "packet",
}
ALLOWED_NESTED_KEYS = {"p50", "p95"}


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                return True
            if _contains_forbidden_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def validate_artifact(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "artifact must be a JSON object"
    if set(payload) != ALLOWED_TOP_LEVEL:
        return False, "artifact keys are not the approved aggregate shape"
    if _contains_forbidden_key(payload):
        return False, "artifact includes a forbidden content-bearing key"
    if not isinstance(payload.get("latency_ms"), dict) or set(payload["latency_ms"]).difference(ALLOWED_NESTED_KEYS):
        return False, "latency summary has unsupported fields"
    if not isinstance(payload.get("stage_latency_ms"), dict):
        return False, "stage latency must be a summary object"
    return True, "privacy-safe aggregate artifact"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Feature 005 benchmark artifact for privacy violations.")
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("REJECTED: artifact is not readable JSON")
        return 1
    valid, reason = validate_artifact(payload)
    print(("PASS" if valid else "REJECTED") + ": " + reason)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
