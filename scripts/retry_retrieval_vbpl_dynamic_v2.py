#!/usr/bin/env python3
"""Render HTTP-200 VBPL shells and verify their actual legal content.

This is an evidence-only retry for dynamic pages.  It does not update the
source audit, database or serving state; a separate versioned merge is needed
after review.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.audit_retrieval_source_content_v2 import classify_html_identity, load_json
from scripts.audit_retrieval_source_content_v2 import summarize_verification


DEFAULT_INPUT = ROOT / "reports" / "retrieval-release-v2" / "source-content-audit-12236-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "source-content-dynamic-retry-v4.json"
DEFAULT_BROWSER = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SELECTORS = ("div.preview-content", "#toanvancontent", ".prov-content", "main")


def _text_from_html(fragment: str) -> str:
    soup = BeautifulSoup(str(fragment or ""), "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    return " ".join(soup.stripped_strings)


async def _render_one(context, record: dict[str, Any]) -> dict[str, Any]:
    page = await context.new_page()
    url = str(record.get("source_url") or "")
    base = {
        "document_id": record.get("document_id"),
        "law_number": record.get("law_number"),
        "source_url": url,
        "dynamic_transport_status": "render_failed",
        "dynamic_verification_status": "rejected",
        "dynamic_identity_verified": False,
    }
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1_500)
        selected_html = ""
        selected_selector = ""
        for selector in SELECTORS:
            locator = page.locator(selector).first
            try:
                if await locator.count() == 0:
                    continue
                candidate = await locator.inner_html(timeout=5_000)
            except Exception:
                continue
            text = _text_from_html(candidate)
            if len(text) >= 200 and any(marker in text.casefold() for marker in ("điều", "chương", "căn cứ", "quy định")):
                selected_html = candidate
                selected_selector = selector
                break
        if not selected_html:
            body_html = await page.locator("body").inner_html(timeout=5_000)
            body_text = _text_from_html(body_html)
            if len(body_text) >= 1_000 and any(marker in body_text.casefold() for marker in ("điều 1", "chương i", "cộng hòa xã hội chủ nghĩa việt nam")):
                selected_html = body_html
                selected_selector = "body"
        final_url = str(page.url)
        title = await page.title()
        base.update({
            "dynamic_transport_status": "rendered",
            "final_url": final_url,
            "page_title": title[:500],
            "selected_selector": selected_selector,
        })
        if not selected_html:
            base["dynamic_reason"] = "legal_content_not_rendered"
            return base
        body_text = _text_from_html(selected_html)
        identity = classify_html_identity(
            selected_html,
            law_number=record.get("law_number"),
            title=record.get("title"),
            issuing_agency=record.get("issuing_agency"),
        )
        base.update({
            "dynamic_verification_status": identity["verification_status"],
            "dynamic_identity_verified": bool(identity["identity_verified"]),
            "dynamic_identity_evidence": identity,
            "dynamic_content_sha256": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
            "dynamic_visible_characters": len(body_text),
        })
        return base
    except Exception as exc:
        base["dynamic_reason"] = type(exc).__name__
        return base
    finally:
        await page.close()


async def _run(records: list[dict[str, Any]], browser_path: Path, workers: int) -> list[dict[str, Any]]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=str(browser_path),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        semaphore = asyncio.Semaphore(max(1, min(int(workers), 8)))
        completed = 0
        lock = asyncio.Lock()

        async def guarded(record: dict[str, Any]) -> dict[str, Any]:
            nonlocal completed
            async with semaphore:
                result = await _render_one(context, record)
            async with lock:
                completed += 1
                if completed % 10 == 0:
                    print(json.dumps({"stage": "vbpl_dynamic_retry", "completed": completed, "total": len(records)}), flush=True)
            return result

        results = await asyncio.gather(*(guarded(record) for record in records))
        await context.close()
        await browser.close()
        return results


def build_report(*, input_path: Path, output_path: Path, browser_path: Path, workers: int = 4, limit: int | None = None) -> dict[str, Any]:
    source_report = load_json(input_path)
    records = [
        dict(row)
        for row in source_report.get("records") or []
        if int((row.get("transport_observation") or {}).get("status_code") or 0) == 200
        and row.get("verification_status") != "verified"
        and (urlparse(str(row.get("source_url") or "")).hostname or "").lower().endswith("vbpl.vn")
    ]
    if limit is not None:
        records = records[: max(0, int(limit))]
    results = asyncio.run(_run(records, browser_path, workers))
    status_counts = Counter(str(row.get("dynamic_verification_status") or "") for row in results)
    report = {
        "schema_version": "legal-retrieval-source-content-dynamic-retry-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_report_sha256": file_sha256(input_path),
        "source_snapshot_sha256": source_report.get("source_snapshot_sha256"),
        "active_pointer": source_report.get("active_pointer"),
        "candidate_count": len(records),
        "browser_path": str(browser_path),
        "summary": {
            "candidate_count": len(records),
            "dynamic_verification_status_counts": dict(sorted(status_counts.items())),
            "dynamic_verified_count": int(status_counts["verified"]),
        },
        "records": sorted(results, key=lambda row: int(row.get("document_id") or 0)),
        "legal_review_required": True,
        "approved_for_serving": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii")
    return report


def merge_dynamic_evidence(*, base_path: Path, dynamic_path: Path, output_path: Path) -> dict[str, Any]:
    """Create an immutable v3 source audit with dynamic evidence merged in."""

    base = load_json(base_path)
    dynamic = load_json(dynamic_path)
    dynamic_by_id = {int(row["document_id"]): row for row in dynamic.get("records") or []}
    merged: list[dict[str, Any]] = []
    for original in base.get("records") or []:
        record = dict(original)
        retry = dynamic_by_id.get(int(record["document_id"]))
        if retry:
            record["dynamic_retry_evidence"] = retry
            dynamic_status = str(retry.get("dynamic_verification_status") or "")
            if dynamic_status == "verified":
                record["verification_status"] = "verified"
                record["identity_verified"] = True
                record["identity_evidence"] = retry.get("dynamic_identity_evidence") or {}
                record["source_content_evidence"] = {
                    "verified": True,
                    "content_sha256": retry.get("dynamic_content_sha256"),
                    "reason": "dynamic_browser_render_identity_verified",
                    "selected_selector": retry.get("selected_selector"),
                }
            elif dynamic_status == "needs_review":
                record["verification_status"] = "needs_review"
                record["identity_verified"] = False
                record["identity_evidence"] = retry.get("dynamic_identity_evidence") or {}
                record["source_content_evidence"] = {
                    "verified": False,
                    "reason": "dynamic_identity_needs_review",
                    "selected_selector": retry.get("selected_selector"),
                }
            else:
                record["verification_status"] = "rejected"
                record["identity_verified"] = False
                record["source_content_evidence"] = {
                    "verified": False,
                    "reason": retry.get("dynamic_reason") or "dynamic_content_not_rendered",
                }
        merged.append(record)
    merged.sort(key=lambda row: int(row["document_id"]))
    if len(merged) != 12_236 or len({int(row["document_id"]) for row in merged}) != 12_236:
        raise RuntimeError("merged_source_audit_partition_invalid")
    report = {
        "schema_version": "legal-retrieval-source-content-audit-v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_report_sha256": file_sha256(base_path),
        "dynamic_retry_report_sha256": file_sha256(dynamic_path),
        "source_snapshot_sha256": base.get("source_snapshot_sha256"),
        "inventory_document_count": 12_236,
        "legal_as_of": base.get("legal_as_of"),
        "active_pointer": base.get("active_pointer"),
        "summary": summarize_verification(merged),
        "records": merged,
        "legal_review_required": True,
        "approved_for_serving": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_path.with_suffix(output_path.suffix + ".sha256").write_text(f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--browser", type=Path, default=DEFAULT_BROWSER)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--merge-base", type=Path, default=None)
    parser.add_argument("--merge-output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = build_report(input_path=args.input.resolve(), output_path=args.output.resolve(), browser_path=args.browser.resolve(), workers=args.workers, limit=args.limit)
    if args.merge_base:
        merge_output = (args.merge_output or args.output.with_name("source-content-audit-12236-v5.json")).resolve()
        merged = merge_dynamic_evidence(base_path=args.merge_base.resolve(), dynamic_path=args.output.resolve(), output_path=merge_output)
        report["merged_output"] = str(merge_output)
        report["merged_summary"] = merged["summary"]
    print(json.dumps({"status": "EVIDENCE_ONLY", "summary": report["summary"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
