import asyncio
import html
import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from loguru import logger


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
        # VBPL's RSC transport can mix Latin-1 control bytes with their
        # Windows-1252 punctuation equivalents (for example byte 0x91 appears
        # as U+2018). Rebuild the original byte stream deterministically.
        try:
            mixed_bytes = bytearray()
            for character in current:
                codepoint = ord(character)
                if codepoint <= 255:
                    mixed_bytes.append(codepoint)
                else:
                    encoded = character.encode("cp1252")
                    if len(encoded) != 1:
                        raise UnicodeEncodeError(
                            "cp1252", character, 0, 1, "not one byte"
                        )
                    mixed_bytes.extend(encoded)
            candidate = bytes(mixed_bytes).decode("utf-8")
            candidate_score = sum(
                candidate.count(marker) for marker in _MOJIBAKE_MARKERS
            )
            if candidate_score < best_score:
                best = candidate
                best_score = candidate_score
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for encoding in ("latin-1", "cp1252"):
            try:
                candidate = current.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidate_score = sum(
                candidate.count(marker) for marker in _MOJIBAKE_MARKERS
            )
            if candidate_score < best_score:
                best = candidate
                best_score = candidate_score
        if best == current:
            break
        current = best
    return current


def _extract_vbpl_document_page(payload: str) -> dict | None:
    """Extract one document-page object from a Next.js server-action response."""

    for line in str(payload or "").splitlines():
        _record_id, separator, raw_json = line.partition(":")
        if not separator or not raw_json.startswith("{"):
            continue
        try:
            candidate = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        items = candidate.get("items") if isinstance(candidate, dict) else None
        if not isinstance(items, list) or not items:
            continue
        first = items[0]
        if not isinstance(first, dict):
            continue
        if all(key in first for key in ("id", "title", "docNum")):
            return candidate
    return None


def _iso_to_listing_date(value: object) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        return f"{raw[8:10]}/{raw[5:7]}/{raw[:4]}"
    return raw


def _public_listing_url(url: str) -> tuple[str, int]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not (parsed.hostname or "").lower().endswith(
        "vbpl.vn"
    ):
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
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(public_query, doseq=True),
            "",
        )
    )
    return public_url, page_number


def _build_vbpl_listing_html(
    page: dict,
    source_url: str,
    *,
    page_number: int,
    has_next: bool | None = None,
) -> str:
    """Convert exact official server-action rows into deterministic anchors."""

    public_url, _ = _public_listing_url(source_url)
    parts = ["<html><body><main id=\"vbpl-rendered-listing\">"]
    items = page.get("items") if isinstance(page, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("id") or "").strip()
        title = _repair_mojibake_text(str(item.get("title") or "").strip())
        law_number = _repair_mojibake_text(str(item.get("docNum") or "").strip())
        document_type_raw = item.get("docType")
        document_type = _repair_mojibake_text(
            str(
                document_type_raw.get("name")
                if isinstance(document_type_raw, dict)
                else document_type_raw or ""
            ).strip()
        )
        issuing_agency = _repair_mojibake_text(
            str(item.get("agencyName") or "").strip()
        )
        issued_date = _iso_to_listing_date(item.get("issueDate"))
        effective_date = _iso_to_listing_date(item.get("effFrom"))
        if not document_id or not title:
            continue
        detail_url = f"https://vbpl.vn/van-ban/chi-tiet/{document_id}"
        parts.extend(
            (
                "<article class=\"vbpl-document\">",
                f'<a href="{html.escape(detail_url, quote=True)}">'
                f"{html.escape(title)}</a>",
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
    should_add_next = (
        has_next
        if has_next is not None
        else page_size > 0 and page_number * page_size < total
    )
    if should_add_next:
        parsed = urlparse(public_url)
        next_query = parse_qsl(parsed.query, keep_blank_values=True)
        next_query.append(("_crawler_page", str(page_number + 1)))
        next_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(next_query, doseq=True),
                "",
            )
        )
        parts.append(
            f'<a rel="next" href="{html.escape(next_url, quote=True)}">Sau</a>'
        )
    parts.append("</main></body></html>")
    return "".join(parts)


