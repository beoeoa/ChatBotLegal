"""VBPL.vn crawler - crawl legal documents from Thư viện Pháp luật Vietnam."""

from __future__ import annotations
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from loguru import logger
from api.crawlers.base_crawler import BaseCrawler, normalize_text

class VBPLCrawler(BaseCrawler):
    """Specialized crawler for VBPL.vn - Thư viện Pháp luật Vietnam."""
    
    def extract_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1.title", "h1", ".vbpq-title", ".title-detail"]:
            el = soup.select_one(selector)
            if el:
                return normalize_text(el.get_text(strip=True))
        if soup.title:
            return normalize_text(soup.title.get_text(strip=True))
        return ""
    
    def extract_content(self, soup: BeautifulSoup) -> str:
        for selector in ["#contentPrint", ".content", ".vbpq-content", "#main-content"]:
            el = soup.select_one(selector)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                return normalize_text(el.get_text(separator="\n", strip=True))
        if soup.body:
            for tag in soup.select("nav, footer, .sidebar, .menu, .header, script, style"):
                tag.decompose()
            return normalize_text(soup.body.get_text(separator="\n", strip=True))
        return ""
    
    def extract_metadata(self, soup: BeautifulSoup) -> dict:
        meta = {}
        selectors = {
            "law_number": [".sohieu", ".law-number", ".vb-number"],
            "issuing_agency": [".coquanbanhanh", ".issuing-agency"],
            "issued_date": [".ngaybanhanh", ".issued-date"],
            "effective_date": [".ngaycohieuluc", ".effective-date"],
            "expired_date": [".ngayhethieuluc", ".expired-date"],
        }
        for key, css_list in selectors.items():
            for css in css_list:
                el = soup.select_one(css)
                if el:
                    meta[key] = normalize_text(el.get_text(strip=True))
                    break
        return meta
    
    def discover_sitemap_urls(self, sitemap_url: str) -> list[str]:
        urls = []
        try:
            import xml.etree.ElementTree as ET
            resp = self.client.get(sitemap_url)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for url_elem in root.findall(".//sm:url", ns):
                loc = url_elem.find("sm:loc", ns)
                if loc is not None and loc.text:
                    urls.append(loc.text.strip())
            for sitemap_elem in root.findall(".//sm:sitemap", ns):
                loc = sitemap_elem.find("sm:loc", ns)
                if loc is not None and loc.text:
                    urls.extend(self.discover_sitemap_urls(loc.text.strip()))
        except Exception as e:
            logger.warning(f"Failed to parse sitemap {sitemap_url}: {e}")
        return urls
    
    def filter_vbpl_urls(self, urls: list[str]) -> list[str]:
        """Only keep actual legal document pages."""
        patterns = ["/chi-tiet/", "/Pages/vbpq-toanvan.aspx", "/VBPL/"]
        filtered = []
        for url in urls:
            if any(p in url for p in patterns):
                filtered.append(url)
        return filtered
    
    def crawl(self) -> list[dict]:
        results = []
        if self.source.sitemap_url:
            all_urls = self.discover_sitemap_urls(self.source.sitemap_url)
            legal_urls = self.filter_vbpl_urls(all_urls)
            urls = legal_urls[:self.source.max_per_run]
        else:
            urls = []
            for list_url in self.source.list_urls:
                html = self.fetch_page(list_url)
                if html:
                    urls.extend(self.discover_links(html, list_url))
        
        for url in urls:
            html = self.fetch_page(url)
            if not html:
                continue
            soup = self.parse_html(html)
            title = self.extract_title(soup)
            content = self.extract_content(soup)
            if not content or len(content) < 100:
                continue
            meta = self.extract_metadata(soup)
            doc = self.build_document(url, title, content, meta)
            results.append(doc)
        
        logger.info(f"VBPL crawled {len(results)} documents")
        return results
