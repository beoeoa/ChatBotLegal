import bs4, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
html = open(r'J:\ChatBotLegal\scripts\_tests\ubnd_hp_detail.html', encoding='utf-8').read()
soup = bs4.BeautifulSoup(html, 'html.parser')

title = soup.title.string.strip() if soup.title else 'NONE'
h1 = soup.find('h1')
h1_text = h1.get_text(strip=True)[:200] if h1 else 'NONE'

# Find content area - try common selectors
output = [f'TITLE: {title[:200]}', f'H1: {h1_text}']

for sel in ['#main-content', '.detail-content', '.article-content', '.content', 'article', '.noidung', '[class*=detail]', '[class*=content]', '[class*=article]']:
    el = soup.select_one(sel)
    if el:
        txt = el.get_text(separator='\n', strip=True)
        output.append(f'\nSELECTOR: {sel} -> {len(txt)} chars')
        output.append(txt[:500])
        break

# Fallback: find div with most text
best = None
best_len = 0
for tag in soup.find_all(['div', 'article', 'section']):
    txt = tag.get_text(strip=True)
    if len(txt) > best_len:
        best_len = len(txt)
        best = tag

output.append(f'\nBEST ELEMENT: {best.name if best else "NONE"} len={best_len} id={best.get("id","") if best else ""} class={best.get("class","") if best else ""}')
if best:
    output.append(best.get_text(separator='\n', strip=True)[:1000])

with open(r'J:\ChatBotLegal\scripts\_tests\ubnd_hp_detail_structure.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))
print('Done')
