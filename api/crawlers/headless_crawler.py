"""
Headless browser crawler for JavaScript-rendered legal websites.
Uses Playwright to render SPA pages and extract content.
Supports: VBPL.vn (Next.js), UBND Hai Phong (dynamic), DVC portals.
"""

from __future__ import annotations
import asyncio, re, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from loguru import logger

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_fingerprint(content: str) -> str:
    normalized = ' '.join(content.lower().split())[:2000]
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class HeadlessCrawler:
    """Crawl JavaScript-rendered pages using Playwright."""

    def __init__(self, headless: bool = True, timeout_ms: int = 30000):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("playwright not installed. Run: pip install playwright && playwright install chromium")
        self.headless = headless
        self.timeout_ms = timeout_ms

    async def fetch_page_content(self, url: str) -> dict[str, Any] | None:
        """Fetch and extract content from a JS-rendered page."""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                page = await browser.new_page()
                await page.goto(url, wait_until='networkidle', timeout=self.timeout_ms)
                
                # Wait for content to render
                await page.wait_for_timeout(2000)
                
                # Get title
                title = await page.title()
                
                # Try to get H1
                try:
                    h1 = await page.inner_text('h1')
                except Exception:
                    h1 = ''
                
                # Get main content
                content = ''
                for selector in [
                    '#contentPrint', '.content', '.vbpq-content',
                    '#main-content', '.detail-content', '.article-content',
                    'article', '[class*=content]', '[class*=detail]',
                ]:
                    try:
                        el = page.locator(selector).first
                        if await el.count() > 0:
                            content = await el.inner_text()
                            if len(content) > 100:
                                break
                    except Exception:
                        continue
                
                # Fallback: get body text
                if len(content) < 100:
                    try:
                        content = await page.inner_text('body')
                    except Exception:
                        content = ''
                
                # Clean content
                content = ' '.join(content.split())
                
                # Get URL after redirects
                final_url = page.url
                
                # Get HTML for metadata extraction
                html = await page.content()
                
                await browser.close()
                
                return {
                    'url': final_url,
                    'title': ' '.join(title.split()) if title else '',
                    'h1': ' '.join(h1.split()) if h1 else '',
                    'content': content,
                    'content_len': len(content),
                    'html': html,
                }
        except Exception as e:
            logger.error(f"HeadlessCrawler failed for {url}: {e}")
            return None

    async def extract_metadata_from_html(self, html: str) -> dict[str, str]:
        """Extract legal metadata from rendered HTML."""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        meta = {}
        # Common VBPL metadata selectors
        selectors = {
            'law_number': ['.sohieu', '.law-number'],
            'issuing_agency': ['.coquanbanhanh', '.issuing-agency'],
            'issued_date': ['.ngaybanhanh', '.issued-date'],
            'effective_date': ['.ngaycohieuluc', '.effective-date'],
            'expired_date': ['.ngayhethieuluc', '.expired-date'],
        }
        
        for key, css_list in selectors.items():
            for css in css_list:
                el = soup.select_one(css)
                if el:
                    meta[key] = ' '.join(el.get_text(strip=True).split())
                    break
        
        return meta

    async def crawl_vbpl_page(self, url: str) -> dict[str, Any] | None:
        """Crawl a VBPL.vn document page."""
        page_data = await self.fetch_page_content(url)
        if not page_data or not page_data.get('content'):
            return None
        
        html = page_data.get('html', '')
        metadata = await self.extract_metadata_from_html(html)
        
        # Extract law number from title/content
        text = f"{page_data['title']} {page_data['content'][:5000]}"
        law_number = ''
        m = re.search(r'(\d{1,4}/\d{4}/[A-ZĐÀ-Ỹ\-]+)', text, re.IGNORECASE)
        if m:
            law_number = m.group(1)
        
        # Detect scope
        scope = 'central'
        if re.search(r'hải phòng|tp hải phòng|thành phố hải phòng', text, re.IGNORECASE):
            scope = 'haiphong'
        
        return {
            'title': page_data['title'] or metadata.get('title', ''),
            'law_number': law_number or metadata.get('law_number', ''),
            'document_type': metadata.get('document_type', 'VanBanPhapLuat'),
            'issuing_agency': metadata.get('issuing_agency', ''),
            'scope': scope,
            'sector': 'PhapLuat',
            'issued_date': metadata.get('issued_date', ''),
            'effective_date': metadata.get('effective_date', ''),
            'expired_date': metadata.get('expired_date', ''),
            'source_url': url,
            'source_name': 'VBPL.vn',
            'official_level': 'reference',
            'content': page_data['content'][:100000],
            'content_fingerprint': content_fingerprint(page_data['content']),
            'crawled_at': utcnow(),
        }

    async def crawl_ubnd_page(self, url: str, source_name: str = 'UBND') -> dict[str, Any] | None:
        """Crawl an UBND/government page."""
        page_data = await self.fetch_page_content(url)
        if not page_data or not page_data.get('content'):
            return None
        
        text = f"{page_data['title']} {page_data['content'][:5000]}"
        
        # Try extract law number
        law_number = ''
        m = re.search(r'(?:Số|số|SỐ)[\s.:]*(\d+[\d/]*[A-ZĐÀ-Ỹ/\-]+)', text)
        if m:
            law_number = m.group(1)
        
        scope = 'haiphong'
        if re.search(r'hải phòng', text, re.IGNORECASE):
            scope = 'haiphong'
        elif re.search(r'phường|quận|huyện|xã', text, re.IGNORECASE):
            scope = 'local'
        
        return {
            'title': page_data['title'],
            'law_number': law_number,
            'document_type': 'VanBanPhapLuat',
            'issuing_agency': source_name,
            'scope': scope,
            'sector': 'HanhChinhCong',
            'issued_date': '',
            'effective_date': '',
            'expired_date': '',
            'source_url': url,
            'source_name': source_name,
            'official_level': 'official',
            'content': page_data['content'][:100000],
            'content_fingerprint': content_fingerprint(page_data['content']),
            'crawled_at': utcnow(),
        }


# Test function
async def test_crawl(url: str, source: str = 'vbpl'):
    crawler = HeadlessCrawler(headless=True)
    if source == 'vbpl':
        result = await crawler.crawl_vbpl_page(url)
    else:
        result = await crawler.crawl_ubnd_page(url, source)
    
    if result:
        print(f"Title: {result['title'][:100]}")
        print(f"Law number: {result['law_number']}")
        print(f"Scope: {result['scope']}")
        print(f"Content: {result['content_len']} chars")
        print(f"Source: {result['source_name']} ({result['official_level']})")
    else:
        print("FAILED to crawl")
    return result


if __name__ == '__main__':
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-249-2025-nd-cp-quy-dinh-co-che-chinh-sach-thu-hut-chuyen-gia-khoa-hoc-cong-nghe-doi-moi-sang-tao-va-chuyen-doi-so--187193'
    source = sys.argv[2] if len(sys.argv) > 2 else 'vbpl'
    asyncio.run(test_crawl(url, source))
