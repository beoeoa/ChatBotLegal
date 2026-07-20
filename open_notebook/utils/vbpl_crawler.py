import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from loguru import logger

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
            title = title.replace(" | Cơ sở dữ liệu quốc gia về pháp luật", "").strip()
            
            # Extract content from preview-content or toanvancontent or body
            content_html = None
            for selector in ["div.preview-content", "#toanvancontent", "body"]:
                try:
                    content_html = await page.eval_on_selector(selector, "el => el.innerHTML")
                    if content_html and len(content_html.strip()) > 200:
                        logger.info(f"Successfully extracted content using selector: {selector}")
                        break
                except Exception:
                    continue
            
            if not content_html:
                content_html = await page.content()
                
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
            if "Đang tải dữ liệu" in cleaned_text and len(cleaned_text) < 1000:
                raise ValueError("Scraped content contains only loading placeholder text")
                
            return {
                "title": title,
                "content": cleaned_text,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"Failed to crawl VBPL URL {url}: {e}")
            raise ValueError(f"Failed to crawl VBPL URL: {str(e)}")
        finally:
            await browser.close()