async def crawl_vbpl_listing(url: str) -> dict:
    """Render a dynamic VBPL listing and retain exact IDs from official RSC data."""

    public_url, target_page = _public_listing_url(url)
    logger.info(f"Rendering VBPL listing page {target_page}: {public_url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        captured_pages: list[dict] = []
        pending: set[asyncio.Task] = set()

        async def capture_response(response) -> None:
            if response.request.resource_type != "fetch":
                return
            if not str(response.headers.get("content-type") or "").startswith(
                "text/x-component"
            ):
                return
            try:
                payload = (await response.body()).decode("utf-8")
            except Exception:
                return
            document_page = _extract_vbpl_document_page(payload)
            if document_page:
                captured_pages.append(document_page)

        def queue_capture(response) -> None:
            task = asyncio.create_task(capture_response(response))
            pending.add(task)
            task.add_done_callback(pending.discard)

        page.on("response", queue_capture)
        try:
            await page.goto(public_url, wait_until="domcontentloaded", timeout=30_000)
            card_locator = page.locator('[class*="DocumentCard_documentCard"]')
            await card_locator.first.wait_for(state="visible", timeout=20_000)
            # Local listings first paint an unfiltered national page and then
            # apply the province filter through a server action. Wait for that
            # second exact document payload instead of treating the first
            # transient paint as the official source result.
            if "/van-ban/dia-phuong" in urlparse(public_url).path and any(
                key == "province"
                for key, _value in parse_qsl(
                    urlparse(public_url).query, keep_blank_values=True
                )
            ):
                for _ in range(32):
                    if len({int(item.get("total") or 0) for item in captured_pages}) >= 2:
                        break
                    await asyncio.sleep(0.25)
            for _current_page in range(1, target_page):
                previous_title = _repair_mojibake_text(
                    await page.locator(
                        '[class*="DocumentCard_documentTitle"]'
                    ).first.inner_text()
                )
                next_button = page.locator(
                    "li.ant-pagination-next:not(.ant-pagination-disabled)"
                )
                if await next_button.count() != 1:
                    raise ValueError("VBPL listing does not expose the requested page")
                await next_button.click()
                for _ in range(40):
                    await asyncio.sleep(0.25)
                    current_title = _repair_mojibake_text(
                        await page.locator(
                            '[class*="DocumentCard_documentTitle"]'
                        ).first.inner_text()
                    )
                    if current_title != previous_title:
                        break
                else:
                    raise TimeoutError("VBPL listing page did not advance")
            await asyncio.sleep(1)
            if pending:
                await asyncio.gather(*list(pending), return_exceptions=True)
            visible_title = _repair_mojibake_text(
                await page.locator(
                    '[class*="DocumentCard_documentTitle"]'
                ).first.inner_text()
            )
            selected: dict | None = None
            for candidate in reversed(captured_pages):
                items = candidate.get("items") or []
                if not items:
                    continue
                candidate_title = _repair_mojibake_text(
                    str(items[0].get("title") or "")
                )
                if candidate_title == visible_title:
                    selected = candidate
                    break
            if selected is None:
                raise ValueError("VBPL listing did not expose exact document records")
            has_next = (
                await page.locator(
                    "li.ant-pagination-next:not(.ant-pagination-disabled)"
                ).count()
                > 0
            )
            rendered_html = _build_vbpl_listing_html(
                selected,
                public_url,
                page_number=target_page,
                has_next=has_next,
            )
            return {
                "html": rendered_html,
                "final_url": public_url,
                "page_number": target_page,
                "total": int(selected.get("total") or 0),
                "success": True,
            }
        finally:
            await browser.close()

async def crawl_vbpl_url(url: str) -> dict:
    """
    Crawls a VBPL (vbpl.vn) URL using Playwright, waits for the dynamic content
    inside `div.preview-content` or `#toanvancontent` to load, and extracts the text.
    """
    logger.info(f"Crawling VBPL URL with Playwright helper: {url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            # Go to URL and wait for DOM load
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Wait for dynamic text content container to appear
            try:
                await page.wait_for_selector("div.preview-content, #toanvancontent", timeout=15000)
            except Exception as e:
                logger.warning(f"Timeout waiting for preview selector: {e}. Attempting to scrape body.")
            
            # Additional short sleep to let any final JS render
            await asyncio.sleep(2)
            
            # Get the page title
            title = await page.title()
            title = (
                title.replace(" | Cơ sở dữ liệu quốc gia về pháp luật", "")
                .replace(" | CSDL quốc gia về pháp luật", "")
                .strip()
            )
            
            # Extract content from preview-content or toanvancontent or body
            content_html = None
            selected_selector = None
            for selector in ["div.preview-content", "#toanvancontent", "body"]:
                try:
                    candidate_html = await page.eval_on_selector(selector, "el => el.innerHTML")
                    candidate_text = BeautifulSoup(candidate_html or "", "html.parser").get_text(" ", strip=True)
                    loading_only = (
                        "Đang tải dữ liệu" in candidate_text
                        or "Portal VBPL - Đang tải nội dung" in candidate_text
                    )
                    has_legal_structure = any(
                        marker in candidate_text
                        for marker in ("Điều 1", "Chương I", "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM")
                    )
                    accepted = (
                        bool(candidate_html and len(candidate_text) > 200 and not loading_only)
                        and (selector != "body" or (len(candidate_text) > 1000 and has_legal_structure))
                    )
                    if accepted:
                        content_html = candidate_html
                        selected_selector = selector
                        logger.info(f"Successfully extracted content using selector: {selector}")
                        break
                except Exception:
                    continue
            
            if not content_html:
                raise ValueError("VBPL page did not expose complete legal text after rendering")
                
            # Use BeautifulSoup to convert HTML to text/markdown
            soup = BeautifulSoup(content_html, "html.parser")
            
            # Decompose headers, scripts, styles to get clean text
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
                
            text = soup.get_text("\n").strip()
            
            # Clean up consecutive newlines
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            cleaned_text = "\n".join(lines)
            
            # Filter out the loading placeholders if they somehow persisted
            if "Đang tải dữ liệu" in cleaned_text or "Portal VBPL - Đang tải nội dung" in cleaned_text:
                raise ValueError("Scraped content contains only loading placeholder text")

            # Validate and improve title if it's generic / Error
            if not title or title.lower() in ("error", "trang chủ", "vbpl", "portal vbpl - đang tải nội dung", "csdl quốc gia về pháp luật", ""):
                for h_sel in ["h1", "h2", ".title-doc", ".doc-title", ".header-title"]:
                    try:
                        h_val = await page.eval_on_selector(h_sel, "el => el.innerText")
                        if h_val and len(h_val.strip()) > 5 and h_val.strip().lower() not in ("error", "trang chủ", "vbpl"):
                            title = h_val.strip()
                            break
                    except Exception:
                        pass

            if not title or title.lower() in ("error", "trang chủ", "vbpl", ""):
                for line in lines[:15]:
                    sline = line.strip()
                    if any(sline.upper().startswith(prefix) for prefix in ("LUẬT", "NGHỊ ĐỊNH", "QUYẾT ĐỊNH", "THÔNG TƯ", "NGHỊ QUYẾT", "CHỈ THỊ")):
                        title = sline
                        break

            if not title or title.lower() in ("error", "trang chủ", "vbpl", ""):
                slug = url.split("chi-tiet/")[-1].split("--")[0] if "chi-tiet/" in url else url.split("/")[-1]
                slug_title = slug.replace("-", " ").strip().capitalize()
                if slug_title:
                    title = slug_title
                
            return {
                "title": title or "Văn bản pháp luật",
                "content": cleaned_text,
                "html": content_html,
                "final_url": page.url,
                "selector": selected_selector,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"Failed to crawl VBPL URL {url}: {e}")
            raise ValueError(f"Failed to crawl VBPL URL: {str(e)}")
        finally:
            await browser.close()
