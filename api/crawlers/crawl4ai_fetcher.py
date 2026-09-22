"""
Shared Crawl4AI fetcher for JavaScript-rendered legal pages.

Renders a URL with a headless browser (via the pinned Crawl4AI package),
waits for the network to settle, and returns a normalized result dict:

    {
        "status": "ok" | "unavailable" | "error",
        "final_url": str,
        "html": str,
        "rendered_text": str,
        "pdf_links": list[str],
        "reason": str,   # only when status != "ok"
    }

The fetcher fails gracefully: if Crawl4AI / Chromium is not installed or a
render fails, it returns a result with ``status`` set and a human-readable
``reason`` instead of raising. Callers must not silently switch to a different
HTML transport after a Crawl4AI failure.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from loguru import logger

try:  # crawl4ai is pinned in the project runtime dependencies
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

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


def _source_compatibility_profile(url: str, *, high_compatibility: bool) -> dict[str, Any]:
    """Return standards-compliant rendering tweaks for a known public source.

    Some official sites expose record navigation only through JavaScript click
    handlers instead of anchor tags.  In high compatibility mode we let the
    page build its own public detail URLs, capture those URLs, and append
    ordinary anchors to the rendered DOM for the deterministic listing parser.
    This does not bypass robots.txt, CAPTCHA, authentication or access controls.
    """

    if not high_compatibility:
        return {}

    host = (urlparse(url).hostname or "").lower()
    path = urlparse(url).path.rstrip("/")
    if host not in {"vbpl.vn", "www.vbpl.vn"} or path not in {
        "/van-ban/trung-uong",
        "/van-ban/dia-phuong",
    }:
        return {}

    return {
        "wait_for": 'css:[class*="DocumentCard_documentTitle"]',
        "wait_for_timeout": 15000,
        "js_code": r"""
(async () => {
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let attempt = 0; attempt < 30; attempt += 1) {
    if (document.querySelector('[class*="DocumentCard_documentTitle"]')) break;
    await delay(250);
  }

  const titles = Array.from(
    document.querySelectorAll('[class*="DocumentCard_documentTitle"]')
  );
  if (!titles.length) return 0;

  const captured = [];
  const originalOpen = window.open;
  try {
    window.open = (target) => {
      if (typeof target === 'string' && target.trim()) captured.push(target.trim());
      return null;
    };
    for (const title of titles) {
      const clickTarget = title.closest('span.cursor-pointer') || title.parentElement || title;
      clickTarget.dispatchEvent(new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        view: window,
      }));
    }
  } finally {
    window.open = originalOpen;
  }

  document.getElementById('crawl4ai-vbpl-detail-links')?.remove();
  const holder = document.createElement('section');
  holder.id = 'crawl4ai-vbpl-detail-links';
  holder.setAttribute('aria-label', 'Liên kết chi tiết văn bản');
  holder.style.display = 'none';

  titles.forEach((title, index) => {
    const target = captured[index];
    if (!target) return;
    const anchor = document.createElement('a');
    anchor.href = new URL(target, window.location.href).href;
    anchor.textContent = (title.textContent || '').trim();
    anchor.setAttribute('data-crawl4ai-record-link', 'vbpl');
    holder.appendChild(anchor);
  });
  document.body.appendChild(holder);
  return holder.querySelectorAll('a').length;
})()
""",
    }


def _resolve_browser_channel() -> str:
    """Use an installed browser when Playwright's bundled Chromium is absent."""

    configured = str(os.getenv("CRAWL4AI_BROWSER_CHANNEL") or "").strip().lower()
    if configured in {"chromium", "chrome", "msedge"}:
        return configured
    if os.name == "nt":
        candidates = (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google/Chrome/Application/chrome.exe",
        )
        if any(path.is_file() for path in candidates):
            return "chrome"
    return "chromium"


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
    check_robots_txt: bool = True,
    compatibility_mode: str = "standard",
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
                "Install the project dependencies and run `patchright install chromium`."
            ),
        }

    high_compatibility = str(compatibility_mode or "standard").lower() == "high"
    source_profile = _source_compatibility_profile(
        url,
        high_compatibility=high_compatibility,
    )
    browser_channel = _resolve_browser_channel()
    browser_config = BrowserConfig(
        browser_type="chromium",
        chrome_channel=browser_channel,
        channel=browser_channel,
        headless=headless,
        user_agent=USER_AGENT,
        viewport_width=1366,
        viewport_height=900,
        enable_stealth=True,
        memory_saving_mode=True,
    )
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until=wait_until,
        page_timeout=timeout_ms,
        delay_before_return_html=max(0.0, settle_ms / 1000.0),
        check_robots_txt=check_robots_txt,
        # Compatibility mode is intentionally limited to standards-compliant
        # rendering features. It does not disable robots checks, solve CAPTCHA,
        # rotate proxies or use an undetected browser adapter.
        max_retries=1 if high_compatibility else 0,
        only_text=False,
        process_iframes=high_compatibility,
        flatten_shadow_dom=high_compatibility,
        remove_overlay_elements=high_compatibility,
        remove_consent_popups=high_compatibility,
        scan_full_page=high_compatibility,
        max_scroll_steps=8 if high_compatibility else None,
        preserve_https_for_internal_links=True,
        **source_profile,
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
            "status_code": getattr(result, "status_code", None),
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
        "status_code": getattr(result, "status_code", None),
        "final_url": final_url,
        "html": html,
        "rendered_text": rendered_text,
        "pdf_links": pdf_links,
        "reason": "",
    }


if __name__ == "__main__":
    import asyncio
    import sys

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
