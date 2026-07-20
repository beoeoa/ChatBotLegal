"""Collect official Hai Phong commune-level procedure and form packages.

The collector uses deterministic HTML parsing for the public Hai Phong portal
and CDN. Files are downloaded into a review queue, validated by their bytes,
deduplicated by SHA-256, and never promoted directly into the answer corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_candidates.json"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

SEED_LISTINGS = (
    {
        "name": "Hải Phòng - Phường Phù Liễn",
        "url": "https://phulien.haiphong.gov.vn/quy-trinh-giai-quyet-tthc",
        "scope": "mixed",
    },
    {
        "name": "Hải Phòng - Xã Tiên Lãng",
        "url": "https://tienlang.haiphong.gov.vn/quy-trinh-bieu-mau-danh-muc-tthc",
        "scope": "mixed",
    },
)

HAI_PHONG_CATEGORY_INDEX = "https://phulien.haiphong.gov.vn/"

DOMAIN_TERMS = {
    "ho_tich_chung_thuc": (
        "hộ tịch",
        "chứng thực",
        "khai sinh",
        "khai tử",
        "kết hôn",
        "nuôi con nuôi",
        "tư pháp",
    ),
    "dat_dai_xay_dung": (
        "đất đai",
        "xây dựng",
        "nhà ở",
        "địa chính",
        "môi trường",
        "quy hoạch",
    ),
    "cu_tru_an_ninh": (
        "cư trú",
        "công an",
        "an ninh",
        "trật tự",
        "phòng cháy",
        "quốc phòng",
        "quân sự",
    ),
    "khieu_nai_to_cao_xu_phat": (
        "khiếu nại",
        "tố cáo",
        "thanh tra",
        "tiếp công dân",
        "xử phạt",
        "vi phạm hành chính",
    ),
    "an_sinh_y_te_giao_duc": (
        "bảo trợ",
        "an sinh",
        "người có công",
        "trẻ em",
        "y tế",
        "giáo dục",
        "lao động",
        "việc làm",
        "văn hóa",
        "thể thao",
        "dân tộc",
        "tôn giáo",
    ),
}

COMMUNE_MARKERS = (
    "cấp xã",
    "ủy ban nhân dân cấp xã",
    "ubnd cấp xã",
    "xã, phường",
    "xã/phường",
    "phường/xã",
    "trung tâm phục vụ hành chính công cấp xã",
    "thẩm quyền của ủy ban nhân dân cấp xã",
)

ARCHIVE_MARKERS = (
    "bãi bỏ",
    "hết hiệu lực",
    "thay thế toàn bộ",
)

DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", value).strip()


def safe_name(value: str, fallback: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", ascii_value).strip("-._")
    return cleaned[:120] or fallback


def classify_domain(text: str) -> str:
    normalized = normalized_text(text)
    scores = {
        domain: sum(1 for term in terms if term in normalized)
        for domain, terms in DOMAIN_TERMS.items()
    }
    domain, score = max(scores.items(), key=lambda item: item[1])
    return domain if score else "hanh_chinh_cong"


def category_listings(client: httpx.Client) -> list[dict[str, str]]:
    """Discover relevant official TTHC field pages from the ward portal."""
    response = client.get(HAI_PHONG_CATEGORY_INDEX, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    parsed_index = urlparse(HAI_PHONG_CATEGORY_INDEX)
    rows: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        title = " ".join(anchor.stripped_strings).strip()
        href = urljoin(HAI_PHONG_CATEGORY_INDEX, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        if parsed.netloc != parsed_index.netloc or not title:
            continue
        if not (
            parsed.path.startswith("/linh-vuc-")
            or parsed.path.startswith("/giai-quyet-khieu-nai")
        ):
            continue
        domain = classify_domain(title)
        if domain == "hanh_chinh_cong":
            continue
        rows[href] = {
            "name": f"Hải Phòng - {title}",
            "url": href,
            "scope": "field_category",
            "source_domain": domain,
        }
    return list(rows.values())


def detect_file_type(content: bytes, url: str) -> str | None:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
            if "[Content_Types].xml" in names and "word/document.xml" in names:
                return "docx"
            if "[Content_Types].xml" in names and any(name.startswith("xl/") for name in names):
                return "xlsx"
        except zipfile.BadZipFile:
            return None
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        suffix = Path(urlparse(url).path).suffix.lower()
        return "xls" if suffix == ".xls" else "doc"
    return None


def extract_text(content: bytes, file_type: str) -> str:
    if file_type == "docx":
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                raw = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            raw = re.sub(r"</w:(?:p|tr)>", "\n", raw)
            return re.sub(r"<[^>]+>", " ", raw)
        except (KeyError, zipfile.BadZipFile):
            return ""
    if file_type == "pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:80])
        except (ImportError, OSError, ValueError):
            return ""
    return ""


def commune_evidence(title: str, extracted_text: str) -> list[str]:
    haystack = normalized_text(f"{title}\n{extracted_text[:250000]}")
    return [marker for marker in COMMUNE_MARKERS if marker in haystack]


def pagination_config(html: str) -> dict[str, Any] | None:
    patterns = {
        "article_category_id": r"article_category_id:\s*(\d+)",
        "categoryIds": r'categoryIds:\s*"([^"]+)"',
        "site_id": r"site_id:\s*(\d+)",
        "page_size": r"page_size:\s*(\d+)",
        "max_page": r"maxPage\s*=\s*parseInt\((\d+)\)",
    }
    values: dict[str, Any] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, html)
        if not match:
            return None
        values[key] = match.group(1)
    for key in ("article_category_id", "site_id", "page_size", "max_page"):
        values[key] = int(values[key])
    return values


def article_links(page_url: str, html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    parsed_page = urlparse(page_url)
    page_path = parsed_page.path.rstrip("/")
    rows: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        title = " ".join(anchor.stripped_strings).strip()
        if parsed.netloc != parsed_page.netloc or not title:
            continue
        if not parsed.path.startswith(page_path + "/"):
            continue
        rows[href] = {"title": title, "detail_url": href}
    return list(rows.values())


def attachment_links(page_url: str, html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        href = urljoin(page_url, str(anchor.get("href") or "").strip())
        suffix = Path(urlparse(href).path).suffix.lower()
        if suffix not in DOCUMENT_SUFFIXES:
            continue
        label = " ".join(anchor.stripped_strings).strip() or Path(urlparse(href).path).name
        rows[href] = {"label": label, "url": href}
    for element in soup.select("iframe[src], embed[src], object[data]"):
        raw_url = element.get("src") or element.get("data") or ""
        href = urljoin(page_url, str(raw_url).strip())
        suffix = Path(urlparse(href).path).suffix.lower()
        if suffix not in DOCUMENT_SUFFIXES:
            continue
        label = (
            str(element.get("title") or "").strip()
            or Path(urlparse(href).path).name
        )
        rows[href] = {"label": label, "url": href}
    return list(rows.values())


def fetch_listing_pages(client: httpx.Client, listing_url: str) -> list[str]:
    first = client.get(listing_url, timeout=45)
    first.raise_for_status()
    pages = [first.text]
    config = pagination_config(first.text)
    if not config:
        return pages

    endpoint = urljoin(
        listing_url,
        "/DesktopModule/UIArticleInMenu/ArticleInMenuPagination.aspx/LoadArticle",
    )
    for page in range(2, config["max_page"] + 1):
        response = client.post(
            endpoint,
            data={
                "article_category_id": config["article_category_id"],
                "categoryIds": config["categoryIds"],
                "site_id": config["site_id"],
                "page": page,
                "page_size": config["page_size"],
                "keyword": "",
                "date_begin": "",
                "date_end": "",
                "show_no": "False",
                "show_post_date": "True",
                "num_of_text": 0,
                "show_view_count": "False",
                "filter_order_in_list": "False",
                "is_default": "False",
                "new": "False",
                "number_of_day": 3,
                "no": -config["page_size"],
                "articlelang": "vi-VN",
                "is_authenticated": "False",
            },
            headers={"Referer": listing_url},
            timeout=45,
        )
        response.raise_for_status()
        pages.append(response.text)
    return pages


def save_payload(payload: dict[str, Any]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT_PATH)


def crawl(limit_articles: int | None = None) -> dict[str, Any]:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen_articles: set[str] = set()
    seen_hashes: dict[str, str] = {}

    with httpx.Client(
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi-VN,vi;q=0.9"},
    ) as client:
        articles: list[dict[str, str]] = []
        sources = list(SEED_LISTINGS)
        try:
            sources.extend(category_listings(client))
        except httpx.HTTPError as exc:
            errors.append({"url": HAI_PHONG_CATEGORY_INDEX, "error": str(exc)})

        for source in sources:
            try:
                pages = fetch_listing_pages(client, source["url"])
                for page_html in pages:
                    for row in article_links(source["url"], page_html):
                        if row["detail_url"] in seen_articles:
                            continue
                        seen_articles.add(row["detail_url"])
                        articles.append(
                            {
                                **row,
                                "source_name": source["name"],
                                "source_domain": source.get("source_domain"),
                            }
                        )
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"url": source["url"], "error": str(exc)})

        if limit_articles is not None:
            articles = articles[:limit_articles]

        for index, article in enumerate(articles, start=1):
            try:
                detail = client.get(article["detail_url"], timeout=45)
                detail.raise_for_status()
                attachments = attachment_links(str(detail.url), detail.text)
            except httpx.HTTPError as exc:
                errors.append({"url": article["detail_url"], "error": str(exc)})
                continue

            for attachment in attachments:
                base = {
                    "title": article["title"],
                    "attachment_name": attachment["label"],
                    "source_name": article["source_name"],
                    "source_page_url": str(detail.url),
                    "download_url": attachment["url"],
                    "publisher": "Cổng thông tin điện tử thành phố Hải Phòng",
                    "locality": "Hải Phòng",
                    "administrative_level": "commune_candidate",
                    "review_status": "candidate_pending_review",
                    "crawled_at": utcnow(),
                }
                try:
                    response = client.get(
                        attachment["url"],
                        headers={"Referer": str(detail.url)},
                        timeout=90,
                    )
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    records.append({**base, "status": "download_failed", "error": str(exc)})
                    continue

                file_type = detect_file_type(response.content, str(response.url))
                if not file_type:
                    records.append({**base, "status": "invalid_file"})
                    continue

                digest = hashlib.sha256(response.content).hexdigest()
                extracted = extract_text(response.content, file_type)
                evidence = commune_evidence(article["title"], extracted)
                combined = f"{article['title']} {attachment['label']} {extracted[:100000]}"
                domain = article.get("source_domain") or classify_domain(combined)
                normalized_title = normalized_text(article["title"])
                effectivity_flags = [
                    marker for marker in ARCHIVE_MARKERS if marker in normalized_title
                ]
                legal_status = "needs_admin_effectivity_review"

                if digest in seen_hashes:
                    records.append(
                        {
                            **base,
                            "status": "duplicate",
                            "duplicate_of": seen_hashes[digest],
                            "sha256": digest,
                            "file_type": file_type,
                            "domain": domain,
                            "commune_scope_evidence": evidence,
                            "legal_status": legal_status,
                            "effectivity_flags": effectivity_flags,
                        }
                    )
                    continue

                identifier = digest[:16]
                filename = f"{identifier}-{safe_name(attachment['label'], identifier)}.{file_type}"
                destination = DOWNLOAD_DIR / filename
                destination.write_bytes(response.content)
                seen_hashes[digest] = str(destination.relative_to(ROOT))
                status = "downloaded_pending_review" if evidence else "outside_scope_pending_review"
                records.append(
                    {
                        **base,
                        "status": status,
                        "local_path": str(destination.relative_to(ROOT)),
                        "file_type": file_type,
                        "size_bytes": len(response.content),
                        "sha256": digest,
                        "domain": domain,
                        "commune_scope_evidence": evidence,
                        "legal_status": legal_status,
                        "effectivity_flags": effectivity_flags,
                    }
                )
            print(f"[{index}/{len(articles)}] {article['title'][:80]}: {len(attachments)} tệp")

    status_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "downloaded_pending_review":
            domain = str(record.get("domain") or "unknown")
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    payload = {
        "generated_at": utcnow(),
        "sources": sources,
        "summary": {
            "articles_discovered": len(seen_articles),
            "attachments_found": len(records),
            "status_counts": status_counts,
            "domain_counts": domain_counts,
            "errors": len(errors),
        },
        "records": records,
        "errors": errors,
    }
    save_payload(payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-articles", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = crawl(limit_articles=args.limit_articles)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Candidate manifest: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
