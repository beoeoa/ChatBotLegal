"""Audit and download legal form files without generating fake fallbacks.

The crawler only downloads files that are publicly reachable from the form detail
page. Login pages, HTML responses renamed as documents, and malformed files are
rejected and recorded for admin review.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "notebook_data" / "forms" / "forms_manifest.json"
STATUS_PATH = ROOT / "notebook_data" / "forms" / "forms_download_status.json"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official"
BROWSER_DOWNLOAD_DIR = ROOT / "notebook_data" / "forms" / "browser_downloads"
LEGACY_DIR = ROOT / "data" / "uploads" / "forms"
QUARANTINE_DIR = ROOT / "data" / "quarantine" / "forms_synthetic"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

SYNTHETIC_MARKERS = (
    "BIỂU MẪU CHUẨN THỦ TỤC",
    "BIỂU MẪU HÀNH CHÍNH THỰC TẾ",
    "BIỂU MẪU HÀNH CHÍNH (MÃ SỐ TVPL:",
    "NỘI DUNG KIẾN NGHỊ / ĐỀ XUẤT GIẢI QUYẾT",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def flatten_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain, forms in (manifest.get("forms") or {}).items():
        for form in forms or []:
            rows.append({**form, "domain": form.get("domain") or domain})
    return rows


def normalized_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value or "").strip("-._")
    return cleaned[:120] or fallback


def extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    raw = re.sub(r"</w:p>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw)


def is_synthetic_file(path: Path) -> bool:
    if path.suffix.lower() != ".docx":
        return False
    text = extract_docx_text(path)
    return any(marker in text for marker in SYNTHETIC_MARKERS)


def quarantine_synthetic_files(move_files: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not LEGACY_DIR.exists():
        return results

    resolved_legacy = LEGACY_DIR.resolve()
    resolved_quarantine = QUARANTINE_DIR.resolve()
    if ROOT.resolve() not in resolved_legacy.parents:
        raise RuntimeError(f"Unsafe legacy form directory: {resolved_legacy}")
    if ROOT.resolve() not in resolved_quarantine.parents:
        raise RuntimeError(f"Unsafe quarantine directory: {resolved_quarantine}")

    for path in sorted(LEGACY_DIR.glob("*")):
        if not path.is_file() or not is_synthetic_file(path):
            continue
        result = {
            "source_path": str(path.relative_to(ROOT)),
            "status": "synthetic_detected",
        }
        if move_files:
            QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
            destination = QUARANTINE_DIR / path.name
            if destination.exists():
                destination = QUARANTINE_DIR / f"{path.stem}-{int(time.time())}{path.suffix}"
            shutil.move(str(path), str(destination))
            result.update(
                {
                    "status": "synthetic_quarantined",
                    "quarantine_path": str(destination.relative_to(ROOT)),
                }
            )
        results.append(result)
    return results


def detect_file_type(content: bytes, content_type: str, url: str) -> str | None:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        try:
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
            if "[Content_Types].xml" in names and "word/document.xml" in names:
                return "docx"
        except zipfile.BadZipFile:
            return None
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return "doc"

    lowered_type = (content_type or "").lower()
    suffix = Path(urlparse(url).path).suffix.lower()
    if "html" in lowered_type or content.lstrip().startswith((b"<!DOCTYPE", b"<html")):
        return None
    if suffix in {".pdf", ".doc", ".docx"}:
        return suffix.lstrip(".")
    return None


def candidate_download_urls(detail_url: str, page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    candidates: list[str] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        label = anchor.get_text(" ", strip=True).casefold()
        href_lower = href.casefold()
        if not href:
            continue
        is_document = any(token in href_lower for token in (".docx", ".doc", ".pdf"))
        is_download = "download" in href_lower or "tải" in label or "download" in label
        if is_document or is_download:
            candidates.append(urljoin(detail_url, href))

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(candidates))


def existing_verified_file(record: dict[str, Any]) -> bool:
    relative_path = record.get("local_path")
    if not relative_path:
        return False
    path = ROOT / str(relative_path)
    if not path.exists():
        return False
    content = path.read_bytes()
    return detect_file_type(content, record.get("content_type") or "", record.get("download_url") or "") is not None


def store_browser_download(
    form: dict[str, Any],
    downloaded_files: list[str],
    base_record: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate a browser download and move it into the official form store."""
    invalid_files: list[str] = []
    for raw_path in downloaded_files:
        path = Path(raw_path).resolve()
        if not path.is_file():
            invalid_files.append(f"{raw_path}: file_not_found")
            continue

        content = path.read_bytes()
        file_type = detect_file_type(content, "", path.name)
        if not file_type:
            invalid_files.append(f"{path.name}: invalid_or_html_response")
            continue

        form_id = str(form.get("id") or "").strip()
        domain = normalized_filename(str(form.get("domain") or ""), "unclassified")
        slug = normalized_filename(str(form.get("slug") or ""), form_id)
        destination_dir = DOWNLOAD_DIR / domain
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{form_id}-{slug}.{file_type}"
        shutil.move(str(path), str(destination))
        return {
            **base_record,
            "status": "downloaded_verified",
            "download_url": str(form.get("full_url") or ""),
            "local_path": str(destination.relative_to(ROOT)),
            "file_type": file_type,
            "content_type": "",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "download_method": "crawl4ai_browser_click",
        }

    if invalid_files:
        return {
            **base_record,
            "status": "invalid_download",
            "errors": invalid_files[:10],
            "download_method": "crawl4ai_browser_click",
        }
    return None


