"""Step 20 smoke test: health, feature rollout, admin, citizen and five officers.

Credential values are never printed. Officer temporary-password login is expected
to return must_change_password=true; that proves first-login enforcement without
consuming the credential supplied to the pilot administrator.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = os.getenv("STEP20_API_URL", "http://127.0.0.1:5055").rstrip("/")
FRONTEND = os.getenv("STEP20_FRONTEND_URL", "http://127.0.0.1:3000")
CREDENTIAL_FILE = ROOT / "data" / "private" / "bootstrap_officer_credentials.json"
REPORT_PATH = ROOT / "notebook_data" / "regression" / "step20_pilot_smoke.json"
EXPECTED = {
    "officer_hotich": "ho_tich_chung_thuc",
    "officer_daidai": "dat_dai_xay_dung",
    "officer_ansinh": "an_sinh_y_te_giao_duc",
    "officer_hanhchinh": "hanh_chinh_cong",
    "officer_trattu": "trat_tu_do_thi",
}


def _require_token(env_name: str) -> str:
    token = str(os.getenv(env_name) or "").strip()
    if not token:
        raise RuntimeError(f"Set {env_name} before running the live pilot smoke")
    return token


def record(rows, name, ok, detail):
    rows.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    rows = []
    admin_token = _require_token("STEP20_ADMIN_TOKEN")
    citizen_token = _require_token("STEP20_CITIZEN_TOKEN")
    with httpx.Client(timeout=30) as client:
        for name, url in (("backend health", f"{API}/health"), ("frontend", FRONTEND)):
            try:
                response = client.get(url)
                record(rows, name, response.status_code < 400, f"HTTP {response.status_code}")
            except Exception as exc:
                record(rows, name, False, f"{type(exc).__name__}: {exc}")

        try:
            flags = json.loads((ROOT / "data" / "pilot" / "feature_flags.json").read_text(encoding="utf-8"))
            enabled = flags.get("flags") or {}
            correct = all(enabled.get(name) is True for name in ("conversations", "document_viewer_pdf", "faq", "support_chat", "proposals_crawler_ocr"))
            safe = enabled.get("auto_import") is False and enabled.get("auto_approve") is False
            record(rows, "pilot flags staged and safe", correct and safe, f"stage={flags.get('stage')} auto_import={enabled.get('auto_import')} auto_approve={enabled.get('auto_approve')}")
        except Exception as exc:
            record(rows, "pilot flags staged and safe", False, str(exc))

        admin_headers = {
            "Authorization": f"Bearer {admin_token}",
            "X-User-Role": "admin",
        }
        try:
            response = client.get(f"{API}/api/admin/control/users", headers=admin_headers)
            users = response.json() if response.status_code == 200 else []
            profiles = {item.get("username"): item for item in users}
            configured = all(
                profiles.get(username, {}).get("allowed_domains") == [domain]
                and profiles.get(username, {}).get("must_change_password") is True
                for username, domain in EXPECTED.items()
            )
            record(rows, "admin sees five least-privilege officers", response.status_code == 200 and configured, f"HTTP {response.status_code}; found={sum(key in profiles for key in EXPECTED)}/5")
        except Exception as exc:
            record(rows, "admin sees five least-privilege officers", False, str(exc))

        try:
            response = client.get(f"{API}/api/legal/quality/summary", headers=admin_headers)
            record(rows, "admin quality dashboard", response.status_code == 200, f"HTTP {response.status_code}")
        except Exception as exc:
            record(rows, "admin quality dashboard", False, str(exc))

        try:
            response = client.post(
                f"{API}/api/search",
                headers={
                    "Authorization": f"Bearer {citizen_token}",
                    "X-User-Role": "citizen",
                },
                json={"query": "đăng ký khai sinh", "type": "text", "limit": 3},
            )
            record(rows, "citizen legal search", response.status_code == 200, f"HTTP {response.status_code}")
        except Exception as exc:
            record(rows, "citizen legal search", False, str(exc))

        if not CREDENTIAL_FILE.exists():
            record(rows, "five officer first-login authentication", False, "bootstrap credential file missing")
        else:
            credentials = json.loads(CREDENTIAL_FILE.read_text(encoding="utf-8")).get("credentials") or []
            by_username = {item.get("username"): item.get("password") for item in credentials}
            all_ok = True
            for username in EXPECTED:
                password = by_username.get(username)
                try:
                    response = client.post(f"{API}/api/auth/login", json={"identifier": username, "password": password or "", "role": "officer"})
                    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    okay = response.status_code == 200 and data.get("must_change_password") is True
                    all_ok = all_ok and okay
                except Exception:
                    all_ok = False
            record(rows, "five officer first-login authentication", all_ok, "5 temporary credentials verified; passwords not printed")

    passed = sum(row["status"] == "PASS" for row in rows)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "api": API, "frontend": FRONTEND, "passed": passed, "total": len(rows), "all_pass": passed == len(rows), "results": rows}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {REPORT_PATH}")
    return 0 if report["all_pass"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
