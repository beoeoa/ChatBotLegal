"""Run the repeatable pilot checks and write one machine-readable report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
REPORTS = ROOT / "reports"

CORE_BACKEND_TESTS = [
    "tests/test_public_auth_flow.py",
    "tests/test_domain_authorization.py",
    "tests/test_conversation_service.py",
    "tests/test_ask_history.py",
    "tests/test_legal_grounding.py",
    "tests/test_grounding_verification.py",
    "tests/test_forms_metadata.py",
    "tests/test_form_recommendation_and_citations.py",
    "tests/test_legal_forms_pipeline.py",
    "tests/test_legal_document_viewer.py",
    "tests/test_review_workflow.py",
    "tests/test_ai_assessment.py",
]


def run_check(name: str, command: list[str], cwd: Path, results: list[dict]) -> None:
    started = time.perf_counter()
    print(f"\n=== {name} ===", flush=True)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (completed.stdout + completed.stderr).strip()
        if output:
            print(output[-12000:], flush=True)
        code = completed.returncode
    except Exception as exc:  # pragma: no cover - runner failure path
        output = repr(exc)
        print(output, flush=True)
        code = 1
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if code == 0 else "FAIL"
    results.append(
        {
            "name": name,
            "status": status,
            "exit_code": code,
            "duration_seconds": duration,
            "output_tail": output[-4000:],
        }
    )
    print(f"[{status}] {name} ({duration}s)", flush=True)


def check_health(api_url: str, results: list[dict]) -> None:
    started = time.perf_counter()
    name = "Backend health"
    try:
        with urlopen(f"{api_url.rstrip('/')}/health", timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ok = payload.get("status") == "healthy"
        output = json.dumps(payload, ensure_ascii=False)
        code = 0 if ok else 1
    except Exception as exc:
        output = repr(exc)
        code = 1
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if code == 0 else "FAIL"
    print(f"[{status}] {name}: {output}", flush=True)
    results.append(
        {
            "name": name,
            "status": status,
            "exit_code": code,
            "duration_seconds": duration,
            "output_tail": output[-4000:],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:5055")
    parser.add_argument("--full", action="store_true", help="run every backend test")
    parser.add_argument("--build", action="store_true", help="also run the frontend production build")
    args = parser.parse_args()

    REPORTS.mkdir(exist_ok=True)
    results: list[dict] = []
    check_health(args.api_url, results)

    run_check(
        "Backend pilot logic",
        [sys.executable, "-m", "pytest", *CORE_BACKEND_TESTS, "-q"],
        ROOT,
        results,
    )
    if args.full:
        run_check("Full backend test suite", [sys.executable, "-m", "pytest", "tests", "-q"], ROOT, results)

    npm = "npm.cmd" if os.name == "nt" else "npm"
    run_check("Frontend unit tests", [npm, "test", "--", "--run"], FRONTEND, results)
    run_check("Frontend type check", [npm, "exec", "--", "tsc", "--noEmit", "--pretty", "false"], FRONTEND, results)
    if args.build:
        run_check("Frontend production build", [npm, "run", "build"], FRONTEND, results)

    failed = sum(item["status"] == "FAIL" for item in results)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_url": args.api_url,
        "mode": "full" if args.full else "pilot",
        "status": "PASS" if failed == 0 else "FAIL",
        "passed": len(results) - failed,
        "failed": failed,
        "checks": results,
    }
    path = REPORTS / f"pilot-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {path}", flush=True)
    print(f"RESULT: {report['status']} ({report['passed']} passed, {report['failed']} failed)", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