def download_one(
    client: httpx.Client,
    form: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    form_id = str(form.get("id") or "").strip()
    detail_url = str(form.get("full_url") or "").strip()
    base_record = {
        "id": form_id,
        "title": form.get("title"),
        "domain": form.get("domain"),
        "detail_url": detail_url,
        "checked_at": utcnow(),
    }
    if previous and existing_verified_file(previous):
        return {**previous, **base_record, "status": "downloaded_verified"}
    if not detail_url:
        return {**base_record, "status": "invalid_metadata", "error": "Missing detail URL"}

    try:
        page_response = client.get(detail_url, timeout=30)
    except httpx.HTTPError as exc:
        return {**base_record, "status": "detail_request_failed", "error": str(exc)}

    if page_response.status_code in {401, 403}:
        return {
            **base_record,
            "status": "access_required",
            "http_status": page_response.status_code,
        }
    if page_response.status_code == 429:
        return {
            **base_record,
            "status": "rate_limited",
            "http_status": page_response.status_code,
            "retry_after": page_response.headers.get("retry-after"),
        }
    if page_response.status_code >= 400:
        return {
            **base_record,
            "status": "detail_request_failed",
            "http_status": page_response.status_code,
        }

    urls = candidate_download_urls(str(page_response.url), page_response.text)
    if not urls:
        return {**base_record, "status": "no_public_download_link"}

    invalid_reasons: list[str] = []
    for download_url in urls:
        try:
            response = client.get(
                download_url,
                headers={"Referer": str(page_response.url)},
                timeout=60,
            )
        except httpx.HTTPError as exc:
            invalid_reasons.append(f"{download_url}: {exc}")
            continue
        if response.status_code in {401, 403}:
            invalid_reasons.append(f"{download_url}: access_required")
            continue
        if response.status_code >= 400:
            invalid_reasons.append(f"{download_url}: HTTP {response.status_code}")
            continue

        file_type = detect_file_type(
            response.content,
            response.headers.get("content-type", ""),
            str(response.url),
        )
        if not file_type:
            invalid_reasons.append(f"{download_url}: invalid_or_html_response")
            continue

        domain = normalized_filename(str(form.get("domain") or ""), "unclassified")
        slug = normalized_filename(str(form.get("slug") or ""), form_id)
        destination_dir = DOWNLOAD_DIR / domain
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{form_id}-{slug}.{file_type}"
        destination.write_bytes(response.content)
        return {
            **base_record,
            "status": "downloaded_verified",
            "download_url": str(response.url),
            "local_path": str(destination.relative_to(ROOT)),
            "file_type": file_type,
            "content_type": response.headers.get("content-type"),
            "size_bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }

    status = "access_required" if any("access_required" in item for item in invalid_reasons) else "invalid_download"
    return {
        **base_record,
        "status": status,
        "candidate_download_urls": urls,
        "errors": invalid_reasons[:10],
    }


def download_from_discovered_urls(
    client: httpx.Client,
    form: dict[str, Any],
    urls: list[str],
    page_url: str,
) -> dict[str, Any]:
    form_id = str(form.get("id") or "").strip()
    base_record = {
        "id": form_id,
        "title": form.get("title"),
        "domain": form.get("domain"),
        "detail_url": form.get("full_url"),
        "checked_at": utcnow(),
        "crawler": "crawl4ai",
    }
    errors: list[str] = []
    for download_url in urls:
        try:
            response = client.get(
                download_url,
                headers={"Referer": page_url},
                timeout=60,
            )
        except httpx.HTTPError as exc:
            errors.append(f"{download_url}: {exc}")
            continue
        if response.status_code in {401, 403}:
            errors.append(f"{download_url}: access_required")
            continue
        if response.status_code == 429:
            errors.append(f"{download_url}: rate_limited")
            continue
        if response.status_code >= 400:
            errors.append(f"{download_url}: HTTP {response.status_code}")
            continue

        file_type = detect_file_type(
            response.content,
            response.headers.get("content-type", ""),
            str(response.url),
        )
        if not file_type:
            errors.append(f"{download_url}: invalid_or_html_response")
            continue

        domain = normalized_filename(str(form.get("domain") or ""), "unclassified")
        slug = normalized_filename(str(form.get("slug") or ""), form_id)
        destination_dir = DOWNLOAD_DIR / domain
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{form_id}-{slug}.{file_type}"
        destination.write_bytes(response.content)
        return {
            **base_record,
            "status": "downloaded_verified",
            "download_url": str(response.url),
            "local_path": str(destination.relative_to(ROOT)),
            "file_type": file_type,
            "content_type": response.headers.get("content-type"),
            "size_bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }

    status = "access_required" if any("access_required" in item for item in errors) else "invalid_download"
    if any("rate_limited" in item for item in errors):
        status = "rate_limited"
    return {
        **base_record,
        "status": status,
        "candidate_download_urls": urls,
        "errors": errors[:10],
    }


async def run_crawl4ai(
    forms: list[dict[str, Any]],
    prior_records: dict[str, dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    profile_dir = Path(args.profile_dir).resolve() if args.profile_dir else None
    BROWSER_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    browser_config = BrowserConfig(
        headless=not args.interactive,
        verbose=False,
        user_data_dir=str(profile_dir) if profile_dir else None,
        use_managed_browser=bool(profile_dir),
        accept_downloads=True,
        downloads_path=str(BROWSER_DOWNLOAD_DIR),
    )
    results = dict(prior_records)

    with httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"},
    ) as download_client:
        async with AsyncWebCrawler(config=browser_config, verbose=False) as crawler:
            for index, form in enumerate(forms, start=1):
                form_id = str(form.get("id") or "")
                detail_url = str(form.get("full_url") or "")
                base_record = {
                    "id": form_id,
                    "title": form.get("title"),
                    "domain": form.get("domain"),
                    "detail_url": detail_url,
                    "checked_at": utcnow(),
                    "crawler": "crawl4ai",
                }
                try:
                    # TVPL exposes the real file through a JavaScript click. Keep
                    # that click in the authenticated browser so session cookies
                    # are not lost by a separate HTTP client.
                    wait_seconds = (
                        args.interactive_wait
                        if args.interactive and index == 1
                        else 1.5
                    )
                    click_download = """
                    await (async () => {
                      const nodes = Array.from(document.querySelectorAll('a, button'));
                      const target = nodes.find((node) => {
                        const text = (node.innerText || node.textContent || '').trim().toLowerCase();
                        const visible = !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                        return visible && (text === 'tải về' || text.startsWith('tải về '));
                      });
                      if (target) {
                        target.click();
                        await new Promise((resolve) => setTimeout(resolve, 5000));
                      }
                    })();
                    """
                    run_config = CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS,
                        page_timeout=max(30_000, int(wait_seconds * 1000) + 20_000),
                        delay_before_return_html=wait_seconds,
                        js_code=click_download,
                    )
                    crawl_result = await crawler.arun(url=detail_url, config=run_config)
                    error_message = str(getattr(crawl_result, "error_message", "") or "")
                    page_html = str(getattr(crawl_result, "html", "") or "")
                    status_code = getattr(crawl_result, "status_code", None)
                    browser_record = store_browser_download(
                        form,
                        list(getattr(crawl_result, "downloaded_files", None) or []),
                        base_record,
                    )
                    if browser_record:
                        record = browser_record
                    elif "cloudflare" in error_message.casefold() or "anti-bot" in error_message.casefold():
                        record = {
                            **base_record,
                            "status": "blocked_cloudflare",
                            "http_status": status_code,
                            "error": error_message,
                        }
                    elif not getattr(crawl_result, "success", False):
                        record = {
                            **base_record,
                            "status": "crawl_failed",
                            "http_status": status_code,
                            "error": error_message or "Crawl4AI did not return a successful result",
                        }
                    else:
                        urls = candidate_download_urls(
                            str(getattr(crawl_result, "url", "") or detail_url),
                            page_html,
                        )
                        if not urls:
                            record = {
                                **base_record,
                                "status": "no_public_download_link",
                                "http_status": status_code,
                            }
                        else:
                            record = download_from_discovered_urls(
                                download_client,
                                form,
                                urls,
                                str(getattr(crawl_result, "url", "") or detail_url),
                            )
                except Exception as exc:
                    record = {
                        **base_record,
                        "status": "crawl_failed",
                        "error": str(exc),
                    }

                results[form_id] = record
                print(
                    f"[{index}/{len(forms)}] {form_id}: {record.get('status')}",
                    flush=True,
                )
                save_json(
                    STATUS_PATH,
                    {
                        "generated_at": utcnow(),
                        "manifest_total": len(flatten_manifest(load_json(MANIFEST_PATH, {}))),
                        "records": list(results.values()),
                    },
                )
                if args.delay > 0:
                    await asyncio.sleep(args.delay)
    return results


def run(args: argparse.Namespace) -> int:
    manifest = load_json(MANIFEST_PATH, {})
    forms = flatten_manifest(manifest)
    if not forms:
        raise RuntimeError(f"No forms found in {MANIFEST_PATH}")

    prior_payload = load_json(STATUS_PATH, {"records": []})
    prior_records: dict[str, dict[str, Any]] = {}
    for record in prior_payload.get("records", []):
        if not record.get("id"):
            continue
        normalized_record = dict(record)
        if (
            normalized_record.get("status") == "detail_request_failed"
            and normalized_record.get("http_status") == 429
        ):
            normalized_record["status"] = "rate_limited"
        prior_records[str(normalized_record["id"])] = normalized_record

    audit_results = quarantine_synthetic_files(move_files=args.quarantine)
    print(
        f"Synthetic forms: {len(audit_results)} "
        f"({'quarantined' if args.quarantine else 'detected only'})"
    )

    selected = forms[args.offset :]
    if args.limit is not None:
        selected = selected[: args.limit]

    results = dict(prior_records)
    if not args.audit_only and args.engine == "crawl4ai":
        results = asyncio.run(run_crawl4ai(selected, prior_records, args))
    elif not args.audit_only:
        with httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"},
        ) as client:
            for index, form in enumerate(selected, start=1):
                form_id = str(form.get("id") or "")
                record = download_one(client, form, prior_records.get(form_id))
                results[form_id] = record
                print(
                    f"[{index}/{len(selected)}] {form_id}: {record.get('status')}",
                    flush=True,
                )
                save_json(
                    STATUS_PATH,
                    {
                        "generated_at": utcnow(),
                        "manifest_total": len(forms),
                        "records": list(results.values()),
                        "synthetic_audit": audit_results,
                    },
                )
                if args.delay > 0:
                    time.sleep(args.delay)

    payload = {
        "generated_at": utcnow(),
        "manifest_total": len(forms),
        "records": list(results.values()),
        "synthetic_audit": audit_results,
    }
    save_json(STATUS_PATH, payload)

    status_counts: dict[str, int] = {}
    for record in results.values():
        status = str(record.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    print(json.dumps(status_counts, ensure_ascii=False, sort_keys=True))
    print(f"Status file: {STATUS_PATH}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true", help="Only inspect existing local files")
    parser.add_argument("--quarantine", action="store_true", help="Move synthetic files into quarantine")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between detail requests")
    parser.add_argument(
        "--engine",
        choices=("httpx", "crawl4ai"),
        default="httpx",
        help="Page discovery engine",
    )
    parser.add_argument(
        "--profile-dir",
        help="Persistent Crawl4AI browser profile created from a user-authorized login",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Show the Crawl4AI browser for a user-authorized login/challenge",
    )
    parser.add_argument(
        "--interactive-wait",
        type=float,
        default=60.0,
        help="Seconds to wait for manual login/challenge when --interactive is used",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
