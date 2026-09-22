# api/crawlers/base_crawler.py
"""
Base crawler with shared utilities for fetching, parsing, and normalizing
legal documents from any source.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from loguru import logger

from api.crawlers.source_registry import LegalSource

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/131.0.0.0 Safari/537.36'
)

# Common Vietnamese legal metadata patterns
LAW_NUMBER_PATTERN = re.compile(
    r'(\d{1,4}/\d{2,4}/[A-ZĐÀ-Ỹ\-]+(?:/[A-ZĐÀ-Ỹ\-]+)*)',
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
)
ISSUING_AGENCY_PATTERNS = [
    re.compile(
        r'(UBND|ỦY BAN NHÂN DÂN|HỘI ĐỒNG NHÂN DÂN|CHÍNH PHỦ|BỘ|SỞ)'
        r'\s+[A-ZĐÀ-Ỹ\s]+',
        re.IGNORECASE,
    ),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(text: str) -> str:
    """Normalize whitespace while preserving Vietnamese Unicode."""
    return ' '.join(text.split())


def extract_law_number(text: str) -> str | None:
    """Extract law/decree/circular number from text."""
    m = LAW_NUMBER_PATTERN.search(text)
    return m.group(1) if m else None


def extract_date(text: str) -> str | None:
    """Extract first date in DD/MM/YYYY or DD-MM-YYYY format."""
    m = DATE_PATTERN.search(text)
    if m:
        parts = re.split(r'[/-]', m.group(1))
        if len(parts) == 3:
            day, month, year = parts
            if len(year) == 2:
                year = '20' + year if int(year) <= 50 else '19' + year
            return f'{year}-{month.zfill(2)}-{day.zfill(2)}'
    return None


def extract_issuing_agency(text: str) -> str | None:
    """Extract issuing agency from text."""
    for pattern in ISSUING_AGENCY_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


def detect_scope(title: str, content: str, source_url: str = '') -> str:
    """Detect scope: central, haiphong, or local."""
    raw_text = unicodedata.normalize('NFD', f'{title} {content} {source_url}'.casefold())
    text = ''.join(char for char in raw_text if unicodedata.category(char) != 'Mn')
    text = text.replace('đ', 'd')
    
    local_keywords = [
        r'\bphuong\s+\w+', r'\bxa\s+\w+', r'\bubnd\s+phuong', r'\bubnd\s+xa',
        r'\ble\s+chan\b', r'\bnguyen\s+cong\s+tru\b', r'\bcau\s+dat\b',
    ]
    haiphong_keywords = [
        r'\bhai\s+phong\b', r'\btp\s+hai\s+phong\b', r'\bthanh\s+pho\s+hai\s+phong\b',
        r'ubnd\s+tp\s*hai', r'\bo\s+hai\s+phong\b',
    ]
    
    for pattern in local_keywords:
        if re.search(pattern, text, re.IGNORECASE):
            return 'local'
    for pattern in haiphong_keywords:
        if re.search(pattern, text, re.IGNORECASE):
            return 'haiphong'
    return 'central'


def content_fingerprint(content: str) -> str:
    """Create a fingerprint of content for dedup."""
    normalized = ' '.join(content.lower().split())[:2000]
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class BaseCrawler:
    """Base crawler for fetching and parsing legal documents."""
    
    def __init__(self, source: LegalSource):
        self.source = source
        self._last_fetch: dict[str, Any] = {
            "url": "",
            "html": "",
            "rendered_text": "",
            "pdf_links": [],
            "status": "",
            "reason": "",
        }
    
    def fetch_page(self, url: str) -> str | None:
        """Fetch a page through Crawl4AI and return rendered HTML.

        HTML crawling has one deterministic engine. Missing browser support is
        fail-closed and never falls back to a plain HTTP response.
        """
        self._last_fetch = {
            "url": url,
            "html": "",
            "rendered_text": "",
            "pdf_links": [],
            "status": "pending",
            "reason": "",
        }

        try:
            import asyncio

            from api.crawlers.crawl4ai_fetcher import fetch_rendered

            result = asyncio.run(fetch_rendered(url, check_robots_txt=True))
            self._last_fetch.update({
                "status": result.get("status", "error"),
                "html": result.get("html", "") or "",
                "rendered_text": result.get("rendered_text", "") or "",
                "pdf_links": result.get("pdf_links", []) or [],
                "reason": result.get("reason", "") or "",
                "url": result.get("final_url", url) or url,
            })
            html = self._last_fetch["html"]
            if self._last_fetch["status"] == "ok" and html and len(html) > 100:
                return html
            logger.warning(
                f"crawl4ai fetch weak for {url}: status={self._last_fetch['status']} "
                f"reason={self._last_fetch['reason'][:160]}"
            )
            return None
        except Exception as e:
            logger.warning(f"crawl4ai fetch failed for {url}: {e}")
            self._last_fetch.update({"status": "error", "reason": str(e)})
            return None
    
    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML into BeautifulSoup."""
        return BeautifulSoup(html, 'html.parser')
    
    def extract_title(self, soup: BeautifulSoup) -> str:
        """Extract title using configured selector."""
        if self.source.title_selector:
            el = soup.select_one(self.source.title_selector)
            if el:
                return normalize_text(el.get_text(strip=True))
        # Fallback to <title> tag
        if soup.title:
            return normalize_text(soup.title.get_text(strip=True))
        return ''
    
    def extract_content(self, soup: BeautifulSoup) -> str:
        """Extract main content using configured selector."""
        if self.source.content_selector:
            el = soup.select_one(self.source.content_selector)
            if el:
                return normalize_text(el.get_text(separator='\n', strip=True))
        # Fallback to <body>
        if soup.body:
            # Try to remove nav, footer, sidebar
            for tag in soup.select('nav, footer, .sidebar, .menu, .header, script, style'):
                tag.decompose()
            return normalize_text(soup.body.get_text(separator='\n', strip=True))
        return ''
    
    def extract_metadata(self, soup: BeautifulSoup) -> dict[str, Any]:
        """Extract metadata using configured selectors."""
        meta = {}
        for key, selector in self.source.metadata_selectors.items():
            el = soup.select_one(selector)
            if el:
                meta[key] = normalize_text(el.get_text(strip=True))
        return meta
    
    def build_document(
        self, url: str, title: str, content: str, extra_meta: dict | None = None
    ) -> dict[str, Any]:
        """Build a standardized document record."""
        combined = title + ' ' + content
        law_number = extract_law_number(combined) or extract_law_number(title) or ''
        issued_date = extract_date(combined) or ''
        issuing_agency = (
            extract_issuing_agency(combined) 
            or self.source.default_issuing_agency 
            or ''
        )
        scope = detect_scope(title, content, url)
        
        doc = {
            'title': title or 'Untitled',
            'law_number': law_number,
            'document_type': self.source.default_document_type,
            'issuing_agency': issuing_agency,
            'scope': scope,
            'sector': self.source.default_sector,
            'issued_date': issued_date,
            'effective_date': extra_meta.get('effective_date', issued_date) if extra_meta else issued_date,
            'expired_date': extra_meta.get('expired_date', '') if extra_meta else '',
            'source_url': url,
            'source_id': self.source.source_id,
            'source_type': self.source.source_type.value,
            'official_level': self.source.official_level.value,
            'source_name': self.source.name,
            'content': content,
            'content_fingerprint': content_fingerprint(content),
            'crawled_at': utcnow(),
            'pdf_links': list((extra_meta or {}).get('pdf_links') or []),
        }
        return doc
    
    def discover_links(self, html: str, base_url: str) -> list[str]:
        """Discover article links from a list page."""
        soup = self.parse_html(html)
        links = []
        for a in soup.select('a[href]'):
            href = a.get('href', '')
            if not href:
                continue
            # Build absolute URL
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            # Only same domain
            source_parsed = urlparse(self.source.base_url)
            if parsed.netloc != source_parsed.netloc:
                continue
            # Skip non-article pages
            skip_patterns = [
                'login', 'signup', 'register', 'search', 'rss', 'feed',
                'javascript:', 'mailto:', '#', '.pdf', '.doc', '.docx', '.xls',
                'facebook.com', 'twitter.com', 'youtube.com',
            ]
            if any(p in full_url.lower() for p in skip_patterns):
                continue
            if full_url not in links:
                links.append(full_url)
        return links
    
    def discover_sitemap_urls(self, sitemap_url: str) -> list[str]:
        """Discover URLs from a sitemap.xml."""
        try:
            content = self.fetch_page(sitemap_url)
            if not content:
                return []
            soup = self.parse_html(content)
            urls = []
            for loc in soup.select('url loc, sitemap loc'):
                urls.append(loc.get_text(strip=True))
            return urls
        except Exception as e:
            logger.warning(f'Failed to fetch sitemap {sitemap_url}: {e}')
            return []
    
    def crawl(self) -> list[dict[str, Any]]:
        """Execute crawl for this source. Returns list of document dicts."""
        results = []
        urls_to_crawl = []
        
        strategy = self.source.crawl_strategy
        
        if strategy == 'sitemap' and self.source.sitemap_url:
            urls_to_crawl = self.discover_sitemap_urls(self.source.sitemap_url)
        elif strategy == 'list_page':
            for list_url in self.source.list_urls:
                html = self.fetch_page(list_url)
                if html:
                    urls_to_crawl.extend(self.discover_links(html, list_url))
        
        # Limit per run
        urls_to_crawl = urls_to_crawl[:self.source.max_per_run]
        
        for url in urls_to_crawl:
            html = self.fetch_page(url)
            if not html:
                continue
            soup = self.parse_html(html)
            title = self.extract_title(soup)
            content = self.extract_content(soup)
            if not content or len(content) < 100:
                continue
            
            metadata = self.extract_metadata(soup)
            doc = self.build_document(url, title, content, metadata)
            results.append(doc)
        
        logger.info(f'Crawled {len(results)} documents from {self.source.name}')
        return results
