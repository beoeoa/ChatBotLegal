"""Crawler for DVC (Dịch vụ công) portals - national and Hai Phong."""

from __future__ import annotations
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from loguru import logger
from api.crawlers.base_crawler import BaseCrawler, normalize_text, extract_date

class DVCCrawler(BaseCrawler):
    """Crawler for administrative procedure portals (Dịch vụ công)."""
    
    def extract_title(self, soup: BeautifulSoup) -> str:
        for sel in ["h1", ".title", ".thutuc-title", ".procedure-title", ".name"]:
            el = soup.select_one(sel)
            if el:
                return normalize_text(el.get_text(strip=True))
        if soup.title:
            return normalize_text(soup.title.get_text(strip=True))
        return ""
    
    def extract_content(self, soup: BeautifulSoup) -> str:
        for sel in ["#main-content", ".thutuc-content", ".procedure-content", ".content", "article"]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style"):
                    tag.decompose()
                return normalize_text(el.get_text(separator="\n", strip=True))
        if soup.body:
            for tag in soup.select("nav, footer, .sidebar, .menu, .header, script, style"):
                tag.decompose()
            return normalize_text(soup.body.get_text(separator="\n", strip=True))
        return ""
    
    def extract_procedure_fields(self, soup: BeautifulSoup) -> dict:
        """Extract authority, processing time, fee, dossier and form metadata."""
        text = soup.get_text(separator="\n")
        fields = {}
        
        patterns = {
            "co_quan_tiep_nhan": [
                r"Cơ quan tiếp nhận[\s:]+(.+?)(?:\n|$)",
                r"Cơ quan thực hiện[\s:]+(.+?)(?:\n|$)",
            ],
            "thoi_han_xu_ly": [
                r"Thời hạn giải quyết[\s:]+(.+?)(?:\n|$)",
                r"Thời gian xử lý[\s:]+(.+?)(?:\n|$)",
            ],
            "le_phi": [
                r"Lệ phí[\s:]+(.+?)(?:\n|$)",
                r"Phí[\s:]+(.+?)(?:\n|$)",
            ],
            "thanh_phan_ho_so": [
                r"Thành phần hồ sơ[\s:]+(.+?)(?:\n|$)",
                r"Hồ sơ bao gồm[\s:]+(.+?)(?:\n|$)",
            ],
        }
        
        for field, regex_list in patterns.items():
            for regex in regex_list:
                m = re.search(regex, text, re.IGNORECASE)
                if m:
                    fields[field] = m.group(1).strip()
                    break
        
        return fields
    
    def discover_form_links(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Discover downloadable form links."""
        forms = []
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            text = a.get_text(strip=True).lower()
            if any(kw in text for kw in ["mẫu", "biểu mẫu", "tải", "download", "form", ".doc", ".pdf"]):
                if any(href.lower().endswith(ext) for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx"]):
                    forms.append({
                        "name": a.get_text(strip=True),
                        "url": urljoin(base_url, href),
                        "type": href.rsplit(".", 1)[-1].lower(),
                    })
        return forms
    
    def crawl(self) -> list[dict]:
        results = []
        urls = []
        
        for list_url in self.source.list_urls:
            html = self.fetch_page(list_url)
            if not html:
                continue
            urls.extend(self.discover_links(html, list_url))
        
        urls = urls[:self.source.max_per_run]
        
        for url in urls:
            html = self.fetch_page(url)
            if not html:
                continue
            soup = self.parse_html(html)
            title = self.extract_title(soup)
            content = self.extract_content(soup)
            if not content or len(content) < 50:
                continue
            
            proc_fields = self.extract_procedure_fields(soup)
            forms = self.discover_form_links(soup, url)
            
            doc = self.build_document(url, title, content, proc_fields)
            doc["procedure_fields"] = proc_fields
            doc["forms"] = forms
            
            results.append(doc)
        
        logger.info(f"DVC crawled {len(results)} procedures from {self.source.name}")
        return results
