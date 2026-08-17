"""Reconcile Feature 018 requirements/tasks with authoritative local evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURE = ROOT / "specs/018-production-release-readiness"
REPORTS = ROOT / "reports/feature018"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    task_text = (FEATURE / "tasks.md").read_text(encoding="utf-8")
    tasks = [
        {"task_id": task_id, "completed": mark.casefold() == "x", "description": description.strip()}
        for mark, task_id, description in re.findall(r"^- \[([ xX])\] (T\d{3})\s*(.*)$", task_text, re.MULTILINE)
    ]
    spec_text = (FEATURE / "spec.md").read_text(encoding="utf-8")
    requirements = [
        {"requirement_id": requirement_id, "text": text.strip()}
        for requirement_id, text in re.findall(r"\*\*(FR-\d{3})\*\*:\s*(.+)", spec_text)
    ]
    go_no_go = json.loads((REPORTS / "go-no-go.json").read_text(encoding="utf-8"))
    quality = json.loads((REPORTS / "quality-gates.json").read_text(encoding="utf-8"))
    artifacts = [
        REPORTS / "capability-audit.json",
        REPORTS / "local-gates.json",
        REPORTS / "quality-gates.json",
        REPORTS / "restore-rehearsal.json",
        REPORTS / "support-load.json",
        REPORTS / "go-no-go.json",
    ]
    completed = sum(bool(item["completed"]) for item in tasks)
    open_blockers = [
        "live DeepSeek V2 quality gate failed at 734/1,000 passed with 73 non-200 responses",
        "100-concurrent-Ask load is evidenced but failed: client P95 184.962 seconds and HTTP success 927/1,000",
        "browser UAT was approved and started, but Browser URL policy blocked continuation after localhost service recovery",
        "staging/canary/rollback smoke/72-hour observation cannot start while Go/No-Go is NO-GO and no staging target is configured",
    ]
    if go_no_go.get("signature_status") != "signed":
        open_blockers.append(
            "Go/No-Go report is unsigned; an external release-owner Ed25519 key and signer identity are required"
        )
    report = {
        "schema_version": "feature018-completion-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": go_no_go["release_id"],
        "release_fingerprint": go_no_go["release_fingerprint"],
        "status": "complete" if completed == len(tasks) and go_no_go["decision"] == "GO" else "incomplete",
        "production_ready": go_no_go["decision"] == "GO",
        "task_summary": {"total": len(tasks), "completed": completed, "open": len(tasks) - completed},
        "tasks": tasks,
        "requirements": requirements,
        "release_gates": {
            "decision": go_no_go["decision"],
            "passed": go_no_go["passed_gates"],
            "failed": go_no_go["failed_gates"],
            "missing": go_no_go["missing_gates"],
        },
        "quality_passed": bool(quality.get("passed")),
        "authoritative_artifacts": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": sha(path)} for path in artifacts
        ],
        "open_blockers": open_blockers,
        "safety": {
            "live_migration_applied": False,
            "production_pointer_switched": False,
            "vector_store_mutated": False,
            "browser_uat_run": True,
            "canary_started": False,
        },
    }
    output = REPORTS / "completion-audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["task_summary"]}))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
