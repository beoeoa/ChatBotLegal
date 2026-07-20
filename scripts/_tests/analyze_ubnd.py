import bs4, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
html = open(r'J:\ChatBotLegal\scripts\_tests\ubnd_hp_list.html', encoding='utf-8').read()
soup = bs4.BeautifulSoup(html, 'html.parser')

# Find ALL links
links = []
for a in soup.find_all('a'):
    href = a.get('href', '')
    text = a.get_text(strip=True)
    if text and href and href != '#' and not href.startswith('javascript'):
        links.append((href, text))

output = [f'Total links: {len(links)}']
# Filter links with Vietnamese legal keywords
legal_kw = ['van', 'ban', 'phap', 'luat', 'nghi', 'dinh', 'thong', 'tu', 'quyet', 'dinh', 'chi', 'thi']
for href, text in links:
    has_kw = any(kw in text.lower() for kw in legal_kw)
    if has_kw and len(text) > 15:
        output.append(f'  [{href[:80]}] {text[:200]}')

with open(r'J:\ChatBotLegal\scripts\_tests\ubnd_hp_links.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output[:100]))
print('Analysis done')
