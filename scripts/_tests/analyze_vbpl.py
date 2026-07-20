import bs4, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

html = open(r'J:\ChatBotLegal\scripts\_tests\vbpl_sample.html', encoding='utf-8').read()
soup = bs4.BeautifulSoup(html, 'html.parser')

# Extract key info
title = soup.title.string.strip() if soup.title else 'NO TITLE'
h1 = soup.find('h1')
h1_text = h1.get_text(strip=True)[:200] if h1 else 'NO H1'

# Find content-rich elements
best = None
best_len = 0
for tag in soup.find_all(['div', 'article', 'section']):
    txt = tag.get_text(separator='\n', strip=True)
    if len(txt) > best_len:
        best_len = len(txt)
        best = tag

output = []
output.append(f'TITLE: {title[:200]}')
output.append(f'H1: {h1_text}')
output.append(f'BEST ELEMENT: tag={best.name if best else "NONE"} len={best_len}')
if best:
    output.append(f'ID: {best.get("id","")}')
    output.append(f'CLASS: {best.get("class","")}')
    output.append(f'CONTENT PREVIEW:\n{best.get_text(separator="\n",strip=True)[:500]}')

with open(r'J:\ChatBotLegal\scripts\_tests\vbpl_structure.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))

print('Done - check vbpl_structure.txt')
