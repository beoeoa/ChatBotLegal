"""
Shared crawl4ai-based fetcher for JavaScript-rendered legal pages.

Renders a URL with a headless browser (via the vendored crawl4ai package),
waits for the network to settle, and returns a normalized result dict:

    {
        "status": "ok" | "unavailable" | "error",
        "final_url": str,
        "html": str,
        "rendered_text": str,
        "pdf_links": list[str],
        "reason": str,   # only when status != "ok"
    }

The fetcher fails gracefully: if crawl4ai / Playwright is not installed or a
render fails, it returns a result with ``status`` set and a human-readable
``reason`` instead of raising, so callers can fall back to plain HTTP.
"""

from __future__ import annotations

import re
import sys
import io
from typing import Any
from urllib.parse import urljoin, urlparse

from loguru import logger

# Fix Windows console encoding for Vietnamese/Unicode output
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass  # best effort only

try:  # crawl4ai is vendored under external/crawl4ai
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    HAS_CRAWL4AI = True
    _IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - optional dependency
    AsyncWebCrawler = None  # type: ignore[assignment]
    BrowserConfig = None  # type: ignore[assignment]
    CrawlerRunConfig = None  # type: ignore[assignment]
    CacheMode = None  # type: ignore[assignment]
    HAS_CRAWL4AI = False
    _IMPORT_ERROR = str(exc)


_PDF_HREF_RE = re.compile(r"""href=["']([^"']+?\.pdf[^"']*)["']""", re.IGNORECASE)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _collect_pdf_links(result: Any, html: str, base_url: str) -> list[str]:
    """Collect absolute .pdf links from the crawl result and raw HTML."""
    found: list[str] = []

    def _add(raw: str | None) -> None:
        if not raw:
            return
        absolute = urljoin(base_url, raw.strip())
        if ".pdf" in absolute.lower() and absolute not in found:
            found.append(absolute)

    links = getattr(result, "links", None) or {}
    if isinstance(links, dict):
        for bucket in ("internal", "external"):
            for item in links.get(bucket, []) or []:
                if isinstance(item, dict):
                    _add(item.get("href"))
                elif isinstance(item, str):
                    _add(item)

    downloaded = getattr(result, "downloaded_files", None) or []
    for item in downloaded:
        if isinstance(item, str):
            _add(item)

    for match in _PDF_HREF_RE.findall(html or ""):
        _add(match)

    return found


async def fetch_rendered(
    url: str,
    *,
    timeout_ms: int = 30000,
    wait_until: str = "networkidle",
    settle_ms: int = 2000,
    headless: bool = True,
) -> dict[str, Any]:
    """Render ``url`` with crawl4ai and return a normalized result dict.

    Never raises for expected failure modes (missing dependency, render error,
    navigation timeout). Returns ``status`` in {"ok", "unavailable", "error"}.
    """
    if not url or not urlparse(url).scheme.startswith("http"):
        return {
            "status": "error",
            "final_url": url,
            "html": "",
            "rendered_text": "",
            "pdf_links": [],
            "reason": f"Invalid URL: {url!r}",
        }

    if not HAS_CRAWL4AI:
        return {
            "status": "unavailable",
            "final_url": url,
            "html": "",
            "rendered_text": "",
            "pdf_links": [],
            "reason": (
                "crawl4ai/Playwright not available: "
                f"{_IMPORT_ERROR or 'import failed'}. "
                "Install crawl4ai and run `playwright install chromium`."
            ),
        }

    browser_config = BrowserConfig(
        headless=headless,
        user_agent=USER_AGENT,
        viewport_width=1366,
        viewport_height=900,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until=wait_until,
        page_timeout=timeout_ms,
        wait_for_timeout=settle_ms,
        only_text=False,
    )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
    except Exception as exc:  # navigation / browser errors
        logger.warning(f"crawl4ai render failed for {url}: {exc}")
        return {
            "status": "error",
            "final_url": url,
            "html": "",
            "rendered_text": "",
            "pdf_links": [],
            "reason": f"Render failed: {exc}",
        }

    if not getattr(result, "success", False):
        return {
            "status": "error",
            "final_url": getattr(result, "redirected_url", None) or url,
            "html": getattr(result, "html", "") or "",
            "rendered_text": "",
            "pdf_links": [],
            "reason": getattr(result, "error_message", "") or "crawl unsuccessful",
        }

    final_url = getattr(result, "redirected_url", None) or getattr(result, "url", url) or url
    html = getattr(result, "html", "") or ""

    rendered_text = ""
    markdown = getattr(result, "markdown", None)
    if markdown is not None:
        rendered_text = (
            getattr(markdown, "fit_markdown", None)
            or getattr(markdown, "raw_markdown", None)
            or str(markdown)
        )
    if not rendered_text:
        rendered_text = getattr(result, "cleaned_html", "") or ""

    pdf_links = _collect_pdf_links(result, html, final_url)

    return {
        "status": "ok",
        "final_url": final_url,
        "html": html,
        "rendered_text": rendered_text,
        "pdf_links": pdf_links,
        "reason": "",
    }


if __name__ == "__main__":
    import asyncio

    test_url = (
        sys.argv[1] if len(sys.argv) > 1 else "https://haiphong.gov.vn/?pageid=27218&p_steering=126716"
    )

    async def _main() -> None:
        res = await fetch_rendered(test_url)
        print(f"status      : {res['status']}")
        print(f"final_url   : {res['final_url']}")
        print(f"reason      : {res['reason']}")
        print(f"html len    : {len(res['html'])}")
        print(f"text len    : {len(res['rendered_text'])}")
        print(f"pdf_links   : {len(res['pdf_links'])}")
        for link in res["pdf_links"][:5]:
            print(f"   - {link}")
        print("--- text preview ---")
        preview = res["rendered_text"][:500].encode("utf-8", errors="replace").decode("utf-8")
        print(preview)

    asyncio.run(_main())