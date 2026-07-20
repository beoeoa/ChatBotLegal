"""Repeatable pilot UAT API smoke, load and access-control checks.

Uses only synthetic queries and the pilot bearer test credential. It never prints
or persists the credential. The output is evidence for UAT steps 26-27, not a
replacement for two-browser realtime or human quality scoring.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = os.getenv("UAT_API_URL", "http://127.0.0.1:5055").rstrip("/")
REPORT = ROOT / "notebook_data" / "uat" / "load-smoke-report.json"
SECURITY_REPORT = ROOT / "notebook_data" / "uat" / "security-report.json"


def _require_token() -> str:
    token = str(os.getenv("UAT_BEARER_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Set UAT_BEARER_TOKEN before running the live UAT smoke")
    return token


def headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_require_token()}", "X-User-Role": role}


async def timed_get(client: httpx.AsyncClient, url: str, role: str, custom_headers: dict[str, str] | None = None) -> dict:
    started = time.perf_counter()
    try:
        response = await client.get(url, headers=custom_headers or headers(role))
        return {"status": response.status_code, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:
        return {"status": 0, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "error": type(exc).__name__}


async def timed_search(client: httpx.AsyncClient, question: str) -> dict:
    started = time.perf_counter()
    try:
        response = await client.post(
            f"{API}/api/search",
            headers=headers("citizen"),
            json={"query": question, "type": "text", "limit": 3},
        )
        return {"status": response.status_code, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
    except Exception as exc:
        return {"status": 0, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "error": type(exc).__name__}


async def login_officer(client: httpx.AsyncClient, item: dict) -> dict[str, str] | None:
    try:
        response = await client.post(
            f"{API}/api/auth/login",
            json={"identifier": item.get("username", ""), "password": item.get("password", ""), "role": "officer"},
        )
        payload = response.json() if response.status_code == 200 else {}
        token = payload.get("token")
        return {"Authorization": f"Bearer {token}", "X-User-Role": "officer"} if token else None
    except Exception:
        return None


async def main() -> int:
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        health = await timed_get(client, f"{API}/health", "citizen")
        metrics = await timed_get(client, f"{API}/api/admin/control/metrics", "admin")
        quality = await timed_get(client, f"{API}/api/legal/quality/runtime", "admin")
        credential_path = ROOT / "data" / "private" / "bootstrap_officer_credentials.json"
        credentials = json.loads(credential_path.read_text(encoding="utf-8")).get("credentials", []) if credential_path.exists() else []
        officer_headers = await asyncio.gather(*(login_officer(client, item) for item in credentials[:5]))
        queue = await asyncio.gather(*(
            timed_get(client, f"{API}/api/support/tickets?status=open", "officer", custom_headers=item)
            for item in officer_headers if item
        ))
        questions = [
            "đăng ký khai sinh tại phường cần gì",
            "hồ sơ chứng thực chữ ký",
            "xin giấy phép xây dựng nhà ở",
            "đăng ký cư trú cần những bước nào",
            "trợ cấp xã hội nộp ở đâu",
            "xử lý lấn chiếm vỉa hè",
            "thủ tục kết hôn",
            "cấp lại căn cước",
            "đăng ký biến động đất đai",
            "hồ sơ bảo hiểm y tế",
        ]
        searches = await asyncio.gather(*(timed_search(client, q) for q in questions))

        protected = [
            ("citizen_admin_users", f"{API}/api/admin/control/users", "citizen"),
            ("citizen_credentials", f"{API}/api/credentials", "citizen"),
            ("officer_admin_users", f"{API}/api/admin/control/users", "officer"),
            ("officer_credentials", f"{API}/api/credentials", "officer"),
        ]
        security_rows = []
        for name, url, role in protected:
            result = await timed_get(client, url, role)
            security_rows.append({"name": name, "role": role, "status": result["status"], "pass": result["status"] in (401, 403)})

        admin_users = await timed_get(client, f"{API}/api/admin/control/users", "admin")

    latencies = [item["latency_ms"] for item in searches if item.get("status") == 200]
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[max(0, int(len(sorted_latencies) * 0.95) - 1)] if sorted_latencies else None
    load_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api": API,
        "synthetic_only": True,
        "health": health,
        "admin_metrics": metrics,
        "quality_runtime": quality,
        "officer_queue": queue,
        "citizen_search": searches,
        "summary": {
            "concurrent_citizen_requests": len(searches),
            "successful_searches": sum(item.get("status") == 200 for item in searches),
            "error_rate": round(sum(item.get("status") != 200 for item in searches) / len(searches), 4),
            "p50_ms": round(statistics.median(latencies), 2) if latencies else None,
            "p95_ms": round(p95, 2) if p95 is not None else None,
            "five_officer_queue_requests": sum(item.get("status") == 200 for item in queue),
            "officer_login_count": len([item for item in officer_headers if item]),
            "admin_metrics_status": metrics["status"],
        },
    }
    security_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api": API,
        "synthetic_only": True,
        "checks": security_rows,
        "admin_users_status": admin_users["status"],
        "no_credentials_persisted": True,
        "all_access_checks_pass": all(item["pass"] for item in security_rows),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(load_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SECURITY_REPORT.write_text(json.dumps(security_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"load_report": str(REPORT), "security_report": str(SECURITY_REPORT), "load_summary": load_report["summary"], "security": security_report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if load_report["summary"]["error_rate"] == 0 and security_report["all_access_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
