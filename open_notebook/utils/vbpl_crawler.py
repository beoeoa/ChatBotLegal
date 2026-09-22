"""Crawl4AI-backed compatibility helpers for VBPL pages.

The application uses :mod:`api.crawlers.crawl4ai_fetcher` as the single HTML
transport. These functions preserve the legacy return shapes used by older
notebook scripts without maintaining a second Playwright crawler.
"""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from loguru import logger

from api.crawlers.crawl4ai_fetcher import fetch_rendered

_MOJIBAKE_MARKERS = ("Ã", "Â", "Ä", "Æ", "áº", "á»")


def _repair_mojibake_text(value: str) -> str:
    """Repair VBPL server-action strings without altering valid UTF-8 text."""

    current = str(value or "")
    for _ in range(2):
        score = sum(current.count(marker) for marker in _MOJIBAKE_MARKERS)
        if score == 0:
            break
        best = current
        best_score = score
        try:
            mixed_bytes = bytearray()
            for character in current:
                codepoint = ord(character)
                if codepoint <= 255:
                    mixed_bytes.append(codepoint)
                else:
                    encoded = character.encode("cp1252")
                    if len(encoded) != 1:
                        raise UnicodeEncodeError("cp1252", character, 0, 1, "not one byte")
                    mixed_bytes.extend(encoded)
            candidate = bytes(mixed_bytes).decode("utf-8")
            candidate_score = sum(candidate.count(marker) for marker in _MOJIBAKE_MARKERS)
            if candidate_score < best_score:
                best, best_score = candidate, candidate_score
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidate_score = sum(candidate.count(marker) for marker in _MOJIBAKE_MARKERS)
            if candidate_score < best_score:
                best, best_score = candidate, candidate_score
        if best == current:
            break
        current = best
    return current


def _extract_vbpl_document_page(payload: str) -> dict | None:
    """Extract one document-page object from a legacy RSC payload."""

    for line in str(payload or "").splitlines():
        _record_id, separator, raw_json = line.partition(":")
        if not separator or not raw_json.startswith("{"):
            continue
        try:
            candidate = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        items = candidate.get("items") if isinstance(candidate, dict) else None
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            continue
        if all(key in items[0] for key in ("id", "title", "docNum")):
            return candidate
    return None


def _iso_to_listing_date(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        return f"{raw[8:10]}/{raw[5:7]}/{raw[:4]}"
    return raw


def _public_listing_url(url: str) -> tuple[str, int]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not (parsed.hostname or "").lower().endswith("vbpl.vn"):
        raise ValueError("VBPL listing adapter only accepts official HTTPS URLs")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    page_number = 1
    public_query: list[tuple[str, str]] = []
    for key, value in query:
        if key == "_crawler_page":
            try:
                page_number = int(value)
            except (TypeError, ValueError):
                page_number = 1
            continue
        public_query.append((key, value))
    if page_number < 1 or page_number > 50:
        raise ValueError("VBPL crawler page is outside the bounded range")
    public_url = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(public_query, doseq=True), "")
    )
    return public_url, page_number


