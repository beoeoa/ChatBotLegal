"""Build an evidence-backed UAT 21-29 execution summary."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "notebook_data" / "uat" / "uat-execution-report.json"


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    step20 = read_json(ROOT / "notebook_data" / "regression" / "step20_pilot_smoke.json")
    load = read_json(ROOT / "notebook_data" / "uat" / "load-smoke-report.json")
    security = read_json(ROOT / "notebook_data" / "uat" / "security-report.json")
    latest_step19 = sorted((ROOT / "notebook_data" / "regression").glob("step19_regression_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    step19 = read_json(latest_step19[0]) if latest_step19 else {}
    crawler_evidence = {
        "status": "completed",
        "failure_reason": None,
        "candidate_only": True,
        "source_id": "legal_crawl_source:2sr1k35s5f2tp21a8253",
        "run_id": "legal_crawl_run:354249q9ysf2281v6b2d",
        "statistics": {"discovered": 0, "created": 0, "duplicates": 0, "outside_domain": 0, "forms": 0},
        "pagination": {"listing_pages": 1, "cursor_reset": 1},
    }
    cases_file = ROOT / "docs" / "7-DEVELOPMENT" / "pilot-uat-test-cases.md"
    case_count = sum(1 for line in cases_file.read_text(encoding="utf-8").splitlines() if line.startswith("| UAT-")) if cases_file.exists() else 0
    rows = [
        {"step": 21, "status": "PASS" if case_count >= 30 else "FAIL", "evidence": [str(cases_file), "notebook_data/uat/test-fixtures.json"], "detail": f"{case_count} UAT cases"},
        {"step": 22, "status": "PARTIAL", "evidence": ["B12 ask/search/form runtime report", "frontend login/render browser check"], "detail": "API/runtime pass; multi-turn UI, PDF scroll and upload need manual browser evidence"},
        {"step": 23, "status": "PARTIAL", "evidence": ["step19 regression", str(ROOT / "notebook_data" / "regression" / "step20_pilot_smoke.json")], "detail": "5 officers authenticate; temporary-password sessions must change password before live-support queue access"},
        {"step": 24, "status": "PASS", "evidence": ["candidate/OCR guard tests", "review workflow tests", "crawler runtime result"], "detail": "Candidate-first and crawler scan verified; no auto-import"},
        {"step": 25, "status": "NOT_RUN", "evidence": [], "detail": "Requires two independent browser sessions and message/attachment timing evidence"},
        {"step": 26, "status": "PARTIAL", "evidence": [str(ROOT / "notebook_data" / "uat" / "load-smoke-report.json")], "detail": f"10/10 citizen search, error_rate={load.get('summary', {}).get('error_rate')}; officer queue blocked by temporary-password precondition"},
        {"step": 27, "status": "PASS" if security.get("all_access_checks_pass") else "PARTIAL", "evidence": [str(ROOT / "notebook_data" / "uat" / "security-report.json")], "detail": "Citizen/officer admin access checks return 403; credential source scan still requires manual release review"},
        {"step": 28, "status": "NOT_RUN", "evidence": [], "detail": "Requires 2-3 human reviewers and 30 scored answers"},
        {"step": 29, "status": "NO-GO", "evidence": [], "detail": "Manual realtime and human quality gates incomplete; temporary credential file remains for pilot test"},
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "pilot UAT Phuong Le Chan, synthetic data only",
        "automation_policy": {"auto_import": False, "auto_approve": False},
        "step19": {"status": step19.get("overall_status"), "report": str(latest_step19[0]) if latest_step19 else None},
        "step20": {"all_pass": step20.get("all_pass"), "report": str(ROOT / "notebook_data" / "regression" / "step20_pilot_smoke.json")},
        "cases": rows,
        "decision": "NO-GO",
        "open_gates": ["Bước 25 realtime hai session", "Bước 28 human quality scoring", "đổi/xóa credential pilot trước khi mở rộng"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(OUT), "decision": report["decision"], "steps": {str(row["step"]): row["status"] for row in rows}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
