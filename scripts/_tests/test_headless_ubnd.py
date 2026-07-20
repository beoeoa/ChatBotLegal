import asyncio, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'J:\ChatBotLegal')
from api.crawlers.headless_crawler import HeadlessCrawler

async def test():
    crawler = HeadlessCrawler(headless=True, timeout_ms=20000)
    url = 'https://haiphong.gov.vn/?pageid=27218&p_steering=126611'
    result = await crawler.crawl_ubnd_page(url, 'UBND Hai Phong')
    if result:
        print('SUCCESS')
        print('Title:', result['title'][:200])
        print('Law:', result['law_number'])
        print('Scope:', result['scope'])
        print('Content len:', result['content_len'])
        print('Content preview:', result['content'][:500])
        # Save
        with open(r'J:\ChatBotLegal\scripts\_tests\headless_ubnd_result.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    else:
        print('FAILED')
asyncio.run(test())
