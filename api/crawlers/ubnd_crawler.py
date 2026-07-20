"""Crawler for UBND Hai Phong and department-level websites."""

from __future__ import annotations
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from loguru import logger
from api.crawlers.base_crawler import BaseCrawler, normalize_text, extract_date

class UBNDCrawler(BaseCrawler):
    """Crawler for UBND (People Committee) and So Ban Nganh websites."""
    
    def extract_title(self, soup: BeautifulSoup) -> str:
        for sel in [
            "h1", ".title", ".detail-title", ".article-title",
            ".news-title", ".post-title", ".entry-title", ".page-title"
        ]:
            el = soup.select_one(sel)
            if el:
                return normalize_text(el.get_text(strip=True))
        if soup.title:
            return normalize_text(soup.title.get_text(strip=True))
        return ""
    
    def extract_content(self, soup: BeautifulSoup) -> str:
        for sel in [
            "#main-content", ".detail-content", ".article-content",
            ".news-content", ".post-content", ".entry-content",
            ".content", ".noidung", "article", ".main-content"
        ]:
            el = soup.select_one(sel)
            if el:
                for tag in el.select("script, style, iframe"):
                    tag.decompose()
                text = normalize_text(el.get_text(separator="\n", strip=True))
                if len(text) > 100:
                    return text
        
        if soup.body:
            for tag in soup.select("nav, footer, .sidebar, .menu, .header, script, style"):
                tag.decompose()
            return normalize_text(soup.body.get_text(separator="\n", strip=True))
        return ""
    
    def extract_metadata(self, soup: BeautifulSoup) -> dict:
        meta = {}
        for sel, key in [
            (".published-date, .created-date, .date, .ngaydang", "issued_date"),
            (".author, .nguon, .source", "issuing_agency"),
        ]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if key == "issued_date":
                    text = extract_date(text) or text
                meta[key] = text
        return meta
    
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
            meta = self.extract_metadata(soup)

            # Collect PDF links from last fetch + page anchors
            pdf_links = list(getattr(self, "_last_fetch", {}).get("pdf_links") or [])
            for a in soup.select('a[href]'):
                href = a.get("href") or ""
                if ".pdf" in href.lower():
                    pdf_links.append(urljoin(url, href))
            # dedupe preserve order
            seen = set()
            deduped = []
            for p in pdf_links:
                if p not in seen:
                    seen.add(p)
                    deduped.append(p)
            pdf_links = deduped
            meta["pdf_links"] = pdf_links

            # If HTML content is thin but PDFs exist, extract first usable PDF text
            if (not content or len(content) < 100) and pdf_links:
                try:
                    import asyncio
                    from api.crawlers.pdf_extractor import extract_pdf

                    async def _extract_first() -> str:
                        for pdf_url in pdf_links[:3]:
                            res = await extract_pdf(pdf_url)
                            if res.get("status") == "ok":
                                if len(res.get("text") or "") >= 100:
                                    meta["pdf_source_url"] = pdf_url
                                    meta["content_source"] = "pdf"
                                    meta["needs_ocr"] = False
                                    return res["text"]
                                elif res.get("needs_ocr"):
                                    meta["pdf_source_url"] = pdf_url
                                    meta["content_source"] = "pdf_scanned"
                                    meta["needs_ocr"] = True
                                    meta["ocr_status"] = res.get("ocr_status")
                                    return res.get("text") or "[PDF Quét/Scanned - Cần chạy OCR để xem nội dung]"
                        return ""

                    try:
                        content = asyncio.run(_extract_first())
                    except RuntimeError:
                        # nested loop safety
                        content = asyncio.get_event_loop().run_until_complete(_extract_first())  # type: ignore[attr-defined]
                except Exception as e:
                    logger.warning(f"PDF fallback failed for {url}: {e}")

            # Optional: prefer rendered_text if richer than extracted content
            rendered = (getattr(self, "_last_fetch", {}) or {}).get("rendered_text") or ""
            if rendered and len(rendered) > len(content or ""):
                # only use rendered when content is still weak
                if not content or len(content) < max(100, 300):
                    content = rendered
                    meta["content_source"] = meta.get("content_source") or "rendered_text"

            if not content or len(content) < 100:
                continue

            doc = self.build_document(url, title, content, meta)
            doc["content_len"] = len(content)
            doc["pdf_links"] = pdf_links
            if meta.get("pdf_source_url"):
                doc["pdf_source_url"] = meta["pdf_source_url"]
            results.append(doc)

        logger.info(f"ubnd_crawler crawled {len(results)} documents from {self.source.name}")
        return results

