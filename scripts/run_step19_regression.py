# -*- coding: utf-8 -*-
"""Run Step 19 pilot regression and write a PASS/FAIL report.

The runner groups existing unit/API/runtime checks under the 14 acceptance
items from Step 19. Runtime-only checks are reported as ENV_FAIL when the local
API is not reachable, so the report stays honest and repeatable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "notebook_data" / "regression"
API_URL = os.getenv("STEP19_API_URL", "http://127.0.0.1:5055")


@dataclass
class Check:
    name: str
    command: list[str] | None = None
    cwd: Path = ROOT
    timeout: int = 180
    runtime: bool = False
    build: bool = False
    note: str = ""


@dataclass
class Item:
    number: int
    title: str
    checks: list[Check] = field(default_factory=list)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_healthy() -> tuple[bool, str]:
    try:
        response = httpx.get(f"{API_URL}/health", timeout=5)
        return response.status_code == 200, f"status={response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def run_command(check: Check, logs_dir: Path) -> dict:
    start = time.time()
    if not check.command:
        return {
            "name": check.name,
            "status": "PASS",
            "duration_sec": 0,
            "detail": check.note or "static item",
        }

    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in check.name)[:90]
    log_path = logs_dir / f"{safe_name}.log"
    command_display = " ".join(check.command)
    try:
        completed = subprocess.run(
            command_display,
            cwd=str(check.cwd),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=check.timeout,
            shell=True,
        )
        output = completed.stdout or ""
        log_path.write_text(output, encoding="utf-8")
        status = "PASS" if completed.returncode == 0 else "FAIL"
        return {
            "name": check.name,
            "status": status,
            "returncode": completed.returncode,
            "duration_sec": round(time.time() - start, 2),
            "command": command_display,
            "log": str(log_path.relative_to(ROOT)).replace("\\", "/"),
            "tail": "\n".join(output.splitlines()[-25:]),
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + "\n[TIMEOUT]\n" + (exc.stderr or "")
        log_path.write_text(output, encoding="utf-8")
        return {
            "name": check.name,
            "status": "TIMEOUT",
            "duration_sec": round(time.time() - start, 2),
            "command": command_display,
            "log": str(log_path.relative_to(ROOT)).replace("\\", "/"),
            "tail": "\n".join(output.splitlines()[-25:]),
        }


def pytest_cmd(*paths: str) -> list[str]:
    return [sys.executable, "-m", "pytest", *paths, "-q"]


def build_items(include_build: bool = True) -> list[Item]:
    return [
        Item(
            1,
            "Ask hai lần không trùng card và final UI giữ đẹp",
            [
                Check(
                    "frontend ask history/render utils",
                    ["npm", "run", "test", "--", "src/components/search/AskMessageHistory.test.tsx", "src/lib/utils/ask-turn-history.test.ts"],
                    cwd=ROOT / "frontend",
                    timeout=180,
                ),
                Check(
                    "runtime ask/search/form regression",
                    [sys.executable, "scripts/e2e_step12_ask_search_form_regression.py"],
                    timeout=900,
                    runtime=True,
                ),
            ],
        ),
        Item(
            2,
            "Chat follow-up nhớ đúng context riêng của conversation",
            [
                Check("frontend conversation context utils", ["npm", "run", "test", "--", "src/lib/utils/conversation-context.test.ts"], cwd=ROOT / "frontend"),
                Check("backend conversation service", pytest_cmd("tests/test_conversation_service.py")),
            ],
        ),
        Item(
            3,
            "Citizen không thấy/không truy cập Hồ sơ pháp lý",
            [
                Check("retention and access-control cases", pytest_cmd("tests/test_retention_jobs.py"), timeout=240),
            ],
        ),
        Item(
            4,
            "Officer chỉ thấy case/support/domain của mình",
            [
                Check("live support domain routing", pytest_cmd("tests/test_live_support_integration.py"), timeout=240),
                Check("officer proposal domain guard", pytest_cmd("tests/test_officer_document_proposals.py"), timeout=180),
            ],
        ),
        Item(
            5,
            "Admin mở dữ liệu nhạy cảm phải có reason và audit",
            [
                Check("admin sensitive access audit", pytest_cmd("tests/test_retention_jobs.py"), timeout=240),
            ],
        ),
        Item(
            6,
            "Citation mở đúng internal document và highlight điều luật",
            [
                Check("legal PDF/citation pipeline", pytest_cmd("tests/test_legal_pdf_pipeline.py", "tests/test_form_recommendation_and_citations.py"), timeout=240),
                Check("frontend document viewer utils", ["npm", "run", "test", "--", "src/components/legal/legal-document-viewer-utils.test.ts"], cwd=ROOT / "frontend"),
            ],
        ),
        Item(
            7,
            "PDF tiếng Việt đọc đúng dấu, tải không lag",
            [
                Check("legal PDF pipeline", pytest_cmd("tests/test_legal_pdf_pipeline.py"), timeout=240),
            ],
        ),
        Item(
            8,
            "FAQ hai cột, 50 FAQ, form đúng ý định và tối đa 3",
            [
                Check("FAQ API and form limit", pytest_cmd("tests/test_faq_api.py", "tests/test_form_recommendation_and_citations.py"), timeout=240),
            ],
        ),
        Item(
            9,
            "Live support route đúng 5 domain",
            [
                Check("live support integration", pytest_cmd("tests/test_live_support_integration.py"), timeout=240),
            ],
        ),
        Item(
            10,
            "Officer proposal/crawler/OCR candidate chưa approved không xuất hiện trong Ask",
            [
                Check(
                    "candidate first crawler/OCR guards",
                    pytest_cmd(
                        "tests/test_vbpl_candidate_crawler.py",
                        "tests/test_candidate_ocr.py",
                        "tests/test_approved_import_guard.py",
                        "tests/test_officer_document_proposals.py",
                    ),
                    timeout=300,
                ),
            ],
        ),
        Item(
            11,
            "Admin approve mới import/embed/retrieval",
            [
                Check("review workflow approve/import", pytest_cmd("tests/test_review_workflow.py"), timeout=300),
                Check("runtime approve/embed smoke", [sys.executable, "scripts/e2e_crawl_embed_verify.py"], timeout=900, runtime=True),
            ],
        ),
        Item(
            12,
            "Retention 12 tháng/6 tháng/24 tháng",
            [
                Check("retention jobs", pytest_cmd("tests/test_retention_jobs.py"), timeout=240),
            ],
        ),
        Item(
            13,
            "Voice/upload/PII/domain mismatch/admin model không regress",
            [
                Check("voice and upload guards", pytest_cmd("tests/test_voice_input.py", "tests/test_multimodal_uploads.py"), timeout=180),
                Check("runtime B8 legal role/domain regression", [sys.executable, "scripts/e2e_step8_regression.py"], timeout=900, runtime=True),
            ],
        ),
        Item(
            14,
            "Frontend production build pass",
            [
                Check("frontend unit tests", ["npm", "run", "test"], cwd=ROOT / "frontend", timeout=240),
                *(
                    [Check("frontend production build", ["npm", "run", "build"], cwd=ROOT / "frontend", timeout=600, build=True)]
                    if include_build
                    else []
                ),
            ],
        ),
    ]


def summarize_item(checks: Iterable[dict]) -> str:
    statuses = [check["status"] for check in checks]
    if all(status == "PASS" for status in statuses):
        return "PASS"
    if any(status == "FAIL" for status in statuses):
        return "FAIL"
    if any(status == "TIMEOUT" for status in statuses):
        return "TIMEOUT"
    if any(status == "ENV_FAIL" for status in statuses):
        return "ENV_FAIL"
    return "UNKNOWN"


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# Step 19 Regression Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- API: `{report['api_url']}`",
        f"- API health: `{report['api_health']['status']}` ({report['api_health']['detail']})",
        f"- Overall: `{report['overall_status']}`",
        "",
        "| Item | Status | Title | Failed/blocked checks |",
        "| --- | --- | --- | --- |",
    ]
    for item in report["items"]:
        blocked = [
            f"{check['name']} ({check['status']})"
            for check in item["checks"]
            if check["status"] != "PASS"
        ]
        lines.append(
            f"| {item['number']} | `{item['status']}` | {item['title']} | "
            f"{'; '.join(blocked) if blocked else '-'} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.append("- `ENV_FAIL` means the local runtime needed for that item was not reachable.")
    lines.append("- Logs for each command are under the sibling log directory referenced by the JSON report.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Do not run frontend production build.")
    parser.add_argument("--skip-runtime", action="store_true", help="Mark runtime-only checks as ENV_FAIL without probing API.")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logs_dir = REPORT_DIR / f"step19_logs_{run_id}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    health_ok, health_detail = (False, "runtime checks skipped") if args.skip_runtime else api_healthy()
    report = {
        "generated_at": utcnow(),
        "api_url": API_URL,
        "api_health": {"ok": health_ok, "status": "PASS" if health_ok else "ENV_FAIL", "detail": health_detail},
        "items": [],
    }

    for item in build_items(include_build=not args.skip_build):
        item_checks: list[dict] = []
        for check in item.checks:
            if check.runtime and not health_ok:
                item_checks.append(
                    {
                        "name": check.name,
                        "status": "ENV_FAIL",
                        "detail": f"API runtime unavailable: {health_detail}",
                        "command": " ".join(check.command or []),
                    }
                )
                print(f"[ENV_FAIL] {item.number}. {check.name}: {health_detail}")
                continue
            result = run_command(check, logs_dir)
            item_checks.append(result)
            print(f"[{result['status']}] {item.number}. {check.name}")
        report["items"].append(
            {
                "number": item.number,
                "title": item.title,
                "status": summarize_item(item_checks),
                "checks": item_checks,
            }
        )

    statuses = [item["status"] for item in report["items"]]
    report["overall_status"] = "PASS" if all(status == "PASS" for status in statuses) else "FAIL"
    report["summary"] = {
        status: statuses.count(status)
        for status in sorted(set(statuses))
    }

    json_path = REPORT_DIR / f"step19_regression_{run_id}.json"
    md_path = REPORT_DIR / f"step19_regression_{run_id}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, md_path)
    latest_json = REPORT_DIR / "step19_regression_latest.json"
    latest_md = REPORT_DIR / "step19_regression_latest.md"
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(report, latest_md)

    print(json.dumps({"overall_status": report["overall_status"], "summary": report["summary"], "report": str(md_path)}, ensure_ascii=False, indent=2))
    return 0 if report["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
