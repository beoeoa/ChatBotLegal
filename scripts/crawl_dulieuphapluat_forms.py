# -*- coding: utf-8 -*-
"""Crawl form templates from dulieuphapluat.vn into the review queue.

This source is useful for filling common citizen-facing form gaps, but it is a
reference source rather than a Hai Phong official legal source. Records are
therefore saved as ``candidate_pending_review`` and are not exposed as approved
downloads until an admin reviews them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.crawl_legal_forms import detect_file_type

OUTPUT_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
REPORT_PATH = ROOT / "notebook_data" / "forms" / "dulieuphapluat_forms_crawl_report.json"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates" / "dulieuphapluat"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

SEED_URLS = [
    "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/mua-ban-nha-dat.html?page=1",
    "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/thue-muon-nha-dat.html",
    "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/tang-cho-nha-dat.html",
    "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/bao-lanh-the-chap-nha-dat.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/ly-hon.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/con-cai-nuoi-con-sau-khi-ly-hon.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/thua-ke-di-chuc.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/ket-hon-trai-phap-luat.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/thoa-thuan-tai-san.html",
    "https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/bao-luc-gia-dinh.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/thanh-lap-moi-cong-ty-co-phan.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/thay-doi-thong-tin-doanh-nghiep-thay-doi-dia-chi-tru-so-chinh-cong-ty-co-phan.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/giai-the-cong-ty-tnhh-mot-thanh-vien.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/tam-ngung-hoat-dong-cong-ty-tnhh-hai-thanh-vien-tro-len.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/so-huu-tri-tue-nhan-hieu.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/so-huu-tri-tue-bi-mat-kinh-doanh.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/so-huu-tri-tue-bao-mat-thong-tin.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/so-huu-tri-tue-quy-che-noi-bo.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/kinh-doanh-ban-hang-hoa.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/kinh-doanh-cung-cap-dich-vu.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/kinh-doanh-cho-thue-tai-san.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/tai-chinh-vay-tien.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/nhan-su-hop-dong.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/nhan-su-chinh-sach-bao-mat-thong-tin.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/thanh-lap-moi-cong-ty-tnhh-hai-thanh-vien-tro-len.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/giai-the-cong-ty-co-phan.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/nhan-su.html",
    "https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/nhan-su-ky-luat-lao-dong.html",
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def slugify(value: str, fallback: str = "bieu-mau") -> str:
    value = re.sub(r"[^\w.\-]+", "-", value, flags=re.UNICODE).strip("-._")
    value = re.sub(r"-{2,}", "-", value)
    return value[:100] or fallback


def listing_page_url(seed_url: str, page: int) -> str:
    parsed = urlparse(seed_url)
    query = parse_qs(parsed.query)
    if page <= 1:
        query.pop("page", None)
    else:
        query["page"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def detect_domain(url: str) -> tuple[str, bool, str]:
    path = urlparse(url).path
    if "/giao-dich-voi-bat-dong-san/" in path:
        return "dat_dai_xay_dung", True, "high"
    if "/hon-nhan-gia-dinh/" in path:
        return "ho_tich_chung_thuc", True, "high"
    if "/doanh-nghiep/" in path:
        return "unknown", False, "low_non_core"
    return "unknown", False, "unknown"


def extract_detail_links(page_url: str, html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    base_path = re.sub(r"\.html$", "/", urlparse(page_url).path)
    results: list[dict[str, str]] = []
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        if parsed.netloc != "www.dulieuphapluat.vn":
            continue
        if not parsed.path.startswith("/bieu-mau/") or not parsed.path.endswith(".html"):
            continue
        if parsed.path == urlparse(page_url).path:
            continue
        if parsed.path.rstrip(".html").count("/") < 4:
            continue
        if not parsed.path.startswith(base_path):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title or title.isdigit() or title in {"›", "‹"}:
            continue
        results.append({"title": title, "url": href})
    deduped: dict[str, dict[str, str]] = {}
    for item in results:
        deduped[item["url"]] = item
    return list(deduped.values())


def extract_download_links(detail_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        label = anchor.get_text(" ", strip=True).casefold()
        href_lower = href.casefold()
        is_file = any(token in href_lower for token in (".doc", ".docx", ".pdf", ".rtf", ".xls", ".xlsx"))
        is_download = "download_file" in href_lower or "download" in href_lower or "tải xuống" in label
        if is_file or is_download:
            candidate = urljoin(detail_url, href)
            parsed = urlparse(candidate)
            # Some old records expose broken server-local Windows paths such as
            # /C:/Users/...; keep them out of the download client.
            if parsed.scheme in {"http", "https"} and not re.match(r"^/[A-Za-z]:", urlparse(href).path):
                urls.append(candidate)
    return list(dict.fromkeys(urls))


def detail_title(detail_url: str, html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in ("h1", ".gc-form-title", "h2"):
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return fallback


def download_first_valid_file(
    client: httpx.Client,
    detail_url: str,
    title: str,
    domain: str,
    download_urls: list[str],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    for download_url in download_urls:
        try:
            response = client.get(download_url, headers={"Referer": detail_url}, timeout=60)
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{download_url}: {exc}")
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
        digest = sha256_bytes(response.content)
        form_id = stable_id(detail_url, str(response.url))
        destination_dir = DOWNLOAD_DIR / slugify(domain, "unknown")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{form_id}-{slugify(title)}.{file_type}"
        destination.write_bytes(response.content)
        return (
            {
                "id": form_id,
                "file_name": destination.name,
                "file_path": str(destination.relative_to(ROOT)).replace("\\", "/"),
                "local_path": str(destination.relative_to(ROOT)).replace("\\", "/"),
                "source_package_path": str(destination.relative_to(ROOT)).replace("\\", "/"),
                "sha256": digest,
                "source_sha256": digest,
                "size_bytes": len(response.content),
                "file_type": file_type,
                "source_download_url": str(response.url),
                "has_official_file": True,
                "has_download": False,
            },
            errors,
        )
    return ({}, errors)


def build_candidate(
    detail_url: str,
    category_url: str,
    title: str,
    domain: str,
    ward_scope: bool,
    priority_tier: str,
    file_record: dict[str, Any],
    download_urls: list[str],
    errors: list[str],
) -> dict[str, Any]:
    form_id = str(file_record.get("id") or stable_id(detail_url, title))
    status = "downloaded_verified" if file_record else (
        "no_public_download_link" if not download_urls else "invalid_download"
    )
    return {
        **file_record,
        "id": form_id,
        "source_url": detail_url,
        "page_url": detail_url,
        "source_page_url": detail_url,
        "source_category_url": category_url,
        "source_name": "dulieuphapluat.vn",
        "publisher": "Dữ liệu Pháp Luật",
        "locality": "Toàn quốc",
        "administrative_level": "reference_candidate",
        "detected_form_name": title,
        "form_title": title,
        "suggested_domain": domain,
        "domain": domain,
        "suggested_procedure_id": "unknown",
        "confidence": 0.55 if ward_scope else 0.25,
        "reason": (
            "Crawler dulieuphapluat.vn: nguồn biểu mẫu tham khảo, cần admin kiểm tra mẫu còn áp dụng "
            "và có phù hợp nhiệm vụ xã/phường Hải Phòng trước khi duyệt."
        ),
        "review_status": "candidate_pending_review",
        "is_approved": False,
        "official_level": "reference",
        "catalog_status": "candidate_pending_review",
        "is_canonical": False,
        "source_type": "form",
        "ward_scope": ward_scope,
        "priority_tier": priority_tier,
        "crawl_status": status,
        "candidate_download_urls": download_urls[:10],
        "errors": errors[:10],
        "retrieved_at": utcnow(),
        "classified_at": utcnow(),
        "crawler": "dulieuphapluat_httpx_crawl4ai_compatible",
    }


def crawl(args: argparse.Namespace) -> dict[str, Any]:
    seeds = args.url or SEED_URLS
    discovered: dict[str, dict[str, str]] = {}
    category_stats: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    skipped_low_priority = 0

    with httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"},
    ) as client:
        for seed in seeds:
            seed_domain, seed_ward_scope, seed_priority = detect_domain(seed)
            if seed_priority == "low_non_core" and not args.include_low_priority:
                skipped_low_priority += 1
                continue
            empty_pages = 0
            seed_found = 0
            for page in range(1, args.max_pages + 1):
                page_url = listing_page_url(seed, page)
                try:
                    response = client.get(page_url, timeout=45)
                except httpx.HTTPError as exc:
                    category_stats.append({"url": page_url, "status": "listing_failed", "error": str(exc)})
                    break
                if response.status_code >= 400:
                    category_stats.append({"url": page_url, "status": "listing_http_error", "http_status": response.status_code})
                    break
                links = extract_detail_links(str(response.url), response.text)
                new_links = 0
                for link in links:
                    if link["url"] not in discovered:
                        discovered[link["url"]] = {
                            **link,
                            "category_url": seed,
                            "domain": seed_domain,
                            "ward_scope": seed_ward_scope,
                            "priority_tier": seed_priority,
                        }
                        new_links += 1
                seed_found += new_links
                empty_pages = empty_pages + 1 if new_links == 0 else 0
                if args.target and len(discovered) >= args.target:
                    break
                if page > 1 and empty_pages >= 2:
                    break
                if args.delay:
                    time.sleep(args.delay)
            category_stats.append({"url": seed, "status": "scanned", "new_detail_links": seed_found})
            if args.target and len(discovered) >= args.target:
                break

        selected = list(discovered.values())[: args.target if args.target else None]
        for index, item in enumerate(selected, start=1):
            detail_url = item["url"]
            try:
                response = client.get(detail_url, timeout=45)
                response.raise_for_status()
                title = detail_title(detail_url, response.text, item["title"])
                download_urls = extract_download_links(str(response.url), response.text)
                file_record, errors = download_first_valid_file(
                    client,
                    str(response.url),
                    title,
                    item["domain"],
                    download_urls,
                )
                record = build_candidate(
                    str(response.url),
                    item["category_url"],
                    title,
                    item["domain"],
                    bool(item["ward_scope"]),
                    item["priority_tier"],
                    file_record,
                    download_urls,
                    errors,
                )
            except (httpx.HTTPError, OSError) as exc:
                record = build_candidate(
                    detail_url,
                    item["category_url"],
                    item["title"],
                    item["domain"],
                    bool(item["ward_scope"]),
                    item["priority_tier"],
                    {},
                    [],
                    [str(exc)],
                )
                record["crawl_status"] = "detail_failed"
            records.append(record)
            print(f"[{index}/{len(selected)}] {record['crawl_status']}: {record['detected_form_name'][:80]}", flush=True)
            if args.delay:
                time.sleep(args.delay)

    existing = load_json(OUTPUT_PATH, {"records": [], "summary": {}})
    existing_records = list(existing.get("records") or [])
    by_key: dict[str, dict[str, Any]] = {}
    for rec in existing_records:
        key = str(rec.get("id") or rec.get("source_page_url") or rec.get("source_url") or "")
        if key:
            by_key[key] = rec
    added = 0
    updated = 0
    for rec in records:
        key = str(rec.get("id") or rec.get("source_page_url") or "")
        old = by_key.get(key)
        if old:
            by_key[key] = {**old, **rec}
            updated += 1
        else:
            by_key[key] = rec
            added += 1

    merged_records = list(by_key.values())
    status_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    review_counts: dict[str, int] = {}
    for rec in merged_records:
        status = str(rec.get("crawl_status") or rec.get("review_status") or "unknown")
        domain = str(rec.get("suggested_domain") or rec.get("domain") or "unknown")
        review = str(rec.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        review_counts[review] = review_counts.get(review, 0) + 1

    existing["records"] = merged_records
    existing["generated_at"] = utcnow()
    existing["summary"] = {
        **(existing.get("summary") or {}),
        "total_classified": len(merged_records),
        "by_domain": domain_counts,
        "review_status_counts": review_counts,
        "dulieuphapluat_last_added": added,
        "dulieuphapluat_last_updated": updated,
        "dulieuphapluat_last_downloaded": sum(1 for r in records if r.get("crawl_status") == "downloaded_verified"),
    }
    save_json(OUTPUT_PATH, existing)

    report = {
        "generated_at": utcnow(),
        "source": "dulieuphapluat.vn",
        "seed_count": len(seeds),
        "skipped_low_priority_seeds": skipped_low_priority,
        "discovered_detail_links": len(discovered),
        "processed_detail_links": len(records),
        "added": added,
        "updated": updated,
        "status_counts": status_counts,
        "category_stats": category_stats,
    }
    save_json(REPORT_PATH, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", help="Seed category URL. Defaults to the curated list.")
    parser.add_argument("--target", type=int, default=220, help="Maximum detail pages to process.")
    parser.add_argument("--max-pages", type=int, default=30, help="Maximum pages per category.")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between requests.")
    parser.add_argument("--include-low-priority", action="store_true", help="Also crawl non-core business form categories.")
    return parser.parse_args()


def main() -> int:
    report = crawl(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Candidate queue: {OUTPUT_PATH}")
    print(f"Report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
