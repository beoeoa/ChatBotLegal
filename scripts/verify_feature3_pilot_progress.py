"""Capture privacy-safe runtime evidence for a pilot Ask progress request.

The verifier reads a bearer token from a protected dotenv file, sends one
synthetic request, discards every SSE data payload and writes only event names,
HTTP status and timings.  It never writes a question, answer, citation, token,
identity or provider exception message.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable

import httpx


SYNTHETIC_QUESTION = "Vui lòng cho biết giới hạn của nguồn pháp luật hiện có."


def load_env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    raise RuntimeError(f"Missing {key} in protected credentials file")


def sse_event_names(lines: Iterable[str]) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in lines if line.startswith("event:")]


def run(*, base_url: str, token: str, role: str, timeout_seconds: float) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"}
    start = time.perf_counter()
    events: list[str] = []
    first_status_ms: float | None = None
    with httpx.Client(timeout=timeout_seconds) as client:
        identity = client.get(f"{base_url}/api/users/me", headers=headers)
        identity.raise_for_status()
        with client.stream(
            "POST",
            f"{base_url}/api/search/ask/progress",
            headers=headers,
            json={
                "question": SYNTHETIC_QUESTION,
                "role": role,
                "idempotency_key": f"feature3-progress-{role}",
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("event:"):
                    continue
                event = line.split(":", 1)[1].strip()
                events.append(event)
                if event == "status" and first_status_ms is None:
                    first_status_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "schema_version": 1,
        "role": role,
        "identity_status": 200,
        "event_names": events,
        "first_status_ms": first_status_ms,
        "total_ms": round((time.perf_counter() - start) * 1000, 1),
        "final_count": events.count("final"),
        "complete_count": events.count("complete"),
        "error_count": events.count("error"),
        "privacy": "No request, response, citation, credential or identity values are persisted.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--role", choices=("citizen", "officer"), default="citizen")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = load_env_value(args.credentials_file, f"PILOT_{args.role.upper()}_TOKEN")
    report = run(
        base_url=args.base_url.rstrip("/"),
        token=token,
        role=args.role,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "event_names"}, ensure_ascii=True))
    return 0 if report["final_count"] == 1 and report["complete_count"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
