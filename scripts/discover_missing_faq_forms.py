"""Discover downloadable form candidates for FAQs that have no approved file.

This is deliberately candidate-only: network results are never promoted to
the public official index without admin review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
FAQ_PATH = ROOT / "notebook_data" / "faq_store.json"
CANDIDATE_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
DOWNLOAD_DIR = ROOT / "data" / "uploads" / "forms" / "official_candidates" / "web_discovery"
ALLOWED_HOSTS = {
    "dichvucong.gov.vn",
    "dichvucong.haiphong.gov.vn",
    "haiphong.gov.vn",
    "*.haiphong.gov.vn",
    "vbpl.vn",
}
EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")
OFFICIAL_FALLBACK_PAGES = {
    "kết hôn": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html?ma_thu_tuc=2.000806",
    "tình trạng hôn nhân": "https://dichvucong.gov.vn/p/home/dvc-tthc-thu-tuc-hanh-chinh-chi-tiet.html?ma_thu_tuc=189213",
    "trích lục hộ tịch": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html?ma_thu_tuc=1.009073",
    "khai sinh": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-thu-tuc-hanh-chinh.html?ma_thu_tuc=1.014406",
}


def _load(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    host = host.removeprefix("www.")
    return host in ALLOWED_HOSTS or host.endswith(".haiphong.gov.vn")


def _decode_result(url: str) -> str:
    parsed = urlparse(url)
    target = parse_qs(parsed.query).get("uddg", [None])[0]
    return unquote(target or url)


def _form_name(question: str) -> str:
    text = re.sub(r"^\s*(tôi|anh/chị|bạn)\s+(cần|muốn)\s+", "", question, flags=re.I)
    text = re.sub(r"\s+(tải|ở đâu|cần làm gì).*$", "", text, flags=re.I).strip(" .?:")
    return text[:180]


def _queries(question: str) -> list[str]:
    name = _form_name(question)
    return [
        f'"{name}" biểu mẫu site:dichvucong.gov.vn',
        f'"{name}" filetype:docx OR filetype:pdf site:haiphong.gov.vn',
        f'"{name}" Hải Phòng biểu mẫu',
    ]


def _fallback_pages(question: str) -> list[str]:
    lowered = question.casefold()
    return [url for keyword, url in OFFICIAL_FALLBACK_PAGES.items() if keyword in lowered]


def _search(client: httpx.Client, query: str) -> list[str]:
    response = client.get(
        "https://html.duckduckgo.com/html/?q=" + quote(query),
        headers={"User-Agent": "HaiPhongLegalAssistant/1.0 form-discovery"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    urls: list[str] = []
    for anchor in soup.select("a.result__a"):
        url = _decode_result(str(anchor.get("href") or ""))
        if url.startswith("http") and _host_allowed(url) and url not in urls:
            urls.append(url)
    return urls[:8]


def _file_links(client: httpx.Client, page_url: str) -> list[str]:
    if urlparse(page_url).path.lower().endswith(EXTENSIONS):
        return [page_url]
    try:
        response = client.get(page_url, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        href = urljoin(str(response.url), str(anchor.get("href")))
        path = urlparse(href).path.lower()
        label = anchor.get_text(" ", strip=True).lower()
        if (path.endswith(EXTENSIONS) or any(word in label for word in ("tải", "download", "biểu mẫu"))) and _host_allowed(href):
            if href not in links:
                links.append(href)
    return links[:5]


def _download(client: httpx.Client, url: str, form_name: str) -> tuple[str | None, str | None, int]:
    try:
        response = client.get(url, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return None, None, 0
    content = response.content
    if len(content) < 512:
        return None, None, len(content)
    suffix = Path(urlparse(str(response.url)).path).suffix.lower()
    if suffix not in EXTENSIONS:
        content_type = str(response.headers.get("content-type") or "")
        suffix = ".pdf" if "pdf" in content_type else ".docx" if "word" in content_type else ""
    if not suffix:
        return None, None, len(content)
    digest = hashlib.sha256(content).hexdigest()
    safe = re.sub(r"[^a-z0-9]+", "-", form_name.lower()).strip("-")[:80] or "form"
    destination = DOWNLOAD_DIR / f"web-{digest[:16]}-{safe}{suffix}"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return str(destination.relative_to(ROOT)).replace("\\", "/"), digest, len(content)


def discover_missing_forms(limit: int = 50) -> dict:
    """Discover missing FAQ forms and leave every result pending review.

    This function is also used by the API background job. It intentionally
    never changes the approved catalog, even when a file is downloaded.
    """
    faq_data = _load(FAQ_PATH, {"faqs": []})
    candidate_data = _load(CANDIDATE_PATH, {"summary": {}, "records": []})
    records = list(candidate_data.get("records") or [])
    existing_queries = {
        str(item.get("discovery_query") or "")
        for item in records
        if item.get("source_url") or item.get("file_path")
    }
    missing = [item for item in (faq_data.get("faqs") or []) if item.get("requires_forms") and item.get("question")]
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "checked": 0, "candidates": 0, "downloaded": 0, "errors": []}
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        for faq in missing[: max(1, int(limit))]:
            form_name = _form_name(str(faq["question"]))
            query = _queries(str(faq["question"]))[0]
            if query in existing_queries:
                continue
            report["checked"] += 1
            try:
                result_pages = []
                for search_query in _queries(str(faq["question"])):
                    result_pages.extend(_search(client, search_query))
                result_pages = list(dict.fromkeys(result_pages))
                if not result_pages:
                    result_pages = _fallback_pages(str(faq["question"]))
                download_url = next((link for page in result_pages for link in _file_links(client, page)), None)
                file_path, digest, size = _download(client, download_url, form_name) if download_url else (None, None, 0)
                candidate_id = "web-" + hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]
                candidate = {
                    "id": candidate_id,
                    "detected_form_name": form_name,
                    "suggested_procedure_id": None,
                    "suggested_domain": faq.get("domain"),
                    "source_url": download_url or (result_pages[0] if result_pages else None),
                    "page_url": result_pages[0] if result_pages else None,
                    "discovery_query": query,
                    "discovery_source": "duckduckgo + official-domain-filter",
                    "file_path": file_path,
                    "local_path": file_path,
                    "sha256": digest,
                    "size_bytes": size,
                    "review_status": "candidate_pending_review",
                    "official_level": "candidate",
                    "has_official_file": bool(file_path),
                    "downloadable": False,
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                    "review_note": "Tự động tìm theo tên biểu mẫu; admin phải kiểm tra nguồn trước khi duyệt.",
                }
                existing_index = next(
                    (index for index, item in enumerate(records) if str(item.get("id")) == candidate_id),
                    None,
                )
                if existing_index is None:
                    records.append(candidate)
                else:
                    records[existing_index] = {**records[existing_index], **candidate}
                existing_queries.add(query)
                report["candidates"] += 1
                report["downloaded"] += int(bool(file_path))
            except Exception as exc:  # keep batch discovery moving
                report["errors"].append({"faq_id": faq.get("id"), "error": str(exc)})
    candidate_data["records"] = records
    status_counts: dict[str, int] = {}
    for item in records:
        status = str(item.get("review_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    candidate_data["summary"] = {
        **(candidate_data.get("summary") or {}),
        "total_records": len(records),
        "total": len(records),
        "total_forms": len(records),
        "total_available": sum(1 for item in records if item.get("review_status") == "approved"),
        "approved_count": status_counts.get("approved", 0),
        "approved_forms": status_counts.get("approved", 0),
        "review_status_counts": status_counts,
        "last_web_discovery": report["started_at"],
    }
    CANDIDATE_PATH.write_text(json.dumps(candidate_data, ensure_ascii=False, indent=2), encoding="utf-8")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path = ROOT / "notebook_data" / "forms" / "faq_missing_forms_discovery_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    print(json.dumps(discover_missing_forms(args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
