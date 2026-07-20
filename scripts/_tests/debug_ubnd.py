import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

async def debug():
    url = 'https://haiphong.gov.vn/?pageid=27218&p_steering=126611'
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)
        title = await page.title()
        print(f'Title: {title}')
        # Get body text
        body = await page.inner_text('body')
        print(f'Body length: {len(body)} chars')
        # Check iframes
        frames = page.frames
        print(f'Frames: {len(frames)}')
        for f in frames:
            print(f'  Frame URL: {f.url[:100]}')
        # Check for iframe content
        iframe_texts = []
        for frame in frames[1:]:  # skip main
            try:
                t = await frame.inner_text('body')
                iframe_texts.append((frame.url[:80], len(t)))
            except:
                pass
        print(f'Iframe texts: {iframe_texts}')
        await browser.close()

asyncio.run(debug())
