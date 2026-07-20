#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crawl official form files from multi-source government portals.

Sources:
- Cong DVC Quoc gia
- Cong DVC Hai Phong
- Cong thong tin UBND Hai Phong
- So Tu phap Hai Phong
- UBND quan/huyen/phuong neu co

Behavior:
- Discover .doc/.docx/.pdf/.xls/.xlsx links
- Download into data/uploads/forms/official_candidates
- Write metadata to notebook_data/forms/official_forms_candidates_full.json
- Never promote to official/approved
- Never invent procedure names when missing
- Dedup by sha256 + source_url + filename
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates"
OUTPUT_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_full.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
FORM_SUFFIXES = {".doc", ".docx", ".pdf", ".xls", ".xlsx"}
FORM_HINTS = (
    "bieu mau", "bieu mau", "mau don", "to khai", "phu luc",
    "download", "tai ve", ".doc", ".pdf", ".xls",
    "biểu mẫu", "mẫu đơn", "tờ khai", "phụ lục", "tải về",
)

SOURCES: list[dict[str, Any]] = [
    {
        "source_id": "dvc_national",
        "name": "Cổng Dịch vụ công Quốc gia",
        "publisher": "Cổng Dịch vụ công Quốc gia",
        "scope": "central",
        "official_level": "official",
        "seed_urls": [
            "https://dichvucong.gov.vn/p/home/dvc-tthc-quoc-gia.html",
            "https://dichvucong.gov.vn/",
        ],
    },
    {
        "source_id": "dvc_haiphong",
        "name": "Cổng Dịch vụ công Hải Phòng",
        "publisher": "Cổng Dịch vụ công TP Hải Phòng",
        "scope": "haiphong",
        "official_level": "official",
        "seed_urls": ["https://dichvucong.haiphong.gov.vn/"],
    },
    {
        "source_id": "ubnd_haiphong",
        "name": "Cổng thông tin UBND Hải Phòng",
        "publisher": "UBND TP Hải Phòng",
        "scope": "haiphong",
        "official_level": "official",
        "seed_urls": [
            "https://haiphong.gov.vn/",
            "https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat/",
            "https://haiphong.gov.vn/Van-ban-chi-dao-dieu-hanh/",
        ],
    },
    {
        "source_id": "so_tu_phap_hp",
        "name": "Sở Tư pháp Hải Phòng",
        "publisher": "Sở Tư pháp TP Hải Phòng",
        "scope": "haiphong",
        "official_level": "official",
        "seed_urls": [
            "https://sotuphap.haiphong.gov.vn/",
            "https://sotuphap.haiphong.gov.vn/thu-tuc-hanh-chinh/",
            "https://sotuphap.haiphong.gov.vn/van-ban-phap-luat/",
        ],
    },
    {
        "source_id": "ubnd_local_hp",
        "name": "UBND quận/huyện/phường Hải Phòng",
        "publisher": "UBND địa phương Hải Phòng",
        "scope": "local",
        "official_level": "official",
        "seed_urls": [
            "https://phulien.haiphong.gov.vn/quy-trinh-giai-quyet-tthc",
            "https://tienlang.haiphong.gov.vn/quy-trinh-bieu-mau-danh-muc-tthc",
            "https://lechan.haiphong.gov.vn/",
            "https://lechan.haiphong.gov.vn/van-ban/",
        ],
    },
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\u00a0", " ")).strip()


def safe_filename(value: str, fallback: str = "form") -> str:
    text = normalize_ws(value) or fallback
    text = re.sub(r"[^\w.\-()+ ]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text).strip("._")
    return (text or fallback)[:160]


def file_suffix(url: str) -> str:
    return Path(unquote(urlparse(url).path)).suffix.lower()


def is_form_url(url: str) -> bool:
    return file_suffix(url) in FORM_SUFFIXES


def looks_formish(label: str, url: str) -> bool:
    if is_form_url(url):
        return True
    hay = f"{label} {url}".lower()
    return any(token in hay for token in FORM_HINTS)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_existing_manifest() -> dict[str, Any]:
    if not OUTPUT_PATH.exists():
        return {"generated_at": None, "summary": {}, "records": [], "errors": [], "skips": []}
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"generated_at": None, "summary": {}, "records": [], "errors": [], "skips": []}
        data.setdefault("records", [])
        data.setdefault("errors", [])
        data.setdefault("skips", [])
        return data
    except Exception:
        return {"generated_at": None, "summary": {}, "records": [], "errors": [], "skips": []}


def save_manifest(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTPUT_PATH)


def extract_form_links(page_url: str, html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        href_raw = str(anchor.get("href") or "").strip()
        if not href_raw or href_raw.startswith("#") or href_raw.lower().startswith("javascript:"):
            continue
        href = urljoin(page_url, href_raw)
        label = normalize_ws(" ".join(anchor.stripped_strings)) or Path(unquote(urlparse(href).path)).name
        if not looks_formish(label, href) or not is_form_url(href):
            continue
        found[href] = {
            "source_url": href,
            "form_title": label,
            "page_url": page_url,
            "filename": Path(unquote(urlparse(href).path)).name or "form.bin",
            "file_type": file_suffix(href).lstrip("."),
        }
    for el in soup.select("iframe[src], embed[src], object[data]"):
        raw = str(el.get("src") or el.get("data") or "").strip()
        if not raw:
            continue
        href = urljoin(page_url, raw)
        if not is_form_url(href):
            continue
        label = normalize_ws(str(el.get("title") or "")) or Path(unquote(urlparse(href).path)).name
        found[href] = {
            "source_url": href,
            "form_title": label,
            "page_url": page_url,
            "filename": Path(unquote(urlparse(href).path)).name or "form.bin",
            "file_type": file_suffix(href).lstrip("."),
        }
    return list(found.values())


def extract_follow_links(page_url: str, html: str, max_links: int = 12) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    page_host = urlparse(page_url).netloc.lower()
    keywords = (
        "bieu-mau", "biểu mẫu", "thu-tuc", "thủ tục", "quy-trinh", "quy trình",
        "van-ban", "văn bản", "phu-luc", "phụ lục", "download", "tai-lieu",
        "tài liệu", "tthc", "danh-muc", "danh mục",
    )
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = urljoin(page_url, str(a.get("href") or "").strip())
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.lower() != page_host or is_form_url(href):
            continue
        label = normalize_ws(" ".join(a.stripped_strings)).lower()
        hay = f"{label} {href.lower()}"
        if not any(k in hay for k in keywords):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= max_links:
            break
    return links


def extract_article_detail_links(page_url: str, html: str, max_links: int = 30) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    page = urlparse(page_url)
    page_host = page.netloc.lower()
    page_path = page.path.rstrip("/")
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = urljoin(page_url, str(a.get("href") or "").strip())
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc.lower() != page_host or is_form_url(href):
            continue
        path = parsed.path.rstrip("/")
        child_of_listing = bool(page_path) and path.startswith(page_path + "/")
        looks_article = bool(re.search(r"-\d{4,}$", path)) or bool(re.search(r"/\d{5,}(?:/|$)", path))
        if not (child_of_listing or looks_article):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= max_links:
            break
    return links


def detect_content_type(content: bytes, url: str, content_type: str | None) -> str:
    suffix = file_suffix(url).lstrip(".")
    if suffix in {"doc", "docx", "pdf", "xls", "xlsx"}:
        return suffix
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct or "msword" in ct:
        return "docx" if "wordprocessingml" in ct else "doc"
    if "spreadsheetml" in ct or "excel" in ct:
        return "xlsx" if "spreadsheetml" in ct else "xls"
    if content[:4] == b"%PDF":
        return "pdf"
    if content[:2] == b"PK":
        return "docx"
    return suffix or "bin"


def build_record(
    *,
    source: dict[str, Any],
    form_meta: dict[str, str],
    digest: str | None = None,
    local_path: str | None = None,
    size_bytes: int | None = None,
    status: str = "candidate_pending_review",
    dry_run: bool = False,
) -> dict[str, Any]:
    return {
        "id": digest or hashlib.sha1(form_meta["source_url"].encode("utf-8")).hexdigest()[:24],
        "form_title": form_meta.get("form_title") or None,
        "procedure_id": None,
        "procedure_name": None,
        "domain": None,
        "department": None,
        "source_id": source["source_id"],
        "source_name": source["name"],
        "publisher": source["publisher"],
        "scope": source["scope"],
        "official_level": source["official_level"],
        "review_status": "candidate_pending_review",
        "catalog_status": "candidate",
        "is_promoted": False,
        "page_url": form_meta.get("page_url"),
        "source_url": form_meta.get("source_url"),
        "filename": form_meta.get("filename"),
        "file_type": form_meta.get("file_type"),
        "sha256": digest,
        "local_path": local_path,
        "size_bytes": size_bytes,
        "status": status,
        "dry_run": dry_run,
        "retrieved_at": utcnow(),
    }


def crawl(
    *,
    dry_run: bool = False,
    max_files: int = 20,
    max_pages_per_source: int = 4,
    timeout: float = 30.0,
) -> dict[str, Any]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_existing_manifest()
    existing_records = [r for r in list(existing.get("records") or []) if not r.get("dry_run")]
    existing_by_sha = {str(r.get("sha256")): r for r in existing_records if r.get("sha256")}
    existing_by_url = {str(r.get("source_url")): r for r in existing_records if r.get("source_url")}
    existing_by_name = {str(r.get("filename")).lower(): r for r in existing_records if r.get("filename")}

    discovered: list[dict[str, Any]] = []
    downloaded: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    with httpx.Client(follow_redirects=True, headers=headers, timeout=timeout) as client:
        for source in SOURCES:
            pages_to_visit: list[str] = list(source.get("seed_urls") or [])
            visited_pages: set[str] = set()
            source_forms: list[dict[str, str]] = []
            article_pages: list[str] = []

            while pages_to_visit and len(visited_pages) < max_pages_per_source:
                page_url = pages_to_visit.pop(0)
                if page_url in visited_pages:
                    continue
                visited_pages.add(page_url)
                try:
                    resp = client.get(page_url)
                    if resp.status_code >= 400:
                        errors.append({"source_id": source["source_id"], "url": page_url, "error": f"http_{resp.status_code}"})
                        continue
                    html = resp.text
                    source_forms.extend(extract_form_links(str(resp.url), html))
                    for child in extract_follow_links(str(resp.url), html, max_links=6):
                        if child not in visited_pages and child not in pages_to_visit:
                            pages_to_visit.append(child)
                    for detail in extract_article_detail_links(str(resp.url), html, max_links=20):
                        if detail not in article_pages and detail not in visited_pages:
                            article_pages.append(detail)
                except Exception as exc:  # noqa: BLE001
                    errors.append({"source_id": source["source_id"], "url": page_url, "error": str(exc)})

            for detail_url in article_pages[: max(8, max_pages_per_source * 3)]:
                if detail_url in visited_pages:
                    continue
                visited_pages.add(detail_url)
                try:
                    detail_resp = client.get(detail_url)
                    if detail_resp.status_code >= 400:
                        errors.append({"source_id": source["source_id"], "url": detail_url, "error": f"http_{detail_resp.status_code}"})
                        continue
                    source_forms.extend(extract_form_links(str(detail_resp.url), detail_resp.text))
                except Exception as exc:  # noqa: BLE001
                    errors.append({"source_id": source["source_id"], "url": detail_url, "error": str(exc)})

            unique_forms: dict[str, dict[str, str]] = {}
            for form in source_forms:
                unique_forms[form["source_url"]] = form

            for form in unique_forms.values():
                if form["source_url"] in seen_urls:
                    continue
                seen_urls.add(form["source_url"])
                discovered.append({**form, "source_id": source["source_id"]})

                if not dry_run and len(downloaded) >= max_files:
                    skips.append({"reason": "max_files_reached", "source_url": form["source_url"], "source_id": source["source_id"]})
                    continue

                if form["source_url"] in existing_by_url:
                    skips.append({
                        "reason": "duplicate_source_url",
                        "source_url": form["source_url"],
                        "source_id": source["source_id"],
                        "existing_id": existing_by_url[form["source_url"]].get("id"),
                    })
                    continue

                fname_key = (form.get("filename") or "").lower()
                if fname_key and fname_key in existing_by_name:
                    skips.append({
                        "reason": "duplicate_filename",
                        "source_url": form["source_url"],
                        "filename": form.get("filename"),
                        "source_id": source["source_id"],
                        "existing_id": existing_by_name[fname_key].get("id"),
                    })
                    continue

                if dry_run:
                    if len(downloaded) < max_files:
                        downloaded.append(build_record(source=source, form_meta=form, status="dry_run_candidate", dry_run=True))
                    continue

                try:
                    file_resp = client.get(form["source_url"])
                    if file_resp.status_code >= 400:
                        skips.append({
                            "reason": f"download_http_{file_resp.status_code}",
                            "source_url": form["source_url"],
                            "source_id": source["source_id"],
                        })
                        continue
                    content = file_resp.content or b""
                    if len(content) < 64:
                        skips.append({
                            "reason": "empty_or_tiny_file",
                            "source_url": form["source_url"],
                            "source_id": source["source_id"],
                            "size_bytes": len(content),
                        })
                        continue
                    digest = sha256_bytes(content)
                    if digest in existing_by_sha:
                        skips.append({
                            "reason": "duplicate_sha256",
                            "source_url": form["source_url"],
                            "source_id": source["source_id"],
                            "sha256": digest,
                            "existing_id": existing_by_sha[digest].get("id"),
                        })
                        continue
                    ftype = detect_content_type(content, form["source_url"], file_resp.headers.get("content-type"))
                    base_name = safe_filename(Path(form.get("filename") or f"form.{ftype}").stem, fallback=source["source_id"])
                    out_name = f"{digest[:16]}-{base_name}.{ftype}"
                    out_path = DOWNLOAD_DIR / out_name
                    out_path.write_bytes(content)
                    form_meta = dict(form)
                    form_meta["filename"] = out_name
                    form_meta["file_type"] = ftype
                    record = build_record(
                        source=source,
                        form_meta=form_meta,
                        digest=digest,
                        local_path=str(out_path.relative_to(ROOT)).replace("\\", "/"),
                        size_bytes=len(content),
                        status="downloaded_pending_review",
                        dry_run=False,
                    )
                    downloaded.append(record)
                    existing_by_sha[digest] = record
                    existing_by_url[form["source_url"]] = record
                    existing_by_name[out_name.lower()] = record
                except Exception as exc:  # noqa: BLE001
                    errors.append({"source_id": source["source_id"], "url": form["source_url"], "error": str(exc)})
                    skips.append({
                        "reason": "download_exception",
                        "source_url": form["source_url"],
                        "source_id": source["source_id"],
                        "error": str(exc),
                    })

    if dry_run:
        merged_records = existing_records + [r for r in downloaded if r.get("dry_run")]
    else:
        existing_ids = {x.get("id") for x in existing_records}
        merged_records = existing_records + [r for r in downloaded if r.get("id") not in existing_ids]

    skip_reasons: dict[str, int] = {}
    for s in skips:
        reason = str(s.get("reason") or "unknown")
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    by_source: dict[str, int] = {}
    for r in downloaded:
        sid = str(r.get("source_id") or "unknown")
        by_source[sid] = by_source.get(sid, 0) + 1

    summary = {
        "mode": "dry_run" if dry_run else "download",
        "max_files": max_files,
        "sources_configured": len(SOURCES),
        "forms_discovered": len(discovered),
        "downloaded_or_dryrun_candidates": len(downloaded),
        "downloaded_success": 0 if dry_run else len(downloaded),
        "skipped": len(skips),
        "errors": len(errors),
        "skip_reasons": skip_reasons,
        "by_source": by_source,
        "output_path": str(OUTPUT_PATH.relative_to(ROOT)).replace("\\", "/"),
        "download_dir": str(DOWNLOAD_DIR.relative_to(ROOT)).replace("\\", "/"),
        "promoted_to_official": False,
    }
    payload = {
        "generated_at": utcnow(),
        "summary": summary,
        "sources": [
            {
                "source_id": s["source_id"],
                "name": s["name"],
                "publisher": s["publisher"],
                "scope": s["scope"],
                "seed_urls": s["seed_urls"],
            }
            for s in SOURCES
        ],
        "records": merged_records,
        "latest_batch": downloaded,
        "skips": skips,
        "errors": errors,
        "discovered_sample": discovered[:50],
        "notes": [
            "Candidates only. Do not promote to official automatically.",
            "procedure_id/procedure_name/domain left null when unknown.",
            "Dedup keys: sha256 + source_url + filename.",
            "UTF-8 JSON output with ensure_ascii=False.",
        ],
    }
    save_manifest(payload)
    return payload


def print_report(payload: dict[str, Any]) -> None:
    summary = payload.get("summary") or {}
    print("=== OFFICIAL FORMS CRAWL REPORT ===")
    print(f"mode: {summary.get('mode')}")
    print(f"forms_discovered: {summary.get('forms_discovered')}")
    print(f"downloaded_success: {summary.get('downloaded_success')}")
    print(f"downloaded_or_dryrun_candidates: {summary.get('downloaded_or_dryrun_candidates')}")
    print(f"skipped: {summary.get('skipped')}")
    print(f"errors: {summary.get('errors')}")
    print("skip_reasons:")
    for reason, count in (summary.get("skip_reasons") or {}).items():
        print(f"  - {reason}: {count}")
    print("by_source:")
    for source, count in (summary.get("by_source") or {}).items():
        print(f"  - {source}: {count}")
    print(f"output: {summary.get('output_path')}")
    print(f"download_dir: {summary.get('download_dir')}")
    print(f"promoted_to_official: {summary.get('promoted_to_official')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Discover only, do not download files")
    parser.add_argument("--max-files", type=int, default=20, help="Max files to download (or dry-run candidates)")
    parser.add_argument("--max-pages-per-source", type=int, default=4, help="Max listing pages visited per source")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = crawl(
        dry_run=bool(args.dry_run),
        max_files=max(1, int(args.max_files)),
        max_pages_per_source=max(1, int(args.max_pages_per_source)),
        timeout=float(args.timeout),
    )
    print_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
