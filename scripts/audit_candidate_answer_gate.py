"""Record why candidate answer-level Golden could or could not run.

This audit never copies credentials and never calls a paid provider. It reports
the observable local prerequisites so a missing provider cannot be mistaken for
an answer-quality PASS.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.request
from datetime import datetime, timezone

from dotenv import dotenv_values


def endpoint_status(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return {"available": True, "status": int(response.status)}
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}:{exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--api-url",
        default=os.getenv("ANSWER_GATE_API_URL", "http://127.0.0.1:5055"),
        help="Local API base URL used for the readiness probe.",
    )
    args = parser.parse_args()
    env = dotenv_values(Path(".env"))
    deepseek = str(env.get("DEEPSEEK_API_KEY") or "").strip()
    openrouter = str(env.get("OPENROUTER_API_KEY") or "").strip()
    ollama = endpoint_status("http://127.0.0.1:11434/api/tags")
    api_log = args.api_log.resolve()
    log_text = api_log.read_text(encoding="utf-8", errors="replace") if api_log.exists() else ""
    startup_errors = [
        line.strip()
        for line in log_text.splitlines()
        if "Application startup failed" in line or "RuntimeError:" in line or "WinError" in line
    ]
    provider_available = bool(deepseek or openrouter or ollama.get("available"))
    api_ready = endpoint_status(f"{str(args.api_url).rstrip('/')}/ready/import")
    gate_status = "BLOCKED_ENVIRONMENT" if not provider_available or not api_ready.get("available") else "PENDING_ANSWER_RUN"
    report = {
        "schema_version": "legal-candidate-answer-gate-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "answer_cases_executed": 0,
        "answer_cases_passed": 0,
        "provider_prerequisites": {
            "deepseek_key_present": bool(deepseek),
            "openrouter_key_present": bool(openrouter),
            "ollama": ollama,
            "provider_available": provider_available,
        },
        "api_prerequisites": {"api_url": str(args.api_url).rstrip("/"), "shadow_api_ready": api_ready, "startup_errors_observed": startup_errors[-8:], "log": str(api_log)},
        "gate": {"passed": False, "status": gate_status, "reason_code": "NO_PROVIDER_AND_API_DEPENDENCY_UNAVAILABLE" if not provider_available or not api_ready.get("available") else "ANSWER_RUN_NOT_EXECUTED"},
        "safety": {"credentials_recorded": False, "provider_called": False, "database_mutated": False, "active_pointer_changed": False},
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "gate": report["gate"], "provider": report["provider_prerequisites"], "api": report["api_prerequisites"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
