# -*- coding: utf-8 -*-
"""Check viewer, PDF and form URLs referenced by the live 334 run."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx


ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "notebook_data" / "quality_runs" / "live-334-latest.json"
OUTPUT = ROOT / "notebook_data" / "quality_runs" / "asset-health-334.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _collect_urls(payload: dict[str, Any], api_url: str) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for result in payload.get("results") or []:
        for citation in result.get("citations") or []:
            for kind, key in (("viewer", "internal_url"), ("pdf", "pdf_url"), ("source", "source_url")):
                value = str(citation.get(key) or "").strip()
                if value:
                    url = urljoin(api_url.rstrip("/") + "/", value)
                    found[url] = {"kind": kind, "url": url}
        for form in result.get("recommended_forms") or []:
            value = str(form.get("download_url") or "").strip()
            if value:
                url = urljoin(api_url.rstrip("/") + "/", value)
                found[url] = {"kind": "form", "url": url}
    return list(found.values())


def _configure_admin_auth(client: httpx.Client, api_url: str) -> None:
    token = str(
        os.getenv("ASSET_HEALTH_ADMIN_TOKEN")
        or os.getenv("PILOT_ADMIN_TOKEN")
        or ""
    ).strip()
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
        return
    password = str(
        os.getenv("OPEN_NOTEBOOK_ADMIN_PASSWORD")
        or os.getenv("OPEN_NOTEBOOK_PASSWORD")
        or ""
    ).strip()
    if not password:
        raise RuntimeError(
            "Set ASSET_HEALTH_ADMIN_TOKEN or OPEN_NOTEBOOK_ADMIN_PASSWORD "
            "before checking live assets"
        )
    login = client.post(
        urljoin(api_url, "/api/auth/login"),
        json={"password": password, "role": "admin"},
    )
    login.raise_for_status()
    auth_token = login.json().get("token")
    if not auth_token:
        raise RuntimeError("Admin login returned no bearer token")
    client.headers["Authorization"] = f"Bearer {auth_token}"


def check(*, api_url: str, live: dict[str, Any]) -> dict[str, Any]:
    checks = _collect_urls(live, api_url)
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        _configure_admin_auth(client, api_url)
        for item in checks:
            try:
                with client.stream("GET", item["url"]) as response:
                    prefix = next(response.iter_bytes(1024), b"")
                    content_type = response.headers.get("content-type", "")
                    valid = response.status_code < 400 and bool(prefix)
                    if item["kind"] == "pdf":
                        valid = valid and (prefix.startswith(b"%PDF") or "application/pdf" in content_type)
                    results.append({**item, "status_code": response.status_code, "content_type": content_type, "valid": valid})
            except Exception as exc:  # noqa: BLE001
                results.append({**item, "status_code": None, "valid": False, "error": f"{type(exc).__name__}: {exc}"})
    by_kind: dict[str, dict[str, int | float]] = {}
    for kind in {item["kind"] for item in checks}:
        group = [item for item in results if item["kind"] == kind]
        good = sum(1 for item in group if item.get("valid"))
        by_kind[kind] = {"total": len(group), "valid": good, "rate": round(good / max(1, len(group)), 5)}
    return {"total": len(results), "valid": sum(1 for item in results if item.get("valid")), "by_kind": by_kind, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check citation, PDF and form assets from the live 334 run")
    parser.add_argument("--api-url", default=os.getenv("CHATBOT_API_URL", "http://127.0.0.1:5055"))
    parser.add_argument("--live", type=Path, default=LIVE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = check(api_url=args.api_url, live=_read(args.live))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("total", "valid", "by_kind")}, ensure_ascii=False, indent=2))
    rates = [float(item.get("rate") or 0) for item in result["by_kind"].values() if item.get("total")]
    return 0 if result["total"] and all(rate >= 0.99 for rate in rates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