def _build_vbpl_listing_html(
    page: dict,
    source_url: str,
    *,
    page_number: int,
    has_next: bool | None = None,
) -> str:
    """Convert legacy exact listing rows into deterministic anchors."""

    public_url, _ = _public_listing_url(source_url)
    parts = ['<html><body><main id="vbpl-rendered-listing">']
    items = page.get("items") if isinstance(page, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("id") or "").strip()
        title = _repair_mojibake_text(str(item.get("title") or "").strip())
        law_number = _repair_mojibake_text(str(item.get("docNum") or "").strip())
        document_type_raw = item.get("docType")
        document_type = _repair_mojibake_text(
            str(document_type_raw.get("name") if isinstance(document_type_raw, dict) else document_type_raw or "").strip()
        )
        issuing_agency = _repair_mojibake_text(str(item.get("agencyName") or "").strip())
        issued_date = _iso_to_listing_date(item.get("issueDate"))
        effective_date = _iso_to_listing_date(item.get("effFrom"))
        if not document_id or not title:
            continue
        detail_url = f"https://vbpl.vn/van-ban/chi-tiet/{document_id}"
        parts.extend(
            (
                '<article class="vbpl-document">',
                f'<a href="{html.escape(detail_url, quote=True)}">{html.escape(title)}</a>',
                f"<p>Số hiệu: {html.escape(law_number)}</p>",
                f"<p>Loại văn bản: {html.escape(document_type)}</p>",
                f"<p>Cơ quan: {html.escape(issuing_agency)};</p>",
                f"<p>Ngày ban hành: {html.escape(issued_date)}</p>",
                f"<p>Ngày hiệu lực: {html.escape(effective_date)}</p>",
                "</article>",
            )
        )
    try:
        total = int(page.get("total") or 0)
        page_size = int(page.get("pageSize") or len(items) or 10)
    except (TypeError, ValueError):
        total = 0
        page_size = len(items) if isinstance(items, list) else 10
    should_add_next = has_next if has_next is not None else page_size > 0 and page_number * page_size < total
    if should_add_next:
        parsed = urlparse(public_url)
        next_query = parse_qsl(parsed.query, keep_blank_values=True)
        next_query.append(("_crawler_page", str(page_number + 1)))
        next_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(next_query, doseq=True), "")
        )
        parts.append(f'<a rel="next" href="{html.escape(next_url, quote=True)}">Sau</a>')
    parts.append("</main></body></html>")
    return "".join(parts)


async def crawl_vbpl_listing(url: str) -> dict[str, Any]:
    """Render a VBPL listing through Crawl4AI."""

    public_url, page_number = _public_listing_url(url)
    rendered = await fetch_rendered(public_url, wait_until="networkidle", check_robots_txt=True)
    html_content = str(rendered.get("html") or "")
    if rendered.get("status") != "ok" or not html_content:
        raise ValueError(f"Crawl4AI could not render VBPL listing: {rendered.get('reason') or rendered.get('status')}")
    return {
        "html": html_content,
        "final_url": rendered.get("final_url") or public_url,
        "page_number": page_number,
        "total": len(BeautifulSoup(html_content, "html.parser").select("a[href*='/van-ban/chi-tiet/']")),
        "success": True,
        "status_code": rendered.get("status_code"),
    }


async def crawl_vbpl_url(url: str) -> dict[str, Any]:
    """Render and extract a VBPL detail page through Crawl4AI."""

    logger.info("Crawling VBPL URL with Crawl4AI: {}", url)
    rendered = await fetch_rendered(url, wait_until="networkidle", check_robots_txt=True)
    html_content = str(rendered.get("html") or "")
    if rendered.get("status") != "ok" or not html_content:
        raise ValueError(f"Crawl4AI could not render VBPL URL: {rendered.get('reason') or rendered.get('status')}")

    soup = BeautifulSoup(html_content, "html.parser")
    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""
    title = title.replace(" | Cơ sở dữ liệu quốc gia về pháp luật", "").replace(" | CSDL quốc gia về pháp luật", "").strip()
    selected_selector = None
    content_html = ""
    for selector in ("div.preview-content", "#toanvancontent", "article", "body"):
        element = soup.select_one(selector)
        if not element:
            continue
        candidate_text = " ".join(element.get_text(" ", strip=True).split())
        if len(candidate_text) > 200 and "Đang tải dữ liệu" not in candidate_text:
            if selector != "body" or len(candidate_text) > 1000:
                content_html = str(element)
                selected_selector = selector
                break
    if not content_html:
        raise ValueError("VBPL page did not expose complete legal text after Crawl4AI rendering")

    content_soup = BeautifulSoup(content_html, "html.parser")
    for tag in content_soup(("script", "style", "nav", "footer", "header")):
        tag.decompose()
    lines = [line.strip() for line in content_soup.get_text("\n").splitlines() if line.strip()]
    cleaned_text = "\n".join(lines)
    if not title or title.casefold() in {"error", "trang chủ", "vbpl"}:
        title = next(
            (line for line in lines[:15] if line.upper().startswith(("LUẬT", "NGHỊ ĐỊNH", "QUYẾT ĐỊNH", "THÔNG TƯ", "NGHỊ QUYẾT", "CHỈ THỊ"))),
            title,
        )
    return {
        "title": title or "Văn bản pháp luật",
        "content": cleaned_text,
        "html": content_html,
        "final_url": rendered.get("final_url") or url,
        "selector": selected_selector,
        "success": True,
    }
