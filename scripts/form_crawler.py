import sys, io, json, re, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from DrissionPage import ChromiumPage, ChromiumOptions

BASE = "https://thuvienphapluat.vn/bieumau"
OUT_DIR = r"J:\ChatBotLegal\notebook_data\forms"
os.makedirs(OUT_DIR, exist_ok=True)

DOMAIN_KEYWORDS = {
    "ho_tich": ["khai sinh", "khai tử", "kết hôn", "hôn nhân", "tình trạng hôn nhân", "nhận cha", "nhận mẹ", "nhận con", "đính chính", "thay đổi hộ tịch", "đăng ký hộ tịch", "giám hộ", "ly hôn", "thừa kế", "di sản", "hộ tịch"],
    "cu_tru": ["thường trú", "tạm trú", "lưu trú", "cư trú", "thay đổi nhân khẩu", "điều chỉnh cư trú", "tạm vắng", "đăng ký cư trú"],
    "dat_dai": ["đất đai", "chuyển nhượng", "quyền sử dụng đất", "GCN", "giấy chứng nhận", "tách thửa", "hợp thửa", "cấp phép xây dựng", "thông báo xây dựng", "sổ đỏ", "QSDĐ", "nhà ở", "thừa kế đất", "chuyển đổi đất", "đất ở"],
    "khieu_nai": ["khiếu nại", "tố cáo", "giải quyết khiếu nại", "thụ lý khiếu nại", "biên bản làm việc", "khởi kiện", "tố tụng"],
    "xu_phat": ["xử phạt", "vi phạm hành chính", "cưỡng chế", "biên bản vi phạm", "tạm giữ", "quyết định xử phạt", "chuyển vụ vi phạm"],
}

def classify_form(title):
    tl = title.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in tl:
                return domain
    return None

def parse_forms_from_html(html):
    pattern = r'href="(/bieumau/(\d+)/([A-Z0-9-]+))"[^>]*>\s*([^<]{3,200})</a>'
    forms = []
    seen = set()
    for m in re.finditer(pattern, html):
        link, fid, slug, title = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        if title and not title.startswith("Tra cứu") and fid not in seen:
            seen.add(fid)
            forms.append({"id": fid, "url": link, "slug": slug, "title": title, "full_url": f"https://thuvienphapluat.vn{link}"})
    return forms

# Search keywords per domain (each triggers ASP.NET postback = 20 new forms)
SEARCH_QUERIES = {
    "ho_tich": ["khai sinh", "kết hôn", "khai tử", "hôn nhân", "thừa kế", "nhận cha", "đính chính", "giám hộ", "ly hôn", "adoption"],
    "cu_tru": ["thường trú", "tạm trú", "cư trú", "nhân khẩu", "lưu trú", "tạm vắng"],
    "dat_dai": ["đất đai", "chuyển nhượng", "tách thửa", "xây dựng", "sổ đỏ", "GCN", "QSDĐ", "thừa kế đất", "đất ở"],
    "khieu_nai": ["khiếu nại", "tố cáo", "khởi kiện", "tố tụng", "thụ lý"],
    "xu_phat": ["xử phạt", "vi phạm", "cưỡng chế", "biên bản", "tạm giữ"],
}

def main():
    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-sandbox")
    co.set_argument("--window-size=1280,800")
    co.set_argument("--window-position=-2000,0")
    co.set_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    
    page = ChromiumPage(co)
    
    print("Loading base page...")
    page.get(BASE)
    for i in range(30):
        time.sleep(3)
        title = page.title
        if "just a moment" not in title.lower():
            print(f"  Ready: {title}")
            break
    else:
        print("  CF not resolved")
        page.quit()
        return
    
    classified = {d: [] for d in DOMAIN_KEYWORDS}
    seen_ids = set()
    
    for domain, queries in SEARCH_QUERIES.items():
        print(f"\n=== {domain} ===")
        for kw in queries:
            if len(classified[domain]) >= 35:
                print(f"  Enough for {domain}")
                break
            print(f"  Search '{kw}'...", end=" ", flush=True)
            try:
                # Go to base, type keyword, click search
                page.get(BASE)
                time.sleep(5)
                # Set keyword and click search (postback)
                page.run_js(f"document.getElementById('txtHopDong').value = '{kw}';")
                time.sleep(0.3)
                page.run_js("document.getElementById('btnHopDong').click();")
                time.sleep(8)
                
                html = page.html
                forms = parse_forms_from_html(html)
                new_count = 0
                for f in forms:
                    if f["id"] not in seen_ids:
                        seen_ids.add(f["id"])
                        d = classify_form(f["title"]) or domain
                        f["domain"] = d
                        classified[d].append(f)
                        new_count += 1
                print(f"{len(forms)} forms, {new_count} new")
            except Exception as ex:
                print(f"ERROR: {str(ex)[:80]}")
            
            # Save progress
            total = sum(len(v) for v in classified.values())
            with open(os.path.join(OUT_DIR, "forms_manifest.json"), "w", encoding="utf-8") as fo:
                json.dump({"total_crawled": len(seen_ids), "total_classified": total, "forms": classified}, fo, ensure_ascii=False, indent=2)
            
            time.sleep(2)
    
    page.quit()
    
    total = sum(len(v) for v in classified.values())
    with open(os.path.join(OUT_DIR, "forms_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"total_crawled": len(seen_ids), "total_classified": total, "forms": classified}, f, ensure_ascii=False, indent=2)
    print(f"\n=== FINAL ===")
    print(f"  Crawled: {len(seen_ids)}, Classified: {total}")
    for d, fs in classified.items():
        print(f"  {d}: {len(fs)}")

main()
