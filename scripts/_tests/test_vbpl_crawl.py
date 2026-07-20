import sys, io, os, json, time
# Force UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r"J:\ChatBotLegal")
from api.crawlers.source_registry import SourceRegistry
from api.crawlers.vbpl_crawler import VBPLCrawler

s = SourceRegistry.get("vbpl_main")
crawler = VBPLCrawler(s)

urls = crawler.discover_sitemap_urls(s.sitemap_url)
legal = crawler.filter_vbpl_urls(urls)
print(f"Sitemap: {len(urls)} total, {len(legal)} legal")

results = []
test_urls = legal[:5]
for i, url in enumerate(test_urls):
    html = crawler.fetch_page(url)
    if not html:
        results.append({"url": url, "error": "fetch_failed"})
        continue
    soup = crawler.parse_html(html)
    title = crawler.extract_title(soup)
    content = crawler.extract_content(soup)
    metadata = crawler.extract_metadata(soup)
    doc = crawler.build_document(url, title, content, metadata)
    results.append({
        "index": i+1,
        "url": url,
        "title": doc.get("title","")[:120],
        "law_number": doc.get("law_number",""),
        "scope": doc.get("scope",""),
        "issuing_agency": doc.get("issuing_agency","")[:60],
        "content_len": len(content),
        "source_type": doc.get("source_type",""),
        "official_level": doc.get("official_level",""),
    })
    print(f"[{i+1}] OK: {len(content)} chars | law={doc.get('law_number','?')} | scope={doc.get('scope','?')}")

output_path = r"J:\ChatBotLegal\notebook_data\crawl_test_results.json"
out_dir = os.path.dirname(output_path)
if not os.path.exists(out_dir):
    os.makedirs(out_dir)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nResults saved to: {output_path}")
print(f"Total crawled: {len(results)} documents")
