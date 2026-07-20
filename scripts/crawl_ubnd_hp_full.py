import asyncio, sys, io, json, re, time, httpx
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright

LEGAL_SEARCH_URL = 'http://127.0.0.1:8765'
LIST_URL = 'https://haiphong.gov.vn/Van-ban-quy-pham-phap-luat/'

async def crawl_ubnd_hp_list():
    print(f'Fetching: {LIST_URL}')
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(LIST_URL, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)
        
        # Get all links with p_steering param
        links = await page.evaluate('''() => {
            const links = [];
            document.querySelectorAll('a[href*="p_steering"]').forEach(a => {
                links.push({href: a.href, text: a.textContent.trim()});
            });
            return links;
        }''')
        
        html = await page.content()
        await browser.close()
        
    print(f'Found {len(links)} steering links')
    
    # Filter legal-looking links
    legal_kw = ['nghị định', 'quyết định', 'thông tư', 'nghị quyết', 'chỉ thị', 'sao y', 'công bố']
    legal_links = []
    for l in links:
        text_lower = l['text'].lower()
        if any(kw in text_lower for kw in legal_kw) and len(l['text']) > 20:
            legal_links.append(l)
    
    print(f'Filtered {len(legal_links)} legal-looking links')
    
    # Also parse HTML for more links
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    bs_links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href','')
        if 'p_steering' in href:
            text = a.get_text(strip=True)
            full_url = href if href.startswith('http') else 'https://haiphong.gov.vn' + href
            bs_links.append({'href': full_url, 'text': text})
    
    print(f'BS4 links: {len(bs_links)}')
    
    # Merge unique
    seen_urls = set()
    merged = []
    for l in legal_links + bs_links:
        if l['href'] not in seen_urls and len(l['text']) > 20:
            seen_urls.add(l['href'])
            merged.append(l)
    
    print(f'Unique legal links: {len(merged)}')
    for i, l in enumerate(merged[:20]):
        print(f'  [{i+1}] {l["text"][:120]}')
        print(f'       {l["href"][:100]}')
    
    return merged

async def crawl_detail_pages(links):
    """Crawl detail pages and extract metadata + content."""
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for i, link in enumerate(links[:10]):  # Limit to 10
            print(f'\n[{i+1}/{min(10,len(links))}] Crawling: {link["text"][:60]}...')
            try:
                page = await browser.new_page()
                await page.goto(link['href'], wait_until='networkidle', timeout=20000)
                await page.wait_for_timeout(2000)
                
                title = await page.title()
                body_text = await page.inner_text('body')
                
                # Extract law number
                law_match = re.search(r'(?:Số|số)[\s.:]*(\d+[\d/]*(?:/NĐ-CP|/TT-B|/QĐ-UBND|/NQ-HĐND)[^\s]*)', body_text, re.IGNORECASE)
                law_number = law_match.group(1) if law_match else ''
                
                # Extract date
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', body_text)
                issued_date = date_match.group(1) if date_match else ''
                
                # Look for PDF link
                pdf_links = await page.evaluate('''() => {
                    const links = [];
                    document.querySelectorAll('a[href*=".pdf"]').forEach(a => {
                        links.push(a.href);
                    });
                    return links;
                }''')
                
                doc = {
                    'title': link['text'][:200],
                    'page_title': title,
                    'law_number': law_number,
                    'issued_date': issued_date,
                    'source_url': link['href'],
                    'source_name': 'UBND TP Hải Phòng',
                    'official_level': 'official',
                    'scope': 'haiphong',
                    'sector': 'HanhChinhCong',
                    'document_type': 'VanBanPhapLuat',
                    'issuing_agency': 'UBND TP Hải Phòng',
                    'body_text_snippet': body_text[:500],
                    'body_text_len': len(body_text),
                    'pdf_links': pdf_links,
                }
                results.append(doc)
                print(f'    Law: {law_number} | Date: {issued_date} | Body: {len(body_text)} chars | PDFs: {len(pdf_links)}')
                
                await page.close()
            except Exception as e:
                print(f'    ERROR: {e}')
        
        await browser.close()
    
    return results

async def main():
    links = await crawl_ubnd_hp_list()
    if not links:
        print('No links found')
        return
    
    docs = await crawl_detail_pages(links)
    
    # Save
    output_file = r'J:\ChatBotLegal\notebook_data\crawl_results\ubnd_hp_metadata.json'
    import os
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print(f'\nSaved {len(docs)} documents to {output_file}')
    
    # Try to embed into legal search
    print('\n=== EMBEDDING INTO LEGAL SEARCH ===')
    async with httpx.AsyncClient(timeout=120) as client:
        for doc in docs:
            if len(doc['body_text_snippet']) < 100:
                print(f'  SKIP (content too short): {doc["title"][:60]}')
                continue
            
            payload = {
                'title': doc['title'],
                'law_number': doc['law_number'],
                'document_type': doc.get('document_type', 'VanBanPhapLuat'),
                'issuing_agency': doc.get('issuing_agency', 'UBND TP Hai Phong'),
                'scope': doc.get('scope', 'haiphong'),
                'sector': doc.get('sector', 'HanhChinhCong'),
                'field_id': 0,
                'issued_date': doc.get('issued_date', ''),
                'effective_date': doc.get('issued_date', ''),
                'expired_date': '',
                'source_url': doc.get('source_url', ''),
                'content': doc['body_text_snippet'],
                'confirmed_official_source': True,
            }
            try:
                resp = await client.post(f'{LEGAL_SEARCH_URL}/import', json=payload)
                resp.raise_for_status()
                result = resp.json()
                print(f'  OK: {doc["title"][:60]} -> {result.get("chunks_count","?")} chunks')
            except Exception as e:
                print(f'  FAIL: {doc["title"][:60]} -> {e}')

asyncio.run(main())
